# Tutorial — your first uplox grammar

This is the "hello, world" tour. By the end you can read every other
example in `examples/` and you know which doc to open next.

If you only have time for one example, open
[`examples/calc.uplox`](../examples/calc.uplox) — it's the running
grammar used throughout this tutorial.

## The toolchain in 60 seconds

uplox is a *generator*. You write a `.uplox` source file, run `uplox
build`, and get back a JSON bundle that any of the four backends (C,
C++, Python, Lua) can load and run.

The two commands you'll use constantly while developing a grammar are:

    uplox check examples/calc.uplox
    uplox build examples/calc.uplox -o /tmp/calc.json

`check` reports the grammar's size and any conflicts; it's your inner
loop. `build` actually emits the JSON. To try parsing real input
without writing a host program yet:

    uplox build examples/calc.uplox -o /tmp/calc.json
    echo '1 + 2 * 3' | uplox parse /tmp/calc.json /dev/stdin

The `check` output for `calc` looks like:

    examples/calc.uplox: calc — 8 tokens, 0 hooks, 9 productions, 16 states, 0 conflicts

Read it left-to-right: 8 declared terminals, 0 hooks wired, 9
productions in `%rules`, 16 LR states in the generated table, and 0
conflicts. **States** and **conflicts** are the two numbers you'll
watch most. See [`lr_modes.md`](lr_modes.md) for what to do when the
state count explodes, and [`conflict_resolution.md`](conflict_resolution.md)
for what to do when conflicts appear.

## Anatomy of a tiny grammar

Here is `examples/calc.uplox` in full. We'll walk through it section
by section.

    %grammar calc

    %define lr.type lalr
    %options
    start = expr

    %tokens
    NUMBER = /[0-9]+/
    PLUS   = '+'
    MINUS  = '-'
    STAR   = '*'
    SLASH  = '/'
    LPAREN = '('
    RPAREN = ')'
    WS     = /[ \t\n]+/   %skip

    %rules
    <expr>   : <expr> '+' <term>        { $$ = $1 + $3; }
             | <expr> '-' <term>        { $$ = $1 - $3; }
             | <term>                   { $$ = $1; }
             ;

    <term>   : <term> '*' <factor>      { $$ = $1 * $3; }
             | <term> '/' <factor>      { $$ = $1 / $3; }
             | <factor>                 { $$ = $1; }
             ;

    <factor> : NUMBER                   { $$ = $1; }
             | '(' <expr> ')'           { $$ = $2; }
             ;

### `%grammar calc`

Names the grammar. This name becomes the prefix for every symbol the
backends emit (`uplox_calc_ctx`, `calc_parse`, etc.). The name must
match `[A-Za-z_][A-Za-z0-9_]*`.

### `%define lr.type lalr`

Picks the LR construction algorithm. For a grammar this small it
doesn't matter — both canonical-LR(1) and LALR(1) produce
single-digit-state tables. For anything larger you almost certainly
want `lalr`; see [`lr_modes.md`](lr_modes.md).

### `%options`

Free-form key=value pairs. The most common one is `start`, which
names the entry non-terminal. If you leave it out, the *first* rule
in `%rules` is the start symbol. Spelling it out is a good habit —
moving rules around shouldn't silently change what the parser
recognises.

### `%tokens`

Every terminal the grammar can see. Two flavours:

* `NAME = /regex/` — the lexer matches this regex and produces a
  `NAME` token. The regex dialect is the practical subset documented
  in [`grammar_format.md`](grammar_format.md#regex-syntax).
* `NAME = '...'` — a literal token. The lexer matches the exact
  string. Use this for punctuation and fixed operators.

`%skip` at the end of a token line tells the lexer to drop matches.
That's how whitespace and comments get filtered out before the parser
sees them.

A non-obvious win: once a token has a literal form (`PLUS = '+'`),
you can write `'+'` directly inside `%rules` instead of remembering
the name `PLUS`. Either spelling resolves to the same terminal.

### `%rules`

Productions, in BNF-ish form:

* `<name>` is a non-terminal. Always angle-bracketed, on both the LHS
  and the RHS.
* Bare uppercase (or otherwise) identifiers on the RHS are terminal
  names from `%tokens`.
* `'...'` on the RHS is an inline literal — same idea as in `%tokens`.
* `:` separates LHS from first alternative, `|` separates further
  alternatives, `;` ends the rule.
* `{ ... }` is the **semantic action**. uplox copies the contents
  verbatim into the JSON bundle as opaque text. The Python runtime
  ignores the body and lets hosts register real callables keyed by
  production index; the C / C++ / Lua backends ignore the body
  entirely and emit a generic parse tree the host shapes after the
  fact. See [`semantics.md`](semantics.md) for the full hand-off.

### Reading the precedence ladder

The three-level ladder `<expr> → <term> → <factor>` is the
single most important pattern in grammar writing. It encodes
operator precedence directly in the recursion structure: `*` and
`/` bind tighter than `+` and `-` because they're "one rung lower"
and a `<term>` is more eagerly closed than an `<expr>`. Left
recursion (`<expr> : <expr> '+' <term>`) gives you left-associative
operators automatically.

[`cookbook.md`](cookbook.md) has the long version: when to use left
vs right recursion, how many rungs you need for a real language, how
to encode unary minus, etc.

## The minimum viable workflow

For a new grammar I'd start with:

1. **Tokens first.** Get `%tokens` right and run `uplox check`. With
   no rules the parser stage errors, but the lexer is still validated.
2. **One rule, then build up.** Start with the start symbol matching
   a single terminal; expand outward. `uplox check` after every step.
3. **Test against real input early.** Build to JSON, pipe a small
   sample through `uplox parse`. If you can't parse a one-line
   example, no amount of grammar-staring will help.
4. **Stay on canonical-LR(1) until states or build time hurt.** It
   gives the most honest conflict diagnostics. Switch to LALR(1)
   when the build is annoying; see [`lr_modes.md`](lr_modes.md).

## Where to go next

* [`cookbook.md`](cookbook.md) — recipes for common language
  features (precedence, optional clauses, lists, nested
  if/then/else, scopes, identifiers vs keywords).
* [`conflict_resolution.md`](conflict_resolution.md) — what to do
  when `uplox check` reports conflicts.
* [`lr_modes.md`](lr_modes.md) — `%define lr.type` in detail with
  concrete state-count costs.
* [`grammar_format.md`](grammar_format.md) — the full DSL reference.
* [`c_backend.md`](c_backend.md), [`cpp_backend.md`](cpp_backend.md),
  [`lua_backend.md`](lua_backend.md), [`py_backend.md`](py_backend.md)
  — the per-language ABIs once you want to embed your parser.
