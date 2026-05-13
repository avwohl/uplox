"""C backend: v3 auto-AST emission.

When a bundle carries a non-empty ``ast`` section, the C emitter
appends:

* a per-kind tagged-union ``uplox_<g>_ast`` with one struct per
  user-declared ``%ast=Name``;
* a ``uplox_<g>_parse_ast()`` entry point that calls the regular
  parser and then walks the resulting parse tree to build the AST;
* helper allocation routines that track AST nodes alongside the
  parse-tree nodes in the existing ``ctx->all_nodes`` machinery,
  so ``uplox_<g>_destroy`` cleans up everything in one place.

The walker dispatches on production index via a switch statement
with one arm per user production. List-rule productions are
handled by a ``collect_list`` helper that descends the parse-tree
spine for a list rule and flattens it into a ``T**`` + ``int``
array pair on the parent AST node.

Memory model: every AST node and every list buffer is allocated
on the heap and tracked by the ctx so the single existing
``destroy`` path frees them. Token fields reuse the parse-tree
leaf node pointers — no copying.
"""

from __future__ import annotations

import re
from typing import Any

from ...spec.ast_plan import AstPlan, NodeField, NodeKind, ProductionAction
from ...tables import ast_from_json


def has_ast(bundle: dict[str, Any]) -> bool:
    """Does this bundle carry a populated ``ast`` section?"""
    section = bundle.get("ast") or {}
    return bool(section.get("node_kinds"))


def plan_from_bundle(bundle: dict[str, Any]) -> AstPlan | None:
    section = bundle.get("ast") or {}
    if not section:
        return None
    return ast_from_json(section)


# ---- Header emission --------------------------------------------------------


def emit_ast_header(ctx, plan: AstPlan) -> list[str]:
    """Lines to insert into the public header after the existing API."""
    g = ctx.lower
    G = ctx.upper
    out: list[str] = [
        "",
        "/* ====================================================================== */",
        "/* AST (v3 auto-AST)                                                      */",
        "/* ====================================================================== */",
        "",
        "/* Per-kind AST node types and a tagged union over all of them.",
        " * Produced by uplox_<g>_parse_ast(). Memory is owned by the ctx",
        " * — uplox_<g>_destroy() frees AST nodes alongside parse-tree nodes. */",
        "",
    ]
    # Kind enum.
    out.append("typedef enum {")
    out.append(f"    {ctx.macro('AST__NONE_')} = 0,")
    for i, kind in enumerate(plan.node_kinds, start=1):
        out.append(f"    {_macro_kind(ctx, kind.name)} = {i},")
    out.append(f"    {ctx.macro('AST__COUNT__')} = {len(plan.node_kinds) + 1}")
    out.append(f"}} uplox_{g}_ast_kind;")
    out.append("")

    # Position span.
    out.append("typedef struct {")
    out.append("    int start_line;")
    out.append("    int start_column;")
    out.append("    int end_line;")
    out.append("    int end_column;")
    out.append(f"}} uplox_{g}_ast_pos;")
    out.append("")

    # Forward declare the union root.
    out.append(f"typedef struct uplox_{g}_ast uplox_{g}_ast;")
    out.append("")

    # Per-kind structs.
    for kind in plan.node_kinds:
        out.extend(_emit_kind_struct(ctx, kind))
        out.append("")

    # Tagged union.
    out.append(f"struct uplox_{g}_ast {{")
    out.append(f"    int kind;                  /* uplox_{g}_ast_kind */")
    out.append(f"    uplox_{g}_ast_pos pos;")
    out.append("    union {")
    for kind in plan.node_kinds:
        out.append(
            f"        struct uplox_{g}_ast_{kind.name} {_struct_field_name(kind.name)};"
        )
    out.append("    } u;")
    out.append("};")
    out.append("")

    # Public API.
    out.append("/* Parse and build a typed AST. Returns 0 on success; -1 on error")
    out.append(" * (with uplox_<g>_error giving the message). The output AST is")
    out.append(" * owned by the ctx and freed by uplox_<g>_destroy. */")
    out.append(
        f"int uplox_{g}_parse_ast(uplox_{g}_ctx *ctx, uplox_{g}_ast **out_root);"
    )
    out.append("")
    out.append(
        f"const char *uplox_{g}_ast_kind_name(int kind);"
    )

    return out


