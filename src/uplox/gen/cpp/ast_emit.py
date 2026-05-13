"""C++ backend: v3 auto-AST emission.

When a bundle carries a non-empty ``ast`` section, the C++ emitter
appends:

* one class per declared ``%ast=Name``, all inheriting from a
  common ``AstNode`` base with a ``kind`` tag and ``AstPos`` span;
* ``Parser::parse_ast()`` and ``Parser::ast_root()`` to expose
  the typed AST surface alongside the existing parse-tree API;
* a recursive walker over the parse tree that constructs AST
  nodes via the production-action dispatch the AstPlan emits.

Memory model: AST nodes are owned by ``Parser::Impl`` via a
``std::vector<std::unique_ptr<AstNode>>`` arena, mirroring how
parse-tree nodes are already tracked. Lists use ``std::vector<AstNode*>``
of non-owning pointers into the same arena. Token fields are
``const Node*`` pointing into the parse-tree (which is kept alive
by the Parser).
"""

from __future__ import annotations

import re
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


# ---- Header -----------------------------------------------------------------


def emit_ast_header(plan: AstPlan) -> list[str]:
    out: list[str] = [
        "",
        "/* ====================================================================== */",
        "/* AST (v3 auto-AST)                                                      */",
        "/* ====================================================================== */",
        "",
        "/* Per-kind classes inherit from AstNode; ownership lives in the",
        " * Parser. Token fields point into the parse-tree leaves and are",
        " * valid for the Parser's lifetime. */",
        "",
        "enum class AstKind : int {",
        "    _None_ = 0,",
    ]
    for kind in plan.node_kinds:
        out.append(f"    {kind.name},")
    out.append("    _Count_")
    out.append("};")
    out.append("")
    out.append("struct AstPos {")
    out.append("    int start_line = 0;")
    out.append("    int start_column = 0;")
    out.append("    int end_line = 0;")
    out.append("    int end_column = 0;")
    out.append("};")
    out.append("")
    out.append("struct AstNode {")
    out.append("    AstKind kind;")
    out.append("    AstPos  pos;")
    out.append("    explicit AstNode(AstKind k) : kind(k) {}")
    out.append("    virtual ~AstNode() = default;")
    out.append("};")
    out.append("")
    # Forward-declare AstNode subclasses so list-of-pointers types
    # work without requiring full definitions in any order.
    for kind in plan.node_kinds:
        out.append(f"struct {kind.name};")
    out.append("")
    for kind in plan.node_kinds:
        out.extend(_emit_kind_class(kind))
        out.append("")
    out.append("/* Built by Parser::parse_ast() after a successful parse(). */")
    out.append("const char* ast_kind_name(AstKind k);")
    out.append("")
    return out


def _emit_kind_class(kind: NodeKind) -> list[str]:
    out = [f"struct {kind.name} : AstNode {{"]
    for f in kind.fields:
        out.append(f"    {_field_decl(f)};")
    out.append(f"    {kind.name}() : AstNode(AstKind::{kind.name}) {{}}")
    out.append("};")
    return out


def _field_decl(f: NodeField) -> str:
    if f.type_kind == "token":
        return f"const Node* {f.name} = nullptr"
    if f.type_kind == "list":
        return f"std::vector<AstNode*> {f.name}"
    # type_kind == "node"
    return f"AstNode* {f.name} = nullptr"


# ---- Header insertion into Parser class -------------------------------------


def emit_parser_ast_methods() -> list[str]:
    """Public methods to add to Parser when the bundle has an ast section."""
    return [
        "",
        "    /* v3 auto-AST: parse the input and build a typed AST tree.",
        "       The returned tree is owned by the Parser and freed with it.",
        "       Returns true on success; on failure inspect error(). */",
        "    bool parse_ast();",
        "    const AstNode* ast_root() const { return ast_root_; }",
    ]


def emit_parser_ast_fields() -> list[str]:
    """Private member fields to add to Parser when ast is present."""
    return [
        "    AstNode* ast_root_ = nullptr;",
    ]


# ---- Impl-struct AST arena --------------------------------------------------


def emit_impl_ast_fields() -> list[str]:
    """Fields on Parser::Impl that own AST node lifetime."""
    return [
        "",
        "    std::vector<std::unique_ptr<AstNode>> all_ast_nodes;",
        "",
        "    template <typename T>",
        "    T* new_ast() {",
        "        auto p = std::make_unique<T>();",
        "        T* raw = p.get();",
        "        all_ast_nodes.push_back(std::move(p));",
        "        return raw;",
        "    }",
    ]


# ---- Impl emission ----------------------------------------------------------


