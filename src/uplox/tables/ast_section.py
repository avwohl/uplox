"""AST section of the canonical JSON bundle: AstPlan <-> JSON.

Layout
------

::

    "ast": {
      "drop_tokens": ["LPAREN", "RPAREN", "SEMI"],
      "node_kinds": [
        {
          "name": "BinOp",
          "fields": [
            {"name": "lhs", "type": "node"},
            {"name": "op",  "type": "token"},
            {"name": "rhs", "type": "node"}
          ]
        },
        {
          "name": "If",
          "fields": [
            {"name": "cond",        "type": "node"},
            {"name": "then_branch", "type": "node"},
            {"name": "otherwise",   "type": "node", "optional": true}
          ]
        },
        {
          "name": "Call",
          "fields": [
            {"name": "callee", "type": "node"},
            {"name": "args",   "type": "list", "element": "expr"}
          ]
        }
      ],
      "production_actions": [
        {"user_index": 0, "kind": "BinOp", "fields": [
            {"from": 0, "to": "lhs"},
            {"from": 1, "to": "op"},
            {"from": 2, "to": "rhs"}
        ]},
        {"user_index": 2, "kind": "_lift", "source_index": 0},
        {"user_index": 5, "kind": "_list_extend",
         "accumulator_index": 0, "element_index": 2}
      ]
    }

Production indices are :class:`uplox.spec.ir.GrammarIR` user indices
— the 0-based count across user productions in IR order. Runtime
dispatch maps a grammar production's ``user_index`` to its action
entry; the augmented-start production has ``user_index == -1`` and
gets a default ``_lift rhs[0]`` from the driver, never a JSON entry.

Bundles with no AST annotations omit this section's contents (the
parent ``ast: {}`` empty object stays for top-level shape stability).
:func:`ast_to_json` returns ``{}`` when given ``None``; downstream
loaders read ``{}`` back as "grammar is not in AST mode."
"""

from __future__ import annotations

from typing import Any, Optional

from ..spec.ast_plan import (
    AstPlan,
    FieldAssignment,
    NodeField,
    NodeKind,
    ProductionAction,
    RUNTIME_KINDS,
)


# ---- Serialise --------------------------------------------------------------


def ast_to_json(plan: Optional[AstPlan]) -> dict[str, Any]:
    """Serialise an :class:`AstPlan` to the bundle's ``ast`` section dict.

    ``None`` plans serialise to ``{}`` — the grammar is not in AST
    mode and the section is empty.
    """
    if plan is None:
        return {}
    return {
        "drop_tokens": sorted(plan.drop_tokens),
        "node_kinds": [_kind_to_json(k) for k in plan.node_kinds],
        "production_actions": [_action_to_json(a) for a in plan.production_actions],
    }


def _kind_to_json(kind: NodeKind) -> dict[str, Any]:
    return {
        "name": kind.name,
        "fields": [_field_to_json(f) for f in kind.fields],
    }


def _field_to_json(f: NodeField) -> dict[str, Any]:
    out: dict[str, Any] = {"name": f.name, "type": f.type_kind}
    if f.optional:
        out["optional"] = True
    if f.list_element_kind is not None:
        out["element"] = f.list_element_kind
    return out


def _action_to_json(a: ProductionAction) -> dict[str, Any]:
    out: dict[str, Any] = {"user_index": a.user_index, "kind": a.kind}
    if a.kind in RUNTIME_KINDS:
        if a.kind in ("_lift", "_unwrap"):
            # source_index == -1 sentinel = empty alt producing None
            # (or for unreachable rules, a permissive placeholder). Both
            # cases serialise the value; the runtime treats -1 as "no
            # value."
            out["source_index"] = a.source_index
        elif a.kind == "_list_init":
            out["element_indices"] = list(a.element_indices)
        elif a.kind == "_list_extend":
            out["accumulator_index"] = a.accumulator_index
            out["element_indices"] = list(a.element_indices)
        # _list_empty has no extra data.
    else:
        out["fields"] = [_assignment_to_json(fa) for fa in a.field_assignments]
    return out


def _assignment_to_json(fa: FieldAssignment) -> dict[str, Any]:
    return {"from": fa.rhs_index, "to": fa.field_name}


# ---- Deserialise ------------------------------------------------------------


def ast_from_json(section: dict[str, Any]) -> Optional[AstPlan]:
    """Read the bundle's ``ast`` section back into an :class:`AstPlan`.

    Empty dicts (``{}``) deserialise to ``None`` — the round-trip
    image of the back-compat path.
    """
    if not section:
        return None
    return AstPlan(
        drop_tokens=frozenset(section.get("drop_tokens", [])),
        node_kinds=tuple(_kind_from_json(k) for k in section.get("node_kinds", [])),
        production_actions=tuple(
            _action_from_json(a) for a in section.get("production_actions", [])
        ),
    )


def _kind_from_json(d: dict[str, Any]) -> NodeKind:
    return NodeKind(
        name=d["name"],
        fields=tuple(_field_from_json(f) for f in d.get("fields", [])),
    )


def _field_from_json(d: dict[str, Any]) -> NodeField:
    return NodeField(
        name=d["name"],
        type_kind=d["type"],
        optional=bool(d.get("optional", False)),
        list_element_kind=d.get("element"),
    )


def _action_from_json(d: dict[str, Any]) -> ProductionAction:
    kind = d["kind"]
    user_index = d["user_index"]
    if kind in RUNTIME_KINDS:
        if kind in ("_lift", "_unwrap"):
            return ProductionAction(
                user_index=user_index,
                kind=kind,
                source_index=d.get("source_index", -1),
            )
        if kind == "_list_init":
            return ProductionAction(
                user_index=user_index,
                kind=kind,
                element_indices=tuple(d.get("element_indices", ())),
            )
        if kind == "_list_extend":
            return ProductionAction(
                user_index=user_index,
                kind=kind,
                accumulator_index=d.get("accumulator_index", -1),
                element_indices=tuple(d.get("element_indices", ())),
            )
        if kind == "_list_empty":
            return ProductionAction(user_index=user_index, kind=kind)
        raise ValueError(f"unknown runtime kind {kind!r}")
    # Named user kind: deserialise field assignments.
    fields = tuple(
        FieldAssignment(rhs_index=fa["from"], field_name=fa["to"])
        for fa in d.get("fields", [])
    )
    return ProductionAction(user_index=user_index, kind=kind, field_assignments=fields)
