# Proposal: `%columns` for fixed-format / card-image grammars

**Status:** draft, 2026 — design only, no implementation.
Motivated by the planned `fortran.uplox` (F77 fixed-form) and
`cobol.uplox` grammars, both of which partition each source
line into column ranges with different lexical rules.

**Summary.** Add a `%columns` directive that maps column ranges
to lexer modes — comment, label, body, continuation indicator,
or "skip". The runtime uses the column position of each raw
byte to decide which rule applies, lifting the row-and-column
context out of every host-side filter and into uplox itself.

---

## Motivation

Fortran F77 (and its FORTRAN IV ancestor) uses the original
80-column-punched-card layout, preserved into the file-on-disk
era:

    column  1     2  3  4  5  6   7..72             73..80
            └─── label area ──┘   │  └── body ──┘   sequence
                                  continuation indicator

* Column 1 with `C`, `c`, `*`, or `!` → entire line is a comment.
* Columns 1–5 → optional statement label (integer).
* Column 6 → continuation indicator: any non-space, non-zero
  character means "this is a continuation of the previous
  statement" — the body of this line is concatenated onto the
  prior line's body.
* Columns 7–72 → the actual statement body. Whitespace inside
  the body is irrelevant; `END IF` and `ENDIF` are the same
  token.
* Columns 73–80 → ignored (card sequence number area).

COBOL fixed-format does the same thing with different column
boundaries:

    column  1..6   7    8..11    12..72        73..80
            seq    ind  area-A   area-B        identification

* Columns 1–6 → sequence number, ignored.
* Column 7 → indicator: `*` or `/` for comment, `-` for
  continuation, `D` for debug, space for normal.
* Columns 8–11 (Area A) → division headers, section names,
  paragraph names, level numbers (01, 77, 88).
* Columns 12–72 (Area B) → sentences and statements.
* Columns 73–80 → identification, ignored.

Both are wildly non-regular. The lexer needs to know
"what column am I in?" at every raw character to apply the
right rule.

**Cost of doing this in a host filter.** The filter would have
to maintain (or query) per-token line/column annotations, then
re-tokenise input as a side-effect on every line start.
Effectively, every host re-implements a column-aware
pre-lexer. Three planned grammars (F77 fixed, F90 free-form
with fixed-form fallback, COBOL fixed) plus any future
card-image language (RPG II, PL/I) would each ship the same
~200-line filter per backend.

A runtime-side implementation is one extension to the lexer
loop, written once, configured per grammar.

---

## Design

### Directive

A multi-line block at the grammar head:

```
%columns
  width = 80
  cols 1      comment_if = 'Cc*!'        # F77: column-1 comment marker
  cols 1-5    mode       = label         # F77: statement label area
  cols 6      continuation_if_nonblank = 'CONT_MARK'
  cols 7-72   mode       = body          # F77: statement body
  cols 73-80  mode       = skip          # F77: card sequence area
```

Each `cols <range>` clause declares a behaviour for a column or
column range. Modes:

* **`comment_if = '<chars>'`** — if the byte at this column is
  one of the listed characters, the rest of the line (through
  EOL) is treated as a comment and discarded. Implies the rest
  of the columns in this clause behave the same way on the
  comment lines.
* **`continuation_if = '<chars>'`** / **`continuation_if_nonblank
  = 'TOKEN_NAME'`** — if the byte matches, emit the named token
  to signal "this line continues the previous". The grammar
  consumes that token to chain lines. (`if_nonblank` is the F77
  convention: any non-space, non-zero character.)
* **`mode = label`** — treat this column range as a token-emitting
  zone where the only valid tokens are integer literals
  (statement labels). Effectively a "narrow regex set" sub-lexer.
* **`mode = body`** — the normal lexing rules apply here. The
  bulk of the line.
* **`mode = skip`** — discard these columns entirely. They never
  reach the lexer.
* **`mode = areaA` / `mode = areaB`** — synonyms for `body` but
  emit a synthetic terminal at zone-entry / zone-exit boundaries.
  Lets the COBOL grammar require certain productions (division
  headers, level numbers) in Area A only.

`width` (default 80) is the line-wrap column — columns beyond it
are silently skipped, allowing source files with longer lines to
remain compatible. For free-form Fortran (F90+), `width` is set
high (132 or unlimited) and only columns-1 and continuation
behaviour are used.

### Runtime behaviour

The lexer's outer loop changes from "consume bytes until DFA
accepts a token" to "consume bytes within the current column's
mode until the column boundary OR the DFA accepts":