def _emit_kind_struct(ctx, kind: NodeKind) -> list[str]:
    g = ctx.lower
    out = [
        f"/* {kind.name}. Access via uplox_<g>_ast.u.{_struct_field_name(kind.name)}. */",
        f"struct uplox_{g}_ast_{kind.name} {{",
    ]
    if not kind.fields:
        out.append("    /* No fields; this node carries only its kind and position. */")
        out.append("    int _unused;")
    else:
        for f in kind.fields:
            out.append(f"    {_field_decl(ctx, f)};")
    out.append("};")
    return out


def _field_decl(ctx, f: NodeField) -> str:
    """Render one ``type name`` declaration."""
    g = ctx.lower
    if f.type_kind == "token":
        return f"uplox_{g}_node *{f.name}"
    if f.type_kind == "list":
        # `name` is a pointer-to-pointer (array of AST*) and `name_count`
        # is the int length. Convention is to suffix with `_count`.
        return f"uplox_{g}_ast **{f.name}; int {f.name}_count"
    # type_kind == "node"
    return f"uplox_{g}_ast *{f.name}"


# ---- Implementation emission ------------------------------------------------


def emit_ast_ctx_fields(ctx) -> list[str]:
    """Extra ctx-struct fields for AST node tracking."""
    g = ctx.lower
    return [
        "",
        "    /* AST nodes and list buffers; freed by destroy. */",
        f"    uplox_{g}_ast **all_ast_nodes;",
        "    int    num_ast_nodes;",
        "    int    cap_ast_nodes;",
        f"    uplox_{g}_ast ***all_ast_lists;",
        "    int    num_ast_lists;",
        "    int    cap_ast_lists;",
    ]


def emit_ast_destroy_fragment(ctx) -> list[str]:
    """Lines to insert into destroy() before freeing all_nodes."""
    g = ctx.lower
    return [
        "    for (int i = 0; i < c->num_ast_nodes; ++i) {",
        "        free(c->all_ast_nodes[i]);",
        "    }",
        "    free(c->all_ast_nodes);",
        "    for (int i = 0; i < c->num_ast_lists; ++i) {",
        "        free(c->all_ast_lists[i]);",
        "    }",
        "    free(c->all_ast_lists);",
    ]


def emit_ast_impl(ctx, plan: AstPlan) -> list[str]:
    """Lines appended to the impl file after the existing parser."""
    g = ctx.lower
    out: list[str] = [
        "",
        "/* ====================================================================== */",
        "/* AST builder (v3 auto-AST)                                              */",
        "/* ====================================================================== */",
        "",
    ]
    out.extend(_emit_ast_kind_names(ctx, plan))
    out.append("")
    out.extend(_emit_unwrap_source_table(ctx, plan))
    out.append("")
    out.extend(_emit_ast_helpers(ctx))
    out.append("")
    out.extend(_emit_compute_pos(ctx))
    out.append("")
    out.extend(_emit_build_ast(ctx, plan))
    out.append("")
    out.extend(_emit_collect_list(ctx, plan))
    out.append("")
    out.extend(_emit_parse_ast_entry(ctx))
    return out


def _emit_ast_kind_names(ctx, plan: AstPlan) -> list[str]:
    g = ctx.lower
    out = [f"static const char *uplox_{g}__ast_kind_names[] = {{"]
    out.append('    "<none>",')
    for kind in plan.node_kinds:
        out.append(f'    "{kind.name}",')
    out.append("};")
    out.append("")
    out.append(f"const char *uplox_{g}_ast_kind_name(int k) {{")
    out.append(f"    if (k < 0 || k >= {ctx.macro('AST__COUNT__')}) return \"<invalid>\";")
    out.append(f"    return uplox_{g}__ast_kind_names[k];")
    out.append("}")
    return out


