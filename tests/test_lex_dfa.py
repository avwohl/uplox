"""Subset construction and minimisation tests."""

from __future__ import annotations

from plox.lex.dfa import DEAD, DFA, from_nfa, minimise
from plox.lex.nfa import build, combine
from plox.lex.regex import parse


def run(dfa: DFA, text: str) -> tuple[bool, str]:
    """Run ``dfa`` greedily; return (whole_input_accepted, longest_token_seen).

    Used as a tiny end-to-end check that the DFA tables actually recognise what
    the regex was supposed to accept. The real scanner with longest-match
    backtracking lives in :mod:`plox.lex.scanner`.
    """
    state = dfa.start
    last_accept_name = ""
    last_accept_end = -1  # -1 means we have not seen any accept yet
    if state in dfa.accepts:
        last_accept_name = dfa.accepts[state]
        last_accept_end = 0
    for i, ch in enumerate(text.encode("utf-8")):
        nxt = dfa.transitions[state][ch]
        if nxt == DEAD:
            break
        state = nxt
        if state in dfa.accepts:
            last_accept_name = dfa.accepts[state]
            last_accept_end = i + 1
    return (last_accept_end == len(text), last_accept_name)


def build_dfa_for(pattern: str, name: str = "T") -> DFA:
    return from_nfa(build(parse(pattern), token_name=name))


def test_single_char_accepts():
    dfa = build_dfa_for("a")
    ok, tok = run(dfa, "a")
    assert ok and tok == "T"


def test_single_char_rejects_other():
    dfa = build_dfa_for("a")
    ok, _ = run(dfa, "b")
    assert not ok


def test_concat():
    dfa = build_dfa_for("abc")
    assert run(dfa, "abc")[0]
    assert not run(dfa, "ab")[0]
    assert not run(dfa, "abd")[0]


def test_alternation():
    dfa = build_dfa_for("a|b")
    assert run(dfa, "a")[0]
    assert run(dfa, "b")[0]
    assert not run(dfa, "c")[0]


def test_star_zero_matches():
    dfa = build_dfa_for("a*")
    assert run(dfa, "")[0]
    assert run(dfa, "a")[0]
    assert run(dfa, "aaaa")[0]


def test_plus_requires_one():
    dfa = build_dfa_for("a+")
    assert not run(dfa, "")[0]
    assert run(dfa, "a")[0]
    assert run(dfa, "aaaa")[0]


def test_optional():
    dfa = build_dfa_for("a?b")
    assert run(dfa, "b")[0]
    assert run(dfa, "ab")[0]
    assert not run(dfa, "aab")[0]


def test_class_range():
    dfa = build_dfa_for("[a-c]+")
    assert run(dfa, "abc")[0]
    assert not run(dfa, "abd")[0]


def test_identifier_pattern():
    dfa = build_dfa_for("[A-Za-z_][A-Za-z0-9_]*", name="IDENT")
    assert run(dfa, "x")[0]
    assert run(dfa, "foo_bar1")[0]
    assert not run(dfa, "1foo")[0]


def test_multiple_tokens_priority():
    # IF should beat IDENT when the input is exactly "if" — earlier in the
    # combine list (lower priority number) wins ties.
    nfa = combine([
        ("IF", build(parse("if"))),
        ("IDENT", build(parse("[a-z]+"))),
    ])
    dfa = from_nfa(nfa)
    _ok, tok = run(dfa, "if")
    assert tok == "IF"


def test_multiple_tokens_distinguish():
    nfa = combine([
        ("IF", build(parse("if"))),
        ("IDENT", build(parse("[a-z]+"))),
    ])
    dfa = from_nfa(nfa)
    _ok, tok = run(dfa, "ifx")
    # Longest match drives this: "ifx" is longer than "if", and only IDENT
    # accepts "ifx".
    assert tok == "IDENT"


def test_minimise_does_not_grow():
    nfa = combine([
        ("IF", build(parse("if"))),
        ("IDENT", build(parse("[a-z]+"))),
        ("WS", build(parse("[ \\t]+"))),
    ])
    dfa = from_nfa(nfa)
    mini = minimise(dfa)
    assert mini.state_count() <= dfa.state_count()
    # All accept token labels survive.
    assert set(mini.accepts.values()) == set(dfa.accepts.values())


def test_minimise_preserves_recognition():
    nfa = combine([
        ("IF", build(parse("if"))),
        ("IDENT", build(parse("[a-z]+"))),
    ])
    dfa = from_nfa(nfa)
    mini = minimise(dfa)
    for txt, expected in [("if", "IF"), ("ifx", "IDENT"), ("z", "IDENT")]:
        _ok, tok = run(mini, txt)
        assert tok == expected, f"{txt!r}: expected {expected}, got {tok}"


def test_minimise_collapses_redundant_states():
    # ``a|a`` builds an NFA with a redundant alt branch. The DFA from subset
    # construction may have more states than necessary; minimisation must
    # collapse it to the obvious 2-state recogniser (start, accept).
    dfa = from_nfa(build(parse("a|a")))
    mini = minimise(dfa)
    assert mini.state_count() == 2


def test_deterministic_output():
    # Two builds of the same pattern must produce identical tables, byte-for-byte.
    a = minimise(from_nfa(build(parse("[A-Za-z_][A-Za-z0-9_]*"), token_name="IDENT")))
    b = minimise(from_nfa(build(parse("[A-Za-z_][A-Za-z0-9_]*"), token_name="IDENT")))
    assert a.transitions == b.transitions
    assert a.accepts == b.accepts
