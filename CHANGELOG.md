# Changelog

All notable changes to plox land here. Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); the project follows
[Semantic Versioning](https://semver.org/spec/v2.0.0.html) for the public
surface (CLI, JSON bundle schema, Python API, hook firing points).

## Unreleased

Backwards-compatible additions to the C, C++, and Lua backends. Each one
mirrors a runtime feature that had been Python-only, closing the parity
gap so non-Python hosts can implement the C typedef-name hack and other
lexer-feedback grammars end-to-end in their target language.

### Added

- **Self-host bootstrap.** `examples/plox_self.plox` describes the .plox
  DSL in itself. plox builds it (52 states, conflict-free) and the
  generated parser handles every committed example .plox, including
  parsing its own definition. The bootstrap reader at
  `plox/spec/reader.py` still ships because the self-host grammar
  doesn't cover embedded action bodies (`{ ... }` needs balanced-brace
  matching, a non-LR construct); hosts using the self-host grammar strip
  actions in a pre-pass first.
- **~30% faster LR(1) build.** Two stacked optimisations: pre-compute
  FIRST(rhs[pos:]) per (production, position) at compile time, and
  bucket items by next-symbol in `build_lr1`'s per-state loop instead of
  re-iterating items per symbol via `_goto`. c_subset (1649 states)
  drops from ~21s to ~14s on the reference machine; the full test suite
  drops from ~57s to ~45s.
- **C, C++, Lua: token-filter ABI.** Hosts can install a callback that
  receives the lookahead's terminal kind and source text and returns
  the (possibly rewritten) terminal kind. Fires on every freshly fetched
  lookahead and again after every reduction. Mirrors the Python
  runtime's `token_filter=` parameter.
  - C:   `plox_<g>_set_token_filter(ctx, fn, user_data)`
  - C++: `parser.set_token_filter(std::function<...>)` (TokenFilter typedef)
  - Lua: `parser:set_token_filter(function ... end)`
- **C, C++, Lua: post-reduce hook.** A general callback invoked after
  every successful reduction, before the runtime re-applies the token
  filter. Hosts use it to update typedef tables, scope stacks, error
  counters — anything they want visible to the next action lookup.
  - C:   `plox_<g>_set_post_reduce(ctx, fn, user_data)`
  - C++: `parser.set_post_reduce(std::function<...>)` (PostReduce typedef)
  - Lua: `parser:set_post_reduce(function ... end)`
- **GLR runtime: default-reduction parity with LR.** `GLRTable.default_reductions`
  is propagated from the underlying LR table; the GLR reduce phase
  consults it when no explicit actions exist for the current
  (state, lookahead). Lexer-feedback grammars now work the same under GLR.
- **End-to-end typedef-name tests.** Three new tests (one per non-Python
  backend) exercise the full classical hack: a tiny grammar where
  `typedef Foo;` registers Foo and a subsequent `Foo;` parses via the
  TNAME-prefixed alternative. Together with the Python-side test that
  shipped in 1.0.0, the hack is now covered end-to-end across all four
  backends.

### Status of post-1.0 follow-ups

- **Done in this cycle**: token-filter ABI in C/C++/Lua, post-reduce
  hook in C/C++/Lua, GLR default-reduction symmetry, ~30% faster LR(1)
  build, self-host bootstrap. All but the last appeared on the 1.0.0
  release notes' "out of scope (post-v1)" list; self-host was the
  remaining plan item from `plox_plan.txt`.
- **Still deferred**: full `bdos.plm` (needs upstream macro expander),
  function-pointer abstract declarators, variadic `...`, compound
  literals, designated initializers, `_Generic`, bit-fields, multi-line
  preprocessor macros, embedded action bodies in the self-host grammar.

## 1.0.0 — 2026-05-02

First stable release. All nine phases of the original plan land here.

### Lexer

- Regex AST → Thompson NFA → subset-construction DFA → Moore minimization,
  with deterministic state numbering for byte-identical re-builds.
- Maximal-munch scanner with priority-by-declaration order.
- `%skip` flag for tokens (whitespace, comments, preprocessor lines).
- JSON serialization round-trips bit-perfectly through the canonical bundle.

