# uplox

[![Tests](https://github.com/avwohl/uplox/actions/workflows/pytest.yml/badge.svg)](https://github.com/avwohl/uplox/actions/workflows/pytest.yml)

Compiler front-end generator. Consumes a grammar specification and emits:

1. A canonical **JSON bundle** describing the lexer DFA, parser tables, AST schema, and hook points.
2. **Driver skeletons** in C, C++, Python, and Lua that load (or embed) those tables and run the front-end.

The JSON bundle is the contract. Backends are independent and replaceable. uplox itself never emits target-language code from grammar source directly — it always goes through the JSON.

## Goals

- Replace the hand-written front-ends in the `uc*` compiler family (uc80, uc386, uplm80, uada80, ucow, …) with a single generator.
- Re-entrant by construction: every backend keeps all parser state in a `uplox_<grammar>_ctx` struct/object and prefixes generated symbols by grammar name. Two front-ends produced by uplox can link into the same binary without symbol collisions.
- Pluggable hooks (pre-shift / pre-reduce / post-reduce / on-error) for context-sensitive concerns like name resolution, scoped symbol tables, and error recovery — without leaking those concerns into the grammar.
- Lexer feedback for grammars that need it: the C typedef-name hack and friends via a runtime token-filter callback paired with the `TypedefTracker` helper, plus three declarative directives that subsume the most common context-sensitive lexing patterns — `%layout` (INDENT / DEDENT / NEWLINE for indentation-sensitive languages: YAML, Python, Haskell), `%columns` (column-range dispatch for card-image languages: Fortran F77, COBOL), `%continuation` (line-continuation marker handling: Python `\`, Fortran `&`, COBOL column-7 `-`). See [`docs/proposals/`](docs/proposals/) for the design notes.
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
examples/      .uplox grammars: production (c23, plm_*, ada_*, cowgol,
               uplox_self), feature smoke tests (calc, ambig_expr, …),
               and first-cut language grammars (cpp, go, java,
               javascript, kotlin, swift, typescript, tsx, …)
docs/          DSL spec, per-backend notes
```

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
uplox version                                  # uplox 3.1.0 (schema 1)
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

## Lexer feedback

Some grammars need the parser to influence what the lexer returns, as with
the C typedef-name hack. uplox handles lexer feedback with a **token filter**
callback plus a **post-reduce** hook. The
[cookbook](docs/cookbook.md#lexer-feedback--the-typedef-name-hack) explains
the two callbacks and the turnkey Python helpers.

## Documentation

Getting started:

- [`docs/example_grammars.md`](docs/example_grammars.md) — the 30+ bundled example grammars: production-tested, feature smoke tests, and first-cut modern-language grammars.
- [`docs/backend_maturity.md`](docs/backend_maturity.md) — how far each of the four backends (Python, C, C++, Lua) has been tested in production.
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
- [`docs/history.md`](docs/history.md) — the original phase plan and which release closed each phase.

## Related projects

Compilers in [github.com/avwohl](https://github.com/avwohl) that consume
uplox-built bundles. Each one builds its grammar from `examples/<g>.uplox`
and parses through the Python runtime, so these projects are the
real-world test surface for the production-tested grammars listed in
[`docs/example_grammars.md`](docs/example_grammars.md).

- [uc_core](https://github.com/avwohl/uc_core) — Shared C23 frontend and AST optimizer. It reads `c23.uplox`. The backend protocol is independent of the target, and these targets use it:
- [uc80](https://github.com/avwohl/uc80) — C compiler for the Z80 processor and CP/M. It is the Z80 backend on this C23 frontend.
- [uc386](https://github.com/avwohl/uc386) — C23 compiler for the i386 processor and MS-DOS. It scores 100 percent on `gcc-c-torture` (1514 tests) and `c-testsuite` (220 tests), and it writes real DOS `.exe` files with NASM and PMODE/W.
- [uplm80](https://github.com/avwohl/uplm80) — PL/M-80 compiler for the Z80 processor and CP/M. It writes 8080 and Z80 assembly language. It reads `plm_pre.uplox`, the preprocessor for LITERALLY and the `$` directives, and then `plm_full.uplox`. It rebuilds original CP/M utilities such as the BDOS from their PL/M-80 source code.
- [uada80](https://github.com/avwohl/uada80) — Ada compiler for the Z80 processor and CP/M 2.2. It compiles a subset of Ada 2012, writes MP/M II `.prl` files, and reads `ada_full.uplox`. It scores 100 percent on ACATS A/C/D/E/L (2846/2846) and 97.8 percent on the GNAT run time (1072/1096). [WIP.md](WIP.md) gives the numbers for each corpus.
- [ucow](https://github.com/avwohl/ucow) — Cowgol compiler for the Z80 processor and CP/M. It reads `cowgol.uplox` through the Python parser module `uplox_cowgol.py` that uplox writes.

## License

GPL-3.0-or-later. See `LICENSE`.