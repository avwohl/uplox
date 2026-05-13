# uplox grammar source format (`.uplox`)

The canonical example is [`examples/uplox_self.uplox`](../examples/uplox_self.uplox),
the .uplox grammar that describes the .uplox DSL itself.

## File shape

A `.uplox` file starts with `%grammar`, then has any combination of
sections and one-shot directives:

```
%grammar <name>
%options          (optional)
%define KEY VALUE (optional, one-shot, repeatable for distinct keys)
%keyword_prefix   (optional, one-shot directive)
%keywords         (optional)
%tokens
%hooks            (optional)
%shift            (optional)
%reduce           (optional)
%rules
```

`%grammar` declares the grammar name, which becomes the prefix for every
generated symbol in every backend (`uplox_<name>_ctx`, `<name>_parse()`, …).
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

## `%define KEY VALUE` (directive)

Bison-style one-shot directive setting a single key to a single value.
Each `%define` line is independent; repeat the directive for distinct keys:

```
%define lr.type lalr
```

Supported keys:

| Key       | Values                       | Default        | Effect                                      |
|-----------|------------------------------|----------------|---------------------------------------------|
| `lr.type` | `canonical-lr` `lalr` `ielr` | `canonical-lr` | LR table construction algorithm (see below) |

Unknown keys are an error. Values are validated against the per-key set
above; an unrecognised value rejects the build. Pick `lr.type lalr`
when build time or bundle size start to hurt and you've verified your
grammar is LALR-friendly (a quick A/B `uplox check` with and without
the directive surfaces any LALR-only conflicts up-front). The
`%shift` and `%reduce` escape hatches apply identically to both
construction modes.

### `lr.type` algorithm choice

**`canonical-lr` (default)** keeps each LR(1) state distinct by its
lookahead set. Tables are larger but every conflict reported by
`uplox check` is a real LR(1) conflict — no merge artefacts.

**`lalr`** post-processes the canonical table by collapsing states
that share the same LR(0) core (items with lookaheads stripped),
taking the union of lookahead sets per merged state. Tables are
typically ~10× smaller; build times shrink proportionally. The cost
is that LALR-merging can manufacture **reduce/reduce** conflicts that
canonical LR(1) doesn't have — when two states with the same core
were each unambiguous on their own lookahead, the merged state may
have two competing reduces on the same lookahead. (Shift/reduce
conflicts present in the canonical table are preserved.) When this
happens, the right move is usually a small grammar restructure (e.g.
splitting an empty production into explicit empty + non-empty halves);
see the generic-renaming case in `examples/ada_full.uplox` for a
worked example.

**`ielr`** is the best-of-both: same conflict count as canonical
LR(1), table size typically equal to LALR(1). Implementation is
iterative refinement on top of LALR — start with the LALR mapping,
detect any conflicts in the merged table that don't exist at any of
the contributing canonical states, force those contributing states
into singleton merge groups, repeat until stable. For
LALR-friendly grammars (most real grammars) IELR returns after
the first iteration with a table identical to LALR. For grammars
that have LALR-only conflicts (e.g. the ASU 4.47 textbook case),
IELR splits exactly the states needed and stops. Worst case it
degenerates to canonical LR(1) — guaranteeing the canonical
conflict count regardless of how aggressive the merging needed to be.

