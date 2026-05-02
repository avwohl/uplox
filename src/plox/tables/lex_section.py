"""Lex section of the canonical JSON bundle: DFA <-> JSON.

Layout
------

::

    "lex": {
      "alphabet_size": 256,
      "start": 0,
      "tokens": ["IF", "IDENT", "WS"],         // declaration order
      "skip":   ["WS"],                        // subset of tokens
      "states": [
        {
          "id": 0,
          "accept": null,                       // or token name string
          "edges": {
            "0x69": 1,                          // hex byte -> next state
            ...
          }
        },
        ...
      ]
    }

The byte values are stringified as ``"0xHH"`` so the JSON stays human-readable
and the keys sort lexically in a sensible order. Empty rows omit ``edges``
entirely. The serialiser writes a new dict each call — the input DFA is not
mutated.

Compression (row-displacement, equivalence-class compression on the alphabet)
will land in this module under additional keys (``equiv_classes``, ``base/check/default``)
without changing this base shape. v0 readers must accept and ignore unknown keys.
"""

from __future__ import annotations

from typing import Any

from ..lex.dfa import ALPHABET_SIZE, DEAD, DFA


def dfa_to_json(
    dfa: DFA,
    *,
    tokens: list[str],
    skip: list[str] | None = None,
    balanced: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Serialise ``dfa`` plus token metadata into the lex section dict.

    ``tokens`` is the declaration order — used by both backends and humans
    reading the bundle. ``skip`` lists the names that the scanner should drop.
    Both must contain only names present in the DFA's accept labels (the
    serialiser does not enforce this; the bundle validator in
    :mod:`plox.tables.schema` is the appropriate gate).

    ``balanced`` maps token names to their closing-delimiter characters
    for the ``%balanced=`` runtime-extension feature; absent or empty means
    no balanced tokens. The key is omitted from the serialised dict when
    empty so old bundles round-trip unchanged.
    """
    states_json: list[dict[str, Any]] = []
    for s in range(dfa.state_count()):
        edges: dict[str, int] = {}
        row = dfa.transitions[s]
        for b in range(ALPHABET_SIZE):
            tgt = row[b]
            if tgt != DEAD:
                edges[f"0x{b:02x}"] = tgt
        entry: dict[str, Any] = {"id": s, "accept": dfa.accepts.get(s)}
        if edges:
            entry["edges"] = edges
        states_json.append(entry)

    out: dict[str, Any] = {
        "alphabet_size": ALPHABET_SIZE,
        "start": dfa.start,
        "tokens": list(tokens),
        "skip": list(skip or []),
        "states": states_json,
    }
    if balanced:
        out["balanced"] = dict(sorted(balanced.items()))
    return out


def dfa_from_json(section: dict[str, Any]) -> tuple[DFA, list[str], list[str]]:
    """Inverse of :func:`dfa_to_json`. Returns ``(dfa, tokens, skip)``.

    Tolerates and ignores unknown keys so future schema-compatible extensions
    do not break old readers (the schema bump rule is in ``docs/grammar_format.md``).
    Use :func:`balanced_from_json` to read the balanced-token map separately.
    """
    if section.get("alphabet_size", ALPHABET_SIZE) != ALPHABET_SIZE:
        raise ValueError(
            f"unsupported alphabet_size {section.get('alphabet_size')!r}; "
            f"v0 lexer is byte-oriented"
        )
    states_json = section["states"]
    n = len(states_json)
    transitions = [[DEAD] * ALPHABET_SIZE for _ in range(n)]
    accepts: dict[int, str] = {}
    for entry in states_json:
        sid = entry["id"]
        if entry.get("accept") is not None:
            accepts[sid] = entry["accept"]
        for byte_str, tgt in entry.get("edges", {}).items():
            b = int(byte_str, 16)
            transitions[sid][b] = tgt
    dfa = DFA(transitions=transitions, accepts=accepts, start=section.get("start", 0))
    return dfa, list(section.get("tokens", [])), list(section.get("skip", []))


def balanced_from_json(section: dict[str, Any]) -> dict[str, str]:
    """Read the ``%balanced=`` map from a serialised lex section.

    Pre-1.2 bundles have no ``balanced`` key; that is just an empty map. The
    key is also absent (rather than ``{}``) when no token in the grammar
    uses the feature, so the bundle remains byte-identical to a pre-1.2
    bundle for grammars that don't opt in.
    """
    raw = section.get("balanced") or {}
    return {str(k): str(v) for k, v in raw.items()}
