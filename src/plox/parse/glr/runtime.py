"""Tomita-style GLR driver — stack-list implementation.

Operates on a :class:`GLRTable` plus a token stream and returns a parse
forest for the start symbol. For unambiguous grammars the forest is a
single tree (same shape as the LR runtime would produce); for ambiguous
grammars, multiple derivations are packed into :class:`AmbiguityNode`
instances.

Implementation choice for v0
----------------------------

Rather than the full graph-structured stack from Tomita's original paper,
this implementation tracks a *list* of independent stacks. When ACTION
returns multiple actions for the current top, the stack forks: one copy
takes each action. When two stacks reach an identical (state-stack,
value-stack) configuration after a shift, they merge — the merged stack
keeps a single value-stack but its top value is wrapped in an
:class:`AmbiguityNode` so both derivations are preserved in the forest.

This is exponential in the worst case but correct on the textbook
ambiguous grammars (e+e, dangling-else, etc.) and on every unambiguous
grammar. The full GSS-based implementation lands in a Phase-6 follow-up
once we have benchmarks that justify the complexity.

Algorithm
---------

For each token (including a synthetic end-of-input):

1. **Reduce loop**: while any active stack has a reduce action available
   on the current lookahead, pop its RHS, build the parse-forest value,
   apply GOTO, push the resulting state. Reduces don't consume input;
   we iterate to fixed point.
2. **Accept check**: if the lookahead is the end marker, every stack
   whose top has an accept action contributes its top value to the
   result set.
3. **Shift step**: every stack with a shift action consumes the token
   and advances. Stacks with no usable action are dropped.

When the input is exhausted, surviving accepted values are collapsed
into either a single :class:`GLRNode` / :class:`Token` (if the
derivations were equivalent) or an :class:`AmbiguityNode` rooted at
the start symbol's lhs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Optional

from ...lex.scanner import Token
from ..grammar import END_MARKER
from ..lr1 import AcceptAction, ReduceAction, ShiftAction
from .table import GLRTable


# ---- Parse-forest value types ------------------------------------------------


@dataclass
class GLRNode:
    """One node in the parse forest. Equivalent to ParseNode in the LR runtime."""

    kind: str
    children: list["ForestValue"] = field(default_factory=list)
    production: int = -1

    def __repr__(self) -> str:  # pragma: no cover - cosmetic
        return f"GLRNode({self.kind!r}, prod={self.production}, children={self.children!r})"


@dataclass
class AmbiguityNode:
    """Multiple alternative subtrees deriving the same nonterminal at the same span."""

    kind: str
    alternatives: list["ForestValue"]

    def __repr__(self) -> str:  # pragma: no cover - cosmetic
        return f"AmbiguityNode({self.kind!r}, {len(self.alternatives)} alts)"


ForestValue = Token | GLRNode | AmbiguityNode


# ---- Errors ------------------------------------------------------------------


class GLRParseError(Exception):
    def __init__(self, message: str, token: Optional[Token] = None):
        super().__init__(message)
        self.token = token


# ---- Stack ------------------------------------------------------------------


@dataclass
class _Stack:
    """One LR stack — parallel to LR's, but multiple coexist during a GLR run.

    ``states[i]`` is the state after pushing ``values[i]`` (so ``len(states) ==
    len(values) + 1``).
    """

    states: list[int]
    values: list[ForestValue] = field(default_factory=list)

    def top(self) -> int:
        return self.states[-1]

    def clone(self) -> "_Stack":
        return _Stack(states=list(self.states), values=list(self.values))


def _stack_signature(stack: _Stack) -> tuple:
    """Stable identity for stack-merge: state stack + structural values."""
    return (tuple(stack.states), tuple(_value_signature(v) for v in stack.values))


def _value_signature(v: ForestValue) -> tuple:
    if isinstance(v, Token):
        return ("T", v.name, v.text, v.line, v.column, v.offset)
    if isinstance(v, GLRNode):
        return ("N", v.kind, v.production, tuple(_value_signature(c) for c in v.children))
    if isinstance(v, AmbiguityNode):
        return ("A", v.kind, frozenset(_value_signature(a) for a in v.alternatives))
    return ("?", repr(v))


def _values_equal(a: ForestValue, b: ForestValue) -> bool:
    return _value_signature(a) == _value_signature(b)


# ---- Driver ------------------------------------------------------------------


def glr_parse(table: GLRTable, tokens: Iterable[Token]) -> ForestValue:
    """Run the GLR parser. Returns the start symbol's parse-forest value."""
    return _GLRDriver(table).parse(tokens)


