"""Recursive-descent reader for ``.plox`` grammar source.

This is the bootstrap reader. It is hand-written so plox can be built without
already having a working plox-generated parser. Once the LR core is stable,
plox's own grammar will be re-expressed as a ``.plox`` file and parsed by a
plox-generated front-end.

Grammar of the DSL itself is documented in ``docs/grammar_format.md``.

The reader is intentionally permissive and reports the first syntax error with
file:line:column. Stage-2 readers (LR-generated) will improve diagnostics; this
one only needs to bootstrap.
"""

from __future__ import annotations

from .ir import GrammarIR, HookDecl, Position, Production, Rule, Symbol, TokenDecl


class ReaderError(Exception):
    pass


def read_source(text: str, filename: str = "<source>") -> GrammarIR:
    """Parse a ``.plox`` source string into a :class:`GrammarIR`.

    The full DSL is not implemented yet; this is the bootstrap stub used so that
    downstream stages have a target. Tracked under Phase 1; full implementation
    lands in Phase 2 / 3 alongside the lexer and parser pipeline.
    """
    raise NotImplementedError(
        "plox.spec.reader is a Phase 2 deliverable; only the IR types are wired up so far"
    )


def read_file(path: str) -> GrammarIR:
    with open(path, "r", encoding="utf-8") as fh:
        return read_source(fh.read(), filename=path)
