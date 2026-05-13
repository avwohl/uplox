"""Lua backend: v3 auto-AST emission.

When a bundle carries a non-empty ``ast`` section, the Lua emitter
appends a typed AST surface: each node kind becomes a table with
a ``kind`` field naming the AST kind; named slots show up as
fields on the table; list fields are Lua arrays; token fields are
references to parse-tree leaf tables.

Two new module functions:

* ``M.AST_KINDS`` — table mapping kind name -> integer ordinal.
* ``Parser:parse_ast()`` — calls ``parse()`` then walks the parse
  tree to build the AST. Returns the root AST table on success,
  ``nil`` on failure (``self.error`` carries the message).

Lua arrays are 1-indexed throughout, so the emitter translates
each 0-based RHS index from the AstPlan to ``children[i + 1]`` at
emission time.
"""

from __future__ import annotations

from typing import Any

from ...spec.ast_plan import AstPlan, NodeField, NodeKind, ProductionAction
from ...tables import ast_from_json


def has_ast(bundle: dict[str, Any]) -> bool:
    section = bundle.get("ast") or {}
    return bool(section.get("node_kinds"))


def plan_from_bundle(bundle: dict[str, Any]) -> AstPlan | None:
    section = bundle.get("ast") or {}
    if not section:
        return None
    return ast_from_json(section)


# ---- Top-level emission -----------------------------------------------------


def emit_ast(plan: AstPlan) -> list[str]:
    """All AST-related lines for the Lua module."""
    out: list[str] = [
        "",
        "-- ========================================================================",
        "-- AST (v3 auto-AST)",
        "-- ========================================================================",
        "",
    ]
    out.extend(_emit_kinds_table(plan))
    out.append("")
    out.extend(_emit_unwrap_table(plan))
    out.append("")
    out.extend(_emit_pos_helper())
    out.append("")
    # Forward-decl the two mutually-recursive walkers so each can call the other.
    out.append("local _build_ast")
    out.append("local _collect_list")
    out.append("")
    out.extend(_emit_build_ast(plan))
    out.append("")
    out.extend(_emit_collect_list(plan))
    out.append("")
    out.extend(_emit_parse_ast_method())
    return out


def _emit_kinds_table(plan: AstPlan) -> list[str]:
    out = ["M.AST_KINDS = {"]
    for i, kind in enumerate(plan.node_kinds, start=1):
        out.append(f'    {kind.name} = {i},')
    out.append("}")
    return out


def _emit_unwrap_table(plan: AstPlan) -> list[str]:
    """Per-production unwrap source-index table; sparse Lua array."""
    out = ["local _unwrap_src = {"]
    for a in plan.production_actions:
        if a.kind == "_unwrap":
            # Emit each entry; missing entries default to nil which
            # signals "not an unwrap production."
            out.append(f"    [{a.user_index + 1}] = {a.source_index},")
    out.append("}")
    out.append("")
    out.append("local function _resolve_to_token(pn)")
    out.append("    while pn and not pn.is_terminal do")
    out.append("        if not pn.children or #pn.children == 0 then return nil end")
    out.append("        local src = _unwrap_src[pn.production]")
    out.append("        if not src or src < 0 then break end")
    out.append("        -- src is the 0-based RHS index; Lua tables are 1-indexed.")
    out.append("        local child = pn.children[src + 1]")
    out.append("        if not child then break end")
    out.append("        pn = child")
    out.append("    end")
    out.append("    if pn and pn.is_terminal then return pn end")
    out.append("    return nil")
    out.append("end")
    return out


def _emit_pos_helper() -> list[str]:
    return [
        "local function _compute_pos(pos, pn)",
        "    local leftmost = pn",
        "    while leftmost and not leftmost.is_terminal and leftmost.children and #leftmost.children > 0 do",
        "        leftmost = leftmost.children[1]",
        "    end",
        "    local rightmost = pn",
        "    while rightmost and not rightmost.is_terminal and rightmost.children and #rightmost.children > 0 do",
        "        rightmost = rightmost.children[#rightmost.children]",
        "    end",
        "    if leftmost and leftmost.is_terminal then",
        "        pos.start_line = leftmost.line",
        "        pos.start_column = leftmost.column",
        "    elseif pn then",
        "        pos.start_line = pn.line or 0",
        "        pos.start_column = pn.column or 0",
        "    end",
        "    if rightmost and rightmost.is_terminal then",
        "        pos.end_line = rightmost.line",
        "        pos.end_column = rightmost.column + #rightmost.text",
        "    else",
        "        pos.end_line = pos.start_line",
        "        pos.end_column = pos.start_column",
        "    end",
        "end",
        "",
        "local function _new_ast(kind_name)",
        "    return {",
        "        kind = kind_name,",
        "        pos = { start_line = 0, start_column = 0, end_line = 0, end_column = 0 },",
        "    }",
        "end",
    ]


# ---- build_ast / collect_list -----------------------------------------------


