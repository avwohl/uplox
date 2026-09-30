# Bundled example grammars

`examples/` ships 30+ grammars. The list below splits them by how much
they've been beaten on:

## Production-tested — drive a real compiler downstream

These grammars are the parsing front-end of an active compiler in the
[Related projects](../README.md#related-projects) section. The Python runtime is
exercised against real-world source every time those projects' test
suites run.

- `c23` — full C23 syntax: function definitions and declarations,
  variadic parameters, every C23 storage class / qualifier (incl.
  `constexpr`, `thread_local`, `_Atomic`, `_Noreturn`,
  `_Static_assert`, `alignas` / `alignof`), all built-in numeric types
  (`_BitInt(N)`, `_Decimal32/64/128`, `_Complex`, `_Imaginary`),
  pointers, arrays, abstract declarators for casts and
  `sizeof(type)`, the full C statement set, expressions across the
  full C precedence ladder, `_Generic`, `typeof` / `typeof_unqual`,
  compound and designated initializers, `__real__` / `__imag__`,
  struct / union / enum with bit-fields and embedded
  `static_assert`, typedef declarations with the lexer-feedback
  typedef-name hack, plus the GCC extensions
  (nested functions, `__label__`) the c-family compilers actually
  use. Preprocessor and `[[…]]` / `__attribute__((…))` are stripped
  upstream by the per-compiler preprocessor pass. Drives **uc_core**
  (shared frontend for **uc80** and **uc386**); uc386 is 100% on
  `gcc-c-torture` (1514/1514) and `c-testsuite` (220/220) with this
  grammar. LALR(1) at 586 states, conflict-free.
- `plm_subset` — the first real grammar (Phase 5). Calc-style PL/M
  with declarations, IF/THEN/ELSE, DO blocks. Foundation for
  `plm_full`.
- `plm_full` — extends `plm_subset` to the constructs Digital
  Research's BDOS uses (LITERALLY, INITIAL, AT, BASED, iterative
  DO TO/BY, DO CASE, structures). Drives **uplm80** for the full
  CP/M PL/M-80 corpus; parses the original `bdos.plm` source
  end-to-end (with `plm_pre` expanding LITERALLY beforehand). LALR(1)
  at 296 states, conflict-free.
- `plm_pre` — PL/M-80 preprocessor (text-substitution layer that runs
  ahead of `plm_full`). Handles `NAME LITERALLY '...'` definitions
  and the `$Q` / `$I` / `$RIGHTMARGIN` / `$INCLUDE` directive set
  the original CP/M PL/M sources actually use; everything else is
  pass-through. One hook records the macro table; the emitted Python
  module ships a turnkey driver that produces a transformed PL/M
  source string ready to feed `plm_full`. 27 states, conflict-free.
- `cowgol` — Cowgol (Ada-inspired systems language for small
  machines), the language as implemented by the ucow Cowgol
  compiler. Covers top-level include / sub / record / typedef /
  interface; the full statement and expression set; all Cowgol
  primitive and compound types (`int8`..`uint32`, `intptr`, ranged
  `int(min, max)`, pointers `[T]`, arrays `T[n]` / `T[]`, records
  with single inheritance, typedefs); the full attribute set
  (`@extern`, `@at`, `@sizeof`, `@indexof`, `implements Interface`);
  inline assembly via `@asm`. Drives **ucow**'s parser end-to-end.
  LALR(1) at 344 states, conflict-free.
- `ada_subset` — Ada 2012 subset sized to the ACATS helper packages
  in `uada80/adalib` (top-level packages, basic types, `if`/`case`/
  `loop`/`for`/`while`/`return`/`exit`/`goto`, attribute references,
  aggregates, `with`/`use`). Out of scope here (handled by
  `ada_full`): generics, tasks, tagged types, access types,
  exceptions, renamings. LALR(1) at 417 states, conflict-free.
