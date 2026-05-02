# plox grammar source format (`.plox`)

The canonical example is [`examples/plox_self.plox`](../examples/plox_self.plox),
the .plox grammar that describes the .plox DSL itself.

## File shape

A `.plox` file starts with `%grammar`, then has any combination of
sections and one-shot directives:

```
%grammar <name>
%options          (optional)
%keyword_prefix   (optional, one-shot directive)
%keywords         (optional)
%tokens
%hooks            (optional)
%rules
```

`%grammar` declares the grammar name, which becomes the prefix for every
generated symbol in every backend (`plox_<name>_ctx`, `<name>_parse()`, …).
The name must match `[A-Za-z_][A-Za-z0-9_]*`.

Lines beginning with `#` are comments. Blank lines are ignored. Sections
can appear in any order; `%keyword_prefix` is a single directive line and
must come before any `%keywords` block that depends on it.

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

## `%keyword_prefix` (directive)

```
%keyword_prefix KW_
```

Sets the prefix for tokens synthesised from `%keywords`. With the prefix
above, listing `WHILE` synthesises a token named `KW_WHILE`. The default
prefix is empty, in which case the token name equals the keyword spelling.

## `%keywords`

```
%keywords
HALT ENABLE DISABLE
IF THEN ELSE WHILE DO END
AND OR XOR NOT
```

Whitespace-separated identifiers, possibly across multiple lines. For each
entry `KW`, the reader synthesises a token whose name is `<prefix><KW>`
and whose literal is `KW` itself. In `%rules`, the bare identifier `KW`
on a production RHS resolves to the synthesised token. The expanded form
`<prefix><KW>` is also a valid spelling.

The win: PL/M-style grammars no longer have to write `WHILE = "WHILE"`
once for every keyword. Existing `%tokens` declarations always take
precedence — listing a name in `%keywords` whose synthesised token name
already exists in `%tokens` is an error.

## `%tokens`

Each line declares one terminal:

```
NUMBER  = /[0-9]+/
IDENT   = /[A-Za-z_][A-Za-z0-9_]*/
WS      = /[ \t\n]+/    %skip
PLUS    = '+'
MINUS   = '-'
```

* `/…/` is a regex pattern (see "Regex syntax" below).
* `'…'` is a literal token; it matches the exact string. Single-quoted.
* `%skip` after a token causes the lexer to drop matches (whitespace, comments).
* `%balanced='<close>'` after a token marks it as a balanced-bracket
  token: the DFA matches only the opening pattern, and the runtime
  extends the match by counting nested instances of (open, close) until
  depth returns to zero. Used for content that is not a regular language —
  target-language action bodies inside the .plox DSL itself are the
  canonical example.

Token names match `[A-Za-z_][A-Za-z0-9_]*`. There is no case rule —
case does not distinguish terminals from non-terminals; angle brackets
do that (see `%rules`).

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
<expr>  : <expr> '+' <term>     %hook=log_token  { $$ = $1 + $3; }
        | <expr> '-' <term>                      { $$ = $1 - $3; }
        | <term>                                 ;
<term>  : NUMBER                                 { $$ = $1; }
        ;
```

* Non-terminals are written `<name>` everywhere — both LHS and RHS.
* Bare identifiers on the RHS are terminal names: a declared token, or
  a synthesised keyword.
* `'…'` is an inline string literal; it must match the literal of some
  declared token. Use this for punctuation in the grammar text without
  having to remember the per-token name (e.g., `'('` instead of
  `LPAREN`).
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
epsilon production). Lists like `<xs> : <xs> <x> | ;` are idiomatic.

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
