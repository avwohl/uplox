"""Lua backend. Emits a single self-contained ``.lua`` module per grammar.

Re-entrant: the module returns constructors for parser instances; the module
itself holds only the const tables. Two grammars built with plox can be
loaded into the same Lua state without sharing any state — the modules
expose a per-instance ``new(input)`` method, never globals.

The emitter targets Lua 5.3+ (uses integer arithmetic and ``string.byte``
without locale assumptions). Tables are 1-indexed per Lua convention; the
emitter biases every state and byte index up by one when emitting the
arrays.
"""

from .emit import emit_lua

__all__ = ["emit_lua"]
