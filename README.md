# uplox

Compiler front-end generator. Consumes a grammar specification and emits:

1. A canonical **JSON bundle** describing the lexer DFA, parser tables, AST schema, and hook points.
2. **Driver skeletons** in C, C++, Python, and Lua that load (or embed) those tables and run the front-end.

The JSON bundle is the contract. Backends are independent and replaceable. uplox itself never emits target-language code from grammar source directly — it always goes through the JSON.

## Status

**v2.0.0** — breaking change to the `.uplox` grammar source format:
single-quote token literals (`'('` not `"("`), `<name>` for every
non-terminal reference (LHS and RHS), and a new `%keyword_prefix` /
`%keywords` shortcut that collapses `KW = "KW"` boilerplate into a
whitespace-separated list. v1 grammars do not parse under the new
reader; all bundled examples and the self-host grammar were ported in
this release. See [`docs/grammar_format.md`](docs/grammar_format.md)
for the current syntax and [CHANGELOG.md](CHANGELOG.md) for the full
1.x → 2.0.0 history.

## Goals

- Replace the hand-written front-ends in the `uc*` compiler family (uc80, uc386, uplm80, uada80, ucow, …) with a single generator.
- Re-entrant by construction: every backend keeps all parser state in a `uplox_<grammar>_ctx` struct/object and prefixes generated symbols by grammar name. Two front-ends produced by uplox can link into the same binary without symbol collisions.
- Pluggable hooks (pre-shift / pre-reduce / post-reduce / on-error) for context-sensitive concerns like name resolution, scoped symbol tables, and error recovery — without leaking those concerns into the grammar.
- Lexer feedback for grammars that need it (the C typedef-name hack and friends) via a runtime token-filter callback paired with the `TypedefTracker` helper.
- Selectable LR construction algorithm: `%define lr.type {canonical-lr | lalr | ielr}`. Default canonical LR(1) for clean diagnostics; opt into LALR(1) when build time or bundle size start to hurt; opt into IELR(1) when you want LALR-size tables but the grammar produces a spurious LALR conflict you can't easily restructure away. Per-terminal `%shift` / `%reduce` escape hatches resolve ambiguity in any mode. (Concrete data: `examples/ada_full.uplox` builds to 58611 states under canonical LR(1) and 1594 states under LALR(1) — ~37× shrink, both conflict-free; IELR matches LALR on every example grammar in this repo, since none have spurious LALR conflicts.)

## Module layout

```
src/uplox/
  spec/        grammar source reader, IR
  lex/         regex → NFA → DFA construction, minimization
  parse/       LR(1) item-set construction, table builder
    glr/       GLR extension
  ast/         node schema, default builder actions
  hooks/       pluggable parse-time callbacks (TypedefTracker, ScopedNameTable, …)
  tables/      JSON schema, canonical serializer
  gen/
    c/         C backend (re-entrant, context-struct based)
    cpp/       C++ backend (class per grammar, namespaced)
    py/        Python backend (embeds bundle, reuses runtime)
    lua/       Lua backend (single 5.3+ module per grammar)
  cli/         `uplox` command
tests/
examples/      .uplox grammars (calc, plm_subset, plm_full, c_subset, uplox_self, …)
docs/          DSL spec, per-backend notes
```

## Phasing

All nine phases of the original plan landed in v1.0.0; the v1.1.0 polish round closed the items the v1.0.0 release notes called out as deferred at the cross-language level (token-filter ABI, post-reduce hook, GLR symmetry, self-host); v1.2.0 closed the post-1.0 deferred-grammar list for c_subset and lifted the action-body carve-out from the self-host grammar.

| Phase | Deliverable                                                              | Shipped |
|-------|--------------------------------------------------------------------------|---------|
| 1     | Repo skeleton, pyproject, CI, license, grammar source format frozen      | 1.0.0   |
| 2     | Lexer pipeline: regex → NFA → DFA → JSON, Python driver, tests           | 1.0.0   |
| 3     | LR(1) parser builder, conflict reporting, JSON, Python driver            | 1.0.0   |
| 4     | AST schema + default builder + hooks framework                           | 1.0.0   |
| 5     | Port the smallest uc* front-end as the first real grammar (`plm_subset`) | 1.0.0   |
| 6     | GLR extension                                                            | 1.0.0   |
| 7     | C backend (re-entrant) — the highest-value target                        | 1.0.0   |
| 8     | C++ and Lua backends                                                     | 1.0.0   |
| 9     | Port remaining uc* front-ends; declare v1                                | 1.0.0   |
| —     | C / C++ / Lua: token-filter ABI + post-reduce hook                       | 1.1.0   |
| —     | Self-host bootstrap (`uplox_self.uplox`)                                   | 1.1.0   |
| —     | LR(1) build perf (~30% on c_subset)                                      | 1.1.0   |
| —     | c_subset: variadic, multi-line `#define`, bit-fields                     | 1.2.0   |
| —     | c_subset: designated initializers, compound literals                     | 1.2.0   |
| —     | c_subset: full abstract declarators, `_Generic`, `sizeof(type)`          | 1.2.0   |
| —     | Lexer `%balanced=` for non-regular tokens; action bodies in uplox_self    | 1.2.0   |
| —     | `%balanced=` parity in JSON bundle + C / C++ / Lua / emitted-Python      | 1.3.0   |
| —     | Parse-error diagnostics (expected-token list, EOI rendering) parity      | 1.4.0   |
| —     | DSL rework: `<name>` non-terminals, `'…'` literals, `%keywords`          | 2.0.0   |

