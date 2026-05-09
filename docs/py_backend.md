# uplox Python backend

The Python backend emits a self-contained module per grammar that
embeds the canonical JSON bundle and reuses the uplox runtime. The
generated file is a thin shim — every backend-specific decision (LR
driver, lexer, hooks) lives in `uplox.parse.runtime` and is shared
across grammars.

This is the right tradeoff for the Python target: the runtime is
small, well-tested, and shared; embedding the bundle is what makes
the module self-describing.

## Generated module

For a grammar named `calc`:

```python
import uplox_calc

# Embedded bundle and metadata.
uplox_calc.BUNDLE        # the full JSON bundle, dict
uplox_calc.TOKENS        # ['NUMBER', 'PLUS', 'WS', ...]
uplox_calc.SKIP          # ['WS', ...]
uplox_calc.GRAMMAR_NAME  # 'calc'

# Parsing.
tree = uplox_calc.parse(text)
tokens = list(uplox_calc.scan(text))     # lexer-only

# Runtime types are re-exported for convenience.
uplox_calc.HookRegistry
uplox_calc.ParseError
uplox_calc.ParseNode
uplox_calc.Token
```

`parse()` mirrors `uplox.parse.runtime.parse()` exactly:

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
typedef-name hack in particular, the `uplox.hooks.TypedefTracker` helper
implements both halves out of the box:

```python
from uplox.hooks import TypedefTracker
import uplox_my_c_grammar

tracker = TypedefTracker()
hooks = HookRegistry(ignore_missing=True)
hooks.register('record_typedef', tracker.record_declaration)

tree = uplox_my_c_grammar.parse(
    src,
    hooks=hooks,
    token_filter=tracker.filter,
)
```

The grammar attaches `%hook=record_typedef` to its declaration rule;
the rest is the generic uplox machinery.

## Balanced-bracket tokens (since 1.2.0; emitted shim 1.3.0)

The Python runtime's Scanner has supported `%balanced='<close>'`
since 1.2.0 via its `balanced` constructor argument. The emitted
shim wires this through automatically by reading the bundle's
`lex.balanced` map via `balanced_from_json` and passing it to
`Scanner(...)`. Hosts don't need to do anything; grammars that
declare `%balanced=` get balanced-bracket scanning out of the box.

## Re-entrancy

Each `parse()` call constructs a fresh `ParseContext`. Nothing mutates
module-level state during parsing. Two grammars are two `import`s.

## Runtime dependency

Unlike the C / C++ / Lua backends, the Python backend depends on
`uplox` at import time. Hosts ship the generated `uplox_<grammar>.py`
alongside `pip install uplox` (or vendor the runtime). The runtime is
small (lexer + LR driver + hooks) and stable; the trade is "share the
runtime" vs. "duplicate it per grammar". Python users almost always
prefer share.