def emit_ast_impl(plan: AstPlan) -> list[str]:
    out: list[str] = [
        "",
        "/* ====================================================================== */",
        "/* AST builder (v3 auto-AST)                                              */",
        "/* ====================================================================== */",
        "",
    ]
    out.extend(_emit_kind_name_table(plan))
    out.append("")
    out.extend(_emit_unwrap_table(plan))
    out.append("")
    out.extend(_emit_compute_pos())
    out.append("")
    out.extend(_emit_build_walker(plan))
    out.append("")
    out.extend(_emit_collect_list_walker(plan))
    out.append("")
    out.extend(_emit_parse_ast_method())
    return out


def _emit_kind_name_table(plan: AstPlan) -> list[str]:
    out = [
        "namespace {",
        "constexpr const char* kAstKindNames[] = {",
        '    "<none>",',
    ]
    for k in plan.node_kinds:
        out.append(f'    "{k.name}",')
    out.append("};")
    out.append("}  // anonymous namespace")
    out.append("")
    out.append("const char* ast_kind_name(AstKind k) {")
    out.append("    int i = static_cast<int>(k);")
    out.append("    if (i < 0 || i >= static_cast<int>(AstKind::_Count_)) return \"<invalid>\";")
    out.append("    return kAstKindNames[i];")
    out.append("}")
    return out


def _emit_unwrap_table(plan: AstPlan) -> list[str]:
    """Per-production unwrap source-index table + token resolver."""
    max_prod = 0
    table: dict[int, int] = {}
    for a in plan.production_actions:
        idx = a.user_index + 1
        max_prod = max(max_prod, idx)
        if a.kind == "_unwrap":
            table[idx] = a.source_index
    out = [
        "namespace {",
        f"constexpr int kUnwrapSrc[{max_prod + 1}] = {{",
    ]
    for i in range(max_prod + 1):
        out.append(f"    /* {i} */ {table.get(i, -1)},")
    out.append("};")
    out.append("")
    out.append("static const Node* resolve_to_token(const Node* pn) {")
    out.append("    /* Walk through _unwrap chains to the underlying terminal leaf. */")
    out.append("    while (pn && !pn->is_terminal) {")
    out.append("        if (pn->children.empty()) return nullptr;")
    out.append("        if (pn->production < 0 || pn->production >= static_cast<int>(sizeof(kUnwrapSrc) / sizeof(int))) break;")
    out.append("        int src = kUnwrapSrc[pn->production];")
    out.append("        if (src < 0 || src >= static_cast<int>(pn->children.size())) break;")
    out.append("        pn = pn->children[src];")
    out.append("    }")
    out.append("    return (pn && pn->is_terminal) ? pn : nullptr;")
    out.append("}")
    out.append("}  // anonymous namespace")
    return out


def _emit_compute_pos() -> list[str]:
    return [
        "namespace {",
        "void compute_pos(AstPos& pos, const Node* pn) {",
        "    const Node* leftmost = pn;",
        "    while (leftmost && !leftmost->is_terminal && !leftmost->children.empty()) {",
        "        leftmost = leftmost->children.front();",
        "    }",
        "    const Node* rightmost = pn;",
        "    while (rightmost && !rightmost->is_terminal && !rightmost->children.empty()) {",
        "        rightmost = rightmost->children.back();",
        "    }",
        "    if (leftmost && leftmost->is_terminal) {",
        "        pos.start_line = leftmost->line;",
        "        pos.start_column = leftmost->column;",
        "    } else if (pn) {",
        "        pos.start_line = pn->line;",
        "        pos.start_column = pn->column;",
        "    }",
        "    if (rightmost && rightmost->is_terminal) {",
        "        pos.end_line = rightmost->line;",
        "        pos.end_column = rightmost->column + static_cast<int>(rightmost->text.size());",
        "    } else {",
        "        pos.end_line = pos.start_line;",
        "        pos.end_column = pos.start_column;",
        "    }",
        "}",
        "}  // anonymous namespace",
    ]


def _emit_build_walker(plan: AstPlan) -> list[str]:
    out = [
        "static AstNode* build_ast(Parser::Impl& impl, const Node* pn);",
        "static int collect_list(Parser::Impl& impl, const Node* pn, std::vector<AstNode*>& out);",
        "",
        "static AstNode* build_ast(Parser::Impl& impl, const Node* pn) {",
        "    if (!pn) return nullptr;",
        "    if (pn->is_terminal) return nullptr;  /* should not be reached */",
        "    int prod = pn->production;",
        "    switch (prod) {",
        "    case 0: return build_ast(impl, pn->children.empty() ? nullptr : pn->children[0]);",
    ]
    for action in plan.production_actions:
        grammar_idx = action.user_index + 1
        out.extend(_emit_action_case(plan, action, grammar_idx))
    out.append("    default: return nullptr;")
    out.append("    }")
    out.append("}")
    return out


