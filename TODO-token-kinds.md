# TODO — TokenKind IntEnum across uplox

Token names are currently `str` in `uplox.lex.scanner.Token`. The
scanner gets each name from `dfa.accepts[state]`, a JSON-loaded
string — **not** interned. The `KW_INT` etc. literals scattered
across host grammars (cpp_core, uc_core, future languages) ARE
interned at module-load.

Result: every `tok.name == "KW_INT"` (~1,250+ sites in ucpp386
alone) does a full byte-by-byte compare. Two-step migration:

## Phase 1 — interning (quick, safe)

One line in `uplox/lex/scanner.py:Scanner.scan`:

```python
yield Token(
    name=sys.intern(last_accept_name),  # ← was just last_accept_name
    ...
)
```

Collapses every `tok.name == "..."` site to a pointer compare.
Zero refactor cost. Speedup probably 2–3× on parse-heavy
workloads (front-end token rewrites are the hot path).

## Phase 2 — IntEnum

Real conversion to typed kinds across uplox and all host
languages:

1. Generate `TokenKind = IntEnum("TokenKind", names)` from each
   grammar's terminal set. Emit it as a sidecar module (e.g.
   `ucpp_core/_token_kinds.py`) at bundle-build time.
2. `Token` gains `kind: TokenKind` alongside `name: str` (keep
   `name` as `@property` returning `kind.name` for error
   messages and old-call-site back-compat during migration).
3. Host code mechanically converts:
   - `tok.name == "KW_INT"` → `tok.kind is TokenKind.KW_INT`
   - `tok.name in ("KW_INT", "KW_CHAR", ...)` → `tok.kind in TYPE_KW_SET`
     where `TYPE_KW_SET = frozenset({TokenKind.KW_INT, TokenKind.KW_CHAR, ...})`
4. For really hot bit-set checks (decl-spec leaders, cast-inner
   tokens), consider packing into a 64-bit `int` mask when total
   kinds ≤ 64; for ucpp386 (146 kinds) a `frozenset[TokenKind]`
   stays O(1) and avoids the big-int overhead.

Why uplox-wide: every host that uses `Scanner` benefits identically
(uc_core, ucpp_core, future languages). Doing it once in uplox
keeps each host's migration mechanical.

## Why this matters

Front-end token rewrites are the inner loop of the compiler.
ucpp386 has ~30 rewrite passes that each walk the entire token
stream; mbasicc's full build is millions of tokens. Token-kind
compares dominate parser time. Worth doing once.

## What NOT to break

- `tok.name` as `str` is used in error messages, debug dumps,
  and a few rewrites that examine the actual name text. Keep
  the str representation accessible.
- Grammar files are text — the build-time codegen needs to
  produce a stable enum (alphabetical by name? sorted by
  grammar-decl order?). Don't reshuffle on every build or
  comparing across versions becomes painful.

## Scope estimate

- uplox: 1 file (~50 lines) — Scanner.scan, Token, bundle-build
  TokenKind emission
- uc_core / ucpp_core / per-host: mechanical sed-replace, with
  some attention to `frozenset` literals → `frozenset[TokenKind]`
- Tests: should all keep passing if the migration is done by
  pattern; type-check passes catch any miss
- 1–2 days for ucpp386 alone, more for the full uc-family
