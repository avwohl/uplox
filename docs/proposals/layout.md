# Proposal: `%layout` for indentation-sensitive grammars

**Status:** shipped. Implemented in the spec reader / lex filters
and used by `examples/yaml.uplox` and `examples/python.uplox`.
Haskell / F# / Nim grammars on top of `%layout` remain future work.
This document is kept as the design rationale.

**Summary.** Add a one-shot grammar directive that tells the
uplox runtime to emit synthetic `INDENT` / `DEDENT` / `NEWLINE`
terminals based on column tracking, with configurable flow
brackets that suspend the tracking. The grammar consumes the
synthetic tokens like opening / closing braces and separators —
no per-grammar host code required.

---

## Motivation

YAML, Python, Haskell, F#, Nim, CoffeeScript, Pug / Jade, and a
handful of DSLs all share the same lexical pattern: indentation
defines block structure. The algorithm is identical across them:

```
maintain a stack of indentation columns; at each line start,
compare the new column to the stack top:
  if greater: emit INDENT, push new column.
  if less:    emit one DEDENT per popped level until match.
  if equal:   emit NEWLINE separator.
suspend the entire algorithm inside flow brackets.
```

Today, `examples/yaml.uplox` declares synthetic `INDENT` /
`DEDENT` / `NEWLINE` terminals with placeholder regexes and
documents in the header that the host must install a filter
implementing the above algorithm. The filter is identical
between YAML, Python, and Haskell — only the bracket set
differs.

**Cost of the status quo.** Every indentation-sensitive grammar
ships with a paragraphs-long filter spec in its header, and
every host of every such grammar has to implement that spec.
Three planned grammars (`python.uplox`, `haskell.uplox`,
`fsharp.uplox`) plus the existing `yaml.uplox` would each
duplicate ~150 lines of column-tracking filter code in every
backend that consumes them. That's ~600 lines × 4 backends =
~2400 lines of nearly-identical code.

A runtime-side implementation is one filter, written once, used
by every grammar that opts in.

---

## Design

### Directive

A single one-shot directive at the grammar head:

```
%layout
  indent_token   = INDENT
  dedent_token   = DEDENT
  newline_token  = NEWLINE
  flow_open      = '(' '[' '{'
  flow_close     = ')' ']' '}'
  tab_width      = 8        # for mixed tab/space normalisation
  blank_lines    = skip     # or `emit_newline`
  comment_lines  = skip     # treat comment-only lines as blank
```

All keys are optional except `indent_token` / `dedent_token` /
`newline_token`, which name the synthetic terminals the runtime
will emit. The named terminals must be declared in `%tokens`
with the standard "filter-produced" idiom — a regex that never
matches at the lexer level (placeholder).

`flow_open` / `flow_close` default to empty (no flow context,
indentation always tracked). YAML uses `'(' '[' '{'` for the
default; Python uses the same; Haskell uses `'(' '[' '{'` plus
`{-` / `-}` for nested comments.

`tab_width` defaults to 8 (matching Python's PEP 8 / YAML 1.2
default). Used only to normalise mixed tab / space leading
whitespace to a column count.

`blank_lines = skip` (default) — blank lines never affect the
indent stack and never emit `NEWLINE`. The alternative
`emit_newline` is for languages where empty lines are
significant (rare, but `nim` mode uses it).

`comment_lines = skip` (default) treats lines whose
non-whitespace content is entirely a comment as blank for
indentation purposes. Python and YAML both want this.

### Runtime behaviour

The lexer runs in two passes — the existing DFA-based scan
produces raw tokens annotated with `(line, column)`, and a new
post-scan `LayoutFilter` consumes that stream and produces an
augmented stream with the synthetic terminals inserted.

Pseudocode:

```
class LayoutFilter:
    def __init__(self, cfg):
        self.cfg = cfg
        self.stack = [0]           # leading-column stack
        self.flow_depth = 0        # nesting inside flow brackets
        self.pending_newline = False
        self.at_line_start = True

    def feed(self, token):
        if token.kind in self.cfg.flow_open:
            self.flow_depth += 1
            yield token
            return
        if token.kind in self.cfg.flow_close:
            self.flow_depth -= 1
            yield token
            return
        if self.flow_depth > 0:
            yield token
            return
        if self.at_line_start:
            self.at_line_start = False
            col = token.column
            top = self.stack[-1]
            if col > top:
                self.stack.append(col)
                yield Token(self.cfg.indent_token, ...)
            elif col < top:
                while self.stack[-1] > col:
                    self.stack.pop()
                    yield Token(self.cfg.dedent_token, ...)
                # NEWLINE emitted by the previous line's end
            else:
                if self.pending_newline:
                    yield Token(self.cfg.newline_token, ...)
                    self.pending_newline = False
        yield token

    def on_line_end(self):
        if self.flow_depth == 0 and not self.at_line_start:
            self.pending_newline = True
            self.at_line_start = True

    def on_eof(self):
        if self.pending_newline:
            yield Token(self.cfg.newline_token, ...)
        while len(self.stack) > 1:
            self.stack.pop()
            yield Token(self.cfg.dedent_token, ...)
```

