"""Canonical LR(1) builder tests."""

from __future__ import annotations

from uplox.parse.grammar import compile_grammar
from uplox.parse.lr1 import (
    AcceptAction,
    Conflict,
    ReduceAction,
    ShiftAction,
    build_lr1,
)
from uplox.spec.reader import read_source


CALC = """
%grammar calc

%tokens
NUMBER = /[0-9]+/
PLUS   = '+'
STAR   = '*'
LPAREN = '('
RPAREN = ')'

%rules
<expr>  : <expr> PLUS <term> | <term> ;
<term>  : <term> STAR <factor> | <factor> ;
<factor> : NUMBER | LPAREN <expr> RPAREN ;
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
    from uplox.parse.grammar import END_MARKER
    assert term == END_MARKER


def test_dangling_else_creates_shift_reduce_conflict():
    src = """
%grammar amb
%tokens
IF    = 'if'
THEN  = 'then'
ELSE  = 'else'
S     = 's'

%rules
<stmt> : IF <cond> THEN <stmt>
     | IF <cond> THEN <stmt> ELSE <stmt>
     | S
     ;
<cond> : S ;
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
A = 'a'
B = 'b'

%rules
<s> : <x> B | <y> B ;
<x> : A ;
<y> : A ;
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
IF    = 'if'
THEN  = 'then'
ELSE  = 'else'
S     = 's'

%rules
<stmt> : IF <cond> THEN <stmt>
     | IF <cond> THEN <stmt> ELSE <stmt>
     | S
     ;
<cond> : S ;
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
    src = "%grammar tiny\n%tokens\nA = 'a'\n%rules\n<s> : A ;\n"
    table = build(src)
    # Augmented start + initial closure + after-shift + accept reduction... 4 states tops.
    assert len(table.states) <= 4
    assert table.conflicts == []


_DANGLING_ELSE = """
%grammar amb
%tokens
IF    = 'if'
THEN  = 'then'
ELSE  = 'else'
S     = 's'

%rules
<stmt> : IF <cond> THEN <stmt>
     | IF <cond> THEN <stmt> ELSE <stmt>
     | S
     ;
<cond> : S ;
"""


def test_shift_directive_resolves_dangling_else_in_favor_of_shift():
    # Without %shift the dangling-else grammar produces one s/r conflict on ELSE.
    # With %shift ELSE the conflict is silently resolved by preferring shift —
    # the canonical yacc-style fix.
    baseline = build(_DANGLING_ELSE)
    assert any(c.terminal == "ELSE" for c in baseline.conflicts), \
        "sanity check: baseline grammar should have the dangling-else conflict"

    src_with_shift = _DANGLING_ELSE + "\n%shift ELSE\n"
    table = build(src_with_shift)
    assert table.conflicts == []

    # In the state that previously had the conflict, the resolution is shift
    # (not reduce). Find a state that has the ELSE conflict in baseline and
    # check the post-resolution table records a shift there.
    baseline_conflict_state = next(
        c.state for c in baseline.conflicts if c.terminal == "ELSE"
    )
    resolved = table.action.get((baseline_conflict_state, "ELSE"))
    assert isinstance(resolved, ShiftAction), \
        f"expected shift on ELSE at state {baseline_conflict_state}, got {resolved!r}"


def test_shift_directive_only_silences_listed_terminals():
    # If we mark a *different* terminal as %shift, the dangling-else conflict
    # on ELSE is still reported.
    src = _DANGLING_ELSE + "\n%shift IF\n"
    table = build(src)
    assert any(c.terminal == "ELSE" for c in table.conflicts)


def test_shift_directive_does_not_resolve_reduce_reduce():
    # Reduce/reduce conflicts are NOT covered by %shift — only s/r are.
    src = """
%grammar rr
%tokens
A = 'a'
B = 'b'

%rules
<s> : <x> B | <y> B ;
<x> : A ;
<y> : A ;
%shift A
%shift B
"""
    table = build(src)
    rr = [c for c in table.conflicts if c.kind() == "reduce/reduce"]
    assert rr, "expected r/r conflict to survive %shift"


