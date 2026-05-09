"""Subset construction (NFA -> DFA) and partition-refinement minimisation.

Representation
--------------

A :class:`DFA` is a flat byte-indexed table:

* ``transitions[state][b]`` gives the next state on byte ``b`` (0..255), or
  ``DEAD`` (-1) for "no transition" — a lexer dead end.
* ``accepts[state]`` gives the token name accepted at that state, or is missing
  for non-accepting states.
* ``start`` is always 0 after construction; minimisation preserves this.

The byte-indexed table keeps the lexer runtime tight (``next = transitions[state][b]``)
and serialises to JSON with a deterministic shape. Compression schemes
(row-displacement, equivalence-class compression) live in :mod:`uplox.tables.lex_section`
so the in-memory DFA stays simple.

Tie-breaking
------------

When subset construction lands two or more NFA accept states in the same DFA
subset, we pick the one whose token has the **lowest priority number** (earlier
in the original token list). Combined with the scanner's longest-match strategy
in :mod:`uplox.lex.scanner`, this is the classic flex/lex rule.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .nfa import EPSILON, NFA

DEAD = -1
ALPHABET_SIZE = 256


@dataclass
class DFA:
    transitions: list[list[int]] = field(default_factory=list)
    """``transitions[state]`` is a list of length 256; entries are next-state ints or DEAD."""
    accepts: dict[int, str] = field(default_factory=dict)
    """Map state -> accepted token name. Missing entry = non-accepting."""
    start: int = 0

    def state_count(self) -> int:
        return len(self.transitions)

    def step(self, state: int, byte: int) -> int:
        """Single-byte transition. Returns DEAD on no transition or from a dead state."""
        if state == DEAD:
            return DEAD
        return self.transitions[state][byte]


# ---- Subset construction -----------------------------------------------------


def _epsilon_closure(nfa: NFA, states: frozenset[int]) -> frozenset[int]:
    """All states reachable from ``states`` via zero or more epsilon transitions."""
    seen = set(states)
    stack = list(states)
    while stack:
        s = stack.pop()
        for label, t in nfa.transitions[s]:
            if label is EPSILON and t not in seen:
                seen.add(t)
                stack.append(t)
    return frozenset(seen)


def _move(nfa: NFA, states: frozenset[int], byte: int) -> frozenset[int]:
    """All NFA states reachable from any state in ``states`` on input byte ``byte``."""
    out: set[int] = set()
    for s in states:
        for label, t in nfa.transitions[s]:
            if label is not EPSILON and byte in label:
                out.add(t)
    return frozenset(out)


def _resolve_accept(nfa: NFA, subset: frozenset[int]) -> Optional[str]:
    """Pick the winning token among NFA accepts in ``subset`` by priority.

    Priority comes from ``nfa.token_priority`` (lower wins). Tokens absent from
    the priority map sort after every named one — single-token NFAs that were
    never run through :func:`combine` have no priority info and so don't compete.
    """
    INF = float("inf")
    best_priority = INF
    best_name: Optional[str] = None
    for s in subset:
        name = nfa.accepts.get(s)
        if name is None:
            continue
        prio = nfa.token_priority.get(name, INF)
        if best_name is None or prio < best_priority:
            best_priority = prio
            best_name = name
    return best_name


def from_nfa(nfa: NFA) -> DFA:
    """Subset-construct a DFA from ``nfa``.

    Output state numbering is deterministic: state 0 is the closure of the NFA's
    start; later states are numbered in the order they are first discovered while
    scanning bytes 0..255 in order. Equivalent NFAs produce identical DFAs.
    """
    start_set = _epsilon_closure(nfa, frozenset({nfa.start}))
    subsets: list[frozenset[int]] = [start_set]
    index_of: dict[frozenset[int], int] = {start_set: 0}
    transitions: list[list[int]] = [[DEAD] * ALPHABET_SIZE]
    accepts: dict[int, str] = {}

    state = 0
    while state < len(subsets):
        subset = subsets[state]

        accept_name = _resolve_accept(nfa, subset)
        if accept_name is not None:
            accepts[state] = accept_name

        for b in range(ALPHABET_SIZE):
            tgt = _epsilon_closure(nfa, _move(nfa, subset, b))
            if not tgt:
                continue
            idx = index_of.get(tgt)
            if idx is None:
                idx = len(subsets)
                index_of[tgt] = idx
                subsets.append(tgt)
                transitions.append([DEAD] * ALPHABET_SIZE)
            transitions[state][b] = idx

        state += 1

    return DFA(transitions=transitions, accepts=accepts, start=0)


# ---- Partition-refinement minimisation ---------------------------------------


def minimise(dfa: DFA) -> DFA:
    """Return a DFA equivalent to ``dfa`` with the minimum number of states.

    Uses Moore's algorithm (partition refinement until fixed point). States with
    different accept tokens are placed in different initial blocks and never
    merge, so the longest-match + earliest-declaration tie-break encoded in
    ``dfa.accepts`` is preserved exactly.

    A virtual dead-state ``n`` extends the input so transitions are total: any
    real DEAD transition is treated as going to the dead state, which sits in
    its own block (it has no accept and no out-transitions other than self-loop).
    The dead state is dropped from the output unless it was reachable in the
    input — which means after :func:`from_nfa` it never is, since from_nfa
    never emits transitions to DEAD that lead anywhere.
    """
    n = dfa.state_count()
    if n == 0:
        return DFA(start=0)

    DEAD_STATE = n  # virtual

    def trans(state: int, byte: int) -> int:
        if state == DEAD_STATE:
            return DEAD_STATE
        nxt = dfa.transitions[state][byte]
        return DEAD_STATE if nxt == DEAD else nxt

    # Initial partition: one block per accept token + one for non-accepting + one for dead.
    by_label: dict[Optional[str], list[int]] = {}
    for s in range(n):
        by_label.setdefault(dfa.accepts.get(s), []).append(s)
    blocks: list[list[int]] = list(by_label.values())
    blocks.append([DEAD_STATE])  # dead state alone — distinguishable from everything

    block_of: list[int] = [0] * (n + 1)
    for i, block in enumerate(blocks):
        for s in block:
            block_of[s] = i

    # Iterate to fixed point. Each pass tries to split every block by the
    # signature (block_of[trans(s, c)] for c in alphabet).
    changed = True
    while changed:
        changed = False
        new_blocks: list[list[int]] = []
        for block in blocks:
            if len(block) == 1:
                new_blocks.append(block)
                continue
            sig_groups: dict[tuple, list[int]] = {}
            for s in block:
                sig = tuple(block_of[trans(s, c)] for c in range(ALPHABET_SIZE))
                sig_groups.setdefault(sig, []).append(s)
            if len(sig_groups) == 1:
                new_blocks.append(block)
            else:
                changed = True
                # Deterministic split order: by sorted signature so output is stable.
                for sig in sorted(sig_groups):
                    new_blocks.append(sig_groups[sig])
        blocks = new_blocks
        for i, block in enumerate(blocks):
            for s in block:
                block_of[s] = i

    # Build the output, renumbering blocks so the start-state's block is 0,
    # then visit reachable blocks in BFS order on byte 0..255 for deterministic ids.
    start_block = block_of[dfa.start]
    dead_block = block_of[DEAD_STATE]
    rename: dict[int, int] = {start_block: 0}
    queue: list[int] = [start_block]
    cursor = 0
    while cursor < len(queue):
        b = queue[cursor]
        cursor += 1
        rep = next(s for s in blocks[b] if s != DEAD_STATE) if any(s != DEAD_STATE for s in blocks[b]) else DEAD_STATE
        for byte in range(ALPHABET_SIZE):
            nb = block_of[trans(rep, byte)]
            if nb == dead_block:
                continue
            if nb not in rename:
                rename[nb] = len(rename)
                queue.append(nb)

    new_n = len(rename)
    new_transitions: list[list[int]] = [[DEAD] * ALPHABET_SIZE for _ in range(new_n)]
    new_accepts: dict[int, str] = {}
    for old_block, new_id in rename.items():
        rep = next(s for s in blocks[old_block] if s != DEAD_STATE)
        for byte in range(ALPHABET_SIZE):
            tb = block_of[trans(rep, byte)]
            if tb == dead_block:
                continue
            new_transitions[new_id][byte] = rename[tb]
        if rep in dfa.accepts:
            new_accepts[new_id] = dfa.accepts[rep]

    return DFA(transitions=new_transitions, accepts=new_accepts, start=0)
