"""C++ backend. Emits a class-per-grammar driver inside a per-grammar namespace.

Re-entrant in the same way the C backend is — but expressed idiomatically:
state lives on a ``Parser`` instance, tables are file-static const inside the
implementation file, and every escaping symbol sits in ``namespace
plox::<grammar>``. Two grammars built with plox can coexist in the same
binary because their identifiers are reachable only through different
namespaces.

Generated outputs:

* ``plox_<grammar>.hpp`` — public types + Parser class declaration.
* ``plox_<grammar>.cpp`` — embedded tables, scanner, parser implementation.

Compiles with C++17 (``-std=c++17``). No exceptions thrown; errors surface
through ``Parser::parse()`` returning ``false`` and ``Parser::error()``.
"""

from .emit import emit_cpp

__all__ = ["emit_cpp"]
