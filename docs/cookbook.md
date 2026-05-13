# Cookbook — recipes for common language features

A reference for the shapes that come up over and over when writing
grammars. Each recipe states the **shape**, the **trap** that catches
beginners, and the **fix** — usually with a pointer to a real example
grammar in `examples/`.

If you haven't read [`tutorial.md`](tutorial.md) yet, start there.

## Operator precedence — the recursion ladder

**Shape.** One non-terminal per precedence level, each defined in
terms of the next-tighter level. Tightest level (atoms / parens /
literals) sits at the bottom.

    <expr>   : <expr> '+' <term>   | <expr> '-' <term>   | <term>   ;
    <term>   : <term> '*' <factor> | <term> '/' <factor> | <factor> ;
    <factor> : NUMBER | '(' <expr> ')' ;

**Left recursion gives you left-associative operators automatically.**
`1 - 2 - 3` parses as `(1 - 2) - 3` because the recursive call is on
the *left* side of the operator. Flip the recursion to the right
side and you get right-associative operators — useful for `=`,
`**`, ternary `?:`.

**Trap: collapsing everything into one rule.**

    <expr> : <expr> '+' <expr> | <expr> '*' <expr> | NUMBER ;

This is *ambiguous*. `1 + 2 * 3` has two parses. uplox refuses to
build a conflicting LR table; you'll see shift/reduce conflicts on
every binary operator. Don't try to fix it with `%shift`; restructure
into a ladder.

**See:** `examples/calc.uplox` (textbook ladder),
`examples/plm_subset.uplox` (8-level PL/M ladder),
`examples/ambig_expr.uplox` (the deliberately broken version, used
to demonstrate the GLR runtime).

## Lists — left vs right recursion

**Shape (left-recursive, the default choice):**

    <items> : <items> <item> | <item> ;
    <items> : <items> ',' <item> | <item> ;     # with separators

**Why left?** LR parsers love left recursion. Each item is reduced
as soon as it's complete; the stack stays shallow regardless of list
length. Right recursion (`<items> : <item> <items> | <item>`) builds
up the whole list on the parse stack before reducing — fine for
short lists, a memory hazard for long ones.

**Trap: empty lists.** If a list can be empty, give the empty
production its own alternative:

    <items> : <items> <item> | ;

The dangling `|` then `;` is the epsilon production. Mixing "empty
or non-empty" with the recursive case sometimes manufactures
reduce/reduce conflicts under LALR — splitting them into
`<items_opt> : <items> | ;` cleanly separates the cases.

**See:** every grammar in `examples/`. `<block_items>` in
`examples/c23.uplox` is the canonical empty-allowed shape.

## Optional clauses

**Shape.** A non-terminal with two alternatives — one with the
clause, one empty:

    <else_part> : KW_ELSE <stmt_seq> | ;

**Trap.** It's tempting to inline the optionality at the call site
with two near-duplicate productions of the parent rule. Don't —
your `<if_stmt>` rule then has 2^N productions for N independent
optional clauses (each new clause doubles the count). The
`<else_part>` indirection costs you one non-terminal and one
extra reduction at parse time; you'll never regret it.

**See:** `<else_part>`, `<elsif_part>`, `<aspect_spec>` in
`examples/ada_full.uplox`. The same grammar uses *dozens* of
these `_opt` non-terminals; that's idiomatic.

## Nested if/then/else — the dangling-else problem

**Shape (Algol/C-family, single-statement bodies, no `END IF`):**

    IF <expr> THEN <stmt>
    IF <expr> THEN <stmt> ELSE <stmt>

**The trap.** In `IF a THEN IF b THEN s1 ELSE s2`, which `IF` does
the `ELSE` belong to? LR(1) sees a shift/reduce conflict on `ELSE`
and refuses to build.

**Three fixes, in order of preference:**

### 1. Make the syntax non-ambiguous.

Ada, Cowgol, modern PL/I, Lua all wrap their `if` with a closer
(`end if;`, `end loop;`, `END IF`). With a closer, there is no
ambiguity to resolve and no grammar gymnastics required:

    <if_stmt> : IF <expr> THEN <stmt_seq> <else_part> END IF ';' ;