def _emit_unwrap_source_table(ctx, plan: AstPlan) -> list[str]:
    """Per-production unwrap source-index table.

    Used by the token resolver to walk through ``%ast=_unwrap`` chains
    when a field is typed "token" but the parse-tree child is a
    wrapper non-terminal that unwraps to a terminal. Indexed by
    grammar production index; entry is the source_index for _unwrap
    productions, -1 otherwise.
    """
    g = ctx.lower
    # Find the highest production index to size the array.
    max_prod = 0
    table: dict[int, int] = {}
    for a in plan.production_actions:
        prod_idx = a.user_index + 1
        max_prod = max(max_prod, prod_idx)
        if a.kind == "_unwrap":
            table[prod_idx] = a.source_index
    out = [f"static const int uplox_{g}__unwrap_src[{max_prod + 1}] = {{"]
    for i in range(max_prod + 1):
        out.append(f"    [{i}] = {table.get(i, -1)},")
    out.append("};")
    out.append("")
    # Token resolver: walks _unwrap chains to find the leaf token.
    out.extend([
        f"static uplox_{g}_node *uplox_{g}__resolve_to_token(uplox_{g}_node *pn) {{",
        "    /* Walk through %ast=_unwrap chains until we hit a terminal",
        "       leaf or a wrapper with no children (empty alt). */",
        "    while (pn && !pn->is_terminal) {",
        "        if (pn->num_children == 0) return NULL;",
        f"        if (pn->production < 0 || pn->production >= (int)(sizeof(uplox_{g}__unwrap_src) / sizeof(int))) break;",
        f"        int src = uplox_{g}__unwrap_src[pn->production];",
        "        if (src < 0 || src >= pn->num_children) break;",
        "        pn = pn->children[src];",
        "    }",
        "    return (pn && pn->is_terminal) ? pn : NULL;",
        "}",
    ])
    return out


def _emit_ast_helpers(ctx) -> list[str]:
    """Tracked allocators for AST nodes and list buffers."""
    g = ctx.lower
    return [
        f"static uplox_{g}_ast *uplox_{g}__ast_new(uplox_{g}_ctx *c, int kind) {{",
        f"    uplox_{g}_ast *n = (uplox_{g}_ast *)calloc(1, sizeof(uplox_{g}_ast));",
        "    if (!n) return NULL;",
        "    n->kind = kind;",
        "    if (c->num_ast_nodes >= c->cap_ast_nodes) {",
        "        int nc = c->cap_ast_nodes ? c->cap_ast_nodes * 2 : 64;",
        f"        uplox_{g}_ast **na = (uplox_{g}_ast **)realloc(c->all_ast_nodes, sizeof(uplox_{g}_ast *) * (size_t)nc);",
        "        if (!na) { free(n); return NULL; }",
        "        c->all_ast_nodes = na;",
        "        c->cap_ast_nodes = nc;",
        "    }",
        "    c->all_ast_nodes[c->num_ast_nodes++] = n;",
        "    return n;",
        "}",
        "",
        f"static int uplox_{g}__ast_track_list(uplox_{g}_ctx *c, uplox_{g}_ast **buf) {{",
        "    if (!buf) return 0;",
        "    if (c->num_ast_lists >= c->cap_ast_lists) {",
        "        int nc = c->cap_ast_lists ? c->cap_ast_lists * 2 : 16;",
        f"        uplox_{g}_ast ***na = (uplox_{g}_ast ***)realloc(c->all_ast_lists, sizeof(uplox_{g}_ast **) * (size_t)nc);",
        "        if (!na) return -1;",
        "        c->all_ast_lists = na;",
        "        c->cap_ast_lists = nc;",
        "    }",
        "    c->all_ast_lists[c->num_ast_lists++] = buf;",
        "    return 0;",
        "}",
        "",
        f"static int uplox_{g}__ast_list_push(uplox_{g}_ctx *c, uplox_{g}_ast ***buf, int *len, int *cap, uplox_{g}_ast *item) {{",
        "    if (*len >= *cap) {",
        "        int nc = *cap ? *cap * 2 : 8;",
        f"        uplox_{g}_ast **nb = (uplox_{g}_ast **)realloc(*buf, sizeof(uplox_{g}_ast *) * (size_t)nc);",
        "        if (!nb) return -1;",
        "        *buf = nb;",
        "        *cap = nc;",
        "    }",
        "    (*buf)[(*len)++] = item;",
        "    return 0;",
        "}",
    ]