def _emit_action_case(
    plan: AstPlan, action: ProductionAction, grammar_idx: int
) -> list[str]:
    if action.kind in ("_list_init", "_list_extend", "_list_empty"):
        return [f"    case {grammar_idx}: return nullptr; /* list production handled by collect_list */"]
    if action.kind == "_lift":
        if action.source_index < 0:
            return [f"    case {grammar_idx}: return nullptr; /* empty alt */"]
        return [
            f"    case {grammar_idx}:",
            f"        return build_ast(impl, pn->children[{action.source_index}]);",
        ]
    if action.kind == "_unwrap":
        return [
            f"    case {grammar_idx}: /* _unwrap */",
            f"        return build_ast(impl, pn->children[{action.source_index}]);",
        ]
    # Named kind.
    kind = _find_kind(plan, action.kind)
    if kind is None:
        return [f"    case {grammar_idx}: return nullptr;"]
    return _emit_named_case(kind, action, grammar_idx)


def _find_kind(plan: AstPlan, name: str) -> NodeKind | None:
    for k in plan.node_kinds:
        if k.name == name:
            return k
    return None


def _emit_named_case(
    kind: NodeKind, action: ProductionAction, grammar_idx: int
) -> list[str]:
    out = [
        f"    case {grammar_idx}: {{",
        f"        auto* n = impl.new_ast<{kind.name}>();",
        "        compute_pos(n->pos, pn);",
    ]
    schema = {f.name: f for f in kind.fields}
    for fa in action.field_assignments:
        f = schema.get(fa.field_name)
        if f is None:
            continue
        out.extend(_emit_field_assignment(action, fa.rhs_index, fa.field_name, f))
    out.append("        return n;")
    out.append("    }")
    return out


def _emit_field_assignment(
    action: ProductionAction, rhs_idx: int, field_name: str, field: NodeField,
) -> list[str]:
    if field.type_kind == "token":
        return [
            f"        n->{field_name} = resolve_to_token(pn->children[{rhs_idx}]);",
        ]
    if field.type_kind == "list":
        return [
            f"        collect_list(impl, pn->children[{rhs_idx}], n->{field_name});",
        ]
    # type_kind == "node"
    return [
        f"        n->{field_name} = build_ast(impl, pn->children[{rhs_idx}]);",
    ]


def _emit_collect_list_walker(plan: AstPlan) -> list[str]:
    out = [
        "static int collect_list(Parser::Impl& impl, const Node* pn, std::vector<AstNode*>& out) {",
        "    if (!pn) return 0;",
        "    if (pn->is_terminal) return 0;",
        "    if (pn->children.empty()) return 0;  /* empty alt -> empty list */",
        "    int prod = pn->production;",
        "    switch (prod) {",
    ]
    for action in plan.production_actions:
        grammar_idx = action.user_index + 1
        if action.kind == "_list_empty":
            out.append(f"    case {grammar_idx}: return 0; /* _list_empty */")
        elif action.kind == "_unwrap":
            out.append(
                f"    case {grammar_idx}: /* _unwrap -> list */"
            )
            out.append(
                f"        return collect_list(impl, pn->children[{action.source_index}], out);"
            )
        elif action.kind == "_lift":
            if action.source_index < 0:
                out.append(f"    case {grammar_idx}: return 0; /* empty-alt sentinel */")
            else:
                out.append(f"    case {grammar_idx}: /* _lift -> list */")
                out.append(
                    f"        return collect_list(impl, pn->children[{action.source_index}], out);"
                )
        elif action.kind == "_list_init":
            out.append(f"    case {grammar_idx}: /* _list_init */")
            for i in action.element_indices:
                out.append(
                    f"        out.push_back(build_ast(impl, pn->children[{i}]));"
                )
            out.append("        return 0;")
        elif action.kind == "_list_extend":
            elem_idx = action.element_indices[0]
            acc_idx = action.accumulator_index
            out.append(f"    case {grammar_idx}: /* _list_extend */")
            if elem_idx < acc_idx:
                # Right-recursive: element first, then recurse.
                out.append(
                    f"        out.push_back(build_ast(impl, pn->children[{elem_idx}]));"
                )
                out.append(
                    f"        return collect_list(impl, pn->children[{acc_idx}], out);"
                )
            else:
                out.append(
                    f"        if (collect_list(impl, pn->children[{acc_idx}], out) != 0) return -1;"
                )
                out.append(
                    f"        out.push_back(build_ast(impl, pn->children[{elem_idx}]));"
                )
                out.append("        return 0;")
    out.append("    default: return 0;")
    out.append("    }")
    out.append("}")
    return out


def _emit_parse_ast_method() -> list[str]:
    return [
        "bool Parser::parse_ast() {",
        "    if (!parse()) return false;",
        "    ast_root_ = build_ast(*impl_, root_);",
        "    if (!ast_root_) {",
        "        if (error_.empty()) error_ = \"build_ast: failed\";",
        "        return false;",
        "    }",
        "    return true;",
        "}",
    ]
