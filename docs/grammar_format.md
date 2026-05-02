# plox grammar source format (`.plox`)

Status: **stable since v1.0**. The DSL described here is what plox
1.x accepts; backwards-compatible extensions arrive behind a future
schema bump (`%plox 2`) and pre-bump files keep parsing unchanged.
Anything not described here is not part of the v1 DSL.

The canonical example is [`examples/plox_self.plox`](../examples/plox_self.plox),
the .plox grammar that describes the .plox DSL itself.

## File shape

A `.plox` file starts with `%grammar`, then has up to four
sections — conventionally in this order, but the reader accepts
any order:

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

Recognised options:

| Key                | Effect                                               |
|--------------------|------------------------------------------------------|
| `start`            | Start non-terminal. Defaults to the first rule.      |
| `prefix_override`  | Override the symbol prefix (default: `<name>`).      |

Unknown keys are an error — silent ignore would mask typos.

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

The four `when` keywords are the only ones recognised. See also the
`%hook=<name>` annotation under `%rules` below for attaching a hook
to a single production.

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
* `%hook=<name>` attaches a hook to a single production. The hook's name
  must match a `%hooks` entry. The runtime fires it at both `pre_reduce`
  and `post_reduce` for every reduction of that production.
* `{ … }` is the semantic action — opaque target-language source. plox
  does not parse the contents; it copies the text verbatim into the JSON
  bundle and leaves interpretation to the backend.
* `$$`, `$1`, `$2`, … inside actions are passed through unchanged. Backends
  rewrite them to language-appropriate accesses on the parser context stack.

An empty alternative is `|` followed immediately by `|` or `;` (the
epsilon production). Lists like `xs : xs x | ;` are idiomatic.

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

Out of scope: backreferences, lookaround, named groups, Unicode
property classes. The lexer operates on bytes; UTF-8 input works because
the regex syntax never asserts code-point boundaries.

## Conflict policy

LR(1) conflicts are reported as errors with the conflicting items, the
look-ahead set, and source positions of the offending productions. There
is no implicit shift-prefer rule — ambiguous grammars must be made
unambiguous, or built with conflicts preserved and parsed via the GLR
runtime (`plox parse --glr`).

## Lexer feedback (typedef-name and friends)

Some grammars need parser state to influence terminal classification —
the canonical case is C's `typedef Foo;` followed by `Foo x;`, where
`Foo` is a different terminal in each position. plox handles this
without a DSL extension: hosts install a **token filter** callback at
parse time that rewrites a lookahead's terminal kind based on whatever
state they're tracking, and a `%hook=<name>` on the typedef-bearing
production fires after each reduce so the host can update that state
between tokens.

The mechanics live in the runtime, not the grammar source. The Python
runtime ships `plox.hooks.TypedefTracker` as a turnkey implementation;
C / C++ / Lua hosts wire `set_token_filter` and `set_post_reduce` on the
emitted parser. See [`c_backend.md`](c_backend.md), [`cpp_backend.md`](cpp_backend.md),
[`lua_backend.md`](lua_backend.md), and [`py_backend.md`](py_backend.md) for the per-backend ABIs.

## Stability promise

The DSL described here will not change for the v1 line. New features
arrive behind a version key (`%plox 2` at the top of the file) and the
reader will keep accepting v1 files unchanged.
