"""C backend. Emits a self-contained ``.c`` / ``.h`` pair from a JSON bundle.

Re-entrant by construction: all parser state lives on a ``plox_<grammar>_ctx``
struct, all symbols are prefixed with the grammar name, no file-scope
mutable state. Two grammars built with plox can link into the same binary
without symbol collisions — the design's acceptance test (per
``docs/c_backend.md``).
"""

from .emit import emit_c

__all__ = ["emit_c"]
