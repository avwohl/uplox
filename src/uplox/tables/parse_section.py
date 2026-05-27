"""Parse section of the canonical JSON bundle: LR table <-> JSON.

Layout
------

::

    "parse": {
      "kind": "lr1",
      "start_state": 0,
      "end_marker": "$",
      "non_terminals": ["expr", "term", "factor"],
      "productions": [
        {"index": 0, "lhs": "$start", "rhs": ["expr"], "hook": null, "action": null},
        ...
      ],
      "states": [
        {
          "id": 0,
          "actions": {
            "NUMBER": "s3",
            "LPAREN": "s4"
          },
          "gotos": {
            "expr": 1,
            "term": 2,
            "factor": 5
          },
          "default_reduction": 12   // optional; production index to reduce
                                    // when ACTION lookup misses (used by
                                    // typedef-name and other lexer-feedback
                                    // grammars). Omitted when the state has
                                    // no default reduction.
        },
        ...
      ]
    }

Action codes are short strings so the bundle stays compact and easy to read by
hand:

* ``"sN"`` shift to state ``N``
* ``"rN"`` reduce by production ``N``
* ``"acc"`` accept

A loader library per backend language hides the encoding from generated drivers;
the Python runtime in :mod:`uplox.parse.runtime` consumes the Python form
directly without a JSON round-trip.

Conflicts
---------

Conflicts are *not* serialised. The bundle is a build artifact for a
non-conflicting grammar; conflicting builds are caught by ``uplox build`` /
``uplox check`` and never reach this layer.
"""

from __future__ import annotations

from typing import Any

from ..parse.grammar import AUGMENTED_START, END_MARKER, Grammar
from ..parse.lr1 import (
    AcceptAction,
    Action,
    LRTable,
    PredicatedActions,
    ReduceAction,
    ShiftAction,
    _compute_default_reductions,
)


def _action_to_code(a):
    if isinstance(a, ShiftAction):
        return f"s{a.state}"
    if isinstance(a, ReduceAction):
        return f"r{a.production}"
    if isinstance(a, AcceptAction):
        return "acc"
    if isinstance(a, PredicatedActions):
        # Encoded as a dict so the JSON survives schemas that key on
        # action codes (the runtime branches on type).
        alts = []
        for pred, alt in a.alternatives:
            alts.append({"predicate": pred, "action": _action_to_code(alt)})
        out: dict = {"predicated": alts}
        if a.fallback is not None:
            out["default"] = _action_to_code(a.fallback)
        return out
    raise TypeError(f"unknown action {a!r}")


def _code_to_action(code):
    if isinstance(code, dict) and "predicated" in code:
        cell = PredicatedActions()
        for entry in code["predicated"]:
            cell.alternatives.append((entry.get("predicate"), _code_to_action(entry["action"])))
        if "default" in code:
            cell.fallback = _code_to_action(code["default"])
        return cell
    if code == "acc":
        return AcceptAction()
    if code.startswith("s"):
        return ShiftAction(state=int(code[1:]))
    if code.startswith("r"):
        return ReduceAction(production=int(code[1:]))
    raise ValueError(f"unknown action code {code!r}")


