# Proposal: `%continuation` for line-continuation markers

**Status:** draft, 2026 — design only, no implementation.
Motivated by the planned `python.uplox` (`\` at line end),
`fortran.uplox` (F90 `&` at line end; F77 column-6 indicator
via [`%columns`](columns.md)), `cobol.uplox` (column-7 `-`),
SQL dialects, and several DSLs.

**Summary.** Add a directive that names a token whose presence
at line end signals "this line continues into the next" — the
runtime collapses the two physical lines into one logical line
before the parser sees them. Optional `%layout` interaction
suppresses INDENT / DEDENT / NEWLINE emission across the
continuation. One sentence in the grammar instead of a
hand-written rule that reaches into every host backend.

---

## Motivation

Many languages let a long statement straddle multiple physical
lines via a marker at the end of the first line. The markers
differ but the runtime behaviour is identical: the lexer / parser
should see the two physical lines as one logical line.

| Language        | Marker                                  |
|-----------------|-----------------------------------------|
| Python          | `\` at end of line (outside brackets)   |
| Fortran F90+    | `&` at end of line (and optionally at start of next) |
| Fortran F77     | non-space, non-zero in column 6         |
| COBOL fixed     | `-` in column 7                         |
| Bash / sh       | `\` at end of line                      |
| SQL (some dialects) | `\` at end of line                  |
| Tcl             | `\` at end of line                      |
| Makefile        | `\` at end of line (recipe continuation) |

Today, every grammar that wants line continuation has to:

1. Lex the source preserving newlines.
2. Install a host-side filter that detects the marker before
   each newline and consumes both the marker and the newline.
3. Document the filter requirement.

The filter is the same eight-line algorithm in every grammar.

---

## Design

### Directive

A one-shot directive at the grammar head:

```
%continuation
  marker = '\'              # the marker character, OR
  marker = '&'              # F90 free-form, OR
  marker = '-' at_column 7  # COBOL fixed-form, OR
  marker = CONTINUE_TOKEN   # named token from %tokens
  applies_in_brackets = false   # default: suppress in flow
  preserve_position = true       # for diagnostics
```

**`marker`** names what counts as a continuation. Three forms:

* **Single character** (`marker = '\'`) — the literal byte at
  end of line (immediately before the newline, or with only
  whitespace between marker and newline).
* **Single character at a specific column** (`marker = '-' at_column 7`)
  — the byte only counts as continuation when it appears in
  the specified column. Used by COBOL fixed-format alongside
  the `%columns` directive.
* **Named token** (`marker = CONTINUE_TOKEN`) — the
  declared token's appearance at end of line is the marker.
  Used when the marker is more than one character (`&\n` for
  F90, where the `&` is plain text and the newline is what
  ends the line). The host can also use this form to reuse a
  token emitted by `%columns` (the F77 column-6 case via the
  `continuation_if_nonblank` clause).

**`applies_in_brackets`** (default `false`) — most languages
implicitly continue lines inside parens / brackets / braces
(`(`...`)` etc.); explicit marker continuation is only needed
outside. Python's behaviour. Set to `true` for languages where
the marker is required even inside brackets (rare).

**`preserve_position`** (default `true`) — when continuing, the
runtime keeps the original `(line, column)` of each token
rather than rebasing onto the joined logical line. Lets error
messages point to the actual source location. Set to `false`
if downstream tooling expects rebased positions (the SQL
case, sometimes).

### Runtime behaviour

A new `ContinuationFilter` runs between the raw lexer and the
host's filter chain (and ahead of any `%layout` filter — the
continuation collapse happens first so layout sees the
already-joined logical lines).

```
class ContinuationFilter:
    def feed(self, token, line_ctx):
        if self.bracketed and not self.cfg.applies_in_brackets:
            yield token; return
        if token.kind == NEWLINE:
            if self.previous_was_marker():
                # swallow both the marker and the newline
                self.drop_pending_marker()
                return
            yield token; return
        if self.is_marker(token):
            self.buffer_pending(token)
            return
        # not a marker, not a newline
        self.flush_pending()
        yield token
```

The filter buffers one token of lookbehind (the most recent
non-whitespace token) to decide whether a newline should be
suppressed.

For `at_column` markers, the filter checks the token's column
attribute. For named-token markers, it checks the token kind.

### Grammar impact

Python:

```
%continuation
  marker = '\'

%tokens
BACKSLASH = '\'   # used by the filter; never reaches the parser
...
```

