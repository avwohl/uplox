"""Canonical LR(1) builder tests."""

from __future__ import annotations

from plox.parse.grammar import compile_grammar
from plox.parse.lr1 import (
    AcceptAction,
    Conflict,
    ReduceAction,
    ShiftAction,
    build_lr1,
)
from plox.spec.reader import read_source


CALC = """
%grammar calc

%tokens
NUMBER = /[0-9]+/
PLUS   = "+"
STAR   = "*"
LPAREN = "("
RPAREN = ")"

%rules
expr  : expr PLUS term | term ;
term  : term STAR factor | factor ;
factor : NUMBER | LPAREN expr RPAREN ;
"""


def build(src: str):
    return build_lr1(compile_grammar(read_source(src)))


def test_calc_has_no_conflicts():
    table = build(CALC)
    assert table.conflicts == []


def test_initial_state_actions_for_calc():
    table = build(CALC)
    g = table.grammar
    # State 0 should shift on NUMBER and LPAREN (the FIRST set of expr).
    s0_actions = {term: act for (state, term), act in table.action.items() if state == 0}
    assert "NUMBER" in s0_actions
    assert "LPAREN" in s0_actions
    assert isinstance(s0_actions["NUMBER"], ShiftAction)


def test_accept_on_end_marker():
    table = build(CALC)
    # There must be exactly one accept action somewhere in the table.
    accepts = [
        (state, term) for (state, term), act in table.action.items() if isinstance(act, AcceptAction)
    ]
    assert len(accepts) == 1
    _state, term = accepts[0]
    from plox.parse.grammar import END_MARKER
    assert term == END_MARKER


def test_dangling_else_creates_shift_reduce_conflict():
    src = """
%grammar amb
%tokens
IF    = "if"
THEN  = "then"
ELSE  = "else"
S     = "s"

%rules
stmt : IF cond THEN stmt
     | IF cond THEN stmt ELSE stmt
     | S
     ;
cond : S ;
"""
    table = build(src)
    sr = [c for c in table.conflicts if c.kind() == "shift/reduce"]
    assert sr, "expected the canonical dangling-else shift/reduce conflict"
    # The conflict is over ELSE.
    assert any(c.terminal == "ELSE" for c in sr)


def test_reduce_reduce_conflict_in_simple_grammar():
    # x -> A and y -> A is *not* a conflict (different LHS), but a reduce/reduce
    # arises when two different LHS rules can both reduce on the same lookahead
    # in the same state.
    src = """
%grammar rr
%tokens
A = "a"
B = "b"

%rules
s : x B | y B ;
x : A ;
y : A ;
"""
    table = build(src)
    rr = [c for c in table.conflicts if c.kind() == "reduce/reduce"]
    assert rr, f"expected r/r conflict; got {[c.kind() for c in table.conflicts]}"


def test_state_count_is_deterministic():
    a = build(CALC)
    b = build(CALC)
    assert len(a.states) == len(b.states)
    assert a.action == b.action
    assert a.goto == b.goto


def test_goto_table_has_entries_for_non_terminals():
    table = build(CALC)
    # State 0 must goto somewhere on each of expr/term/factor.
    s0_gotos = {nt for (state, nt), _ in table.goto.items() if state == 0}
    assert {"expr", "term", "factor"} <= s0_gotos


def test_describe_conflict_is_readable():
    src = """
%grammar amb
%tokens
IF    = "if"
THEN  = "then"
ELSE  = "else"
S     = "s"

%rules
stmt : IF cond THEN stmt
     | IF cond THEN stmt ELSE stmt
     | S
     ;
cond : S ;
"""
    table = build(src)
    conflict = next(c for c in table.conflicts if c.terminal == "ELSE")
    msg = conflict.describe(table.grammar)
    assert "ELSE" in msg
    assert "shift" in msg
    assert "reduce" in msg


def test_simple_unambiguous_grammar_has_few_states():
    # Just a sanity check on canonical LR(1) state explosion: the very simple
    # `s : A` grammar should compile to a small table.
    src = "%grammar tiny\n%tokens\nA = \"a\"\n%rules\ns : A ;\n"
    table = build(src)
    # Augmented start + initial closure + after-shift + accept reduction... 4 states tops.
    assert len(table.states) <= 4
    assert table.conflicts == []
