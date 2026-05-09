"""Lex section round-trip and schema-fit tests."""

from __future__ import annotations

import json

from uplox.lex.dfa import from_nfa, minimise
from uplox.lex.nfa import build, combine
from uplox.lex.regex import parse as re_parse
from uplox.lex.scanner import Scanner
from uplox.tables import dfa_from_json, dfa_to_json, dump_bundle, empty_bundle


def make_calc_dfa():
    nfa = combine([
        ("NUMBER", build(re_parse("[0-9]+"))),
        ("PLUS", build(re_parse("\\+"))),
        ("WS", build(re_parse("[ \\t\\n]+"))),
    ])
    return minimise(from_nfa(nfa)), ["NUMBER", "PLUS", "WS"], ["WS"]


def test_dfa_round_trip_preserves_recognition():
    dfa, tokens, skip = make_calc_dfa()
    section = dfa_to_json(dfa, tokens=tokens, skip=skip)
    dfa2, tokens2, skip2 = dfa_from_json(section)

    assert tokens2 == tokens
    assert skip2 == skip
    assert dfa2.transitions == dfa.transitions
    assert dfa2.accepts == dfa.accepts
    assert dfa2.start == dfa.start

    sc = Scanner(dfa=dfa2, skip_tokens=frozenset(skip2))
    toks = sc.scan_all("12 + 34")
    assert [t.name for t in toks] == ["NUMBER", "PLUS", "NUMBER"]


def test_section_fits_in_bundle():
    dfa, tokens, skip = make_calc_dfa()
    bundle = empty_bundle("calc")
    bundle["lex"] = dfa_to_json(dfa, tokens=tokens, skip=skip)
    text = dump_bundle(bundle)
    parsed = json.loads(text)
    assert parsed["meta"]["grammar"] == "calc"
    assert parsed["lex"]["alphabet_size"] == 256
    assert parsed["lex"]["tokens"] == tokens


def test_serializer_is_deterministic():
    dfa, tokens, skip = make_calc_dfa()
    a = dfa_to_json(dfa, tokens=tokens, skip=skip)
    b = dfa_to_json(dfa, tokens=tokens, skip=skip)
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


def test_unknown_key_is_ignored():
    dfa, tokens, skip = make_calc_dfa()
    section = dfa_to_json(dfa, tokens=tokens, skip=skip)
    section["future_compression"] = {"scheme": "row-displacement", "base": []}
    # Reader should still produce an equivalent DFA.
    dfa2, tokens2, skip2 = dfa_from_json(section)
    assert dfa2.transitions == dfa.transitions
    assert tokens2 == tokens
    assert skip2 == skip


def test_empty_dfa_round_trip():
    from uplox.lex.dfa import DFA  # local import — only needed for this fixture

    dfa = DFA(transitions=[[-1] * 256], accepts={}, start=0)
    section = dfa_to_json(dfa, tokens=[], skip=[])
    # Edges row should be omitted entirely on a fully-DEAD row.
    assert "edges" not in section["states"][0]
    dfa2, _t, _s = dfa_from_json(section)
    assert dfa2.transitions == dfa.transitions
