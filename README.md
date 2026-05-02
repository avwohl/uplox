# plox

Compiler front-end generator. Consumes a grammar specification and emits:

1. A canonical **JSON bundle** describing the lexer DFA, parser tables, AST schema, and hook points.
2. **Driver skeletons** in C, C++, Python, and Lua that load (or embed) those tables and run the front-end.

The JSON bundle is the contract. Backends are independent and replaceable. plox itself never emits target-language code from grammar source directly — it always goes through the JSON.

## Status

Pre-alpha. Phase 1 (skeleton) in progress.

## Goals

- Replace the hand-written front-ends in the `uc*` compiler family (uc80, uc386, uplm80, uada80, ucow, …) with a single generator.
- Re-entrant by construction: every backend keeps all parser state in a `plox_<grammar>_ctx` struct/object and prefixes generated symbols by grammar name. Two front-ends produced by plox can link into the same binary without symbol collisions.
- Pluggable hooks (pre-shift / pre-reduce / post-reduce / on-error) for context-sensitive concerns like name resolution, scoped symbol tables, and error recovery — without leaking those concerns into the grammar.

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

| Phase | Deliverable                                                              |
|-------|--------------------------------------------------------------------------|
| 1     | Repo skeleton, pyproject, CI, license, grammar source format frozen      |
| 2     | Lexer pipeline: regex → NFA → DFA → JSON, Python driver, tests           |
| 3     | LR(1) parser builder, conflict reporting, JSON, Python driver            |
| 4     | AST schema + default builder + hooks framework                           |
| 5     | Port the smallest uc* front-end as the first real grammar                |
| 6     | GLR extension                                                            |
| 7     | C backend (re-entrant) — likely the highest-value target                 |
| 8     | C++ and Lua backends                                                     |
| 9     | Port remaining uc* front-ends; declare v1                                |

## License

GPL-3.0-or-later. See `LICENSE`.