If you're designing the language, this is the answer. If you're
implementing an existing language, you don't get to choose.

**See:** `examples/cowgol.uplox`, `examples/ada_full.uplox`.

### 2. Split the statement non-terminal into matched/unmatched.

The classical Aho/Sethi/Ullman fix. A "matched" statement is one
where every nested `if` has a paired `else`; an "unmatched"
statement has at least one dangling `if`. The grammar forbids
nesting an unmatched statement under a `then` that has an `else`:

    <stmt>           : <matched_stmt> | <unmatched_stmt> ;
    <matched_stmt>   : <matched_if>   | <non_if_stmt>    ;
    <unmatched_stmt> : <unmatched_if>                    ;

    <matched_if>     : IF <expr> THEN <matched_stmt> ELSE <matched_stmt> ;
    <unmatched_if>   : IF <expr> THEN <stmt>
                     | IF <expr> THEN <matched_stmt> ELSE <unmatched_stmt>
                     ;

The grammar grows. Worse, every other statement form that can
contain a body (`WHILE`, `FOR`, labeled statement) has to be threaded
through the matched/unmatched discipline too, otherwise it
re-introduces the conflict.

**See:** `examples/plm_subset.uplox` (the minimal PL/M form),
`examples/c23.uplox` (matched/unmatched threaded through `while`,
`for`, `do`, `switch`, labels, `case` — the cost of doing it
correctly for a real C-like).

### 3. Use `%shift ELSE`.

State that ELSE shifts whenever there's a choice. This is
yacc/bison's classical answer. uplox supports it but doesn't
encourage it — the matched/unmatched split is a strictly better
solution because it documents the disambiguation in the grammar.
`%shift` is invisible to anyone reading the rules alone.

See [`conflict_resolution.md`](conflict_resolution.md) for the full
discussion of `%shift` and `%reduce` and when to reach for them.

## Block scopes

**Shape.** A non-terminal for a block, with a hook attached to the
production:

    <block> : '{' <stmts> '}'  %hook=block_scope ;

The host driver registers a callback against the hook name. uplox
fires it at both `pre_reduce` (before the `<block>` reduction
collapses the contents) and `post_reduce` (after).

**Typical wiring.** Push a new scope on `pre_reduce`, pop on
`post_reduce`. The Python runtime ships `uplox.hooks.scoped_table_hooks`
as a turnkey helper.

**See:** `examples/scoped.uplox` (smallest possible scope demo,
with `%hook=block_scope` on the `<block>` production).

## Comments and whitespace

**Shape.** Declare them as tokens with `%skip`:

    WS      = /[ \t\r\n]+/                      %skip
    LINE_C  = /\/\/[^\n]*/                      %skip
    BLOCK_C = /\/\*([^*]|\*+[^*\/])*\*+\//      %skip

`%skip` causes the lexer to drop matches before they reach the
parser. The skipped runs still consume input — they just don't
produce tokens.

**Trap: block comments and the regex subset.** uplox's regex
dialect is the practical subset described in
[`grammar_format.md`](grammar_format.md#regex-syntax) — no
non-greedy operators, no lookaround. The classic C block-comment
regex `/\*([^*]|\*+[^*\/])*\*+\//` works because it spells out
"any non-star, or a run of stars not followed by a slash" by hand.
Copy it from `plm_subset.uplox` or `c23.uplox`; don't try to
reinvent it.

**Trap: nested block comments.** uplox's regex can't count brace
depth. For non-regular tokens like nested-comment-aware sources or
target-language action bodies, use `%balanced=`:

    ACTION = /\{/   %balanced='}'

The DFA matches the opener; the runtime extends the match by
counting nested (open, close) pairs until depth returns to zero.

**See:** `examples/uplox_self.uplox` uses `%balanced=` to lex action
bodies in `.uplox` files.

## String literals

**Shape.** A single regex that consumes the opening quote, the body
(including escapes), and the closing quote, treated as one token.

    STRING = /"([^"\\\n]|\\.)*"/