- `ada_full` — Ada 2012 in full: exceptions (handlers and `raise`),
  access types and allocators, renamings, tagged types / interfaces
  / abstract / synchronized / limited, discriminated records, aspect
  specifications, generics (declarations and instantiations), tasks
  and protected types with entries / accept / select / delay /
  abort / requeue, private types, separate clauses. Drives
  **uada80**'s Ada front-end. Per the latest
  [WIP.md](../WIP.md): 100.0% on ACATS A/C/D/E/L (2846/2846, with 3
  malformed-source files in xfail), 97.8% on the GNAT runtime
  (1072/1096; every fail is malformed source). LALR(1) at 1594
  states, conflict-free (canonical-LR(1) builds to 58611 states —
  a 37x shrink without losing any parses).
- `uplox_self` — the .uplox DSL described in itself. Parses every
  other example grammar in this list, including its own definition
  — action bodies and all (the lexer's `%balanced=` extension matches
  `{ ... }` runs by counting nested braces). Pinned by
  `test_self_host.py` against every committed example .uplox.

## Feature smoke tests

Compact grammars that exercise a specific uplox feature in isolation —
they live in the test suite, not in any downstream compiler.

- `calc` — arithmetic expressions, the original smoke test.
- `calc_ast` — same language as `calc`, annotated with the v3 auto-AST
  surface (`%ast=`, `?`, `@field`, `%ast_drop`). Emitted Python module
  produces typed `BinOp` / `NumLit` dataclasses instead of a generic
  parse tree. See [`docs/proposals/calc_annotated.md`](proposals/calc_annotated.md).
- `cowgol_ast` — full Cowgol grammar with v3 annotations. 78 tokens, 182
  productions, 68 AST kinds. Validates the v3 surface at real-language
  scale: 12-level expression ladder, 11 auto-optional clauses, 13
  list-shaped rules, nested type expressions, statement sequences.
  Walkthrough: [`docs/proposals/cowgol_annotated.md`](proposals/cowgol_annotated.md).
- `ambig_expr` — classically ambiguous, exercises GLR.
- `scoped` — block-scoped IDs, exercises hooks.
- `c_subset` — the original meaningful subset of C (function
  definitions, full statement repertoire, expressions with C
  precedence, struct/union/enum, casts, the typedef-name lexer hack,
  preprocessor skip). Pre-dates `c23` and parses 21 of uc80's example
  C programs as vendored fixtures. uc_core's production frontend has
  since moved to `c23`; `c_subset` stays as the smaller C demo.
- `c_expr` — C-expressions only, with v3 AST annotations matching
  uc_core's `Expression` AST shape. 15 AST kinds; 126 states. Pilot
  for the uc_core auto-AST migration.

## First-cut "modern language" grammars

LALR(1)-clean, conflict-free grammars for popular contemporary
languages. Each parses a real-world `features.<ext>` fixture in
`tests/fixtures/<lang>/` end-to-end, but none drives a production
compiler yet — treat them as starting points for new front-ends or as
reference data for language-tooling work.

