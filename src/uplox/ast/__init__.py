"""AST node schema and the default tree-builder action set.

The parser runtime in :mod:`uplox.parse.runtime` produces :class:`uplox.parse.runtime.ParseNode`
trees by default — a one-to-one mirror of the grammar's parse tree. That is fine
for prototyping but generally too verbose for downstream tools.

This module gives:

* A neutral :class:`AstNode` schema that grammar authors can target through
  semantic actions, with one node kind per concept rather than per production.
* The :func:`build_ast` helper: walks a ParseNode tree and applies user-supplied
  per-rule reducers to flatten chains, drop literal punctuation, and lift
  meaningful children. This is the "default tree-builder" deliverable from the
  Phase-4 plan.
* JSON ser/deser in :func:`ast_to_json` / :func:`ast_from_json` so AST nodes
  fit cleanly into the bundle's ``ast`` section once a backend wants to
  pre-compute them.

What this module deliberately does *not* do:

* Type checking, name resolution, scope handling — those are hooks (see
  :mod:`uplox.hooks`), not AST concerns.
* Pretty-printing — would couple AST shape to host-language style; left to
  callers.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Optional, Union

from ..lex.scanner import Token
from ..parse.runtime import ParseNode

AstChild = Union["AstNode", Token, str, int, float, bool, None]


@dataclass
class AstNode:
    """Neutral AST node. Distinct from :class:`ParseNode` so the AST shape can
    diverge from the grammar's parse tree.

    ``kind`` is a stable string the user picks (e.g. ``"BinaryOp"``). ``attrs``
    holds named per-node fields (operator, name, value). ``children`` are
    ordered children in source order.
    """

    kind: str
    attrs: dict[str, Any] = field(default_factory=dict)
    children: list[AstChild] = field(default_factory=list)
    position: Optional[tuple[int, int]] = None  # (line, column) of the first token

    def __repr__(self) -> str:  # pragma: no cover - cosmetic
        return f"AstNode({self.kind!r}, attrs={self.attrs!r}, children={self.children!r})"


# A reducer takes a ParseNode (or token leaf) and returns an AstChild.
# The dispatch is by ParseNode.kind (i.e. the grammar's LHS for that node).
Reducer = Callable[[ParseNode, list[AstChild]], AstChild]


def build_ast(
    root: ParseNode | Token,
    reducers: dict[str, Reducer],
    *,
    drop_tokens: Iterable[str] = (),
) -> AstChild:
    """Walk ``root`` bottom-up; transform each ParseNode through its reducer.

    For each ParseNode encountered:
    * Recursively transform every child (Token children become themselves
      unless their token name is in ``drop_tokens``, in which case they are
      filtered out — handy for punctuation like LPAREN/SEMI).
    * Look up ``reducers[node.kind]``. If present, call it with the original
      ParseNode and the (already-transformed) child list. The return value
      is what ends up in the parent's child list.
    * If no reducer is registered, fall back to wrapping the node in an
      AstNode with the same ``kind``.

    Token leaves at the root level pass through unchanged (the caller can
    map them as they please).
    """
    drop_set = set(drop_tokens)

    def visit(node: ParseNode | Token) -> AstChild:
        if isinstance(node, Token):
            return node
        new_children: list[AstChild] = []
        for c in node.children:
            if isinstance(c, Token) and c.name in drop_set:
                continue
            new_children.append(visit(c) if isinstance(c, (ParseNode, Token)) else c)
        reducer = reducers.get(node.kind)
        if reducer is None:
            return AstNode(kind=node.kind, children=new_children)
        return reducer(node, new_children)

    return visit(root)


# ---- JSON serialisation ------------------------------------------------------


def ast_to_json(node: AstChild) -> Any:
    """Serialise an AST tree to JSON-compatible primitives.

    AstNodes become dicts with ``kind``, ``attrs``, ``children`` (and ``position``
    when set). Tokens become ``{"_token": name, "text": ..., "line": ..., "column": ...}``.
    Plain Python primitives pass through untouched. The shape is deliberately
    explicit so a backend in any language can reconstruct it without inferring
    types.
    """
    if isinstance(node, AstNode):
        out: dict[str, Any] = {
            "_kind": node.kind,
            "attrs": dict(node.attrs),
            "children": [ast_to_json(c) for c in node.children],
        }
        if node.position is not None:
            out["position"] = list(node.position)
        return out
    if isinstance(node, Token):
        return {
            "_token": node.name,
            "text": node.text,
            "line": node.line,
            "column": node.column,
        }
    return node


def ast_from_json(value: Any) -> AstChild:
    if isinstance(value, dict):
        if "_token" in value:
            return Token(
                name=value["_token"],
                text=value["text"],
                line=value["line"],
                column=value["column"],
                offset=value.get("offset", -1),
            )
        if "_kind" in value:
            pos = value.get("position")
            return AstNode(
                kind=value["_kind"],
                attrs=dict(value.get("attrs", {})),
                children=[ast_from_json(c) for c in value.get("children", [])],
                position=tuple(pos) if pos else None,
            )
    return value
