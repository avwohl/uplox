# plox

Compiler front-end generator. Consumes a grammar specification and emits:

1. A canonical **JSON bundle** describing the lexer DFA, parser tables, AST schema, and hook points.
2. **Driver skeletons** in C, C++, Python, and Lua that load (or embed) those tables and run the front-end.

The JSON bundle is the contract. Backends are independent and replaceable. plox itself never emits target-language code from grammar source directly — it always goes through the JSON.

## Status

**v1.0.0** — all nine phases of the original plan are landed. See `CHANGELOG.md` for what shipped. The grammar DSL, the JSON bundle schema, and the hook firing points are stable; future minor releases will be backwards-compatible at all three layers.

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
    glr/       GLR extension (phase 6)
  ast/         node schema, default builder actions
  hooks/       pluggable parse-time callbacks
  tables/      JSON schema, canonical serializer
  gen/
    c/         C backend (re-entrant, context-struct based)
    cpp/       C++ backend (class per grammar, namespaced)
    py/        Python backend
    lua/       Lua backend
  cli/         `plox` command
tests/
examples/
docs/
```

## Phasing

All nine phases of the original plan landed in v1.0.0:

| Phase | Deliverable                                                              | Shipped |
|-------|--------------------------------------------------------------------------|---------|
| 1     | Repo skeleton, pyproject, CI, license, grammar source format frozen      | yes     |
| 2     | Lexer pipeline: regex → NFA → DFA → JSON, Python driver, tests           | yes     |
| 3     | LR(1) parser builder, conflict reporting, JSON, Python driver            | yes     |
| 4     | AST schema + default builder + hooks framework                           | yes     |
| 5     | Port the smallest uc* front-end as the first real grammar (`plm_subset`) | yes     |
| 6     | GLR extension                                                            | yes     |
| 7     | C backend (re-entrant) — the highest-value target                        | yes     |
| 8     | C++ and Lua backends                                                     | yes     |
| 9     | Port remaining uc* front-ends; declare v1                                | yes     |

## Quick start

```bash
plox build  examples/calc.plox -o calc.json   # grammar -> bundle
plox check  examples/calc.plox                # build + report conflicts
plox parse  calc.json input.txt               # parse a file
plox emit   calc.json --target=c   --out=gen  # emit C   driver
plox emit   calc.json --target=cpp --out=gen  # emit C++ driver
plox emit   calc.json --target=lua --out=gen  # emit Lua driver
plox emit   calc.json --target=py  --out=gen  # emit Python driver
```

## Bundled example grammars

- `calc` — arithmetic expressions, the smoke test.
- `ambig_expr` — classically ambiguous, exercises GLR.
- `scoped` — block-scoped IDs, exercises hooks.
- `plm_subset` — the first real grammar (Phase 5). Calc-style PL/M with declarations, IF/THEN/ELSE, DO blocks.
- `plm_full` — extends `plm_subset` to the constructs Digital Research's BDOS uses (LITERALLY, INITIAL, AT, BASED, iterative DO TO/BY, DO CASE, structures). Parses the first 162 lines of the real `bdos.plm` source end-to-end.
- `c_subset` — a meaningful subset of C: function definitions, full statement repertoire (if/else/while/for/do-while/switch/break/continue/goto/return), expressions with C precedence, struct/union/enum, casts, the typedef-name lexer hack, and a preprocessor skip. Parses 21 of uc80's example C programs (vendored as fixtures).
- `plox_self` — the .plox DSL described in itself. Parses every other example grammar in this list, including its own definition. Action bodies (`{ ... }`) are out of scope; hosts strip them in a pre-pass.

## License

GPL-3.0-or-later. See `LICENSE`.
