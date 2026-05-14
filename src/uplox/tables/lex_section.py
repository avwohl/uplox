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

from typing import Any, Optional

from ..lex.dfa import ALPHABET_SIZE, DEAD, DFA
from ..spec.ir import (
    ColumnClause,
    ColumnsConfig,
    ContinuationConfig,
    LayoutConfig,
)


def dfa_to_json(
    dfa: DFA,
    *,
    tokens: list[str],
    skip: list[str] | None = None,
    balanced: dict[str, str] | None = None,
    layout: Optional[LayoutConfig] = None,
    columns: Optional[ColumnsConfig] = None,
    continuation: Optional[ContinuationConfig] = None,
) -> dict[str, Any]:
    """Serialise ``dfa`` plus token metadata into the lex section dict.

    ``tokens`` is the declaration order — used by both backends and humans
    reading the bundle. ``skip`` lists the names that the scanner should drop.
    Both must contain only names present in the DFA's accept labels (the
    serialiser does not enforce this; the bundle validator in
    :mod:`uplox.tables.schema` is the appropriate gate).

    ``balanced`` maps token names to their closing-delimiter characters
    for the ``%balanced=`` runtime-extension feature; absent or empty means
    no balanced tokens. The key is omitted from the serialised dict when
    empty so old bundles round-trip unchanged.

    ``layout`` / ``columns`` / ``continuation`` carry the three
    lexer-feedback directives. Each is absent from the output dict when
    its config is ``None``, so bundles without these directives round-trip
    byte-identically to pre-feature bundles.
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
    if layout is not None:
        out["layout"] = layout_to_json(layout)
    if columns is not None:
        out["columns"] = columns_to_json(columns)
    if continuation is not None:
        out["continuation"] = continuation_to_json(continuation)
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


def layout_to_json(cfg: LayoutConfig) -> dict[str, Any]:
    """Serialise a :class:`LayoutConfig` to its bundle dict form.

    See ``docs/proposals/layout.md``. The bundle key is ``lex.layout``;
    the key is absent from bundles whose grammar didn't declare
    ``%layout``.
    """
    return {
        "indent_token": cfg.indent_token,
        "dedent_token": cfg.dedent_token,
        "newline_token": cfg.newline_token,
        "flow_open": list(cfg.flow_open),
        "flow_close": list(cfg.flow_close),
        "tab_width": cfg.tab_width,
        "blank_lines": cfg.blank_lines,
        "comment_lines": cfg.comment_lines,
    }


def layout_from_json(section: dict[str, Any]) -> Optional[LayoutConfig]:
    """Inverse of :func:`layout_to_json`. Returns ``None`` if no layout
    config is present in the bundle.
    """
    raw = section.get("layout")
    if not raw:
        return None
    return LayoutConfig(
        indent_token=str(raw["indent_token"]),
        dedent_token=str(raw["dedent_token"]),
        newline_token=str(raw["newline_token"]),
        flow_open=list(raw.get("flow_open", [])),
        flow_close=list(raw.get("flow_close", [])),
        tab_width=int(raw.get("tab_width", 8)),
        blank_lines=str(raw.get("blank_lines", "skip")),
        comment_lines=str(raw.get("comment_lines", "skip")),
    )


def columns_to_json(cfg: ColumnsConfig) -> dict[str, Any]:
    """Serialise a :class:`ColumnsConfig` to its bundle dict form."""
    clauses_json: list[dict[str, Any]] = []
    for c in cfg.clauses:
        entry: dict[str, Any] = {
            "col_start": c.col_start,
            "col_end": c.col_end,
            "mode": c.mode,
        }
        if c.comment_if is not None:
            entry["comment_if"] = c.comment_if
        if c.continuation_if is not None:
            entry["continuation_if"] = c.continuation_if
        if c.continuation_if_nonblank:
            entry["continuation_if_nonblank"] = True
        if c.continuation_token is not None:
            entry["continuation_token"] = c.continuation_token
        if c.debug_if is not None:
            entry["debug_if"] = c.debug_if
        if c.debug_token is not None:
            entry["debug_token"] = c.debug_token
        clauses_json.append(entry)
    return {
        "width": cfg.width,
        "clauses": clauses_json,
    }


def columns_from_json(section: dict[str, Any]) -> Optional[ColumnsConfig]:
    """Inverse of :func:`columns_to_json`."""
    raw = section.get("columns")
    if not raw:
        return None
    clauses: list[ColumnClause] = []
    for c in raw.get("clauses", []):
        clauses.append(
            ColumnClause(
                col_start=int(c["col_start"]),
                col_end=int(c["col_end"]),
                mode=str(c.get("mode", "body")),
                comment_if=c.get("comment_if"),
                continuation_if=c.get("continuation_if"),
                continuation_if_nonblank=bool(c.get("continuation_if_nonblank", False)),
                continuation_token=c.get("continuation_token"),
                debug_if=c.get("debug_if"),
                debug_token=c.get("debug_token"),
            )
        )
    return ColumnsConfig(width=int(raw.get("width", 80)), clauses=clauses)


def continuation_to_json(cfg: ContinuationConfig) -> dict[str, Any]:
    """Serialise a :class:`ContinuationConfig` to its bundle dict form."""
    out: dict[str, Any] = {
        "applies_in_brackets": cfg.applies_in_brackets,
        "preserve_position": cfg.preserve_position,
    }
    if cfg.marker_char is not None:
        out["marker_char"] = cfg.marker_char
    if cfg.marker_token is not None:
        out["marker_token"] = cfg.marker_token
    if cfg.at_column is not None:
        out["at_column"] = cfg.at_column
    return out


def continuation_from_json(section: dict[str, Any]) -> Optional[ContinuationConfig]:
    """Inverse of :func:`continuation_to_json`."""
    raw = section.get("continuation")
    if not raw:
        return None
    return ContinuationConfig(
        marker_char=raw.get("marker_char"),
        marker_token=raw.get("marker_token"),
        at_column=raw.get("at_column"),
        applies_in_brackets=bool(raw.get("applies_in_brackets", False)),
        preserve_position=bool(raw.get("preserve_position", True)),
    )
