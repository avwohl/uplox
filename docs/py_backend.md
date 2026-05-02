# plox Python backend

The Python backend emits a self-contained module per grammar that
embeds the canonical JSON bundle and reuses the plox runtime. The
generated file is a thin shim — every backend-specific decision (LR
driver, lexer, hooks) lives in `plox.parse.runtime` and is shared
across grammars.

This is the right tradeoff for the Python target: the runtime is
small, well-tested, and shared; embedding the bundle is what makes
the module self-describing.

## Generated module

For a grammar named `calc`:

```python
import plox_calc

# Embedded bundle and metadata.
plox_calc.BUNDLE        # the full JSON bundle, dict
plox_calc.TOKENS        # ['NUMBER', 'PLUS', 'WS', ...]
plox_calc.SKIP          # ['WS', ...]
plox_calc.GRAMMAR_NAME  # 'calc'

# Parsing.
tree = plox_calc.parse(text)
tokens = list(plox_calc.scan(text))     # lexer-only

# Runtime types are re-exported for convenience.
plox_calc.HookRegistry
plox_calc.ParseError
plox_calc.ParseNode
plox_calc.Token
```

`parse()` mirrors `plox.parse.runtime.parse()` exactly:

```python
def parse(
    text: str,
    *,
    hooks: HookRegistry | None = None,
    semantic_actions: dict[int, SemanticAction] | None = None,
    token_filter: TokenFilter | None = None,
) -> StackValue:
    ...
```

## Lexer feedback

The Python runtime had `token_filter=` and per-production hooks since
1.0.0; the emitted module passes both through unchanged. For the
typedef-name hack in particular, the `plox.hooks.TypedefTracker` helper
implements both halves out of the box:

```python
from plox.hooks import TypedefTracker
import plox_my_c_grammar

tracker = TypedefTracker()
hooks = HookRegistry(ignore_missing=True)
hooks.register('record_typedef', tracker.record_declaration)

tree = plox_my_c_grammar.parse(
    src,
    hooks=hooks,
    token_filter=tracker.filter,
)
```

The grammar attaches `%hook=record_typedef` to its declaration rule;
the rest is the generic plox machinery.

## Re-entrancy

Each `parse()` call constructs a fresh `ParseContext`. Nothing mutates
module-level state during parsing. Two grammars are two `import`s.

## Runtime dependency

Unlike the C / C++ / Lua backends, the Python backend depends on
`plox` at import time. Hosts ship the generated `plox_<grammar>.py`
alongside `pip install plox` (or vendor the runtime). The runtime is
small (lexer + LR driver + hooks) and stable; the trade is "share the
runtime" vs. "duplicate it per grammar". Python users almost always
prefer share.
