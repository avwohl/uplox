"""Canonical LR(1) item-set construction and ACTION/GOTO table builder.

This is the textbook algorithm: each item is ``(production_index, dot_position,
lookahead_terminal)`` and the canonical collection is the set of states reachable
from the closure of the start item under symbol-driven goto.

States are deterministic — we sort items canonically when hashing a kernel and
visit symbols in sorted order, so two runs produce the same state numbering.

Conflicts
---------

Shift/reduce and reduce/reduce conflicts are not silently resolved by default.
They are collected into a list on the returned :class:`LRTable`; callers
(e.g. the CLI) decide how to surface them. ``uplox check`` will print every
conflict before erroring out.

Two exceptions are ``%shift`` and ``%reduce``: terminals listed in those
sections have shift/reduce conflicts silently resolved in favour of shift
or reduce respectively at table-build time. ``%shift`` is the yacc-style
escape hatch for cases where the LALR(1) lookahead is one token short —
the canonical example is the dangling-else ambiguity. ``%reduce`` is the
dual, used when an LALR state-merge artefact offers a spurious shift on a
terminal that is genuinely in the followset of the to-be-reduced
non-terminal. See :func:`_record_action`.

Phase 6's GLR extension reuses this collection unchanged: it stops trying to
disambiguate at table-build time and instead remembers all conflicting actions,
exploring them at runtime in parallel.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .grammar import END_MARKER, EPSILON, Grammar


# Items are encoded as (production_index, dot_position, lookahead).
# Tuple form is hashable and tiny — important since we build sets of millions
# of items for big grammars.
Item = tuple[int, int, str]


# ---- Action codes ------------------------------------------------------------

# We use single-character action codes in the table for compactness when the
# table eventually round-trips to JSON. The schema is:
#
#   "s<state>"  shift to <state>
#   "r<prod>"   reduce by production <prod>
#   "acc"       accept
#
# This module surfaces them as Python tuples; the JSON serialiser is in
# uplox.tables.parse_section (Phase 3.4).


@dataclass
class ShiftAction:
    state: int
    def __repr__(self) -> str: return f"shift({self.state})"


@dataclass
class ReduceAction:
    production: int
    def __repr__(self) -> str: return f"reduce({self.production})"


@dataclass
class AcceptAction:
    def __repr__(self) -> str: return "accept"


Action = ShiftAction | ReduceAction | AcceptAction


@dataclass
class Conflict:
    """A shift/reduce or reduce/reduce collision in the table."""
    state: int
    terminal: str
    actions: list[Action]

    def kind(self) -> str:
        kinds = {type(a).__name__ for a in self.actions}
        if "ShiftAction" in kinds and "ReduceAction" in kinds:
            return "shift/reduce"
        if kinds == {"ReduceAction"}:
            return "reduce/reduce"
        return "/".join(sorted(kinds))

    def describe(self, grammar: Grammar) -> str:
        parts = [f"state {self.state} on {self.terminal!r} ({self.kind()}):"]
        for a in self.actions:
            if isinstance(a, ShiftAction):
                parts.append(f"  shift -> state {a.state}")
            elif isinstance(a, ReduceAction):
                p = grammar.productions[a.production]
                rhs = " ".join(p.rhs) or EPSILON or "ε"
                parts.append(f"  reduce by {a.production}: {p.lhs} -> {rhs}")
            elif isinstance(a, AcceptAction):
                parts.append("  accept")
        return "\n".join(parts)


@dataclass
class LRTable:
    grammar: Grammar
    states: list[frozenset[Item]] = field(default_factory=list)
    """Canonical item sets, indexed by state number."""
    action: dict[tuple[int, str], Action] = field(default_factory=dict)
    """ACTION[state, terminal] -> Action (shift/reduce/accept)."""
    goto: dict[tuple[int, str], int] = field(default_factory=dict)
    """GOTO[state, non_terminal] -> next state."""
    conflicts: list[Conflict] = field(default_factory=list)
    start_state: int = 0
    default_reductions: dict[int, int] = field(default_factory=dict)
    """state -> production index for states whose only valid actions are
    reduces by the same production. The runtime uses this as a fallback when
    ACTION lookup misses, so a token-filter (typedef-name hack, etc.) can
    update host state from the resulting reduce's hooks before the next
    action lookup. Without this, canonical LR(1) errors before the reduce
    can fire — see uplox.hooks.TypedefTracker for the canonical use case."""


# ---- Closure / goto ----------------------------------------------------------


def _closure(grammar: Grammar, items: frozenset[Item]) -> frozenset[Item]:
    """LR(1) closure: extend ``items`` until no new items are added.

    For an item ``(p, dot, la)`` whose dot is in front of a non-terminal ``B``,
    add every item ``(q, 0, b)`` for each B-production ``q`` and every
    ``b ∈ FIRST(β la)`` where ``β`` is the rest of the original item's RHS
    after ``B``.

    Uses Grammar.first_tail (populated at compile time) to avoid recomputing
    FIRST(β) from scratch on every call — the hot loop for big grammars.
    """
    productions = grammar.productions
    non_terminals = grammar.non_terminals
    productions_by_lhs = grammar.productions_by_lhs
    first_tail = grammar.first_tail

    out = set(items)
    work: list[Item] = list(items)
    while work:
        prod_idx, dot, la = work.pop()
        prod = productions[prod_idx]
        if dot >= len(prod.rhs):
            continue
        sym = prod.rhs[dot]
        if sym not in non_terminals:
            continue
        # FIRST(rhs[dot+1:] · la). first_tail has FIRST(β) pre-computed; the
        # only la-dependent piece is whether β can derive ε.
        beta_first = first_tail[(prod_idx, dot + 1)]
        if EPSILON in beta_first:
            lookaheads = (beta_first - {EPSILON}) | {la}
        else:
            lookaheads = beta_first
        for q in productions_by_lhs.get(sym, ()):
            for new_la in lookaheads:
                new_item = (q, 0, new_la)
                if new_item not in out:
                    out.add(new_item)
                    work.append(new_item)
    return frozenset(out)


def _kernel_key(items: frozenset[Item]) -> tuple[Item, ...]:
    """Stable identifier for an item set. Sorted for determinism."""
    return tuple(sorted(items))


# ---- Top-level builder -------------------------------------------------------


def build_lr1(grammar: Grammar) -> LRTable:
    """Build the canonical LR(1) parse table.

    Always returns an :class:`LRTable`. Conflicts populate the ``conflicts``
    list rather than aborting the build, so callers can decide whether to
    surface every conflict at once or stop on the first.
    """
    table = LRTable(grammar=grammar)
    productions = grammar.productions

    # Initial state: closure of {(start_prod, 0, $)}.
    start_item: Item = (0, 0, END_MARKER)
    initial = _closure(grammar, frozenset({start_item}))
    table.states.append(initial)
    index_of: dict[tuple[Item, ...], int] = {_kernel_key(initial): 0}

    cursor = 0
    while cursor < len(table.states):
        state_id = cursor
        cursor += 1
        items = table.states[state_id]

        # Bucket items by the symbol after the dot, in one pass. Items with
        # the dot at the end go to the reduce list. This avoids the
        # quadratic "iterate items once per next-symbol" pattern: each goto
        # group has its `moved` set built up directly from the bucket.
        moved_by_sym: dict[str, set[Item]] = {}
        reduce_items: list[Item] = []
        for item in items:
            prod_idx, dot, la = item
            prod = productions[prod_idx]
            if dot >= len(prod.rhs):
                reduce_items.append(item)
                continue
            sym = prod.rhs[dot]
            advanced = (prod_idx, dot + 1, la)
            bucket = moved_by_sym.get(sym)
            if bucket is None:
                bucket = {advanced}
                moved_by_sym[sym] = bucket
            else:
                bucket.add(advanced)

        # Build successor states for each next-symbol in sorted order so state
        # numbering is deterministic.
        for sym in sorted(moved_by_sym):
            successor = _closure(grammar, frozenset(moved_by_sym[sym]))
            key = _kernel_key(successor)
            if key not in index_of:
                index_of[key] = len(table.states)
                table.states.append(successor)
            target = index_of[key]
            if sym in grammar.terminals:
                _record_action(table, state_id, sym, ShiftAction(target))
            else:
                table.goto[(state_id, sym)] = target

        # Reductions: any item with the dot at the end contributes a reduce on
        # its lookahead. Production 0 (the augmented start) reduces to ACCEPT
        # on $; all others reduce by their production.
        for prod_idx, dot, la in reduce_items:
            if prod_idx == 0 and la == END_MARKER:
                _record_action(table, state_id, END_MARKER, AcceptAction())
            else:
                _record_action(table, state_id, la, ReduceAction(prod_idx))

    _compute_default_reductions(table)
    return table


def build_lalr1(grammar: Grammar) -> LRTable:
    """Build an LALR(1) parse table by merging canonical LR(1) states that
    share the same LR(0) core.

    Tables are typically ~10× smaller than canonical LR(1) (state count
    proportional to LR(0) cores rather than ``cores × lookahead sets``).
    The trade-off is that LALR-merging can synthesise reduce/reduce
    conflicts that don't exist in canonical LR(1) — when two states with
    the same core but different lookaheads merge, an item that was the
    only reduce on its lookahead in each member may become one of two
    competing reduces in the merged state.

    The %shift / %reduce escape hatches still apply at conflict-resolution
    time on the merged table.
    """
    canonical = build_lr1(grammar)
    return _merge_to_lalr(canonical)


def _merge_to_lalr(canonical: LRTable) -> LRTable:
    """Collapse canonical LR(1) states that share the same LR(0) core."""
    # 1. Compute the LR(0) core of each canonical state — the set of
    #    (production, dot) pairs with lookaheads stripped.
    cores: list[frozenset[tuple[int, int]]] = []
    for items in canonical.states:
        cores.append(frozenset((p, d) for (p, d, _la) in items))

    # 2. Walk states in order, assigning each unique core a fresh new-state
    #    ID. Renumbering preserves the start state at 0 because state 0's
    #    core is encountered first.
    new_id_for_core: dict[frozenset[tuple[int, int]], int] = {}
    mapping: list[int] = [0] * len(canonical.states)
    new_states: list[frozenset[Item]] = []
    for state_id, core in enumerate(cores):
        if core not in new_id_for_core:
            new_id_for_core[core] = len(new_states)
            # Merged item set = union of items across all states sharing the
            # core (different lookaheads merge into the same set).
            merged: set[Item] = set()
            for member, member_core in enumerate(cores):
                if member_core is core or member_core == core:
                    merged.update(canonical.states[member])
            new_states.append(frozenset(merged))
        mapping[state_id] = new_id_for_core[core]

    # 3. Build a fresh table and replay actions/gotos through the mapping.
    #    _record_action handles the %shift/%reduce resolution and detects
    #    any new conflicts the merge may have introduced.
    new_table = LRTable(grammar=canonical.grammar)
    new_table.states = new_states
    new_table.start_state = mapping[canonical.start_state]

    def _translate(act: Action) -> Action:
        if isinstance(act, ShiftAction):
            return ShiftAction(mapping[act.state])
        return act

    for (state_id, term), action in canonical.action.items():
        _record_action(new_table, mapping[state_id], term, _translate(action))
    # canonical.action holds only the *first* action at a conflicted
    # (state, terminal); the competing actions live in canonical.conflicts.
    # Replay both halves so post-merge conflict detection sees the full
    # picture, and so any new shift/reduce conflicts the merge introduces
    # are surfaced (or silenced by %shift/%reduce) consistently.
    for conflict in canonical.conflicts:
        new_state = mapping[conflict.state]
        for action in conflict.actions:
            _record_action(new_table, new_state, conflict.terminal, _translate(action))

    for (state_id, nt), target in canonical.goto.items():
        new_state = mapping[state_id]
        new_target = mapping[target]
        existing = new_table.goto.get((new_state, nt))
        if existing is not None and existing != new_target:
            # GOTO targets must agree across states with the same LR(0) core,
            # because GOTO is determined by the core (no lookahead in
            # play). If this fires, something is structurally wrong with
            # the canonical table — assert rather than silently mis-merge.
            raise AssertionError(
                f"LALR merge: GOTO[{new_state}, {nt!r}] disagreement: "
                f"{existing} vs {new_target}"
            )
        new_table.goto[(new_state, nt)] = new_target

    _compute_default_reductions(new_table)
    return new_table


def build_table(grammar: Grammar) -> LRTable:
    """Dispatcher — pick the LR construction algorithm from
    ``grammar.lr_type``.

    ``canonical-lr`` (default) → :func:`build_lr1`.
    ``lalr``                  → :func:`build_lalr1`.
    """
    if grammar.lr_type == "canonical-lr":
        return build_lr1(grammar)
    if grammar.lr_type == "lalr":
        return build_lalr1(grammar)
    raise ValueError(
        f"unknown grammar.lr_type {grammar.lr_type!r}; "
        f"expected one of 'canonical-lr', 'lalr'"
    )


def _compute_default_reductions(table: LRTable) -> None:
    """Mark each state whose only actions are reduces by a single production.

    A state qualifies iff:

    * Every entry in :attr:`LRTable.action` for the state is a
      :class:`ReduceAction` for the same production index.
    * The state has no conflicts (any conflict means the lookahead is needed
      to disambiguate, so we cannot blindly default-reduce).

    This is the standard "default reduction" optimization. Sound because at
    a state with only reduce-X actions, applying the reduce eagerly produces
    the same parse (or the same error one step later) as consulting the
    lookahead — but it lets the runtime fire post_reduce hooks *before* the
    parser has to classify the next token, which is what makes lexer
    feedback (TypedefTracker etc.) work."""
    by_state: dict[int, list[Action]] = {}
    for (state, _term), action in table.action.items():
        by_state.setdefault(state, []).append(action)
    conflict_states = {c.state for c in table.conflicts}
    for state, actions in by_state.items():
        if state in conflict_states:
            continue
        if not actions or not all(isinstance(a, ReduceAction) for a in actions):
            continue
        prods = {a.production for a in actions}  # type: ignore[union-attr]
        if len(prods) == 1:
            table.default_reductions[state] = prods.pop()


def _record_action(table: LRTable, state: int, terminal: str, action: Action) -> None:
    """Record ``action`` in ACTION[state, terminal], collecting any conflict.

    Shift/reduce conflicts on a terminal listed in ``%shift`` are silently
    resolved in favour of shift (the canonical dangling-else fix). All other
    conflicts are recorded for the caller to surface."""
    key = (state, terminal)
    existing = table.action.get(key)
    if existing is None:
        table.action[key] = action
        return
    # Same action twice (e.g. two items giving the same shift target) is fine.
    if _actions_equal(existing, action):
        return
    # %shift escape hatch: silently prefer shift over reduce on this terminal.
    if terminal in table.grammar.shift_terminals:
        if isinstance(action, ShiftAction) and isinstance(existing, ReduceAction):
            table.action[key] = action
            return
        if isinstance(action, ReduceAction) and isinstance(existing, ShiftAction):
            return
    # %reduce escape hatch: dual of %shift — prefer reduce over shift.
    if terminal in table.grammar.reduce_terminals:
        if isinstance(action, ReduceAction) and isinstance(existing, ShiftAction):
            table.action[key] = action
            return
        if isinstance(action, ShiftAction) and isinstance(existing, ReduceAction):
            return
    # Conflict — record it, keep the first action (deterministic) and move on.
    found = next(
        (c for c in table.conflicts if c.state == state and c.terminal == terminal),
        None,
    )
    if found is None:
        table.conflicts.append(Conflict(state=state, terminal=terminal, actions=[existing, action]))
    else:
        if not any(_actions_equal(action, prev) for prev in found.actions):
            found.actions.append(action)


def _actions_equal(a: Action, b: Action) -> bool:
    if type(a) is not type(b):
        return False
    if isinstance(a, ShiftAction):
        return a.state == b.state  # type: ignore[union-attr]
    if isinstance(a, ReduceAction):
        return a.production == b.production  # type: ignore[union-attr]
    return True  # AcceptAction has no fields
