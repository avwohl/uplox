"""GLR table view derived from a canonical :class:`~uplox.parse.lr1.LRTable`.

The LR builder records actions in two places: ``table.action`` (one canonical
action per ``(state, terminal)``) and ``table.conflicts`` (every additional
action that collided). For GLR, both views collapse into a single
multi-valued ACTION map: every ``(state, terminal)`` lists *every* viable
action, deterministically sorted so output is reproducible.

The GOTO map is unchanged — non-terminals never conflict.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..grammar import Grammar
from ..lr1 import (
    AcceptAction,
    Action,
    LRTable,
    ReduceAction,
    ShiftAction,
)


@dataclass
class GLRTable:
    """Multi-action LR table for GLR consumption.

    ``actions[(state, terminal)]`` is a list of every Action recorded for that
    cell. An empty cell is *absent* from the dict (parser error on lookahead).
    Single-action cells contain exactly one entry; conflicting cells contain
    the conflict's full action list, sorted shifts-then-reduces.

    ``default_reductions`` mirrors :attr:`LRTable.default_reductions` — for
    each state listed, the production index to reduce by when ACTION lookup
    finds no entries. Lexer-feedback grammars (typedef-name etc.) need this
    for the same reason canonical LR(1) does, even under GLR.
    """

    grammar: Grammar
    actions: dict[tuple[int, str], list[Action]] = field(default_factory=dict)
    goto: dict[tuple[int, str], int] = field(default_factory=dict)
    state_count: int = 0
    start_state: int = 0
    default_reductions: dict[int, int] = field(default_factory=dict)


def _action_sort_key(a: Action) -> tuple[int, int]:
    """Stable sort key: shifts first (kind=0), then reduces (kind=1, by prod), then accept."""
    if isinstance(a, ShiftAction):
        return (0, a.state)
    if isinstance(a, ReduceAction):
        return (1, a.production)
    if isinstance(a, AcceptAction):
        return (2, 0)
    raise TypeError(f"unhandled action {a!r}")


def glr_from_lr(table: LRTable) -> GLRTable:
    """Derive a :class:`GLRTable` from an :class:`LRTable` (with or without conflicts).

    Combines ``table.action`` and ``table.conflicts`` into a per-cell action list.
    Equivalent actions are deduplicated; the resulting list is sorted so two
    runs over the same grammar produce identical GLR tables.
    """
    actions: dict[tuple[int, str], list[Action]] = {}

    for key, act in table.action.items():
        actions[key] = [act]

    for c in table.conflicts:
        key = (c.state, c.terminal)
        existing = actions.setdefault(key, [])
        for a in c.actions:
            if not any(_actions_equal(a, e) for e in existing):
                existing.append(a)

    for key, lst in actions.items():
        lst.sort(key=_action_sort_key)

    return GLRTable(
        grammar=table.grammar,
        actions=actions,
        goto=dict(table.goto),
        state_count=len(table.states),
        start_state=table.start_state,
        default_reductions=dict(table.default_reductions),
    )


def _actions_equal(a: Action, b: Action) -> bool:
    if type(a) is not type(b):
        return False
    if isinstance(a, ShiftAction):
        return a.state == b.state  # type: ignore[union-attr]
    if isinstance(a, ReduceAction):
        return a.production == b.production  # type: ignore[union-attr]
    return True
