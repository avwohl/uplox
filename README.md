# plox

Compiler front-end generator. Consumes a grammar specification and emits:

1. A canonical **JSON bundle** describing the lexer DFA, parser tables, AST schema, and hook points.
2. **Driver skeletons** in C, C++, Python, and Lua that load (or embed) those tables and run the front-end.

The JSON bundle is the contract. Backends are independent and replaceable. plox itself never emits target-language code from grammar source directly — it always goes through the JSON.

## Status

**v1.3.0** — all nine phases of the original plan landed in [v1.0.0](CHANGELOG.md#100--2026-05-02); v1.1.0 closed feature parity between the Python runtime and the C / C++ / Lua backends, shipped the self-host bootstrap, and sped up the LR(1) builder by ~30%; v1.2.0 closed the post-1.0 deferred-grammar list (variadic `...`, multi-line `#define`, bit-fields, designated initializers, compound literals, full abstract declarators, `_Generic`) and lifted the action-body carve-out from the self-host grammar via a new `%balanced=` lexer feature; v1.3.0 closes the cross-language parity gap for `%balanced=` (JSON bundle, plus C / C++ / Lua / emitted-Python scanners). See [CHANGELOG.md](CHANGELOG.md) for the full release notes. The grammar DSL, the JSON bundle schema, and the hook firing points are stable; future minor releases will be backwards-compatible at all three layers.

## Goals

- Replace the hand-written front-ends in the `uc*` compiler family (uc80, uc386, uplm80, uada80, ucow, …) with a single generator.
- Re-entrant by construction: every backend keeps all parser state in a `plox_<grammar>_ctx` struct/object and prefixes generated symbols by grammar name. Two front-ends produced by plox can link into the same binary without symbol collisions.
- Pluggable hooks (pre-shift / pre-reduce / post-reduce / on-error) for context-sensitive concerns like name resolution, scoped symbol tables, and error recovery — without leaking those concerns into the grammar.
- Lexer feedback for grammars that need it (the C typedef-name hack and friends) via a runtime token-filter callback paired with the `TypedefTracker` helper.

## Module layout

```
src/plox/
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
  cli/         `plox` command
tests/
examples/      .plox grammars (calc, plm_subset, plm_full, c_subset, plox_self, …)
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
| —     | Self-host bootstrap (`plox_self.plox`)                                   | 1.1.0   |
| —     | LR(1) build perf (~30% on c_subset)                                      | 1.1.0   |
| —     | c_subset: variadic, multi-line `#define`, bit-fields                     | 1.2.0   |
| —     | c_subset: designated initializers, compound literals                     | 1.2.0   |
| —     | c_subset: full abstract declarators, `_Generic`, `sizeof(type)`          | 1.2.0   |
| —     | Lexer `%balanced=` for non-regular tokens; action bodies in plox_self    | 1.2.0   |
| —     | `%balanced=` parity in JSON bundle + C / C++ / Lua / emitted-Python      | 1.3.0   |

## Quick start

```bash
plox version                                  # plox 1.3.0 (schema 1)
plox build  examples/calc.plox -o calc.json   # grammar -> bundle
plox check  examples/calc.plox                # build + report conflicts
plox parse  calc.json input.txt               # parse a file
plox emit   calc.json --target=c   --out=gen  # emit C   driver
plox emit   calc.json --target=cpp --out=gen  # emit C++ driver
plox emit   calc.json --target=lua --out=gen  # emit Lua driver
plox emit   calc.json --target=py  --out=gen  # emit Python driver
```

`plox build` accepts `--lex-only` to omit the parse table when a host
only needs the lexer; `plox parse` accepts `--glr` to use the GLR
runtime when the bundle preserves conflicts.

## Lexer feedback (typedef-name and friends)

Some grammars need the parser to influence what the lexer returns —
the canonical example is C's `typedef foo;` followed by `foo x;`,
where `foo` lexes as a different terminal in the second position than
the first. plox handles this with a **token filter** callback the host
installs alongside its **post-reduce** hook:

* The post-reduce hook fires after every reduction; the host updates
  whatever state it needs (a typedef set, a scope stack, …).
* The token filter receives every freshly fetched lookahead (and
  re-runs after every reduce) and returns the (possibly rewritten)
  terminal kind.

Both callbacks are exposed in all four backends. The Python runtime
also ships `plox.hooks.TypedefTracker`, a turnkey helper that
implements the full classical hack on top of these primitives.

## Bundled example grammars

- `calc` — arithmetic expressions, the smoke test.
- `ambig_expr` — classically ambiguous, exercises GLR.
- `scoped` — block-scoped IDs, exercises hooks.
- `plm_subset` — the first real grammar (Phase 5). Calc-style PL/M with declarations, IF/THEN/ELSE, DO blocks.
- `plm_full` — extends `plm_subset` to the constructs Digital Research's BDOS uses (LITERALLY, INITIAL, AT, BASED, iterative DO TO/BY, DO CASE, structures). Parses the first 162 lines of the real `bdos.plm` source end-to-end.
- `c_subset` — a meaningful subset of C: function definitions, full statement repertoire (if/else/while/for/do-while/switch/break/continue/goto/return), expressions with C precedence, struct/union/enum, casts, the typedef-name lexer hack, and a preprocessor skip. Parses 21 of uc80's example C programs (vendored as fixtures).
- `plox_self` — the .plox DSL described in itself. Parses every other example grammar in this list, including its own definition — action bodies and all (the lexer's `%balanced=` extension matches `{ ... }` runs by counting nested braces).

## Documentation

- [`docs/grammar_format.md`](docs/grammar_format.md) — the `.plox` DSL spec.
- [`docs/c_backend.md`](docs/c_backend.md) — generated C API.
- [`docs/cpp_backend.md`](docs/cpp_backend.md) — generated C++ API.
- [`docs/lua_backend.md`](docs/lua_backend.md) — generated Lua module.
- [`docs/py_backend.md`](docs/py_backend.md) — generated Python module.
- [`CHANGELOG.md`](CHANGELOG.md) — versioned release notes.

## License

GPL-3.0-or-later. See `LICENSE`.
