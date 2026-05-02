"""Thompson construction sanity tests.

We don't yet have a DFA stage, so these tests verify the NFA structurally
(reachability, accepts, transition shape) rather than by running it on input.
"""

from __future__ import annotations

from plox.lex.nfa import EPSILON, NFA, build, combine
from plox.lex.regex import parse


def reachable(nfa: NFA, start: int) -> set[int]:
    """All states reachable from ``start`` via any transition (epsilon or labelled)."""
    seen = {start}
    stack = [start]
    while stack:
        s = stack.pop()
        for _label, t in nfa.transitions[s]:
            if t not in seen:
                seen.add(t)
                stack.append(t)
    return seen


def epsilon_closure(nfa: NFA, start: int) -> set[int]:
    seen = {start}
    stack = [start]
    while stack:
        s = stack.pop()
        for label, t in nfa.transitions[s]:
            if label is EPSILON and t not in seen:
                seen.add(t)
                stack.append(t)
    return seen


def test_single_char_nfa():
    nfa = build(parse("a"), token_name="A")
    assert nfa.state_count() == 2
    # The accept state must be reachable from the start.
    assert any(name == "A" for name in nfa.accepts.values())
    accept = next(s for s, name in nfa.accepts.items() if name == "A")
    assert accept in reachable(nfa, nfa.start)


def test_concat_chain_length():
    nfa = build(parse("abc"), token_name="T")
    # Three CharClass fragments of 2 states each, concat adds no states.
    assert nfa.state_count() == 6


def test_alt_has_extra_split_join():
    nfa = build(parse("a|b"), token_name="T")
    # 2 + 2 leaves + Alt's own start + end = 6 states.
    assert nfa.state_count() == 6


def test_star_zero_match_path_exists():
    nfa = build(parse("a*"), token_name="T")
    # The accept state is reachable from start by epsilon only.
    accept = next(iter(nfa.accepts))
    assert accept in epsilon_closure(nfa, nfa.start)


def test_plus_requires_one_match():
    nfa = build(parse("a+"), token_name="T")
    accept = next(iter(nfa.accepts))
    # Accept must NOT be in start's epsilon closure (Plus requires at least one match).
    assert accept not in epsilon_closure(nfa, nfa.start)


def test_optional_zero_match_path_exists():
    nfa = build(parse("a?"), token_name="T")
    accept = next(iter(nfa.accepts))
    assert accept in epsilon_closure(nfa, nfa.start)


def test_combine_two_tokens():
    a = build(parse("a"), token_name="A")
    b = build(parse("b"), token_name="B")
    merged = combine([("A", a), ("B", b)])
    # Two accept states, one per source.
    assert set(merged.accepts.values()) == {"A", "B"}
    # Both accepts reachable from the new shared start.
    r = reachable(merged, merged.start)
    for accept_state in merged.accepts:
        assert accept_state in r