This is a simplified post-merge-split refinement, not the full
Pager / Denny IELR algorithm — the full algorithm computes a
finer-grained per-action split that can produce strictly smaller
tables in edge cases. The simplified splitter matches the full
algorithm's table size on every grammar measured in this repo so
far. Pick `ielr` when you want LALR-size tables but get a spurious
LALR conflict you can't easily restructure away.

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
  target-language action bodies inside the .uplox DSL itself are the
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
* `{ … }` is the semantic action — opaque target-language source. uplox
  does not parse the contents; it copies the text verbatim into the JSON
  bundle, where `$$` / `$1` / `$2` / … are preserved unchanged. No
  current backend interprets the action body: the C / C++ / Lua emitters
  ignore it and build a generic parse tree the host shapes after the
  fact, and the Python runtime lets hosts register real Python callables
  keyed by production index (also bypassing the body text). The action
  body in `.uplox` source is therefore documentation today — useful for
  conveying intent to readers. See [`semantics.md`](semantics.md) for
  what the parser actually hands to your semantics and the patterns for
  building an AST from it.

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
is no implicit shift-prefer rule by default — ambiguous grammars must be
made unambiguous, listed under `%shift` (see below), or built with
conflicts preserved and parsed via the GLR runtime (`uplox parse --glr`).

## `%shift`

Optional. Lists terminals on which shift/reduce conflicts should be
silently resolved in favour of shift, yacc-style. Reduce/reduce
conflicts are *not* affected and will still surface as errors.

```
%shift
ELSE
KW_pragma KW_with
```

Whitespace separates entries; line breaks are insignificant. Each entry
must resolve to a declared terminal — either a `%tokens` name or a
`%keywords` spelling (the latter is mapped through the keyword-prefix
alias).

Use sparingly. The intended use case is the dangling-else family of
ambiguities, where LALR(1) lookahead is one token short and the
greedy-shift interpretation is uniformly correct. For most grammar
ambiguities, restructuring the productions is the right fix.

When a listed terminal participates in a shift/reduce conflict, the
shift is recorded and the conflict is dropped from the table's
`conflicts` list — `uplox check` will not flag it. This means a typo in
`%shift` (a terminal that never actually conflicts) is silent. Run with
the `%shift` line removed first to confirm the conflict you intend to
silence really exists.

## `%reduce`

Optional. The dual of `%shift`: lists terminals on which shift/reduce
conflicts should be silently resolved in favour of the (longer)
reduce. Reduce/reduce conflicts are still surfaced as errors. A
terminal listed in both `%shift` and `%reduce` is rejected.

```
%reduce
SEMI
```

Same surface syntax as `%shift` (whitespace-separated entries, line
breaks insignificant, `%keywords` aliases honoured).

Use case: an LALR state-merge artefact (or genuine LR(1) ambiguity in
a position the grammar can't easily restructure) offers a spurious
shift on a terminal that is genuinely in the followset of the
to-be-reduced non-terminal. `%reduce` closes the non-terminal and
lets the surrounding decl shift the terminal cleanly.

The canonical example in this repo is
`<subtype_indication> -> <name> 'range' <simple_expr>` in
`examples/ada_full.uplox`: at `subtype X is T range Y;` the SEMI
shift would extend a different production whose state was merged in;
`%reduce SEMI` closes the subtype indication first.

Same warning as `%shift`: a typo in `%reduce` (a terminal that never
actually conflicts) is silent. Land the new alternative WITHOUT the
`%reduce` line first to confirm the conflict you intend to resolve
really exists.

## Lexer feedback (typedef-name and friends)

Some grammars need parser state to influence terminal classification —
the canonical case is C's `typedef Foo;` followed by `Foo x;`, where
`Foo` is a different terminal in each position. uplox handles this
without a DSL extension: hosts install a **token filter** callback at
parse time that rewrites a lookahead's terminal kind based on whatever
state they're tracking, and a `%hook=<name>` on the typedef-bearing
production fires after each reduce so the host can update that state
between tokens.

The mechanics live in the runtime, not the grammar source. The Python
runtime ships `uplox.hooks.TypedefTracker` as a turnkey implementation;
C / C++ / Lua hosts wire `set_token_filter` and `set_post_reduce` on the
emitted parser. See [`c_backend.md`](c_backend.md), [`cpp_backend.md`](cpp_backend.md),
[`lua_backend.md`](lua_backend.md), and [`py_backend.md`](py_backend.md) for the per-backend ABIs.
