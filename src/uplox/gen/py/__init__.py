"""Python backend: emit a self-contained module that reuses the uplox runtime.

See :mod:`uplox.gen.py.emit` for the emitter; this re-exports the public
entry point.
"""

from .emit import emit_py

__all__ = ["emit_py"]