The host's token-filter callback chain runs AFTER this layout
filter — so a grammar can still install a typedef-tracker-style
filter on top of the layout-emitted stream.

### Grammar impact

Today's `yaml.uplox` declares:

```
INDENT  = /\x01/    # filter-emitted indent open
DEDENT  = /\x02/    # filter-emitted indent close
NEWLINE = /\x03/    # filter-emitted line break
```

Under `%layout`, those declarations become:

```
%layout
  indent_token  = INDENT
  dedent_token  = DEDENT
  newline_token = NEWLINE
  flow_open     = '{' '['
  flow_close    = '}' ']'

%tokens
INDENT  = /\x01/    # filter-emitted
DEDENT  = /\x02/    # filter-emitted
NEWLINE = /\x03/    # filter-emitted
```

The placeholder regexes stay — uplox needs them to register the
terminal IDs. The header documentation block describing the
filter goes away.

For `python.uplox` the directive would be identical except for
the flow bracket set:

```
%layout
  indent_token  = INDENT
  dedent_token  = DEDENT
  newline_token = NEWLINE
  flow_open     = '(' '[' '{'
  flow_close    = ')' ']' '}'
  tab_width     = 8
```

### Backend impact

* **Python runtime** (`uplox.parse.runtime`): add `LayoutFilter`
  class, wire it ahead of the existing host-filter chain when the
  bundle's `lex` section carries a `layout` config.
* **C / C++ / Lua emitters**: emit the equivalent state machine
  inline in the generated lexer driver. The config carries over
  to a static struct in the emitted lexer.
* **JSON bundle**: add `lex.layout` object with the directive's
  values verbatim. Schema bump from 1 to 2 — back-compat: old
  bundles have no `lex.layout` key and behave as before.

---

## Alternatives considered

**Per-grammar host filter (status quo).** The current approach.
Works, but every grammar duplicates the spec and every backend
host duplicates the implementation. Doesn't scale beyond the
two indentation-sensitive grammars we already have (yaml,
upcoming python).

**Lexer-mode-stack with mode-switching tokens.** Some lexer
generators (flex's start conditions) handle this with named
modes. Doesn't fit uplox's DFA model — modes would require
either runtime DFA switching (slow) or N×modes pre-built DFAs
(table bloat).

**Embed the indentation algorithm in the grammar itself via
explicit INDENT/DEDENT non-terminals with empty-string regex
hacks.** Not actually possible — the algorithm needs (line,
column) of each token, which the parser doesn't see.

**A general "%lexer_hook" callback that runs after token
production.** Equivalent to the current filter mechanism;
doesn't help with the duplication problem.

---

## Open questions

1. **Should `flow_open` / `flow_close` accept arbitrary tokens
   or only literal bracket characters?** Haskell's `{- -}` block
   comments and Lua's `[[ ]]` would want multi-character. The
   simpler design is "any declared terminal name"; users put
   the bracket character in a token and reference the token.
   
2. **Should `blank_lines` have a third mode that emits the
   `NEWLINE` only between non-blank lines (today's `skip`) but
   also emits `DEDENT` if a blank line is followed by a less-
   indented non-blank line?** This matches PEP 8 / most
   indent-tracked tooling. Probably yes; default to that
   behaviour and document it as "Python-style".

3. **Mixed tabs / spaces.** Python raises `TabError` if the
   indentation can't be unambiguously interpreted. Should the
   layout filter mirror that, or treat mixed-indent as an
   on_error hook event? Probably the latter — let the host
   decide whether mixed is fatal.

4. **Should the directive's keys be specified per-line as in
   the sketch above, or as a flat `KEY = VALUE` list like
   `%define`?** The `%define KEY VALUE` style is already
   established for `lr.type`; reusing it would be more uniform
   but bumps the number of directives the host has to know
   about. Multi-line block form is more discoverable.

---

## What lands together

The minimum viable implementation:

1. Spec reader accepts `%layout` block with the documented keys.
2. Python runtime ships the `LayoutFilter` class.
3. Bundle schema 2 adds `lex.layout` config.
4. `yaml.uplox` migrated to use `%layout` (validates the
   design against a real grammar).
5. One indentation-using test grammar (`examples/python_lite.uplox`
   or similar) demonstrates the directive on a second language.

The C / C++ / Lua backends ship the inlined state machine in a
follow-up — Python alone proves the design.