## Install

```bash
pip install uplox        # PyPI distribution name
```

The PyPI distribution is `uplox` because the bare `uplox` name on PyPI
is already taken by an unrelated matplotlib helper; the GitHub repo
([avwohl/uplox](https://github.com/avwohl/uplox)) was renamed to
match. The Python module, the CLI binary, and the `.uplox` grammar
format all keep the original `uplox` name — `pip install uplox`
installs a top-level `import uplox` and a `uplox` command.

## Quick start

```bash
uplox version                                  # uplox 2.0.0 (schema 1)
uplox build  examples/calc.uplox -o calc.json   # grammar -> bundle
uplox check  examples/calc.uplox                # build + report conflicts
uplox parse  calc.json input.txt               # parse a file
uplox emit   calc.json --target=c   --out=gen  # emit C   driver
uplox emit   calc.json --target=cpp --out=gen  # emit C++ driver
uplox emit   calc.json --target=lua --out=gen  # emit Lua driver
uplox emit   calc.json --target=py  --out=gen  # emit Python driver
```

`uplox build` accepts `--lex-only` to omit the parse table when a host
only needs the lexer; `uplox parse` accepts `--glr` to use the GLR
runtime when the bundle preserves conflicts.

## Lexer feedback (typedef-name and friends)

Some grammars need the parser to influence what the lexer returns —
the canonical example is C's `typedef foo;` followed by `foo x;`,
where `foo` lexes as a different terminal in the second position than
the first. uplox handles this with a **token filter** callback the host
installs alongside its **post-reduce** hook:

* The post-reduce hook fires after every reduction; the host updates
  whatever state it needs (a typedef set, a scope stack, …).
* The token filter receives every freshly fetched lookahead (and
  re-runs after every reduce) and returns the (possibly rewritten)
  terminal kind.

Both callbacks are exposed in all four backends. The Python runtime
also ships `uplox.hooks.TypedefTracker`, a turnkey helper that
implements the full classical hack on top of these primitives.

## Bundled example grammars

- `calc` — arithmetic expressions, the smoke test.
- `calc_ast` — same language as `calc`, annotated with the v3 auto-AST
  surface (`%ast=`, `?`, `@field`, `%ast_drop`). Emitted Python module
  produces typed `BinOp` / `NumLit` dataclasses instead of a generic
  parse tree. See [`docs/proposals/calc_annotated.md`](docs/proposals/calc_annotated.md).
- `cowgol_ast` — full Cowgol grammar with v3 annotations. 78 tokens, 182
  productions, 68 AST kinds. Validates the v3 surface at real-language
  scale: 12-level expression ladder, 11 auto-optional clauses, 13
  list-shaped rules, nested type expressions, statement sequences.
  Walkthrough: [`docs/proposals/cowgol_annotated.md`](docs/proposals/cowgol_annotated.md).
- `ambig_expr` — classically ambiguous, exercises GLR.
- `scoped` — block-scoped IDs, exercises hooks.
- `plm_subset` — the first real grammar (Phase 5). Calc-style PL/M with declarations, IF/THEN/ELSE, DO blocks.
- `plm_full` — extends `plm_subset` to the constructs Digital Research's BDOS uses (LITERALLY, INITIAL, AT, BASED, iterative DO TO/BY, DO CASE, structures). Parses the first 162 lines of the real `bdos.plm` source end-to-end.
- `c_subset` — a meaningful subset of C: function definitions, full statement repertoire (if/else/while/for/do-while/switch/break/continue/goto/return), expressions with C precedence, struct/union/enum, casts, the typedef-name lexer hack, and a preprocessor skip. Parses 21 of uc80's example C programs (vendored as fixtures).
- `uplox_self` — the .uplox DSL described in itself. Parses every other example grammar in this list, including its own definition — action bodies and all (the lexer's `%balanced=` extension matches `{ ... }` runs by counting nested braces).

## Documentation

Getting started:

- [`docs/tutorial.md`](docs/tutorial.md) — your first uplox grammar, walking through `calc`.
- [`docs/cookbook.md`](docs/cookbook.md) — recipes for common language features (precedence, optional clauses, nested if/then/else, scopes, identifiers vs keywords, lexer feedback).
- [`docs/lr_modes.md`](docs/lr_modes.md) — picking `%define lr.type` (canonical-LR / LALR / IELR) with concrete state-count costs.
- [`docs/conflict_resolution.md`](docs/conflict_resolution.md) — diagnosing conflicts, when to use `%shift` / `%reduce`, and when to restructure instead.
- [`docs/semantics.md`](docs/semantics.md) — what the parser passes to semantic actions and how to use that interface to build an AST.

Reference:

- [`docs/grammar_format.md`](docs/grammar_format.md) — the `.uplox` DSL spec.
- [`docs/c_backend.md`](docs/c_backend.md) — generated C API.
- [`docs/cpp_backend.md`](docs/cpp_backend.md) — generated C++ API.
- [`docs/lua_backend.md`](docs/lua_backend.md) — generated Lua module.
- [`docs/py_backend.md`](docs/py_backend.md) — generated Python module.
- [`CHANGELOG.md`](CHANGELOG.md) — versioned release notes.

## License

GPL-3.0-or-later. See `LICENSE`.