- `json` — strict ECMA-404 / RFC 8259 JSON. The data-language sized counterpart to `calc`'s expression-language smoke test: one value-recursive non-terminal, two compound shapes (object / array), three keyword literals (`true` / `false` / `null`), plus a spec-exact number regex and a backslash-escape-permissive string regex. LALR(1) at 27 states, conflict-free. No relaxed forms (trailing commas, comments, unquoted keys) — those belong in a JSONC / JSON5 variant.
- `yaml` — YAML 1.2 in both flow and block styles. Flow style (`{key: val}` / `[a, b]`) is LR(1)-clean directly; block style is indentation-sensitive and uses the `%layout` directive — the runtime emits synthetic `INDENT` / `DEDENT` / `NEWLINE` terminals based on column tracking, with `{` `[` configured as flow brackets that suspend tracking. The grammar handles multi-document streams (`---` / `...`), `%YAML` / `%TAG` directives, anchors / aliases / tags, complex keys (`? key`), and all five scalar styles. LALR(1) at 104 states, conflict-free.
- `fortran77` — Fortran 77 (ANSI X3.9-1978) fixed-form source. Card-image layout (columns 1-5 label area, column 6 continuation indicator, columns 7-72 body, columns 73-80 sequence number) handled by the `%columns` directive. Continuation lines joined by `%continuation` consuming the dispatcher-emitted CONTINUE_LINE token. Covers PROGRAM / SUBROUTINE / FUNCTION / BLOCK DATA program units, the full specification statement set (INTEGER / REAL / DOUBLE PRECISION / COMPLEX / LOGICAL / CHARACTER, DIMENSION, COMMON, EQUIVALENCE, IMPLICIT, PARAMETER, DATA), executable statements (assignment, IF in all three flavours, DO loops including DO WHILE, GOTO assigned and computed, CALL, RETURN, STOP, PAUSE, I/O statements with FORMAT), and the F77 expression precedence ladder with dotted operators (`.AND.`, `.OR.`, `.NOT.`, `.EQ.`, `.LT.`, etc.). LALR(1) at 432 states, conflict-free.
- `python` — Python 3.10+ via `%layout` (indentation tracking with `(`/`[`/`{` as flow brackets) + `%continuation` (backslash at end of line). Covers module structure, all simple and compound statements (if/elif/else, while, for, try/except/finally, with, def, class, async def, match-case), function and class definitions with decorators / type params / annotations, the full Python expression precedence ladder with walrus / ternary / lambda / chained comparisons / `is not` / `not in`, comprehensions (list, set, dict, generator), all collection literals, pattern matching with literal / capture / sequence / mapping / class / or / as / guard forms. LALR(1) at 594 states, conflict-free.
- `cobol` — COBOL ANSI X3.23-1985 fixed-format. Column-7 indicator area handled by `%columns` (`*` / `/` for comment lines, `-` for continuation, `D` for debug). Covers the four divisions (IDENTIFICATION / ENVIRONMENT / DATA / PROCEDURE), level-numbered data items with PIC / USAGE / VALUE / OCCURS / REDEFINES clauses, the standard PROCEDURE statement set (MOVE, COMPUTE, ADD, SUBTRACT, MULTIPLY, DIVIDE, IF / ELSE / END-IF, PERFORM with TIMES / UNTIL / VARYING, READ / WRITE, OPEN / CLOSE, DISPLAY / ACCEPT, STOP RUN, EVALUATE / WHEN / OTHER), relational and logical conditions, and arithmetic expressions inside COMPUTE. LALR(1) at 385 states, conflict-free.
- `pascal` — standard Pascal (Wirth / ISO 7185), with the universal extensions real compilers shipped (typed constants, `//` line comments, `$hex` literals, `external` directives). Programs with the full declaration repertoire (label / const / type / var / procedure / function), all six type-denoter shapes (simple / subrange / enum / array / record with variant `case` part / set / file / pointer), the classic statement set including `case/of/else` and `with`, expressions with the four-precedence ladder plus set constructors and `in`. LALR(1) at 352 states, conflict-free.
- `delphi` — Delphi / Object Pascal, sized to Turbo Pascal / Borland Pascal / Delphi 1-7 and the Free Pascal `objfpc` / `delphi` modes. Builds on `pascal` and adds the OO layer: `program` / `unit` / `library` compilation units with `uses` / `interface` / `implementation` / `initialization` / `finalization`; classes with single inheritance, implemented interfaces, four visibility sections (plus `strict`), instance + class methods, constructors / destructors, class-level const / type / var sections, properties with the full specifier set including indexed and default; interfaces with `[GUID]` and parent; `try / except / finally` with named `on E: T do` handlers; `string` type, dynamic arrays (`array of T`), open arrays of `const`, procedural types (`procedure(...) of object`); `out` parameters and default parameter values; the full method-directive set (virtual / override / abstract / overload / inline / message / cdecl / stdcall / ...) split into modifier vs terminator directives to disambiguate the impl-routine tail; `is` / `as` operators, `shl` / `shr`, `inherited`. LALR(1) at 836 states, conflict-free (with `%shift IDENT` for the class-body section-continuation ambiguity).
- `cpp` — C++20 / C++23: classes with inheritance, member functions (virtual / override / final / const / noexcept / ref-qualified), constructors with member-initializer lists, destructors, function and class templates with variadic-template pack expansion and `requires`-clause / `requires`-expression constraints, concepts (`concept Name = expr;`), modules (`module M;`, `import M;`, `export …`), lambda expressions with full capture syntax, range-based for, auto / decltype, references (`&` and `&&`), pointer-to-member, smart-pointer instantiations, the four C++ casts, exception handling, attributes `[[…]]`, constexpr / consteval / constinit, trailing return types, structured bindings, three-way `<=>`, scoped enums, complex parenthesised abstract declarators, and coroutines (`co_await` / `co_yield` / `co_return`). Preprocessor directives are accepted as opaque PREPROC tokens at top-level / statement / member positions. LALR(1) at 879 states, conflict-free.
- `csharp` — C# 8 (Roslyn / ECMA-334 §7-ish): namespaces, `using` directives, classes / structs / interfaces / enums / delegates with generics + `where T : …` constraints, fields / methods / properties (full accessor and expression-bodied) / events / indexers / ctors / dtors / operators / nested types / constants, the full statement set including `try / catch / finally` with `when` filters, all expression forms with precedence + lambdas + `is` / `as` patterns + tuple types + nullable types + pointer types + arrays. Uses the **lexer-feedback** pattern that Mono mcs uses for the four canonical LR-incompatible C# ambiguities: `LT_GENERIC` / `GT_GENERIC` for type argument lists vs less-than/greater-than, `LPAREN_LAMBDA` / `LPAREN_CAST` / `LPAREN_TUPLE` for the parenthesised-expression / lambda / cast / tuple split, and `IDENT_TYPE` for the local-decl-vs-expression-statement decl/expression split. Hosts install a token filter (the same uplox mechanism `c_subset` uses for typedef-name disambiguation) that implements the ECMA-334 §7.5.4.2 disambiguation rules. LALR(1) at 1029 states, conflict-free.
- `rust` — stable Rust 2021 edition: items (`fn` / `struct` / `enum` / `trait` / `impl` / `use` / `mod` / `type` / `const` / `static` / `extern` / `union`), full generic type parameters with `where` clauses and lifetime bounds, all type denoters (paths with turbofish, references / raw pointers / arrays / slices / tuples / function pointers / `dyn Trait` / `impl Trait` / `Self`), all pattern shapes (literal, identifier with `ref` / `mut` / `ref mut` modifiers, wildcard, rest, reference, tuple, slice, struct with `..` rest, tuple struct, range, or-pattern), the full expression precedence ladder with closures + ranges + `?` propagation + `as` casts + `is` / `if let` / `while let`, attributes (outer and inner), visibility modifiers (`pub`, `pub(crate)`, `pub(in path)`), `async fn` / `async {}` / `.await`, common macro invocations in paren / bracket / brace form. Uses the **lexer-feedback** pattern for the one Rust-specific LR(1) ambiguity — attribute-path versus expression-path on `::` continuation: hosts install a token filter that emits `IDENT_ATTR` / `KW_*_ATTR` for path leaves inside attribute / visibility / use directive positions (the same uplox mechanism `c_subset` uses for typedef-name). LALR(1) at 1019 states, conflict-free.
- `go` — Go 1.21+: packages, imports, type / var / const declarations, functions and methods, generics (Go 1.18+ `[T any]` type parameters with `T[U]` instantiation), structs / interfaces / maps / slices / channels, goroutines, defer, the full statement set (incl. type switch and select), composite literals, type assertions, and standard operator precedence. The grammar requires explicit `;` after every statement (Go's automatic semicolon insertion is the lexer's job, not the parser's); composite-literal-vs-block ambiguity in `if` / `for` / `switch` heads is resolved by routing only through `<expression>` so the `<primary_expr> '{...}'` trailer never fires there. LALR(1) at 543 states, conflict-free.
- `java` — Java 21 LTS: records, sealed classes, switch expressions with arrow patterns, pattern matching for `instanceof` (including record patterns and unnamed pattern variables), text blocks, `var`, enhanced enums, generics with wildcards, lambdas, method references, the Java 9 module system, JEP 512 compact source files (unnamed classes with top-level methods), and the full JLS §3-§15 surface (compilation units, all type declarations and modifiers, members, statements, expressions). LALR(1) at 837 states, conflict-free.
- `javascript` — ECMAScript 2020+: modules (full `import`/`export`), `var`/`let`/`const` with destructuring, function and class declarations (incl. generators, async functions, arrow functions, class fields with private `#name`), the full ES2020 expression precedence ladder with optional chaining and nullish coalescing, template literals, async/await, destructuring, spread/rest, default parameters. Two known LR(1) tradeoffs are documented: ASI (this grammar requires explicit semicolons; pair with `prettier --semi`) and the `/` regex/divide ambiguity (omits regex literals; use `new RegExp(...)`). LALR(1) at 868 states, conflict-free.
- `typescript` — TypeScript 5.x: every JavaScript construct above, plus the TS type system — type annotations on bindings / parameters / return types / class members, optional and definite-assignment markers (`name?:`, `name!:`), generics on functions / methods / classes / interfaces / type aliases (with `<T extends U = D>`) and on calls (`obj.method<T>(args)`), interface / type / enum / `const enum` / `namespace` / `module` declarations, ambient `declare`, member modifiers (`public`/`private`/`protected`/`readonly`/`abstract`/`override`/`static`), parameter properties on constructors, the full TS type surface (unions, intersections, conditionals, mapped, indexed, `keyof`, `typeof`, `infer`, template literal types, etc.), and type assertions (`expr as T`, `expr as const`). Uses the **lexer-feedback** generic-bracket filter shared with `kotlin` / `swift` / `tsx` to disambiguate `<` between less-than and type-argument-list. LALR(1) at 1392 states, conflict-free.
- `tsx` — TypeScript + JSX (TSX 5.x): `typescript` plus JSX elements (`<Tag attrs>children</Tag>`, self-closing `<Tag />`, fragments `<>children</>`), with attribute shapes (spread / boolean / string / expression-container) and child kinds (nested JSX, `{expr}` containers, JSX text). Adds the `LT_JSX` / `LT_JSX_SLASH` / `JSX_TEXT` synthetic terminals over the TS generic-bracket ones; hosts install the `tsx_filter` to emit them per JSX context. LALR(1) at 1443 states, conflict-free.
- `kotlin` — Kotlin 1.9 (incl. Kotlin Multiplatform's `expect` / `actual` modifiers): top-level packages and imports; class / interface / object / `enum class` / `sealed class` / `data class` / `annotation class` with the standard modifier set; primary constructors with property-promoting params; properties (`val` / `var`), member / extension / `infix` / `operator` / `inline` / `suspend` / `tailrec` / `override` functions; the full statement set incl. labelled jumps; the Kotlin expression ladder with safe-call / Elvis / not-null-assertion / range / `when` expressions; generics with variance (`in T` / `out T`) and constraints. Uses the **lexer-feedback** generic-bracket filter to disambiguate `<`. LALR(1) at 605 states, conflict-free.
- `swift` — Swift 5.x: top-level imports / types / extensions / functions / variables / statements (script form); `let` / `var` with type annotations and computed-property accessors; `func` (incl. `async` / `throws` / `rethrows` / generic / `where`-clause), `init` (designated / convenience / required / failable), `deinit`, `subscript`, `typealias`, `associatedtype`; class / struct / enum (raw + associated values + `indirect`) / protocol / extension / actor; the full Swift access / instance / method / storage / concurrency modifier set; attributes (`@available(…)`, `@escaping`, `@MainActor`, …) and property wrappers (`@Published var x`). Uses the **lexer-feedback** generic-bracket filter to disambiguate `<`. LALR(1) at 924 states, conflict-free.
