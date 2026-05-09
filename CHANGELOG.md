# Changelog

All notable changes to uplox land here. Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); the project follows
[Semantic Versioning](https://semver.org/spec/v2.0.0.html) for the public
surface (CLI, JSON bundle schema, Python API, hook firing points).

## 2.0.0 — 2026-05-02

Breaking change to the `.uplox` grammar source format. v1 grammars do not
parse under the new reader. All bundled examples (`calc`, `ambig_expr`,
`scoped`, `plm_subset`, `plm_full`, `c_subset`, `uplox_self`) and the
self-host bootstrap have been ported.

### Changed

- **Token literals are single-quoted.** `LPAREN = '('` instead of
  `LPAREN = "("`. Same for inline literals on rule RHS and for the
  `%balanced='<close>'` attribute. The change matches yacc/bison
  convention and keeps the grammar source's `'…'` and the regex `/…/`
  visually distinct from target-language `"…"` strings inside action
  bodies.
- **Non-terminals are written `<name>` everywhere — both LHS and RHS.**
  Bare identifiers on RHS are always terminal names. Case is no longer
  the terminal / non-terminal signal, so identifier names (rule LHS,
  terminal names, keyword spellings) can be any case. Token names are
  no longer constrained to `[A-Z][A-Z0-9_]*`.
- **`Symbol.kind` field replaces string-shape inspection.** Internal
  to the IR; downstream consumers that read `prod.rhs` (already
  resolved tuples of strings) are unaffected.

### Added

- **`%keyword_prefix` directive and `%keywords` section.** Listing
  `HALT` under `%keywords` with `%keyword_prefix KW_` synthesises a
  token named `KW_HALT` whose literal is `"HALT"`; bare `HALT` on rule
  RHS resolves to `KW_HALT`. The win shows up in `plm_subset` /
  `plm_full`, where ~30 lines of `HALT = "HALT"` boilerplate collapse
  into one whitespace-separated list. Either the bare or the prefixed
  spelling resolves on rule RHS, so generated code can reference
  whichever is more idiomatic for the target language.

### Test surface

358 tests pass, ~68s wall on the reference machine. New `test_spec_keywords.py`
adds 9 tests for `%keyword_prefix` / `%keywords` (default empty prefix,
KW_-prefixed synthesis, multi-line lists, alias resolution, plus error
paths for duplicate prefix directive, duplicate keyword, collision with
an existing `%tokens` declaration, malformed identifier).

### Migration

There is no automatic migration. The substantive transformation is:
1. `"x"` → `'x'` for every token literal and inline rule literal.
2. Every non-terminal reference (LHS and RHS) gains `<>`: `expr` → `<expr>`.
3. Optional: refactor `KW_FOO = "foo"` declarations into a `%keywords`
   section with `%keyword_prefix`.

The repository's bundled examples demonstrate both styles — `plm_subset`
/ `plm_full` use `%keywords`; `c_subset` keeps explicit `KW_FOO = 'foo'`
declarations to retain C-idiomatic uppercase code-side names.

## 1.4.0 — 2026-05-02

Quality-of-life pass on parse-error diagnostics. Every backend now
produces the same shape of message: the unexpected token (or
"unexpected end of input"), the source position when applicable, and
the set of terminals the offending state has actions for, capped at
12 with a `+N more` suffix.

### Added

- **LR runtime (`uplox.parse.runtime`): expected-token list in
  `ParseError`.** The error message now reads
  `unexpected token 'X' 'x' at line N, column M; expected one of: A, B, C`.
  The synthetic end-of-input token is rendered as
  `unexpected end of input` rather than leaking the internal
  line=0/column=0 coordinates; the END_MARKER terminal is rendered
  as `<end of input>` in the expected list, not the raw `$` the
  LR table uses internally. The `ParseError.token` field is
  unchanged, so hosts already inspecting it keep working.
- **GLR runtime (`uplox.parse.glr.runtime`): same shape on
  `GLRParseError`.** The expected list is the union across live
  stack tops at the point of failure, since any of those tops could
  have been the one to absorb the lookahead.
- **C, C++, Lua emitted parsers: parity error format.** Each emitted
  parser walks its own action row at error time (no extra
  generated-table storage) to produce the same wording as the
  Python runtime. The Lua emitter gained a `kTokenCount` constant
  to drive the iteration; the C emitter widened the error buffer
  from 200 bytes to 1024 to fit longer expected lists.

### Test surface

349 tests, ~57s wall on the reference machine. New coverage in this
cycle: 3 LR-runtime tests pinning the new message shape; 4 emitter
tests (one per non-Python backend, plus a second C test covering
the non-EOI path) verifying the binaries produce the same wording.

## 1.3.0 — 2026-05-02

Closes the cross-language parity gap for the `%balanced=` lexer feature
that landed in 1.2.0. The Python runtime had it; the emitted C / C++ /
Lua scanners did not. Same shape of work as 1.1.0's token-filter parity
fix: thread the metadata through the JSON bundle and teach every emitted
scanner to honour it.

### Added

- **JSON bundle: `lex.balanced` field.** The serialiser writes a
  `{token_name: close_char}` map under the lex section when at least
  one token uses `%balanced=`; the key is omitted otherwise, so
  pre-1.3 bundles round-trip byte-identical and pre-1.3 readers see
  no schema change. New `balanced_from_json` helper reads the map
  back; existing `dfa_from_json` keeps its 3-tuple return.
- **CLI: `uplox build` writes the balanced map.** `uplox parse`
  rebuilds the Scanner with the map applied, so `%balanced=`
  grammars round-trip through the bundle from the command line.
- **C backend: balanced-bracket scanner support.** The emitted file
  carries a per-grammar `uplox_<g>_token_balanced[]` array (close
  byte per token; 0 = not balanced). After every DFA accept whose
  token is balanced, the scanner counts nested open/close pairs
  until depth zero, extending `last_accept_end`. An unterminated
  run surfaces as "unterminated balanced token" through the
  existing `uplox_<g>_error` path.
- **C++ backend: balanced-bracket scanner support.** Same shape as
  the C backend, with `kTokenBalanced[]` in the anonymous namespace
  and the balanced extension inside `next_token()`.
- **Lua backend: balanced-bracket scanner support.** Same shape with
  a `token_balanced` Lua table and the balanced extension inside
  `Parser:next_token()`.
- **Python emitted shim: balanced-bracket scanner support.** The
  generated `uplox_<grammar>.py` shim now constructs `Scanner(...)`
  with `balanced=balanced_from_json(BUNDLE["lex"])`. Hosts using
  the in-process `uplox.parse.runtime.parse(...)` already had this
  in 1.2.0; this commit closes the gap for hosts using the
  generated module.

### Test surface

342 tests, ~57s wall on the reference machine. New coverage in this
cycle: end-to-end balanced-token tests for the C, C++, and Lua
backends — each parses input where action bodies contain nested
braces and asserts the scanner emits one ACTION token per top-level
item; each also asserts that an unterminated `{` returns a non-zero
exit code with a useful error.

## 1.2.0 — 2026-05-02

Closes the post-1.0 deferred-grammar list and lifts the action-body
carve-out from the self-host grammar. The c_subset grammar now covers
the C constructs the v1.0 release notes explicitly punted on; the
bootstrap reader is no longer required for hosts that want to read
real .uplox files (with action bodies) via uplox_self.uplox.

### Added

- **c_subset: variadic `...`.** `parameter_type_list = parameter_list
  | parameter_list COMMA ELLIPSIS`. The COMMA-ELLIPSIS form enforces
  the C rule that `...` cannot appear as the only parameter.
- **c_subset: multi-line preprocessor macros.** PREPROC's body now
  matches `[^\n\\] | \\\n | \\.`, so `\<newline>` line continuations
  inside `#define` directives are absorbed into the skip token.
- **c_subset: bit-fields.** struct_declarator gains
  `declarator COLON conditional_expr` and the anonymous
  `COLON conditional_expr` form (zero-width forces alignment, etc.).
- **c_subset: designated initializers (C99).** `{ .x = 1, .y = 2 }`,
  `{ [3] = 9 }`, chained `{ .inner.z = 7, [1][1] = 9 }`, and the
  C99-allowed positional/designated mix all parse.
- **c_subset: compound literals (C99).** `(type){init_list}` joins
  postfix_expr. Sharing the `LPAREN type_name RPAREN` prefix with
  cast_expr is resolved by one-token lookahead (LBRACE picks the
  compound literal; anything else picks the cast).
- **c_subset: full abstract declarators.** type_name's previous
  shape is replaced by the standard C99 abstract_declarator +
  direct_abstract_declarator recursion. This admits
  `int (*)(int)`, `int (*)[5]`, `int *[3]` etc. wherever
  type_name appears (casts, compound literals, _Generic
  associations, sizeof).
- **c_subset: `_Generic` (C11).** primary_expr admits
  `_Generic ( assignment_expr , generic_assoc_list )`; each
  association is `type_name : assignment_expr` or
  `default : assignment_expr`. Reuses KW_DEFAULT.
- **c_subset: `sizeof(type)`.** unary_expr gains
  `KW_SIZEOF LPAREN type_name RPAREN`. Disambiguated from the
  existing `sizeof unary_expr` form by the token after LPAREN —
  type-keyword/qualifier/struct/union/enum/TYPEDEF_NAME picks the
  type_name path; expression-starters keep the old path.
- **Lexer: `%balanced=<close>` token attribute.** A new lexer-runtime
  feature: a token can declare itself balanced-bracket; after the DFA
  matches the opening pattern, the scanner extends the match by
  counting nested instances of (open, close) until depth returns to
  zero. The opening byte is whatever the DFA already consumed. Used
  for content that is not a regular language — target-language action
  bodies inside the .uplox DSL itself are the canonical example.
- **uplox_self.uplox: action bodies covered end-to-end.** ACTION_BODY
  is declared with `%balanced="}"`; alt has a new optional
  ACTION_BODY suffix; token-line syntax recognises `%balanced=`
  itself (so uplox_self can describe its own use of the feature).
  The strip-actions pre-pass that hosts used as a workaround is no
  longer required — every committed example .uplox, including
  calc.uplox's eight action bodies and uplox_self.uplox's own
  description, parses verbatim through uplox_self.

### Status of post-1.0 follow-ups

- **Done in this cycle**: variadic `...`, multi-line preprocessor
  macros, bit-fields, designated initializers, compound literals,
  full abstract declarators (function-pointer abstract declarators,
  `(int (*)(int))`-style), `_Generic`, embedded action bodies in
  the self-host grammar via the new `%balanced=` lexer feature,
  plus `sizeof(type)` rounding out the abstract-declarator work.
- **Still deferred**: full `bdos.plm` (needs an upstream LITERALLY
  macro expander, not a uplox concern). The c_subset deferred list
  from the 1.1.0 entry is now empty.

### Test surface

336 tests, ~57s wall on the reference machine. New coverage in this
cycle: 31 c_subset tests across the seven new grammar features, plus
3 self-host tests covering nested, multi-line, and unterminated
action bodies. The state count for c_subset went 1649 → 2175 (no
new conflicts); uplox_self went 52 → 54 with ACTION_BODY threaded in.

## 1.1.0 — 2026-05-02

Backwards-compatible additions to the C, C++, and Lua backends, plus
the self-host bootstrap and a perf pass on the LR(1) builder. Each of
the backend additions mirrors a runtime feature that had been
Python-only in 1.0.0, closing the parity gap so non-Python hosts can
implement the C typedef-name hack and other lexer-feedback grammars
end-to-end in their target language.

### Added

- **Self-host bootstrap.** `examples/uplox_self.uplox` describes the .uplox
  DSL in itself. uplox builds it (52 states, conflict-free) and the
  generated parser handles every committed example .uplox, including
  parsing its own definition. The bootstrap reader at
  `uplox/spec/reader.py` still ships because the self-host grammar
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
  - C:   `uplox_<g>_set_token_filter(ctx, fn, user_data)`
  - C++: `parser.set_token_filter(std::function<...>)` (TokenFilter typedef)
  - Lua: `parser:set_token_filter(function ... end)`
- **C, C++, Lua: post-reduce hook.** A general callback invoked after
  every successful reduction, before the runtime re-applies the token
  filter. Hosts use it to update typedef tables, scope stacks, error
  counters — anything they want visible to the next action lookup.
  - C:   `uplox_<g>_set_post_reduce(ctx, fn, user_data)`
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
  remaining plan item from `uplox_plan.txt`.
- **Still deferred**: full `bdos.plm` (needs upstream macro expander),
  function-pointer abstract declarators, variadic `...`, compound
  literals, designated initializers, `_Generic`, bit-fields, multi-line
  preprocessor macros, embedded action bodies in the self-host grammar.

### Test surface

305 tests, ~45s wall on the reference machine. New coverage in this
cycle: end-to-end typedef-name hack tests for the C, C++, and Lua
backends (each parsing `typedef Foo; Foo;` with both a token filter and
a post-reduce hook installed), 17 self-host tests covering the
`uplox_self.uplox` grammar against every committed example .uplox.

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
- Conflict reporting names the colliding actions; `uplox check` and
  `uplox build` surface them.
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

- **C** (Phase 7): generated header + impl, `uplox_<g>_ctx` struct,
  `uplox_<g>_parse(ctx, &root)` entry point. Default reductions land in a
  `uplox_<g>_default_reduction` array.
- **C++** (Phase 8): one `Parser` class per grammar in a `uplox_<g>`
  namespace, pImpl-hidden state, single ownership via a per-parser
  `unique_ptr` arena.
- **Lua** (Phase 8): single Lua 5.3+ module exporting `M.new(input)`,
  `parser:parse()`, and the production / token enums.
- **Python** (Phase 9): self-contained shim that embeds the bundle and
  reuses the uplox runtime. `parse(text, *, hooks, semantic_actions,
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

The 1.0.0 release notes called these out as deferred. The 1.1.0 entry
above lists which ones moved into scope; the rest remain out for now.

- Full `bdos.plm`: needs an upstream LITERALLY macro expander.
- Function-pointer abstract declarators (`(int (*)(int)) p`), variadic
  `...`, compound literals, designated initializers, `_Generic`,
  bit-fields. None are common in uc-family code.
- Multi-line `\`-continued preprocessor macros.
- Token filter ABI in the C / C++ / Lua backends — *lifted in 1.1.0.*

### Test surface

282 tests, ~52s wall on the reference machine. Coverage spans regex AST,
NFA / DFA construction, LR(1) and GLR runtime, hook firing, JSON
round-trips, all four backends (with cc / c++ / lua interpreter
acceptance tests), and end-to-end fixture parsing for plm_full and
c_subset.
