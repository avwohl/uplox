# Changelog

All notable changes to uplox land here. Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); the project follows
[Semantic Versioning](https://semver.org/spec/v2.0.0.html) for the public
surface (CLI, JSON bundle schema, Python API, hook firing points).

## 3.1.0 — 2026-05-16

Substantial additive release: three new lexer directives that subsume
the most common context-sensitive lexing patterns, selectable LR
construction algorithm, the v3 auto-AST surface across all four
backends, a full Ada 2012 grammar driving uada80, multi-file source
tracking, and fifteen new bundled example grammars. No breaking
changes — every 3.0.0 grammar still parses unchanged.

### Added

- **Three new lexer directives.** Each subsumes a context-sensitive
  lexing pattern that previously required per-grammar host code:
  - `%layout` — INDENT / DEDENT / NEWLINE emission for
    indentation-sensitive languages (Python, YAML); column tracking
    with configurable flow brackets that suspend the algorithm.
  - `%columns` — column-range dispatch for card-image / fixed-format
    languages (Fortran 77, COBOL); maps column spans to lexer modes
    (label / body / comment / continuation indicator / skip).
  - `%continuation` — line-continuation marker handling (Python `\`,
    Fortran column-6, COBOL column-7 `-`); collapses physical lines
    into one logical line before the parser sees them. Composes with
    `%layout` to suppress INDENT / DEDENT across continuations.

  Bundle schema picks up `lex.layout`, `lex.columns`, `lex.continuation`
  fields when used; pre-3.1 readers still round-trip bundles that
  don't use them. See `docs/proposals/{layout,columns,continuation}.md`
  for the design rationale.

- **Selectable LR construction algorithm via `%define lr.type`.**
  Three modes: `canonical-lr` (default; most honest diagnostics),
  `lalr` (LALR(1) — typical 4-37x state shrink on real grammars,
  e.g. `ada_full` goes 58611 → 1594 states), and `ielr` (IELR(1) —
  LALR-size tables with canonical-LR conflict resolution; matches
  LALR on every example grammar in this repo since none have
  spurious LALR conflicts).

- **Per-terminal s/r conflict escapes.** Two new yacc-style directives
  resolve shift-reduce conflicts without restructuring the grammar:
  `%shift <T>` (prefer the shift on `T`) and `%reduce <T>` (prefer
  the reduce). Used by `ada_full` to handle Ada's context-pragma /
  type-aspect / range-attribute s/r ambiguities.

- **v3 auto-AST surface across all four backends.** Four new grammar
  annotations (`%ast_drop` on tokens, `?` on optional-clause LHS,
  `%ast=<Name>` per alternative, `@field=…` per RHS position) so
  `uplox build` produces a typed AST schema in the bundle, and each
  backend emits typed AST node types + a builder function that
  turns the parse tree into AST nodes automatically. Shipped in
  nine slices: spec parser, AST plan compiler, bundle serialisation,
  then per-backend emitters (Python, C, C++, Lua). Validated against
  `calc_ast`, `cowgol_ast` (78 tokens / 182 productions / 68 AST
  kinds), `c_expr`, and the full `c23` grammar; eliminates the
  hand-written parse-tree-to-AST lowering pass from every uc_core
  consumer.

- **Multi-file source tracking: `Token.file_id` + `FileTable`.** New
  field on every emitted token; populated by a host-supplied
  `FileTable` that maps file paths to ids. The C / C++ / Lua / Python
  scanners all thread the id through. Lets downstream front-ends
  emit diagnostics that point at original source files after the
  preprocessor pass.

- **`uplox.hooks.generic_brackets.rewrite_generics(...)`.** Reference
  host-filter for the `<…>`-vs-comparison ambiguity that
  `csharp` / `java` / `kotlin` / `swift` / `typescript` / `tsx`
  grammars declare via `LT_GENERIC` / `GT_GENERIC` synthetic terminals.
  Ships dialect tables for those six languages; pattern matches
  Mono's `cs-tokenizer.cs` pairing-and-disambiguating-set approach.

- **Inline literals in rule RHS.** `<if_stmt> ::= 'if' <expr> 'then'
  <stmts> 'end if'` resolves to the matching token by literal text
  — no `KW_IF`/`KW_THEN`/`KW_END` declarations required. Cuts
  `KW_*` boilerplate by ~40% across the example grammars.

- **`ada_full.uplox` — full Ada 2012 grammar driving uada80.** 671
  productions, 1594 states under LALR(1) (58611 under canonical-LR;
  37x shrink), 0 conflicts. Covers exceptions, access types,
  renamings, tagged types / interfaces / abstract / synchronized /
  limited, discriminated records, aspect specifications, generics,
  tasks / protected types with the full entry / accept / select /
  delay / abort / requeue surface, private types, separate clauses,
  extended-return, qualified aggregates, variant records, and the
  rest of the RM-95 / Ada 2005 / Ada 2012 additions to `ada_subset`.
  Drives uada80's front-end at 100.0% on ACATS A/C/D/E/L (2846/2846,
  with 3 malformed-source xfails) and 97.8% on the GNAT runtime
  (1072/1096).

- **`ada_subset.uplox` — Ada subset for the ACATS helper packages.**
  214 productions, 417 states LALR(1), 0 conflicts.

- **`cowgol.uplox` — Cowgol (Ada-inspired systems language).** Drives
  ucow's parser end-to-end. 181 productions, 344 states LALR(1).

- **`c23.uplox` — full C23 grammar driving uc_core.** Production
  C frontend for uc80 and uc386; validated by uc386 at 100% on
  `gcc-c-torture` (1514/1514) and `c-testsuite` (220/220). Covers
  every C23 storage class / qualifier, all built-in numeric types
  (`_BitInt(N)`, `_Decimal32/64/128`, `_Complex`, `_Imaginary`),
  `_Generic`, `typeof` / `typeof_unqual`, compound and designated
  initializers, the full statement and expression set, struct /
  union / enum with bit-fields and embedded `static_assert`, plus
  the GCC extensions (nested functions, `__label__`) the c-family
  compilers actually use. Annotated with the v3 AST surface for
  uc_core adoption. LALR(1) at 549 states, conflict-free.

- **Fifteen first-cut "modern language" grammars.** LALR(1)-clean,
  conflict-free, each parsing a real-world `features.<ext>`
  fixture in `tests/fixtures/<lang>/`: `pascal` (350 states),
  `delphi` (751), `json` (27), `yaml` (104), `python` (594),
  `cobol` (385), `fortran77` (432), `csharp` (890), `rust` (1019),
  `cpp` (879), `go` (543), `java` (837), `javascript` (868),
  `typescript` (1392), `tsx` (1443), `kotlin` (605), `swift` (924).
  None drives a production compiler yet — treat them as starting
  points or as reference for language-tooling work.

- **`c_expr.uplox` and `c_subset.uplox` (phased fill-in).** `c_expr`
  is a 76-production C-expressions grammar with v3 AST annotations
  matching uc_core's `Expression` AST shape; pilot for the uc_core
  auto-AST migration. `c_subset` was extended through five phases
  covering statements, declarations, control flow, type qualifiers /
  casts / `sizeof(type)` / abstract declarators, and the typedef
  lexer hook.

- **`plm_pre.uplox` — PL/M-80 preprocessor.** 26-production grammar
  for the LITERALLY / `$Q` / `$I` / `$RIGHTMARGIN` / `$INCLUDE`
  text-substitution layer that runs ahead of `plm_full` in uplm80.

- **Real-world parse fixtures.** `tests/test_grammar_fixtures.py`
  walks `tests/fixtures/<lang>/` and parses every source file
  through the matching grammar. Generic-bracket dialects get the
  filter applied automatically.

- **Self-host grammar parses the v3 AST annotations.** Plus the new
  directives and integer-literal forms; `uplox_self.uplox` is now
  capable of parsing every other example grammar in this repo
  verbatim.

- **Unused-token check.** `compile_grammar` now errors when a token
  declared in `%tokens` is never referenced from any rule, directive,
  or `%layout` / `%columns` / `%continuation` config. `%skip` tokens
  are exempt by design. Catches dead declarations that accumulate
  when rules get refactored without removing the corresponding
  token. Diagnostic shape: `token(s) declared in %tokens but never
  referenced: <name> (line N), …` with remediation hints.

### Changed

- **Bundled grammars cleaned up against the new unused-token
  check.** 43 dead token declarations removed across 11 grammars:
  `ada_full` (1), `c_expr` (1), `c_subset` (2), `cobol` (1),
  `cowgol` / `cowgol_ast` (1 each), `csharp` (2), `plm_subset` (5),
  `python` (1), `rust` (4), `uplox_self` (4). `ada_subset`'s 28
  Ada-reserved-but-not-modelled keywords moved from `%keywords`
  into `%tokens` with `%skip`, preserving lex-time reservation
  (so `task : Integer := 1;` doesn't silently parse `task` as an
  identifier) without firing the unused-token check.
- **Rule-RHS KW_ cleanup.** Grammars now use inline literals
  (`'volatile'`) on rule RHS in preference to the `KW_VOLATILE`
  token name. The KW_ form survives only where the token is a
  multi-spelling regex alternation (`c23`'s 17 GCC-alias tokens
  like `__volatile__|__volatile|volatile`) or a host-filter-
  emitted synthetic terminal (`csharp`'s 13 `KW_*_LINQ` tokens,
  `rust`'s 4 `KW_*_ATTR` tokens). Convention notes added to the
  c23, csharp, and rust grammar headers.

### Documentation

- **First-pass user docs.** New: `docs/tutorial.md` (your first
  uplox grammar, walking through `calc`), `docs/cookbook.md`
  (recipes for common language features), `docs/lr_modes.md`
  (`%define lr.type` choice with concrete state-count costs),
  `docs/conflict_resolution.md` (when to use `%shift`/`%reduce`
  vs restructure), `docs/semantics.md` (what the parser passes to
  semantic actions). README rewritten with Backend maturity, a
  three-way grammar split (Production-tested / Feature smoke
  tests / First-cut modern languages), and a Related projects
  section linking the avwohl compilers that consume uplox bundles.

### Test surface

471+ tests covering all four backends end-to-end, the new
directives, the v3 AST surface, and the real-world fixture parses.

## 3.0.0 — 2026-05-09

End-to-end rename plox → uplox. PyPI distribution, GitHub
repository, Python module, and CLI binary all moved to the
`uplox` name; the bare `plox` name on PyPI is squatted by an
unrelated matplotlib helper. No grammar / API surface changes —
3.0.0 grammars parse identically under 2.0.0 once renamed.

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