```
for each line:
    for each (col_start, col_end, clause) in columns_config:
        bytes = line[col_start:col_end+1]
        if clause.comment_if and bytes[0] in clause.comment_if:
            discard rest of line
            break
        if clause.continuation_if and bytes[0] matches:
            emit Token(clause.continuation_token, ...)
            continue
        if clause.mode == skip:
            continue
        if clause.mode == label:
            try to lex an INT_LIT from bytes, ignoring whitespace
            if found: emit it
            continue
        if clause.mode == body:
            feed bytes into the main DFA
```

The DFA itself doesn't change. The outer loop becomes a
column-mode dispatcher.

### Grammar impact

F77 fixed-form grammar header:

```
%grammar fortran77

%columns
  width = 72
  cols 1      comment_if   = 'Cc*!'
  cols 1-5    mode         = label
  cols 6      continuation_if_nonblank = CONTINUE_LINE
  cols 7-72   mode         = body
```

COBOL fixed-form grammar header:

```
%grammar cobol

%columns
  width = 72
  cols 1-6    mode         = skip
  cols 7      comment_if   = '*/'
  cols 7      continuation_if = '-' continuation_token = CONTINUE_LINE
  cols 7      debug_if     = 'D'    debug_token        = DEBUG_LINE
  cols 8-11   mode         = areaA
  cols 12-72  mode         = areaB
```

Grammars opt in by declaring the directive; everything else
about the lexer (regex tokens, balanced brackets, the existing
`%skip` flag for in-body whitespace) works as before.

### Backend impact

* **Python runtime**: the outer scanner loop already iterates
  by character; gating it by `(column, mode)` is one method.
  The `ColumnsConfig` object lives on the bundle alongside
  `lex.layout` and friends.
* **C / C++ / Lua emitters**: emit a column-aware outer loop
  in the generated lexer. Adds ~100 lines per emitter; one-time
  cost.
* **JSON bundle**: `lex.columns` array of clauses with the
  directive's values verbatim. Schema bump.

---

## Alternatives considered

**Pre-process the source before lexing.** Have the host strip
columns 73–80, prepend a marker for comments / continuation,
then feed the result to a normal lexer. Works for F77 in
practice (this is what some F77→F90 converters do). Doesn't
work for COBOL — Area A vs Area B is a property the grammar
needs to use, not just discard.

**Two pre-built DFAs per zone, switched by column position.**
Equivalent to the proposed design but exposes the DFA structure
in the user-facing directive, which is the wrong abstraction
level. The proposal hides that detail.

**One generic "%columns_filter" callback.** Same problem as
the layout case: every grammar reinvents the same algorithm.

---

## Open questions

1. **Free-form Fortran (F90+).** Free-form keeps the
   column-1-comment-marker rule from fixed-form (`!` anywhere
   triggers a comment) but drops the 6/7/72 boundaries. Should
   the directive accept both forms in one grammar (so a single
   `fortran.uplox` handles both flavours via a runtime
   flag)? Or should there be two grammars? The latter is
   cleaner; the former is what FPC and gfortran do.

2. **Continuation lines.** The proposal emits a token at the
   continuation column; the grammar consumes it via a
   `<statement_line>` rule that accepts the continuation
   marker. An alternative is to physically concatenate the
   bodies in the lexer, so the parser sees one logical line.
   Concatenation matches the historical "card image" model
   better but loses position information for diagnostics. I
   recommend the token-emission approach; the host's
   post-reduce hook can rejoin if needed. (See also the
   parallel `%continuation` proposal.)

3. **Tab handling.** Real F77 sources sometimes use tabs to
   shift past column 6. Treat tab as advancing the column
   counter to the next multiple of `tab_width` (default 8)?
   Or as a hard error? Probably the former, matching common
   compiler behaviour.

4. **Interaction with `%layout`.** Layout-sensitive
   languages don't have fixed columns; fixed-column languages
   don't have indentation-sensitive structure. The two
   directives are mutually exclusive in practice. The reader
   should reject a grammar that declares both.

---

## What lands together

Minimum viable:

1. Spec reader accepts `%columns` block with the documented
   clause forms.
2. Python lexer runtime: column-aware outer loop.
3. Bundle schema gains `lex.columns`.
4. `fortran77.uplox` example grammar validates the directive
   against F77 (the simpler case).
5. `cobol.uplox` example follows, exercising the `areaA` /
   `areaB` modes and the multi-character indicator.

C / C++ / Lua backends ship the column-aware lexer loop in a
follow-up.
