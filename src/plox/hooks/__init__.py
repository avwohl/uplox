"""Pluggable hooks framework with built-in helpers.

Hooks fire at well-defined points in the parser: ``pre_shift``, ``pre_reduce``,
``post_reduce``, ``on_error``. The base :class:`HookRegistry` and
:class:`ParseContext` live in :mod:`plox.parse.runtime` because the runtime
needs to know about them; this module provides the **helpers** the plan calls
for:

* :class:`ScopedNameTable` — a stack of dicts, push/pop driven by hooks.
* :class:`TypeTable` — flat name->type mapping with a single source of truth.
* :class:`SyncSetRecovery` — error-recovery helper that synchronises on a set
  of terminals.

These ship with plox so common context-sensitive concerns (name resolution,
typing, panic-mode recovery) do not leak into the grammar.

Note on LR scope timing
-----------------------

Bottom-up LR reducers run innermost-first: by the time the outer
``block`` reduces, every nested ``block`` has already completed its pre/post
pair. That is fine for **declaring** scopes after the fact (sealing a frame
for downstream tooling), but a host that needs *open* nested scopes during
parsing — e.g. for ``forward`` declarations to be visible to inner blocks —
should either:

* Use mid-rule markers (a planned Phase-5 DSL extension), or
* Maintain its own scope stack from a ``pre_shift`` hook keyed on the opening
  terminal (``LBRACE``, ``BEGIN``, …). The grammar shape matters more than
  hook plumbing for that pattern.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Iterable

from ..parse.runtime import HookRegistry, ParseContext

__all__ = [
    "HookRegistry",
    "ParseContext",
    "ScopedNameTable",
    "TypeTable",
    "SyncSetRecovery",
    "scoped_table_hooks",
]


# ---- Scoped name table -------------------------------------------------------


@dataclass
class ScopedNameTable:
    """Lexical scoping with explicit push/pop. Inner scopes shadow outer ones.

    The table is fully re-entrant: every instance is independent, and
    :class:`ParseContext.user` is the natural home for an instance during a
    parse run. Nothing in this class touches module globals.

    .. code-block:: python

        nt = ScopedNameTable()
        nt.push("function")
        nt.bind("x", 42)
        assert nt.lookup("x") == 42
        nt.pop()
        assert nt.lookup("x") is None
    """
    stack: list[dict[str, Any]] = field(default_factory=lambda: [{}])
    scope_kinds: list[str] = field(default_factory=lambda: ["global"])

    def push(self, kind: str = "block") -> None:
        self.stack.append({})
        self.scope_kinds.append(kind)

    def pop(self) -> dict[str, Any]:
        if len(self.stack) == 1:
            raise IndexError("cannot pop the global scope")
        self.scope_kinds.pop()
        return self.stack.pop()

    def bind(self, name: str, value: Any) -> None:
        """Bind ``name`` in the innermost scope. Overwrites a same-scope shadow."""
        self.stack[-1][name] = value

    def bind_unique(self, name: str, value: Any) -> None:
        """Like :meth:`bind` but raises if the name is already bound at this scope.

        Useful for languages that forbid redeclaration in the same scope.
        """
        if name in self.stack[-1]:
            raise NameError(f"redefinition of {name!r} in {self.scope_kinds[-1]!r} scope")
        self.bind(name, value)

    def lookup(self, name: str) -> Any:
        """Innermost-first lookup. Returns ``None`` when not bound."""
        for frame in reversed(self.stack):
            if name in frame:
                return frame[name]
        return None

    def depth(self) -> int:
        return len(self.stack)


def scoped_table_hooks(
    registry: HookRegistry,
    *,
    scope_on: Iterable[str] = (),
    user_key: str = "scope",
) -> None:
    """Wire scope-management callbacks onto ``registry`` for a :class:`ScopedNameTable`.

    ``scope_on`` is a list of hook names (matching ``%hook=…`` in the grammar).
    Each one represents a scope-bearing production: when the production is
    being reduced, the runtime fires the hook twice, once with
    ``when="pre_reduce"`` and once with ``when="post_reduce"``. The wrapper
    pushes a new scope at ``pre_reduce`` and pops it at ``post_reduce``, so
    a single hook name handles the entire scope lifecycle.

    The host driver must put a :class:`ScopedNameTable` instance into
    ``ctx.user[user_key]`` before parsing — that way the hooks are stateless
    and the driver controls the scope's lifetime independently of the parser.
    """
    names = set(scope_on)

    def fn(ctx: ParseContext, payload: dict) -> None:
        when = payload.get("when")
        if when not in ("pre_reduce", "post_reduce"):
            return
        table = ctx.user.get(user_key)
        if not isinstance(table, ScopedNameTable):
            raise RuntimeError(
                f"scoped_table_hooks: ctx.user[{user_key!r}] is not a ScopedNameTable"
            )
        if when == "pre_reduce":
            table.push(payload.get("scope_kind", "block"))
        else:
            table.pop()

    for name in names:
        registry.register(name, fn)


# ---- Type table --------------------------------------------------------------


@dataclass
class TypeTable:
    """Flat name -> type mapping. No scoping — a single source of truth.

    For lexically-scoped types, layer this on top of :class:`ScopedNameTable`
    (one TypeTable per scope frame) at the host-driver level.
    """
    types: dict[str, Any] = field(default_factory=dict)

    def declare(self, name: str, ty: Any, *, allow_redeclaration: bool = False) -> None:
        if name in self.types and not allow_redeclaration:
            raise NameError(f"redeclaration of type for {name!r}")
        self.types[name] = ty

    def type_of(self, name: str) -> Any:
        return self.types.get(name)


# ---- Error recovery: sync sets -----------------------------------------------


@dataclass
class SyncSetRecovery:
    """Panic-mode error recovery driver.

    Configured with a set of synchronising terminals (typically end-of-statement
    or end-of-block markers). On parse error, the host scans forward in the
    token stream until a sync token is found, then resumes parsing from there.

    The recovery itself is implemented in the host driver — this class is the
    decision policy. It records every error so the host can stop after a
    threshold, format diagnostics in source order, etc.
    """
    sync_terminals: frozenset[str]
    errors: list[dict] = field(default_factory=list)
    max_errors: int = 100

    def record(self, payload: dict) -> bool:
        """Record an error; return True if parsing should keep trying to recover."""
        self.errors.append(payload)
        return len(self.errors) < self.max_errors

    def is_sync(self, terminal: str) -> bool:
        return terminal in self.sync_terminals