def table_to_json(table: LRTable) -> dict[str, Any]:
    """Serialise ``table`` into the parse-section dict.

    Refuses tables with conflicts — those are build errors and shouldn't
    reach the bundle. The CLI gates on this, so the only path to this
    function with a conflicting table is a programming error in uplox itself.
    """
    if table.conflicts:
        raise ValueError(
            f"refusing to serialise a parse table with {len(table.conflicts)} conflict(s); "
            f"resolve them in the grammar first"
        )
    g = table.grammar

    productions_json: list[dict[str, Any]] = []
    for p in g.productions:
        entry: dict[str, Any] = {
            "index": p.index,
            "lhs": p.lhs,
            "rhs": list(p.rhs),
            "hook": p.hook,
            "action": p.action,
        }
        # Phase-3/2 context-sensitive fields. Only emit when set so older
        # bundles (no classifier/predicate/action wiring) round-trip
        # byte-for-byte through the new code.
        if p.predicate is not None:
            entry["predicate"] = p.predicate
        if p.post_action is not None:
            entry["post_action"] = p.post_action
        if p.post_action_arg_pos is not None:
            entry["post_action_arg_pos"] = p.post_action_arg_pos
        productions_json.append(entry)

    # Group ACTION and GOTO by state for compact, locality-friendly JSON.
    states_json: list[dict[str, Any]] = []
    for s in range(len(table.states)):
        actions: dict[str, Any] = {}
        gotos: dict[str, int] = {}
        for (state, sym), act in table.action.items():
            if state == s:
                actions[sym] = _action_to_code(act)
        for (state, sym), tgt in table.goto.items():
            if state == s:
                gotos[sym] = tgt
        entry: dict[str, Any] = {"id": s, "actions": actions, "gotos": gotos}
        if s in table.default_reductions:
            entry["default_reduction"] = table.default_reductions[s]
        states_json.append(entry)

    out = {
        "kind": "lr1",
        "start_state": table.start_state,
        "end_marker": END_MARKER,
        "augmented_start": AUGMENTED_START,
        "start_symbol": g.start_symbol,
        "non_terminals": sorted(g.non_terminals),
        "productions": productions_json,
        "states": states_json,
    }
    # Context-sensitive parsing wiring (Phases 1-4). Each block is only
    # emitted when the grammar declared at least one entry — empty wiring
    # stays absent so older bundles compare byte-for-byte under the new
    # serializer.
    if g.classifier_map:
        out["classifiers"] = {
            src: list(alts) for src, alts in sorted(g.classifier_map.items())
        }
    if g.predicate_names:
        out["predicates"] = list(g.predicate_names)
    if g.action_names:
        out["actions"] = list(g.action_names)
    if g.modes:
        out["modes"] = list(g.modes)
    return out


def table_from_json(section: dict[str, Any]) -> LRTable:
    """Inverse of :func:`table_to_json`. Reconstructs an :class:`LRTable`.

    Builds a minimal :class:`Grammar` skeleton — enough for the runtime to do
    its work (productions, terminal/non-terminal split, start symbol). FIRST
    and FOLLOW are *not* reconstructed because the runtime does not need them.
    """
    if section.get("kind") != "lr1":
        raise ValueError(f"unsupported parse table kind {section.get('kind')!r}")

    from ..parse.grammar import CompiledProduction, Grammar

    productions: list[CompiledProduction] = []
    for p in section["productions"]:
        productions.append(CompiledProduction(
            index=p["index"],
            lhs=p["lhs"],
            rhs=tuple(p["rhs"]),
            action=p.get("action"),
            hook=p.get("hook"),
            user_index=p.get("user_index", -1),
            predicate=p.get("predicate"),
            post_action=p.get("post_action"),
            post_action_arg_pos=p.get("post_action_arg_pos"),
        ))

    non_terminals = set(section["non_terminals"])
    # Anything mentioned in productions that isn't a non-terminal is a terminal.
    terminals: set[str] = set()
    for p in productions:
        for sym in p.rhs:
            if sym not in non_terminals:
                terminals.add(sym)
    terminals.add(section.get("end_marker", END_MARKER))

    grammar = Grammar(
        productions=productions,
        terminals=terminals,
        non_terminals=non_terminals,
        start_symbol=section["start_symbol"],
        augmented_start=section.get("augmented_start", AUGMENTED_START),
    )
    # Restore context-sensitive wiring blocks. Absent blocks leave the
    # grammar's defaults (empty / no-op) intact.
    cls = section.get("classifiers")
    if isinstance(cls, dict):
        for src, alts in cls.items():
            grammar.classifier_map[src] = tuple(alts)
    if isinstance(section.get("predicates"), list):
        grammar.predicate_names = tuple(section["predicates"])
    if isinstance(section.get("actions"), list):
        grammar.action_names = tuple(section["actions"])
    if isinstance(section.get("modes"), list):
        grammar.modes = tuple(section["modes"])
    for p in productions:
        grammar.productions_by_lhs.setdefault(p.lhs, []).append(p.index)

    table = LRTable(
        grammar=grammar,
        states=[frozenset() for _ in section["states"]],  # state contents unused at runtime
        start_state=section.get("start_state", 0),
    )
    for entry in section["states"]:
        s = entry["id"]
        for sym, code in entry["actions"].items():
            table.action[(s, sym)] = _code_to_action(code)
        for sym, tgt in entry["gotos"].items():
            table.goto[(s, sym)] = tgt
        if "default_reduction" in entry:
            table.default_reductions[s] = entry["default_reduction"]
    # Older bundles (pre-default-reductions) don't carry the field, so we
    # recompute it from the action table — the result is byte-identical to
    # what the original build produced.
    if not table.default_reductions:
        _compute_default_reductions(table)
    return table