# Hard cap on simultaneous stacks — defends against pathological grammars
# while developers are iterating. Set high enough that real ambiguous test
# grammars don't trip it.
_MAX_LIVE_STACKS = 4096


class _GLRDriver:
    def __init__(self, table: GLRTable) -> None:
        self.table = table

    def parse(self, tokens: Iterable[Token]) -> ForestValue:
        token_iter = iter(tokens)
        end = Token(name=END_MARKER, text="", line=0, column=0, offset=-1)

        def fetch_next() -> Token:
            try:
                return next(token_iter)
            except StopIteration:
                return end

        stacks: list[_Stack] = [_Stack(states=[self.table.start_state])]
        lookahead = fetch_next()
        accepted: list[ForestValue] = []

        while True:
            stacks = self._reduce_phase(stacks, lookahead)
            if not stacks and not accepted:
                raise GLRParseError(
                    f"unexpected token {lookahead.name!r} {lookahead.text!r} at "
                    f"line {lookahead.line}, column {lookahead.column}",
                    token=lookahead,
                )

            if lookahead.name == END_MARKER:
                # Collect every stack that has an accept action.
                for s in stacks:
                    for act in self.table.actions.get((s.top(), END_MARKER), ()):
                        if isinstance(act, AcceptAction):
                            # The accepted value is what's on top of this stack.
                            if s.values:
                                accepted.append(s.values[-1])
                if accepted:
                    return _pack_alternatives(accepted)
                raise GLRParseError(
                    f"unexpected end of input at line {lookahead.line}",
                    token=lookahead,
                )

            stacks = self._shift_phase(stacks, lookahead)
            if not stacks:
                raise GLRParseError(
                    f"unexpected token {lookahead.name!r} {lookahead.text!r} at "
                    f"line {lookahead.line}, column {lookahead.column}",
                    token=lookahead,
                )

            stacks = _merge_stacks(stacks)
            lookahead = fetch_next()

    # ---- reduce phase --------------------------------------------------------

    def _reduce_phase(self, stacks: list[_Stack], lookahead: Token) -> list[_Stack]:
        """Process all reduces until quiescent. Stacks may fork during this phase."""
        out: list[_Stack] = []
        worklist: list[_Stack] = list(stacks)

        while worklist:
            stack = worklist.pop()
            actions = self.table.actions.get((stack.top(), lookahead.name), ())
            reduces = [a for a in actions if isinstance(a, ReduceAction)]
            non_reduces = [a for a in actions if not isinstance(a, ReduceAction)]

            if not reduces:
                # Leave the stack as-is; shift/accept handled later.
                out.append(stack)
                continue

            # If both reduces and shifts are available, the shift-bearing
            # version is preserved separately.
            if non_reduces:
                out.append(stack)

            for r in reduces:
                forked = stack.clone()
                self._apply_reduce(forked, r)
                worklist.append(forked)
                if len(out) + len(worklist) > _MAX_LIVE_STACKS:
                    raise GLRParseError(
                        f"more than {_MAX_LIVE_STACKS} live GLR stacks; grammar may be "
                        f"unboundedly ambiguous on this input"
                    )

        return _merge_stacks(out)

    def _apply_reduce(self, stack: _Stack, action: ReduceAction) -> None:
        prod = self.table.grammar.productions[action.production]
        rhs_len = len(prod.rhs)

        if rhs_len:
            popped_values = stack.values[-rhs_len:]
            del stack.states[-rhs_len:]
            del stack.values[-rhs_len:]
        else:
            popped_values = []

        # Build the forest value. The augmented start production unwraps so
        # the caller sees the start symbol's value directly (matches LR).
        if action.production == 0 and len(popped_values) == 1:
            new_value: ForestValue = popped_values[0]
        else:
            new_value = GLRNode(
                kind=prod.lhs,
                children=list(popped_values),
                production=action.production,
            )

        # Apply GOTO.
        target = self.table.goto.get((stack.top(), prod.lhs))
        if target is None:
            # Production 0 reduces but the augmented start has no GOTO. Still
            # push the value; the next ACTION lookup with the end marker
            # yields ACCEPT.
            stack.values.append(new_value)
            return
        stack.values.append(new_value)
        stack.states.append(target)

    # ---- shift phase ---------------------------------------------------------

    def _shift_phase(self, stacks: list[_Stack], lookahead: Token) -> list[_Stack]:
        out: list[_Stack] = []
        for stack in stacks:
            for act in self.table.actions.get((stack.top(), lookahead.name), ()):
                if not isinstance(act, ShiftAction):
                    continue
                forked = stack.clone()
                forked.values.append(lookahead)
                forked.states.append(act.state)
                out.append(forked)
        return out