### Parser

- Canonical LR(1) item-set construction with FIRST/FOLLOW.
- Conflict reporting names the colliding actions; `plox check` and
  `plox build` surface them.
- Default reductions: states whose only valid actions are reduces by a
  single production reduce eagerly on any unmapped lookahead. Required for
  lexer-feedback grammars (typedef-name etc.); also useful as an LR(1) /
  LALR(1) optimisation. Round-trip through the JSON bundle, recomputed at
  load time when a pre-1.0 bundle lacks the field.
- GLR extension (Phase 6): stack-list runtime, ambiguity packing into
  `AmbiguityNode`. Same conflict-tolerance, same default-reduction
  behaviour as the LR runtime.

### AST + hooks

- Default tree builder (`ParseNode`); per-production semantic actions can
  override.
- Hooks fire at four points: `pre_shift`, `pre_reduce`, `post_reduce`,
  `on_error`. Per-production attachment with `%hook=<name>` in the grammar.
- Built-in helpers: `ScopedNameTable`, `TypeTable`, `SyncSetRecovery`,
  `TypedefTracker`. The last is the host-side piece of the C
  typedef-name lexer hack.
- Token filter callback (`token_filter=` on `parse()`): rewrite a
  lookahead's terminal name based on host state, with re-application after
  every reduce so a hook updating that state is visible before the next
  action lookup.

### Backends

All four backends emit re-entrant code: every parser instance keeps state
on a per-instance struct/object, and generated symbols are prefixed by
grammar name so two grammars can link into the same binary.

- **C** (Phase 7): generated header + impl, `plox_<g>_ctx` struct,
  `plox_<g>_parse(ctx, &root)` entry point. Default reductions land in a
  `plox_<g>_default_reduction` array.
- **C++** (Phase 8): one `Parser` class per grammar in a `plox_<g>`
  namespace, pImpl-hidden state, single ownership via a per-parser
  `unique_ptr` arena.
- **Lua** (Phase 8): single Lua 5.3+ module exporting `M.new(input)`,
  `parser:parse()`, and the production / token enums.
- **Python** (Phase 9): self-contained shim that embeds the bundle and
  reuses the plox runtime. `parse(text, *, hooks, semantic_actions,
  token_filter)` mirrors the runtime's signature exactly.

### Bundled grammars

- `calc` — smoke-test arithmetic.
- `ambig_expr` — classically ambiguous, exercises GLR.
- `scoped` — block-scoped IDs, exercises scope hooks.
- `plm_subset` — Phase 5 deliverable.
- `plm_full` — Phase 9, parses the first 162 lines of Digital Research's
  `bdos.plm` end-to-end. EQU-as-LITERALLY macro alias requires an upstream
  expander and is documented as out of scope.
- `c_subset` — Phase 9. Functions, statements (incl. switch/case/default,
  do-while, goto, labels), expressions with full C precedence,
  struct/union/enum, casts, typedef-name resolution, preprocessor skip,
  array sizes accept constant expressions. Parses 21 vendored uc80
  example programs as fixtures, including three that need libc-typedef
  pre-population (FILE, jmp_buf).

### Out of scope (post-v1)

- Full `bdos.plm`: needs an upstream LITERALLY macro expander.
- Function-pointer abstract declarators (`(int (*)(int)) p`), variadic
  `...`, compound literals, designated initializers, `_Generic`,
  bit-fields. None are common in uc-family code.
- Multi-line `\`-continued preprocessor macros.
- Token filter ABI in the C/C++/Lua backends. The Python backend exposes
  the runtime callback directly; non-Python hosts that need typedef-name
  resolution have to filter their token stream upstream of the parser
  for now. *(Lifted in the Unreleased section above.)*

### Test surface

282 tests, ~52s wall on the reference machine. Coverage spans regex AST,
NFA / DFA construction, LR(1) and GLR runtime, hook firing, JSON
round-trips, all four backends (with cc / c++ / lua interpreter
acceptance tests), and end-to-end fixture parsing for plm_full and
c_subset.