(The backslash token still has to be declared so the filter
can produce it; it just doesn't appear in any rule.)

Fortran F90 free-form:

```
%continuation
  marker = '&'

%tokens
AMP = '&'   # filter-consumed when at end-of-line
```

Fortran F77 fixed-form — leverages `%columns`:

```
%columns
  ...
  cols 6  continuation_if_nonblank = CONTINUE_LINE
  ...

%continuation
  marker = CONTINUE_LINE
```

The `%columns` directive emits `CONTINUE_LINE` for column-6
nonblanks; `%continuation` consumes it and joins the lines.
Two directives composing cleanly.

COBOL fixed-form:

```
%columns
  ...
  cols 7  continuation_if = '-' continuation_token = CONTINUE_LINE
  ...

%continuation
  marker = CONTINUE_LINE
```

Same composition pattern.

### Backend impact

* **Python runtime**: add `ContinuationFilter`, wire it into the
  filter chain ahead of `%layout`.
* **C / C++ / Lua emitters**: emit the buffering state machine
  inline in the generated lexer. ~40 lines per emitter.
* **JSON bundle**: `lex.continuation` config with the
  directive's values. Schema bump.

---

## Alternatives considered

**Document the filter in every grammar and let hosts write
it.** Status quo. Same duplication problem as `%layout` and
`%columns`.

**Make line continuation a per-token attribute (`%continuation`
on individual tokens in `%tokens`).** Awkward — continuation is
a per-grammar property, not a per-token property. The marker
itself is the only "continuation token"; flagging every
content token as "can_continue" would be wrong.

**Use the `%balanced=` machinery to slurp continuation
sequences as one token.** Doesn't work — `%balanced=` consumes
a delimited region into a single token. Continuation isn't a
delimited region; it's a per-line decision.

**Have `%layout` subsume continuation.** Layout already
suppresses newlines inside flow brackets — extending it to
also recognise `\` at line end is plausible. But many
continuation-using languages aren't layout-sensitive
(Fortran F90, COBOL, Bash, SQL). Keeping the two directives
orthogonal lets each apply alone.

---

## Open questions

1. **Whitespace between marker and newline.** Python allows
   `x = 1 \   \n` (trailing whitespace after the backslash is
   a syntax error in CPython but accepted by some
   alternatives). Should the filter be strict (marker
   immediately before newline) or lenient (marker plus
   whitespace plus newline)? The CPython-strict default with
   an opt-out flag is probably right.

2. **Multiple continuation markers in a row.** If the joined
   logical line itself ends with another marker, recursive
   continuation. The filter should handle this naturally
   (each continuation collapses one newline; the next
   physical line is examined independently). Worth an
   explicit test.

3. **F90's optional leading `&` on the continued line.** Some
   F90 styles write `print *, "hello, " &\n   & "world"` —
   the leading `&` on the continuation line is allowed for
   alignment. The filter would need to also skip a leading
   `&` after a continuation. Probably an opt-in
   `marker_repeats_at_continuation = true` flag.

4. **Interaction with comments.** Python treats `# comment` at
   end of line as a continuation-killer — `x = 1 \ # comment`
   is a syntax error. Other languages don't care. Probably an
   opt-in `comment_blocks_continuation = true` flag.

5. **Diagnostic position.** When line N continues onto line
   N+1 and a parse error happens at the joined column 50
   (originally column 5 of line N+1), which position should
   the error message use? `preserve_position = true` keeps
   the original; `false` reports the joined position. Default
   true; most users want "show me the offending line as
   typed."

---

## What lands together

Minimum viable:

1. Spec reader accepts `%continuation` directive with the
   documented forms.
2. Python runtime ships `ContinuationFilter`, wired into the
   filter chain.
3. Bundle schema gains `lex.continuation`.
4. One existing grammar gets migrated (most likely the
   upcoming `python.uplox`, since Python is the canonical
   use case).
5. F77 / COBOL grammars then compose `%columns` +
   `%continuation` (validates the two-directive interaction).

C / C++ / Lua backends ship the state machine in a follow-up.

---

## Composition: how the three lexer-feedback proposals interact

All three of `%layout`, `%columns`, `%continuation` are
filter-like transformations applied between the raw DFA lexer
and the parser. They run in a specific order:

```
raw bytes
   │
   ▼
DFA lexer (regex-driven)
   │
   ▼ raw tokens with (line, col)
%columns  ── strips skip-mode bytes, emits continuation /
              comment / area-zone synthetic tokens, dispatches
              body bytes to the DFA
   │
   ▼
%continuation  ── collapses marker + newline pairs, joining
                   physical lines into logical lines
   │
   ▼
%layout  ── tracks indentation on the joined logical lines,
             emits INDENT / DEDENT / NEWLINE
   │
   ▼
Host-installed token filter (typedef tracker, etc.)
   │
   ▼
Parser
```

A grammar can opt into any combination. Most use none (today's
grammars). Python uses `%layout` + `%continuation`. F77 uses
`%columns` + `%continuation`. F90 uses `%continuation` only.
COBOL fixed uses `%columns` + `%continuation`. YAML uses
`%layout` only.