Read it: a `"`, then any number of "non-quote-non-backslash-non-newline,
OR backslash-anything", then another `"`. The grammar handles
escape *syntax*; escape *semantics* (turning `\n` into a newline)
is the host driver's job after the token is recognised.

**Pascal-style with doubled quotes (PL/M, SQL):**

    STRING = /'([^'\n]|'')*'/

**Trap.** Don't try to make the string contents a non-terminal.
String contents are a regular language; keep them in the lexer.

**See:** `STRING` in `examples/plm_subset.uplox` (PL/M doubled-quote
form), `examples/c23.uplox` (C-style with backslash escapes).

## Identifiers vs keywords

**Shape.** Declare keywords with `%keywords` (and a `%keyword_prefix`
if you want them prefixed in the generated table). Then declare
`IDENT` *after* the keyword block so longest-match keeps `WHILE`
matching the `WHILE` keyword rather than IDENT:

    %keyword_prefix KW_
    %keywords
    IF THEN ELSE WHILE DO END

    %tokens
    IDENT = /[A-Za-z_][A-Za-z0-9_]*/

uplox's lexer breaks longest-match ties in declaration order — so
keyword tokens declared (via `%keywords`) before `IDENT` win the
tie on equal-length matches. The synthesised token for `WHILE` is
`KW_WHILE` (with `%keyword_prefix KW_`); the bare spelling `WHILE`
in `%rules` resolves to `KW_WHILE` automatically.

**Trap: case sensitivity.** uplox is case-sensitive on token names
and on literal matches. For a case-insensitive keyword like PL/M's
`while`/`WHILE`/`While`, you have two options: pre-fold the input
in the host driver, or use a regex (`/[Ww][Hh][Ii][Ll][Ee]/`) — the
regex form is verbose but unambiguous.

**See:** `examples/plm_subset.uplox` (the canonical `%keyword_prefix
KW_` setup), `examples/cowgol.uplox` (case-sensitive keywords),
`examples/ada_full.uplox` (~80 keywords, no prefix).

## Lexer feedback — the typedef-name hack

**The problem.** In C, `typedef Foo Bar;` makes `Bar` a type name.
After that declaration, `Bar x;` parses as "type, identifier";
before that declaration, `Bar x;` doesn't parse at all (two
adjacent identifiers). So the lexer needs to know what the parser
has seen so far. This isn't a context-free situation.

**uplox's answer:** two callbacks the host installs at parse time.

* A **post-reduce hook** that fires after every reduction; the
  host updates whatever state it needs (typedef set, scope stack).
* A **token filter** that runs on every freshly-fetched lookahead
  (and re-runs after every reduce) and returns the (possibly
  rewritten) terminal kind.

The grammar itself stays pure: declare both `IDENT` and
`TYPEDEF_NAME` as terminals, write rules that distinguish them,
and let the runtime rewrite `IDENT` → `TYPEDEF_NAME` based on
the host's state.

**The Python runtime ships a turnkey helper.** `uplox.hooks.TypedefTracker`
implements the entire classical hack on top of these primitives.
For C/C++/Lua, wire `set_token_filter` and `set_post_reduce` on
the emitted parser; see the per-backend docs.

**See:** `examples/c23.uplox` (the production target of this
machinery), `src/uplox/hooks/` (the runtime), and the
"Lexer feedback" section of [`grammar_format.md`](grammar_format.md#lexer-feedback-typedef-name-and-friends).

## Ambiguous grammars on purpose — GLR

**Shape.** Some languages are genuinely ambiguous and need every
parse enumerated (think natural-language parsers, syntactic
fuzzing). Write the grammar in its natural ambiguous form, build
with conflicts preserved, parse with `uplox parse --glr`.

The CLI's standard `build` path refuses conflicts because they're
almost always bugs. To preserve them, use the Python API directly —
see `examples/ambig_expr.uplox` for the canonical demo and
`tests/test_parse_glr.py` for the runtime exercise.

**This is not a workaround for "I can't figure out how to fix my
conflict."** GLR has a real runtime cost and produces a *forest*,
not a tree. Use it for genuine ambiguity.

## Where to go next

* [`conflict_resolution.md`](conflict_resolution.md) — diagnosing
  and fixing conflicts.
* [`lr_modes.md`](lr_modes.md) — `%define lr.type` and the
  state-size trade-off.
* [`grammar_format.md`](grammar_format.md) — full DSL reference.