def test_shift_directive_validates_terminal_name():
    # Listing an undeclared name in %shift must error.
    import pytest
    from uplox.parse.grammar import GrammarError
    src = _DANGLING_ELSE + "\n%shift NOT_A_TERMINAL\n"
    with pytest.raises(GrammarError, match="NOT_A_TERMINAL"):
        build(src)


def test_shift_directive_resolves_keyword_alias():
    # %shift accepts the bare keyword spelling (which the keyword_prefix
    # synthesises into a prefixed token name); the resolution happens at
    # compile_grammar time.
    src = """
%grammar amb_kw
%keyword_prefix KW_
%keywords
if then else

%tokens
S = 's'

%rules
<stmt> : 'if' <cond> 'then' <stmt>
     | 'if' <cond> 'then' <stmt> 'else' <stmt>
     | S
     ;
<cond> : S ;
%shift else
"""
    table = build(src)
    assert table.conflicts == []


def test_reduce_directive_resolves_dangling_else_in_favor_of_reduce():
    # %reduce is the dual of %shift: it silently resolves shift/reduce
    # in favour of reduce. Demonstrate on the same dangling-else grammar.
    baseline = build(_DANGLING_ELSE)
    assert any(c.terminal == "ELSE" for c in baseline.conflicts), \
        "sanity: baseline grammar should have the dangling-else conflict"

    src_with_reduce = _DANGLING_ELSE + "\n%reduce ELSE\n"
    table = build(src_with_reduce)
    assert table.conflicts == []

    baseline_conflict_state = next(
        c.state for c in baseline.conflicts if c.terminal == "ELSE"
    )
    resolved = table.action.get((baseline_conflict_state, "ELSE"))
    assert isinstance(resolved, ReduceAction), \
        f"expected reduce on ELSE at state {baseline_conflict_state}, got {resolved!r}"


def test_reduce_directive_only_silences_listed_terminals():
    src = _DANGLING_ELSE + "\n%reduce IF\n"
    table = build(src)
    assert any(c.terminal == "ELSE" for c in table.conflicts)


def test_reduce_directive_does_not_resolve_reduce_reduce():
    src = """
%grammar rr
%tokens
A = 'a'
B = 'b'

%rules
<s> : <x> B | <y> B ;
<x> : A ;
<y> : A ;
%reduce A
%reduce B
"""
    table = build(src)
    rr = [c for c in table.conflicts if c.kind() == "reduce/reduce"]
    assert rr, "expected r/r conflict to survive %reduce"


def test_reduce_directive_validates_terminal_name():
    import pytest
    from uplox.parse.grammar import GrammarError
    src = _DANGLING_ELSE + "\n%reduce NOT_A_TERMINAL\n"
    with pytest.raises(GrammarError, match="NOT_A_TERMINAL"):
        build(src)


def test_reduce_directive_resolves_keyword_alias():
    src = """
%grammar amb_kw_red
%keyword_prefix KW_
%keywords
if then else

%tokens
S = 's'

%rules
<stmt> : 'if' <cond> 'then' <stmt>
     | 'if' <cond> 'then' <stmt> 'else' <stmt>
     | S
     ;
<cond> : S ;
%reduce else
"""
    table = build(src)
    assert table.conflicts == []


def test_shift_and_reduce_on_same_terminal_is_rejected():
    # A terminal can't be both %shift and %reduce — that would be
    # contradictory. Either the spec reader (same-name string) or the
    # grammar compiler (after keyword-alias resolution) must reject it.
    import pytest
    from uplox.parse.grammar import GrammarError
    from uplox.spec.reader import ReaderError
    src = _DANGLING_ELSE + "\n%shift ELSE\n%reduce ELSE\n"
    with pytest.raises((ReaderError, GrammarError), match="ELSE"):
        build(src)