def _emit_compute_pos(ctx) -> list[str]:
    """Walk parse-tree leaves to compute start/end position spans."""
    g = ctx.lower
    return [
        f"static void uplox_{g}__compute_pos(uplox_{g}_ast_pos *pos, uplox_{g}_node *pn) {{",
        "    /* Find leftmost-leaf start and rightmost-leaf end. The walk",
        "       descends to the first/last terminal child recursively. */",
        f"    uplox_{g}_node *leftmost = pn;",
        f"    while (leftmost && !leftmost->is_terminal && leftmost->num_children > 0) {{",
        "        leftmost = leftmost->children[0];",
        "    }",
        f"    uplox_{g}_node *rightmost = pn;",
        f"    while (rightmost && !rightmost->is_terminal && rightmost->num_children > 0) {{",
        "        rightmost = rightmost->children[rightmost->num_children - 1];",
        "    }",
        "    if (leftmost && leftmost->is_terminal) {",
        "        pos->start_line = leftmost->line;",
        "        pos->start_column = leftmost->column;",
        "    } else {",
        "        pos->start_line = pn->line;",
        "        pos->start_column = pn->column;",
        "    }",
        "    if (rightmost && rightmost->is_terminal) {",
        "        pos->end_line = rightmost->line;",
        "        pos->end_column = rightmost->column + rightmost->text_len;",
        "    } else {",
        "        pos->end_line = pos->start_line;",
        "        pos->end_column = pos->start_column;",
        "    }",
        "}",
    ]


# ---- AST construction dispatch ---------------------------------------------


def _emit_build_ast(ctx, plan: AstPlan) -> list[str]:
    """The recursive walker: parse-tree node -> AST node."""
    g = ctx.lower
    out = [
        # Forward declarations for the two mutually-recursive walkers.
        f"static uplox_{g}_ast *uplox_{g}__build_ast(uplox_{g}_ctx *c, uplox_{g}_node *pn);",
        f"static int uplox_{g}__collect_list(uplox_{g}_ctx *c, uplox_{g}_node *pn, uplox_{g}_ast ***buf, int *len, int *cap);",
        "",
        f"static uplox_{g}_ast *uplox_{g}__build_ast(uplox_{g}_ctx *c, uplox_{g}_node *pn) {{",
        "    if (!pn) return NULL;",
        "    if (pn->is_terminal) {",
        f"        uplox_{g}__set_error(c, \"internal: build_ast called on terminal\");",
        "        return NULL;",
        "    }",
        "    int prod = pn->production;",
        "    switch (prod) {",
        # Augmented start: pass through to rhs[0].
        f"    case 0: return uplox_{g}__build_ast(c, pn->children[0]);",
    ]
    # One case per user production. Grammar production index = user_index + 1.
    list_rule_user_indices = _collect_list_rule_user_indices(plan)
    for action in plan.production_actions:
        grammar_idx = action.user_index + 1
        out.extend(_emit_action_case(ctx, plan, action, grammar_idx, list_rule_user_indices))
    out.append("    default:")
    out.append(f"        uplox_{g}__set_error(c, \"internal: unknown production in build_ast\");")
    out.append("        return NULL;")
    out.append("    }")
    out.append("}")
    return out


def _collect_list_rule_user_indices(plan: AstPlan) -> set[int]:
    """User-indices of productions belonging to list rules.

    Used to avoid emitting top-level dispatch cases for list-rule
    productions — those are handled in collect_list. The walker
    should never reach a list-rule production directly via
    build_ast; lists are always traversed by the collector starting
    from the parent's @field.
    """
    return {
        a.user_index
        for a in plan.production_actions
        if a.kind in ("_list_init", "_list_extend", "_list_empty")
    }


def _emit_action_case(
    ctx, plan: AstPlan, action: ProductionAction, grammar_idx: int,
    list_rule_user_indices: set[int],
) -> list[str]:
    """Emit one ``case <N>:`` arm of the build_ast switch."""
    g = ctx.lower
    if action.kind in ("_list_init", "_list_extend", "_list_empty"):
        # Dead arm: list productions are reached through collect_list,
        # never as a top-level build_ast target. Emit a guard anyway.
        return [
            f"    case {grammar_idx}: /* list-rule production, dispatched via collect_list */",
            f"        uplox_{g}__set_error(c, \"internal: list production reached via build_ast\");",
            "        return NULL;",
        ]
    if action.kind == "_lift":
        if action.source_index < 0:
            return [f"    case {grammar_idx}: return NULL; /* empty alt */"]
        return [
            f"    case {grammar_idx}:",
            f"        return uplox_{g}__build_ast(c, pn->children[{action.source_index}]);",
        ]
    if action.kind == "_unwrap":
        # Like _lift; spec-level distinction doesn't affect runtime.
        return [
            f"    case {grammar_idx}: /* _unwrap */",
            f"        return uplox_{g}__build_ast(c, pn->children[{action.source_index}]);",
        ]
    # Named kind construction.
    kind = _find_kind(plan, action.kind)
    if kind is None:
        return [
            f"    case {grammar_idx}:",
            f"        uplox_{g}__set_error(c, \"internal: kind {action.kind} not in plan\");",
            "        return NULL;",
        ]
    return _emit_named_kind_case(ctx, kind, action, grammar_idx)