def _emit_build_ast(plan: AstPlan) -> list[str]:
    out = [
        "_build_ast = function(pn)",
        "    if not pn then return nil end",
        "    if pn.is_terminal then return nil end",
        "    local prod = pn.production",
        # Augmented start: lift child[1] (1-indexed; rhs[0] -> children[1]).
        "    if prod == 0 then return _build_ast(pn.children[1]) end",
    ]
    for action in plan.production_actions:
        grammar_idx = action.user_index + 1
        out.extend(_emit_action_arm(plan, action, grammar_idx))
    out.append("    return nil")
    out.append("end")
    return out


def _emit_action_arm(
    plan: AstPlan, action: ProductionAction, grammar_idx: int,
) -> list[str]:
    """Emit a single ``if prod == N then ... end`` arm."""
    if action.kind in ("_list_init", "_list_extend", "_list_empty"):
        return [f"    if prod == {grammar_idx} then return nil end  -- list production"]
    if action.kind == "_lift":
        if action.source_index < 0:
            return [f"    if prod == {grammar_idx} then return nil end  -- empty alt"]
        return [
            f"    if prod == {grammar_idx} then return _build_ast(pn.children[{action.source_index + 1}]) end",
        ]
    if action.kind == "_unwrap":
        return [
            f"    if prod == {grammar_idx} then return _build_ast(pn.children[{action.source_index + 1}]) end",
        ]
    # Named kind.
    kind = _find_kind(plan, action.kind)
    if kind is None:
        return [f"    if prod == {grammar_idx} then return nil end"]
    out = [
        f"    if prod == {grammar_idx} then",
        f'        local n = _new_ast("{kind.name}")',
        "        _compute_pos(n.pos, pn)",
    ]
    schema = {f.name: f for f in kind.fields}
    for fa in action.field_assignments:
        f = schema.get(fa.field_name)
        if f is None:
            continue
        out.extend(_emit_field_assignment(fa.rhs_index, fa.field_name, f))
    out.append("        return n")
    out.append("    end")
    return out


def _emit_field_assignment(
    rhs_idx: int, field_name: str, field: NodeField,
) -> list[str]:
    lua_idx = rhs_idx + 1
    if field.type_kind == "token":
        return [f"        n.{field_name} = _resolve_to_token(pn.children[{lua_idx}])"]
    if field.type_kind == "list":
        return [
            f"        n.{field_name} = {{}}",
            f"        _collect_list(pn.children[{lua_idx}], n.{field_name})",
        ]
    # type_kind == "node"
    return [f"        n.{field_name} = _build_ast(pn.children[{lua_idx}])"]


def _emit_collect_list(plan: AstPlan) -> list[str]:
    out = [
        "_collect_list = function(pn, out)",
        "    if not pn then return end",
        "    if pn.is_terminal then return end",
        "    if not pn.children or #pn.children == 0 then return end  -- empty alt",
        "    local prod = pn.production",
    ]
    for action in plan.production_actions:
        grammar_idx = action.user_index + 1
        if action.kind == "_list_empty":
            out.append(f"    if prod == {grammar_idx} then return end  -- _list_empty")
        elif action.kind == "_unwrap":
            out.append(f"    if prod == {grammar_idx} then  -- _unwrap -> list")
            out.append(
                f"        return _collect_list(pn.children[{action.source_index + 1}], out)"
            )
            out.append("    end")
        elif action.kind == "_lift":
            if action.source_index < 0:
                out.append(f"    if prod == {grammar_idx} then return end  -- empty-alt sentinel")
            else:
                out.append(f"    if prod == {grammar_idx} then  -- _lift -> list")
                out.append(
                    f"        return _collect_list(pn.children[{action.source_index + 1}], out)"
                )
                out.append("    end")
        elif action.kind == "_list_init":
            out.append(f"    if prod == {grammar_idx} then  -- _list_init")
            for i in action.element_indices:
                out.append(
                    f"        out[#out + 1] = _build_ast(pn.children[{i + 1}])"
                )
            out.append("        return")
            out.append("    end")
        elif action.kind == "_list_extend":
            elem_idx = action.element_indices[0]
            acc_idx = action.accumulator_index
            out.append(f"    if prod == {grammar_idx} then  -- _list_extend")
            if elem_idx < acc_idx:
                out.append(
                    f"        out[#out + 1] = _build_ast(pn.children[{elem_idx + 1}])"
                )
                out.append(
                    f"        return _collect_list(pn.children[{acc_idx + 1}], out)"
                )
            else:
                out.append(
                    f"        _collect_list(pn.children[{acc_idx + 1}], out)"
                )
                out.append(
                    f"        out[#out + 1] = _build_ast(pn.children[{elem_idx + 1}])"
                )
                out.append("        return")
            out.append("    end")
    out.append("end")
    return out


def _emit_parse_ast_method() -> list[str]:
    return [
        "function Parser:parse_ast()",
        "    if not self:parse() then return nil end",
        "    self.ast = _build_ast(self.root)",
        "    if not self.ast then",
        "        self.error = self.error or 'build_ast: failed'",
        "        return nil",
        "    end",
        "    return self.ast",
        "end",
    ]


def _find_kind(plan: AstPlan, name: str) -> NodeKind | None:
    for k in plan.node_kinds:
        if k.name == name:
            return k
    return None