# ---- Helpers -----------------------------------------------------------------


def _merge_stacks(stacks: list[_Stack]) -> list[_Stack]:
    """Collapse stacks that are identical, packing the differing top values
    into an :class:`AmbiguityNode`.

    Two stacks merge when they share the same state stack and the same value
    stack except possibly at the top — and the top values share the same
    nonterminal kind. That last condition keeps us from packing apples and
    oranges (e.g. a token alongside a node).
    """
    if len(stacks) <= 1:
        return stacks
    by_state_stack: dict[tuple, list[_Stack]] = {}
    for s in stacks:
        by_state_stack.setdefault(tuple(s.states), []).append(s)
    out: list[_Stack] = []
    for _key, group in by_state_stack.items():
        if len(group) == 1:
            out.append(group[0])
            continue
        # Same state stack -> same depth. Compare value stacks element-wise.
        deepest = group[0]
        merged_values = list(deepest.values)
        depth = len(merged_values)
        merged = True
        for stack in group[1:]:
            if len(stack.values) != depth:
                merged = False
                break
            for i in range(depth - 1):
                if not _values_equal(merged_values[i], stack.values[i]):
                    merged = False
                    break
            if not merged:
                break
        if not merged:
            out.extend(group)
            continue
        # Pack differing top values into an AmbiguityNode if appropriate.
        top_alts: list[ForestValue] = []
        for stack in group:
            v = stack.values[-1]
            if not any(_values_equal(v, t) for t in top_alts):
                top_alts.append(v)
        if len(top_alts) == 1:
            new_stack = group[0].clone()
        else:
            kinds = {a.kind for a in top_alts if isinstance(a, (GLRNode, AmbiguityNode))}
            if len(kinds) == 1 and all(isinstance(a, (GLRNode, AmbiguityNode)) for a in top_alts):
                # Flatten nested ambiguities.
                flat: list[ForestValue] = []
                for a in top_alts:
                    if isinstance(a, AmbiguityNode):
                        for inner in a.alternatives:
                            if not any(_values_equal(inner, f) for f in flat):
                                flat.append(inner)
                    else:
                        if not any(_values_equal(a, f) for f in flat):
                            flat.append(a)
                packed = AmbiguityNode(kind=kinds.pop(), alternatives=flat)
                new_stack = group[0].clone()
                new_stack.values[-1] = packed
            else:
                # Heterogeneous tops — keep stacks distinct.
                out.extend(group)
                continue
        out.append(new_stack)
    return out


def _pack_alternatives(values: list[ForestValue]) -> ForestValue:
    """Reduce a list of accepted parses to a single forest value.

    Equivalent values dedup; multiple distinct values collapse to an
    :class:`AmbiguityNode` if they share a nonterminal kind, otherwise the
    first is returned (heterogeneous accept is degenerate in practice).
    """
    unique: list[ForestValue] = []
    for v in values:
        if not any(_values_equal(v, u) for u in unique):
            unique.append(v)
    if len(unique) == 1:
        return unique[0]
    kinds = {u.kind for u in unique if isinstance(u, (GLRNode, AmbiguityNode))}
    if len(kinds) == 1 and all(isinstance(u, (GLRNode, AmbiguityNode)) for u in unique):
        flat: list[ForestValue] = []
        for u in unique:
            if isinstance(u, AmbiguityNode):
                for inner in u.alternatives:
                    if not any(_values_equal(inner, f) for f in flat):
                        flat.append(inner)
            else:
                if not any(_values_equal(u, f) for f in flat):
                    flat.append(u)
        return AmbiguityNode(kind=kinds.pop(), alternatives=flat)
    return unique[0]
