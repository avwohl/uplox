# plox grammar source format (`.plox`)

Status: **frozen for v0**. Anything not described here is not part of v0.
Extensions go through a numbered RFC that bumps the schema version.

## File shape

A `.plox` file has up to four sections, in this order:

```
%grammar <name>
%options    (optional)
%tokens
%hooks      (optional)
%rules
```

`%grammar` declares the grammar name, which becomes the prefix for every
generated symbol in every backend (`plox_<name>_ctx`, `<name>_parse()`, …).
The name must match `[A-Za-z_][A-Za-z0-9_]*`.

Lines beginning with `#` are comments. Blank lines are ignored.

## `%options`

Key-value pairs, one per line, value is everything after the first `=`:

```
%options
start = expr
prefix_override = mygrammar
```

Recognised options for v0:

| Key                | Effect                                               |
|--------------------|------------------------------------------------------|
| `start`            | Start non-terminal. Defaults to the first rule.      |
| `prefix_override`  | Override the symbol prefix (default: `<name>`).      |

Unknown keys are an error in v0 — silent ignore would mask typos.

## `%tokens`

Each line declares one terminal:

```
NUMBER  = /[0-9]+/
IDENT   = /[A-Za-z_][A-Za-z0-9_]*/
WS      = /[ \t\n]+/    %skip
PLUS    = "+"
MINUS   = "-"
```

* `/…/` is a regex pattern (see "Regex syntax" below).
* `"…"` is a literal token; it matches the exact string.
* `%skip` after a token causes the lexer to drop matches (whitespace, comments).

Token names must be `[A-Z][A-Z0-9_]*`. Lower-case names are non-terminals.

## `%hooks`

Optional. Declares hook callback names that the host driver will resolve:

```
%hooks
push_scope   pre_reduce
pop_scope    post_reduce
log_token    pre_shift
recover      on_error
```

The four `when` keywords are the only ones recognised in v0.

## `%rules`

```
expr  : expr PLUS term     %hook=log_token  { $$ = $1 + $3; }
      | expr MINUS term                     { $$ = $1 - $3; }
      | term                                ;
term  : NUMBER                              { $$ = $1; }
      ;
```

* `:` separates LHS from the first production; `|` separates alternatives;
  `;` ends the rule.
* `%hook=<name>` attaches a hook to a single production.
* `{ … }` is the semantic action — opaque target-language source. plox does
  not parse the contents in v0; it copies the text verbatim into the JSON
  bundle and leaves interpretation to the backend.
* `$$`, `$1`, `$2`, … inside actions are passed through unchanged. Backends
  rewrite them to language-appropriate accesses on the parser context stack.

## Regex syntax

A practical subset, chosen to match what existing uc* lexers actually use:

| Form              | Meaning                                       |
|-------------------|-----------------------------------------------|
| `abc`             | literal characters                            |
| `\n \t \r \\ \/`  | escapes (newline, tab, CR, backslash, slash) |
| `\xHH`            | hex byte                                      |
| `.`               | any byte except `\n`                          |
| `[abc]` `[a-z]`   | character class                               |
| `[^abc]`          | negated class                                 |
| `r*` `r+` `r?`    | Kleene closure / one-or-more / optional       |
| `r1\|r2`          | alternation                                   |
| `(r)`             | grouping                                      |

Out of scope for v0: backreferences, lookaround, named groups, Unicode
property classes. The lexer operates on bytes; UTF-8 input works because the
regex syntax never asserts code-point boundaries.

## Conflict policy

LR(1) conflicts are reported as errors with the conflicting items, the
look-ahead set, and source positions of the offending productions. There is
no implicit shift-prefer rule in v0 — ambiguous grammars must be made
unambiguous, or moved to GLR (Phase 6).

## Stability promise

The DSL described here will not change for the v0 line. New features arrive
behind a version key (`%plox 2` at the top of the file) and the reader will
keep accepting v0 files unchanged.
