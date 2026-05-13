"""Compile a v3 :class:`GrammarIR` into an :class:`AstPlan`.

The AST plan is the concrete schema and per-production action table that
downstream backends consume to emit typed AST node types and a default
builder. It is the bridge between the surface annotation set parsed by
:mod:`uplox.spec.reader` and whatever each backend wants to emit.

Pure functional transformation of the IR:

* Inputs: :class:`uplox.spec.ir.GrammarIR` (already annotated).
* Output: :class:`AstPlan` — drop-token set, ordered node-kind schema,
  per-production action list — or ``None`` for grammars with no AST
  annotations (the back-compat path that keeps the parse tree as the
  primary output).

Semantic rules enforced here:

* A grammar is in **AST mode** if any of the four annotation forms
  (``%ast_drop``, ``?``, ``%ast=…``, ``@field``) appears anywhere.
* In AST mode, every production of a **reachable** rule must "do
  something AST-y": carry ``%ast=Name``, be a list-rule production,
  be ``?``-lift-eligible (exactly one non-drop child after filtering),
  or be the empty alt of an optional rule.
* ``%ast=_unwrap`` requires exactly one ``@field``-annotated RHS
  position (post-drop) — the producing rule contributes that child to
  the parent's named slot without wrapping.
* Two productions sharing an ``%ast=Name`` must carry the same field
  names and field types — otherwise the produced node's shape isn't
  stable across reductions.
* A list rule (``%ast=list element=<X>``) is detected per-production
  by counting self-references vs element references; the non-empty
  shape must be either ``<element>`` alone or ``<self> … <element>``
  plus drops/separators.

Auto-derivations:

* A ``@field`` whose source non-terminal has an empty alternative is
  ``optional`` in the schema — **unless** the source is a list rule,
  in which case the field is ``list`` and defaults to ``[]``.

See ``docs/proposals/auto_ast.md`` for the full design.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .ir import GrammarIR, Production, Rule, Symbol


# Reserved %ast= kind names the user can spell explicitly. Currently
# only ``_unwrap``; ``_lift`` / ``_list_*`` are runtime-internal action
# tags and never appear in source.
RESERVED_AST_KINDS = frozenset({"_unwrap"})

# Runtime action kinds — dispatch tags on :class:`ProductionAction`.
# Never user-visible AST node types.
RUNTIME_KINDS = frozenset(
    {"_lift", "_unwrap", "_list_init", "_list_extend", "_list_empty"}
)


class AstPlanError(ValueError):
    """Semantic violation in the v3 AST annotation set."""


# ---- Plan data types --------------------------------------------------------


@dataclass(frozen=True)
class FieldAssignment:
    """Maps an RHS position to a named field on the produced AST node.

    ``rhs_index`` is the position in the *original* (pre-drop) RHS. The
    runtime walks the parser's value stack with the full RHS layout, so
    raw indices are what matter at build time.
    """

    rhs_index: int
    field_name: str


@dataclass(frozen=True)
class NodeField:
    """One field on an AST node kind's schema.

    ``type_kind`` is one of ``"node"``, ``"token"``, or ``"list"``.

    For ``"list"``, ``list_element_kind`` names the source non-terminal
    of the element; the codegen layer resolves the union of node kinds
    that non-terminal can produce.

    ``optional`` is True when the field's source non-terminal has an
    empty alternative, *unless* the field is a list (list fields
    default to ``[]`` per OQ1, never None).
    """

    name: str
    type_kind: str
    optional: bool = False
    list_element_kind: Optional[str] = None


@dataclass(frozen=True)
class NodeKind:
    """Schema for one AST node kind."""

    name: str
    fields: tuple[NodeField, ...]


@dataclass(frozen=True)
class ProductionAction:
    """Runtime action for one user production (IR-order, 0-based).

    ``kind`` is either a user-supplied node-kind name or one of the
    reserved tags in :data:`RUNTIME_KINDS`.

    Field semantics per kind:

    * named kind — populate ``field_assignments`` from the
      ``@field``-annotated RHS positions.
    * ``"_lift"`` — pass ``rhs[source_index]`` through.
    * ``"_unwrap"`` — like ``_lift``; the spec-level distinction
      drives auto-optional inference, not runtime behaviour.
    * ``"_list_init"`` — start a list with
      ``rhs[element_index]`` as its first element.
    * ``"_list_extend"`` — append ``rhs[element_index]`` to
      ``rhs[accumulator_index]``.
    * ``"_list_empty"`` — yield an empty list.
    """

    user_index: int
    kind: str
    field_assignments: tuple[FieldAssignment, ...] = ()
    source_index: int = -1
    accumulator_index: int = -1
    element_index: int = -1


@dataclass(frozen=True)
class AstPlan:
    """Concrete AST schema and builder plan for a grammar."""

    drop_tokens: frozenset[str]
    node_kinds: tuple[NodeKind, ...]
    production_actions: tuple[ProductionAction, ...]


# ---- Compilation context ----------------------------------------------------


@dataclass
class _Context:
    """Threaded through the per-production compiler.

    Carries the pre-resolved data the action classifier needs without
    re-walking the IR for every symbol.
    """

    ir: GrammarIR
    drop_tokens: frozenset[str]
    rules_by_name: dict[str, Rule]
    has_empty_alt: dict[str, bool]
    list_rules: dict[str, str]  # rule.name -> element non-terminal name
    reachable: set[str]
    # Map declared-literal text -> token name, so we can ask whether a
    # `'+'` literal in an RHS is one of the drop tokens.
    literal_to_token: dict[str, str]
    terminal_names: set[str]
    keyword_aliases: dict[str, str]

    def symbol_is_dropped(self, sym: Symbol) -> bool:
        if sym.kind == "nonterm":
            return False
        resolved = self.resolve_terminal_name(sym)
        return resolved is not None and resolved in self.drop_tokens

    def resolve_terminal_name(self, sym: Symbol) -> Optional[str]:
        """Resolve a term/literal Symbol to its declared token name.

        Returns ``None`` for non-terminals or unresolved references —
        the latter would have been flagged by ``compile_grammar``; the
        plan compiler treats them as non-droppable to defer the
        diagnostic to the grammar compiler.
        """
        if sym.kind == "nonterm":
            return None
        if sym.kind == "literal":
            return self.literal_to_token.get(sym.name)
        # kind == "term"
        if sym.name in self.terminal_names:
            return sym.name
        return self.keyword_aliases.get(sym.name)


# ---- Top-level entry --------------------------------------------------------


def compile_ast_plan(ir: GrammarIR) -> Optional[AstPlan]:
    """Compile the IR's v3 AST annotations into a plan, or return None.

    Returns ``None`` when the grammar has no AST annotations — the
    back-compat path. Raises :class:`AstPlanError` on any semantic
    violation.
    """
    if not _grammar_has_ast_annotations(ir):
        return None

    ctx = _build_context(ir)
    actions: list[ProductionAction] = []
    # Per-kind field tuples collected during the pass; reconciled into
    # the final NodeKind schema in :func:`_build_node_kinds` after.
    fields_by_kind: dict[str, list[tuple[Symbol, str, int]]] = {}
    kind_order: list[str] = []

    user_idx = 0
    for rule in ir.rules:
        for prod in rule.productions:
            action = _compile_production(rule, prod, user_idx, ctx)
            actions.append(action)

            if action.kind not in RUNTIME_KINDS:
                # Track field source-symbols for the schema pass.
                if action.kind not in fields_by_kind:
                    fields_by_kind[action.kind] = []
                    kind_order.append(action.kind)
                for fa in action.field_assignments:
                    src_sym = prod.rhs[fa.rhs_index]
                    fields_by_kind[action.kind].append(
                        (src_sym, fa.field_name, user_idx)
                    )
            user_idx += 1

    node_kinds = _build_node_kinds(kind_order, fields_by_kind, ctx)

    return AstPlan(
        drop_tokens=ctx.drop_tokens,
        node_kinds=tuple(node_kinds),
        production_actions=tuple(actions),
    )


# ---- Detection / context construction ---------------------------------------


def _grammar_has_ast_annotations(ir: GrammarIR) -> bool:
    if ir.ast_drop_tokens:
        return True
    for rule in ir.rules:
        if rule.ast_lift or rule.ast_list_element is not None:
            return True
        for prod in rule.productions:
            if prod.ast_kind is not None:
                return True
            for sym in prod.rhs:
                if sym.field_name is not None:
                    return True
    return False


def _build_context(ir: GrammarIR) -> _Context:
    terminal_names = {t.name for t in ir.tokens}
    literal_to_token = {t.literal: t.name for t in ir.tokens if t.literal is not None}
    drop_tokens = _resolve_drop_tokens(ir, terminal_names)
    rules_by_name = {r.name: r for r in ir.rules}
    has_empty_alt = {
        r.name: any(len(p.rhs) == 0 for p in r.productions) for r in ir.rules
    }
    list_rules = {
        r.name: r.ast_list_element
        for r in ir.rules
        if r.ast_list_element is not None
    }
    # Sanity-check list rules: element non-terminal must be a declared
    # rule. We don't require it to itself be a list rule; the element
    # is whatever the non-terminal produces.
    for lhs, elem in list_rules.items():
        if elem not in rules_by_name:
            raise AstPlanError(
                f"rule <{lhs}> %ast=list element=<{elem}>: "
                f"element <{elem}> is not a declared rule"
            )
    return _Context(
        ir=ir,
        drop_tokens=drop_tokens,
        rules_by_name=rules_by_name,
        has_empty_alt=has_empty_alt,
        list_rules=list_rules,
        reachable=_reachable_rules(ir),
        literal_to_token=literal_to_token,
        terminal_names=terminal_names,
        keyword_aliases=ir.keyword_aliases,
    )


def _resolve_drop_tokens(ir: GrammarIR, terminal_names: set[str]) -> frozenset[str]:
    """Resolve every %ast_drop entry to a declared terminal name."""
    resolved: set[str] = set()
    for sym in sorted(ir.ast_drop_tokens):
        if sym in terminal_names:
            resolved.add(sym)
            continue
        alias = ir.keyword_aliases.get(sym)
        if alias is not None and alias in terminal_names:
            resolved.add(alias)
            continue
        raise AstPlanError(f"%ast_drop entry {sym!r} is not a declared terminal")
    return frozenset(resolved)


def _reachable_rules(ir: GrammarIR) -> set[str]:
    """BFS from the start symbol through non-terminal references."""
    rules_by_name = {r.name: r for r in ir.rules}
    if ir.start_symbol is None or ir.start_symbol not in rules_by_name:
        return set()
    seen: set[str] = {ir.start_symbol}
    work = [ir.start_symbol]
    while work:
        cur = work.pop()
        for prod in rules_by_name[cur].productions:
            for sym in prod.rhs:
                if sym.kind == "nonterm" and sym.name in rules_by_name and sym.name not in seen:
                    seen.add(sym.name)
                    work.append(sym.name)
    return seen


# ---- Per-production action compilation --------------------------------------


def _compile_production(
    rule: Rule, prod: Production, user_index: int, ctx: _Context
) -> ProductionAction:
    """Decide what this single production does at AST build time."""

    # List-shaped rules dispatch first — the rule's role is fixed by
    # the LHS annotation, alternatives play different list-machinery
    # roles based on shape.
    if rule.name in ctx.list_rules:
        return _compile_list_production(rule, prod, user_index, ctx)

    # Compute @-tagged RHS positions and surviving (non-drop) positions.
    field_positions: list[tuple[int, str]] = [
        (i, s.field_name) for i, s in enumerate(prod.rhs) if s.field_name is not None
    ]
    surviving = [i for i, s in enumerate(prod.rhs) if not ctx.symbol_is_dropped(s)]

    # Empty alternative: only valid when its rule has at least one
    # other alternative that explains why empty exists (auto-optional
    # source non-terminal). We allow empty alts unconditionally and
    # let the parent's @field auto-optional inference do the work;
    # the action itself is a no-op return value (we model it as a
    # named "None" via the parent's optional schema). The runtime
    # produces None for the empty alt by reading "no source_index".
    if not prod.rhs and prod.ast_kind is None:
        # Generic "empty alt produces None" action — represented as
        # _lift with source_index=-1, which the runtime interprets as
        # "no value." For empty alts of list rules we'd have taken the
        # _list_empty path above.
        return ProductionAction(user_index=user_index, kind="_lift", source_index=-1)

    # %ast=_unwrap: exactly one @field-annotated RHS position
    # (post-drop). The runtime threads that child up; the schema-level
    # auto-optional inference at the parent's @field slot handles
    # missing-vs-present.
    if prod.ast_kind == "_unwrap":
        # All @-tagged positions must be non-drop. Drop tokens never
        # carry information, so @-tagging them is a typo.
        for i, name in field_positions:
            if ctx.symbol_is_dropped(prod.rhs[i]):
                raise AstPlanError(
                    f"production at {prod.position} marked %ast=_unwrap has an "
                    f"@{name} on a dropped token; remove either the @{name} "
                    f"or the %ast_drop"
                )
        if len(field_positions) != 1:
            raise AstPlanError(
                f"production at {prod.position} marked %ast=_unwrap must have "
                f"exactly one @field-annotated RHS position; "
                f"found {len(field_positions)}"
            )
        idx, _ = field_positions[0]
        return ProductionAction(
            user_index=user_index, kind="_unwrap", source_index=idx
        )

    # Explicit named %ast=Name (other than _unwrap): produce a node.
    if prod.ast_kind is not None:
        _validate_named_kind(prod, field_positions, ctx)
        return ProductionAction(
            user_index=user_index,
            kind=prod.ast_kind,
            field_assignments=tuple(
                FieldAssignment(rhs_index=i, field_name=n) for i, n in field_positions
            ),
        )

    # No %ast=, rule has ?-lift. Exactly one surviving child lifts.
    if rule.ast_lift:
        if len(surviving) == 1:
            return ProductionAction(
                user_index=user_index, kind="_lift", source_index=surviving[0]
            )
        # `?`-lift cannot apply: either there are zero surviving
        # children (would need an empty alt — handled above) or
        # multiple. Surface a precise error.
        if rule.name in ctx.reachable:
            raise AstPlanError(
                f"production at {prod.position} of rule <{rule.name}>: "
                f"rule is marked ? for single-child lift but this "
                f"alternative has {len(surviving)} surviving children "
                f"(after %ast_drop). Add %ast=Name or restructure."
            )
        # Unreachable rule — accept silently with a no-op action.
        return ProductionAction(user_index=user_index, kind="_lift", source_index=-1)

    # No %ast=, no `?`. In AST mode this is an error for reachable
    # rules — the production has no construction action.
    if rule.name in ctx.reachable:
        raise AstPlanError(
            f"production at {prod.position} of rule <{rule.name}>: "
            f"grammar is in AST mode but this production has no %ast= "
            f"and rule <{rule.name}> has no `?` or list annotation. "
            f"Either add %ast=Name to this production or mark <{rule.name}>? "
            f"for single-child lift."
        )
    # Unreachable: silently accept as a no-op for index alignment.
    return ProductionAction(user_index=user_index, kind="_lift", source_index=-1)


def _validate_named_kind(
    prod: Production, field_positions: list[tuple[int, str]], ctx: _Context
) -> None:
    """Sanity checks for a production with %ast=Name (non-reserved)."""
    seen: dict[str, int] = {}
    for i, name in field_positions:
        if name in seen:
            raise AstPlanError(
                f"production at {prod.position} marked %ast={prod.ast_kind!r}: "
                f"field {name!r} assigned twice (positions {seen[name]} and {i})"
            )
        seen[name] = i
        if ctx.symbol_is_dropped(prod.rhs[i]):
            raise AstPlanError(
                f"production at {prod.position} marked %ast={prod.ast_kind!r}: "
                f"field {name!r} is on a %ast_drop-listed token; "
                f"remove the @{name} or the %ast_drop"
            )


def _compile_list_production(
    rule: Rule, prod: Production, user_index: int, ctx: _Context
) -> ProductionAction:
    """Compile a single alternative of a list-shaped rule.

    Recognised shapes (after ignoring drop tokens):

    * empty                       -> _list_empty
    * <element>                   -> _list_init  (element_index = its position)
    * <self> … <element>          -> _list_extend (accumulator/element)
    """
    if not prod.rhs:
        return ProductionAction(user_index=user_index, kind="_list_empty")

    elem_kind = ctx.list_rules[rule.name]
    self_indices: list[int] = []
    elem_indices: list[int] = []
    other_count = 0
    for i, sym in enumerate(prod.rhs):
        if sym.kind == "nonterm" and sym.name == rule.name:
            self_indices.append(i)
        elif sym.kind == "nonterm" and sym.name == elem_kind:
            elem_indices.append(i)
        elif ctx.symbol_is_dropped(sym):
            continue
        else:
            other_count += 1

    if len(self_indices) == 0 and len(elem_indices) == 1 and other_count == 0:
        return ProductionAction(
            user_index=user_index,
            kind="_list_init",
            element_index=elem_indices[0],
        )
    if len(self_indices) == 1 and len(elem_indices) == 1 and other_count == 0:
        return ProductionAction(
            user_index=user_index,
            kind="_list_extend",
            accumulator_index=self_indices[0],
            element_index=elem_indices[0],
        )

    raise AstPlanError(
        f"production at {prod.position} of list-rule <{rule.name}> "
        f"%ast=list element=<{elem_kind}>: shape not recognised "
        f"(self_refs={len(self_indices)}, element_refs={len(elem_indices)}, "
        f"non-drop other={other_count}). Allowed shapes are <{elem_kind}> "
        f"alone, or <{rule.name}> … <{elem_kind}> with only drop tokens between."
    )


# ---- Node-kind schema reconciliation ----------------------------------------


def _build_node_kinds(
    kind_order: list[str],
    fields_by_kind: dict[str, list[tuple[Symbol, str, int]]],
    ctx: _Context,
) -> list[NodeKind]:
    """Reconcile per-production field sources into one schema per kind.

    All productions sharing a kind must agree on:

    * the set of field names produced;
    * the field-type category of each field (token / node / list);
    * the optionality flag of each field.

    Disagreement is an error — splitting a "kind" into productions
    with different shapes is a recipe for downstream consumers
    breaking. Use distinct kind names instead.
    """
    out: list[NodeKind] = []
    for kind in kind_order:
        # Group entries by field name for this kind.
        per_field: dict[str, list[tuple[Symbol, int]]] = {}
        for sym, fname, prod_idx in fields_by_kind[kind]:
            per_field.setdefault(fname, []).append((sym, prod_idx))

        # Cross-production consistency: every production of this kind
        # must declare the same field set.
        per_prod_fields: dict[int, set[str]] = {}
        for sym, fname, prod_idx in fields_by_kind[kind]:
            per_prod_fields.setdefault(prod_idx, set()).add(fname)
        all_fields = set(per_field.keys())
        for prod_idx, fields in per_prod_fields.items():
            if fields != all_fields:
                missing = sorted(all_fields - fields)
                extra = sorted(fields - all_fields)
                raise AstPlanError(
                    f"node kind {kind!r}: productions of this kind have "
                    f"inconsistent field sets. User-index {prod_idx} has "
                    f"missing fields {missing!r}, extra fields {extra!r}. "
                    f"All productions sharing %ast={kind} must use the same "
                    f"@field set, or split into separate %ast= names."
                )

        fields_ordered: list[NodeField] = []
        # Field order = first-seen order in the first production with
        # this kind. We tracked insertions in `fields_by_kind[kind]`.
        seen_field_names: set[str] = set()
        for sym, fname, _prod_idx in fields_by_kind[kind]:
            if fname in seen_field_names:
                continue
            seen_field_names.add(fname)
            entries = per_field[fname]
            fields_ordered.append(_resolve_field(kind, fname, entries, ctx))

        out.append(NodeKind(name=kind, fields=tuple(fields_ordered)))
    return out


def _resolve_field(
    kind: str, field_name: str, entries: list[tuple[Symbol, int]], ctx: _Context
) -> NodeField:
    """Resolve a single field across all productions that supply it."""
    types: set[str] = set()
    list_elems: set[str] = set()
    optionals: set[bool] = set()
    for sym, _prod_idx in entries:
        type_kind, list_elem, optional = _classify_field_source(sym, ctx)
        types.add(type_kind)
        if list_elem is not None:
            list_elems.add(list_elem)
        optionals.add(optional)

    if len(types) > 1:
        raise AstPlanError(
            f"node kind {kind!r} field {field_name!r}: type categories "
            f"disagree across productions ({sorted(types)!r}). "
            f"A field must be the same kind (node/token/list) in every "
            f"production that produces this AST node."
        )
    if len(list_elems) > 1:
        raise AstPlanError(
            f"node kind {kind!r} field {field_name!r}: list element "
            f"types disagree across productions ({sorted(list_elems)!r})."
        )
    if len(optionals) > 1:
        # If any production's source has an empty alt, the field is
        # optional. (Equivalent to union over optionals.)
        optional = True
    else:
        optional = next(iter(optionals))

    type_kind = next(iter(types))
    list_elem = next(iter(list_elems)) if list_elems else None

    # List fields always carry a default-empty value, never None.
    if type_kind == "list":
        optional = False

    return NodeField(
        name=field_name,
        type_kind=type_kind,
        optional=optional,
        list_element_kind=list_elem,
    )


def _classify_field_source(
    sym: Symbol, ctx: _Context
) -> tuple[str, Optional[str], bool]:
    """Return (type_kind, list_element_kind, optional) for this RHS source.

    * Terminal/literal (declared token) -> ``("token", None, False)``.
    * Non-terminal in a list rule -> ``("list", <element_lhs>, False)``.
    * Non-terminal with an empty alt -> ``("node", None, True)``.
    * Non-terminal without an empty alt -> ``("node", None, False)``.
    """
    if sym.kind != "nonterm":
        return ("token", None, False)
    # Non-terminal reference.
    if sym.name in ctx.list_rules:
        return ("list", ctx.list_rules[sym.name], False)
    if ctx.has_empty_alt.get(sym.name, False):
        return ("node", None, True)
    return ("node", None, False)