def _find_kind(plan: AstPlan, name: str) -> NodeKind | None:
    for k in plan.node_kinds:
        if k.name == name:
            return k
    return None


def _emit_named_kind_case(
    ctx, kind: NodeKind, action: ProductionAction, grammar_idx: int
) -> list[str]:
    """Emit a switch arm that constructs an AST node of a user-named kind."""
    g = ctx.lower
    field_name = _struct_field_name(kind.name)
    out = [
        f"    case {grammar_idx}: {{",
        f"        uplox_{g}_ast *n = uplox_{g}__ast_new(c, {_macro_kind(ctx, kind.name)});",
        "        if (!n) { return NULL; }",
        f"        uplox_{g}__compute_pos(&n->pos, pn);",
    ]
    # Map field name -> AST field schema (for type lookups).
    field_schema = {f.name: f for f in kind.fields}
    for fa in action.field_assignments:
        f = field_schema.get(fa.field_name)
        if f is None:
            # Should be impossible — plan compiler validates this.
            out.append(
                f"        /* skipped unknown field {fa.field_name!r} */"
            )
            continue
        out.extend(
            _emit_field_assignment(
                ctx, action, fa.rhs_index, fa.field_name, f, field_name
            )
        )
    out.append("        return n;")
    out.append("    }")
    return out


def _emit_field_assignment(
    ctx, action: ProductionAction, rhs_idx: int, field_name: str,
    field: NodeField, struct_name: str,
) -> list[str]:
    g = ctx.lower
    accessor = f"n->u.{struct_name}.{field_name}"
    if field.type_kind == "token":
        # Token field. The parse-tree child might be a terminal leaf
        # directly, or a wrapper non-terminal that unwraps to one
        # (the plan compiler's _unwrap look-through promoted the
        # field's type to "token"). The resolver handles both cases
        # and returns NULL when the wrapper's empty alternative fired.
        return [
            f"        {accessor} = uplox_{g}__resolve_to_token(pn->children[{rhs_idx}]);",
        ]
    if field.type_kind == "list":
        # List field: collect from the parse-tree child (which is the
        # root of the list rule).
        cnt_accessor = f"n->u.{struct_name}.{field_name}_count"
        return [
            "        {",
            f"            uplox_{g}_ast **buf = NULL; int len = 0, cap = 0;",
            f"            if (uplox_{g}__collect_list(c, pn->children[{rhs_idx}], &buf, &len, &cap) != 0) {{ return NULL; }}",
            f"            if (uplox_{g}__ast_track_list(c, buf) != 0) {{ free(buf); return NULL; }}",
            f"            {accessor} = buf;",
            f"            {cnt_accessor} = len;",
            "        }",
        ]
    # type_kind == "node"
    # Could be an Optional (parse-tree child might be NULL when the
    # rule's empty-alt fired). build_ast returns NULL for the empty-alt
    # _lift sentinel, which is exactly what we want for Optional fields.
    return [
        f"        {accessor} = uplox_{g}__build_ast(c, pn->children[{rhs_idx}]);",
    ]


# ---- List collection --------------------------------------------------------


