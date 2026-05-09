"""Pluggable hooks framework with built-in helpers.

Hooks fire at well-defined points in the parser: ``pre_shift``, ``pre_reduce``,
``post_reduce``, ``on_error``. The base :class:`HookRegistry` and
:class:`ParseContext` live in :mod:`uplox.parse.runtime` because the runtime
needs to know about them; this module provides the **helpers** the plan calls
for:

* :class:`ScopedNameTable` — a stack of dicts, push/pop driven by hooks.
* :class:`TypeTable` — flat name->type mapping with a single source of truth.
* :class:`SyncSetRecovery` — error-recovery helper that synchronises on a set
  of terminals.

These ship with uplox so common context-sensitive concerns (name resolution,
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

from ..lex.scanner import Token
from ..parse.runtime import HookRegistry, ParseContext, ParseNode

__all__ = [
    "HookRegistry",
    "ParseContext",
    "ScopedNameTable",
    "TypeTable",
    "SyncSetRecovery",
    "TypedefTracker",
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


# ---- Typedef-name lexer feedback ---------------------------------------------


@dataclass
class TypedefTracker:
    """Tracks identifiers that have been typedef'd, then rewrites IDENT tokens
    bearing those names to a different terminal (default ``TYPEDEF_NAME``).

    This is the canonical solution to the C "typedef-name" problem: in a real
    C grammar, ``MyInt a;`` is a declaration when ``MyInt`` was typedef'd,
    but a function call (``MyInt`` as expression) when it wasn't. The parser
    needs to see two different terminals to pick the right action — but the
    lexer alone can't tell them apart. The classical fix wires a feedback
    edge: after the parser reduces a typedef declaration, a hook tells the
    tracker to mark the new name; the tracker's :meth:`filter` then rewrites
    subsequent IDENT lookahead tokens to ``TYPEDEF_NAME``.

    Wiring is two parts:

    * Pass :meth:`filter` as the ``token_filter=`` argument to ``parse()``.
    * Register :meth:`record_declaration` in the :class:`HookRegistry` under
      the hook name that the grammar's typedef-bearing production uses
      (e.g. ``%hook=record_typedef``). The tracker fires only on
      ``post_reduce`` and only when the reduced node carries a typedef
      storage class.

    The tracker is stateful and reentrant: each parse pass owns one. Its
    state lives outside :class:`ParseContext.user` (so the filter doesn't
    have to hunt for it) but it inspects ``ctx`` only to decide whether
    a node looks like a typedef declaration.
    """

    rewrite_to: str = "TYPEDEF_NAME"
    source_terminal: str = "IDENT"
    storage_class_token: str = "KW_TYPEDEF"
    declaration_kind: str = "declaration"
    names: set[str] = field(default_factory=set)

    def filter(self, ctx: ParseContext, tok: Token) -> Token:
        if tok.name == self.source_terminal and tok.text in self.names:
            return Token(
                name=self.rewrite_to,
                text=tok.text,
                line=tok.line,
                column=tok.column,
                offset=tok.offset,
            )
        return tok

    def record_declaration(self, ctx: ParseContext, payload: dict) -> None:
        if payload.get("when") != "post_reduce":
            return
        node = payload.get("value")
        if not isinstance(node, ParseNode) or node.kind != self.declaration_kind:
            return
        if not _has_token(node, self.storage_class_token):
            return
        for name in _declared_names(node):
            self.names.add(name)


def _has_token(node: ParseNode, target: str) -> bool:
    """True iff a Token with the given terminal name appears anywhere in node."""
    stack: list[Any] = [node]
    while stack:
        cur = stack.pop()
        if isinstance(cur, Token):
            if cur.name == target:
                return True
            continue
        if isinstance(cur, ParseNode):
            stack.extend(cur.children)
    return False


def _declared_names(node: ParseNode) -> Iterable[str]:
    """Yield the IDENT name(s) declared by a C declaration ParseNode.

    The shape relied on is the c_subset grammar:
    ``declaration -> decl_specs init_declarator_list_opt SEMI`` and
    ``init_declarator -> declarator [ASSIGN initializer]``. Each declarator
    nests further wrappers (pointer, array, function-paren) around a single
    direct-declarator IDENT — the leftmost IDENT in a depth-first walk of
    the declarator subtree.
    """
    if not isinstance(node, ParseNode):
        return
    init_list = _find_first_kind(node, "init_declarator_list_opt")
    if init_list is None:
        return
    for declarator in _walk_declarators(init_list):
        name = _leftmost_ident(declarator)
        if name is not None:
            yield name


def _find_first_kind(node: ParseNode, kind: str) -> ParseNode | None:
    """Depth-first search for the first ParseNode child with matching kind."""
    if isinstance(node, ParseNode) and node.kind == kind:
        return node
    if not isinstance(node, ParseNode):
        return None
    for child in node.children:
        if isinstance(child, ParseNode):
            result = _find_first_kind(child, kind)
            if result is not None:
                return result
    return None


def _walk_declarators(node: ParseNode) -> Iterable[ParseNode]:
    """Yield each `declarator` ParseNode reachable from an
    init_declarator_list_opt, NOT recursing into parameter declarators
    (those name function arguments, not the typedef'd entity)."""
    if not isinstance(node, ParseNode):
        return
    if node.kind == "declarator":
        yield node
        return
    if node.kind == "parameter_list":
        # Parameter names are not typedef'd by the outer declaration.
        return
    for child in node.children:
        if isinstance(child, ParseNode):
            yield from _walk_declarators(child)


def _leftmost_ident(node: ParseNode) -> str | None:
    """Return the textually-leftmost IDENT inside a declarator subtree.

    Stops at parameter_list so that `MyType` in `MyType func(int x)` wins
    over `x`."""
    if isinstance(node, Token):
        return node.text if node.name == "IDENT" else None
    if not isinstance(node, ParseNode):
        return None
    if node.kind == "parameter_list":
        return None
    for child in node.children:
        result = _leftmost_ident(child)
        if result is not None:
            return result
    return None


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
