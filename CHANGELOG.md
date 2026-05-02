# Changelog

All notable changes to plox land here. Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); the project follows
[Semantic Versioning](https://semver.org/spec/v2.0.0.html) for the public
surface (CLI, JSON bundle schema, Python API, hook firing points).

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
  for now.

### Test surface

282 tests, ~52s wall on the reference machine. Coverage spans regex AST,
NFA / DFA construction, LR(1) and GLR runtime, hook firing, JSON
round-trips, all four backends (with cc / c++ / lua interpreter
acceptance tests), and end-to-end fixture parsing for plm_full and
c_subset.
