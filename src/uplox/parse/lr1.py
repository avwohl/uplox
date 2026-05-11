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


def _state_cores(canonical: LRTable) -> list[frozenset[tuple[int, int]]]:
    """LR(0) core (items with lookaheads stripped) per canonical state."""
    return [frozenset((p, d) for (p, d, _la) in items) for items in canonical.states]


def _lalr_mapping(canonical: LRTable) -> list[int]:
    """Standard LALR(1) mapping — every canonical state with the same
    LR(0) core gets the same merged-state ID. Returned mapping is dense
    (IDs are 0..N-1 in order of first occurrence)."""
    cores = _state_cores(canonical)
    mapping: list[int] = [0] * len(canonical.states)
    new_id_for_core: dict[frozenset[tuple[int, int]], int] = {}
    for state_id, core in enumerate(cores):
        if core not in new_id_for_core:
            new_id_for_core[core] = len(new_id_for_core)
        mapping[state_id] = new_id_for_core[core]
    return mapping


def _apply_mapping(canonical: LRTable, mapping: list[int]) -> LRTable:
    """Materialise a merged LRTable from a canonical LR(1) table and a
    canonical-state → new-state mapping. The mapping must be dense
    (IDs 0..max(mapping)) and may collapse any subset of states; it is
    *not* required to merge by LR(0) core (IELR splits some same-core
    states; canonical-LR uses the identity mapping).

    Replays both ``canonical.action`` and ``canonical.conflicts`` so
    that any new shift/reduce a merge introduces is detected by
    ``_record_action`` (and optionally silenced by %shift / %reduce).
    """
    n_new = max(mapping) + 1 if mapping else 0
    # Merged item set per new state = union of items across all canonical
    # states that map to it.
    merged_items: list[set[Item]] = [set() for _ in range(n_new)]
    for state_id, items in enumerate(canonical.states):
        merged_items[mapping[state_id]].update(items)
    new_table = LRTable(grammar=canonical.grammar)
    new_table.states = [frozenset(s) for s in merged_items]
    new_table.start_state = mapping[canonical.start_state]

    def _translate(act: Action) -> Action:
        if isinstance(act, ShiftAction):
            return ShiftAction(mapping[act.state])
        return act

    for (state_id, term), action in canonical.action.items():
        _record_action(new_table, mapping[state_id], term, _translate(action))
    for conflict in canonical.conflicts:
        new_state = mapping[conflict.state]
        for action in conflict.actions:
            _record_action(new_table, new_state, conflict.terminal, _translate(action))

    for (state_id, nt), target in canonical.goto.items():
        new_state = mapping[state_id]
        new_target = mapping[target]
        existing = new_table.goto.get((new_state, nt))
        if existing is not None and existing != new_target:
            # GOTO targets must agree across canonical states that
            # share an LR(0) core (and thus share GOTO behaviour).
            # IELR/LALR only ever merge core-identical states, so this
            # disagreement would be a builder bug, not a grammar bug.
            raise AssertionError(
                f"merge: GOTO[{new_state}, {nt!r}] disagreement: "
                f"{existing} vs {new_target}"
            )
        new_table.goto[(new_state, nt)] = new_target

    _compute_default_reductions(new_table)
    return new_table


def _merge_to_lalr(canonical: LRTable) -> LRTable:
    """Collapse canonical LR(1) states that share the same LR(0) core."""
    return _apply_mapping(canonical, _lalr_mapping(canonical))


def build_ielr1(grammar: Grammar) -> LRTable:
    """Build an IELR(1)-style parse table — same conflict count as
    canonical LR(1), table size typically near LALR(1).

    Iterative refinement: start from the LALR mapping, find any
    conflicts in the merged table that aren't present at any of the
    contributing canonical states, and force those contributing states
    into singleton merge groups (i.e. unmerge them). Re-build with the
    updated mapping; repeat until no spurious conflicts remain.

    Worst case the algorithm degenerates to canonical LR(1) (every
    state in its own group) — guaranteeing the canonical conflict
    count. Best case it stays at the LALR shrink (no spurious
    conflicts means no splits). For LALR-friendly grammars (which is
    most of them) this returns immediately after the first iteration.

    Note: this is a simplified post-merge-split refinement, not the
    full Pager / Denny IELR algorithm. The full algorithm computes a
    finer-grained split (only the canonical states whose actions
    actually disagree are separated, leaving the rest merged), which
    can produce strictly smaller tables in edge cases. The simple
    splitter here was easier to implement and correct, and matches
    the full algorithm's table size on every grammar measured so far
    in this repo.
    """
    canonical = build_lr1(grammar)
    canonical_conflict_keys = {(c.state, c.terminal) for c in canonical.conflicts}
    cores = _state_cores(canonical)

    # Force-set of canonical state IDs that must each be in their own
    # merge group (singletons). Grows monotonically across iterations.
    forced_singletons: set[int] = set()
    n_states = len(canonical.states)

    def _build_mapping() -> list[int]:
        mapping: list[int] = [0] * n_states
        next_id = 0
        core_to_id: dict[frozenset[tuple[int, int]], int] = {}
        for state_id, core in enumerate(cores):
            if state_id in forced_singletons:
                mapping[state_id] = next_id
                next_id += 1
            elif core in core_to_id:
                mapping[state_id] = core_to_id[core]
            else:
                core_to_id[core] = next_id
                mapping[state_id] = next_id
                next_id += 1
        return mapping

    # Hard cap: at most n_states iterations; in practice usually 1.
    for _ in range(n_states + 1):
        mapping = _build_mapping()
        table = _apply_mapping(canonical, mapping)

        # Build reverse index once: new_state → list of canonical state IDs.
        origs_per_new: dict[int, list[int]] = {}
        for canonical_state, new_state in enumerate(mapping):
            origs_per_new.setdefault(new_state, []).append(canonical_state)

        new_singletons: set[int] = set()
        for c in table.conflicts:
            origs = origs_per_new.get(c.state, [])
            already_in_canonical = any(
                (orig, c.terminal) in canonical_conflict_keys for orig in origs
            )
            if already_in_canonical:
                continue
            # Spurious: split out every canonical state in this merge group
            # that isn't already a singleton. Coarse but correct — the next
            # iteration will see whether the split removed the conflict.
            for orig in origs:
                if orig not in forced_singletons:
                    new_singletons.add(orig)

        if not new_singletons:
            return table
        forced_singletons.update(new_singletons)

    # Should be unreachable: if every state becomes a singleton, the table
    # equals canonical LR(1) and has no spurious conflicts. Surface as an
    # error rather than silently return a possibly-wrong table.
    raise AssertionError("IELR(1) refinement did not converge")


def build_table(grammar: Grammar) -> LRTable:
    """Dispatcher — pick the LR construction algorithm from
    ``grammar.lr_type``.

    ``canonical-lr`` (default) → :func:`build_lr1`.
    ``lalr``                   → :func:`build_lalr1`.
    ``ielr``                   → :func:`build_ielr1`.
    """
    if grammar.lr_type == "canonical-lr":
        return build_lr1(grammar)
    if grammar.lr_type == "lalr":
        return build_lalr1(grammar)
    if grammar.lr_type == "ielr":
        return build_ielr1(grammar)
    raise ValueError(
        f"unknown grammar.lr_type {grammar.lr_type!r}; "
        f"expected one of 'canonical-lr', 'lalr', 'ielr'"
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