def _emit_collect_list(ctx, plan: AstPlan) -> list[str]:
    """Walk a list-rule parse-tree node, flattening into ``buf + len``.

    Also descends through ``_unwrap`` and empty-alt productions so a
    list field whose source goes through a wrapper rule (e.g.
    ``SubBody.body`` via ``<sub_body>`` which unwraps to
    ``<sub_body_items>``) resolves correctly.
    """
    g = ctx.lower
    out = [
        f"static int uplox_{g}__collect_list(uplox_{g}_ctx *c, uplox_{g}_node *pn, uplox_{g}_ast ***buf, int *len, int *cap) {{",
        "    if (!pn) return 0;",
        "    if (pn->is_terminal) return 0;",
        "    /* Empty alt (e.g., wrapper rule with empty alternative): yield",
        "       no list items. The caller's len/cap stay as-is. */",
        "    if (pn->num_children == 0) return 0;",
        "    int prod = pn->production;",
        "    switch (prod) {",
    ]
    for action in plan.production_actions:
        grammar_idx = action.user_index + 1
        if action.kind == "_list_empty":
            out.append(f"    case {grammar_idx}: /* _list_empty */ return 0;")
        elif action.kind == "_unwrap":
            # An _unwrap rule sitting between the parent and the list
            # rule. Descend into the unwrapped child.
            out.append(
                f"    case {grammar_idx}: /* _unwrap -> list */"
            )
            out.append(
                f"        return uplox_{g}__collect_list(c, pn->children[{action.source_index}], buf, len, cap);"
            )
        elif action.kind == "_lift":
            # _lift could be a `?`-lift in front of the list rule.
            # Treat negative source (empty-alt sentinel) as no-op; any
            # other source recurses.
            if action.source_index < 0:
                out.append(f"    case {grammar_idx}: /* empty-alt sentinel */ return 0;")
            else:
                out.append(
                    f"    case {grammar_idx}: /* _lift -> list */"
                )
                out.append(
                    f"        return uplox_{g}__collect_list(c, pn->children[{action.source_index}], buf, len, cap);"
                )
        elif action.kind == "_list_init":
            arm = [f"    case {grammar_idx}: /* _list_init */"]
            for i in action.element_indices:
                arm.append(
                    f"        if (uplox_{g}__ast_list_push(c, buf, len, cap, "
                    f"uplox_{g}__build_ast(c, pn->children[{i}])) != 0) return -1;"
                )
            arm.append("        return 0;")
            out.extend(arm)
        elif action.kind == "_list_extend":
            elem_idx = action.element_indices[0]
            acc_idx = action.accumulator_index
            arm = [f"    case {grammar_idx}: /* _list_extend */"]
            if elem_idx < acc_idx:
                # Right-recursive: element first, then descend
                # accumulator (the rest of the list).
                arm.append(
                    f"        if (uplox_{g}__ast_list_push(c, buf, len, cap, "
                    f"uplox_{g}__build_ast(c, pn->children[{elem_idx}])) != 0) return -1;"
                )
                arm.append(
                    f"        return uplox_{g}__collect_list(c, pn->children[{acc_idx}], buf, len, cap);"
                )
            else:
                # Left-recursive: descend accumulator first, then push.
                arm.append(
                    f"        if (uplox_{g}__collect_list(c, pn->children[{acc_idx}], buf, len, cap) != 0) return -1;"
                )
                arm.append(
                    f"        if (uplox_{g}__ast_list_push(c, buf, len, cap, "
                    f"uplox_{g}__build_ast(c, pn->children[{elem_idx}])) != 0) return -1;"
                )
                arm.append("        return 0;")
            out.extend(arm)
    out.append("    default:")
    out.append(f"        uplox_{g}__set_error(c, \"internal: non-list production in collect_list\");")
    out.append("        return -1;")
    out.append("    }")
    out.append("}")
    return out


# ---- Public entry -----------------------------------------------------------


def _emit_parse_ast_entry(ctx) -> list[str]:
    g = ctx.lower
    return [
        f"int uplox_{g}_parse_ast(uplox_{g}_ctx *c, uplox_{g}_ast **out) {{",
        f"    uplox_{g}_node *root;",
        f"    int rc = uplox_{g}_parse(c, &root);",
        "    if (rc != 0) return rc;",
        f"    uplox_{g}_ast *ast = uplox_{g}__build_ast(c, root);",
        "    if (!ast) {",
        "        if (!c->has_error) {",
        f"            uplox_{g}__set_error(c, \"internal: build_ast returned NULL\");",
        "        }",
        "        return -1;",
        "    }",
        "    *out = ast;",
        "    return 0;",
        "}",
    ]


# ---- Helpers ----------------------------------------------------------------


def _struct_field_name(kind_name: str) -> str:
    """Member name on the union for kind ``Name`` (e.g., ``binop``)."""
    return re.sub(r"(?<!^)(?=[A-Z])", "_", kind_name).lower()


def _macro_kind(ctx, kind_name: str) -> str:
    """Macro name for an AST kind: ``UPLOX_<G>_AST_<KIND_NAME_UPPER>``."""
    sanitised = re.sub(r"(?<!^)(?=[A-Z])", "_", kind_name).upper()
    return ctx.macro(f"AST_{sanitised}")
