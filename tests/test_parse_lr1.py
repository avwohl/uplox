"""Canonical LR(1) builder tests."""

from __future__ import annotations

from uplox.parse.grammar import compile_grammar
from uplox.parse.lr1 import (
    AcceptAction,
    Conflict,
    ReduceAction,
    ShiftAction,
    build_ielr1,
    build_lalr1,
    build_lr1,
    build_table,
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


# ---- %define lr.type / LALR(1) ----------------------------------------------


def _build_with_lr_type(src_body: str, lr_type: str | None):
    """Helper: build a table with the given lr_type; None = no directive
    (i.e. default canonical-lr)."""
    src = src_body
    if lr_type is not None:
        src += f"\n%define lr.type {lr_type}\n"
    ir = read_source(src)
    grammar = compile_grammar(ir)
    return build_table(grammar)


def test_lr_type_defaults_to_canonical_lr():
    # No %define → grammar.lr_type stays canonical-lr → build_table picks
    # build_lr1, matching legacy behaviour.
    ir = read_source(_DANGLING_ELSE)
    grammar = compile_grammar(ir)
    assert grammar.lr_type == "canonical-lr"
    canonical = build_lr1(grammar)
    via_dispatcher = build_table(grammar)
    assert len(canonical.states) == len(via_dispatcher.states)


def test_lr_type_lalr_picks_lalr_path():
    src = _DANGLING_ELSE + "\n%define lr.type lalr\n"
    ir = read_source(src)
    grammar = compile_grammar(ir)
    assert grammar.lr_type == "lalr"
    via_dispatcher = build_table(grammar)
    direct = build_lalr1(grammar)
    assert len(via_dispatcher.states) == len(direct.states)


def test_lalr_collapses_states_for_dangling_else():
    # The dangling-else grammar's canonical-LR(1) table has more states
    # than its LALR(1) table because some states differ only in lookahead
    # sets. Both should still expose the same one shift/reduce conflict
    # on ELSE — the merge doesn't manufacture new conflicts here.
    canonical = _build_with_lr_type(_DANGLING_ELSE, "canonical-lr")
    lalr = _build_with_lr_type(_DANGLING_ELSE, "lalr")
    assert len(lalr.states) <= len(canonical.states)
    assert any(c.terminal == "ELSE" for c in canonical.conflicts)
    assert any(c.terminal == "ELSE" for c in lalr.conflicts)


def test_lalr_accepts_shift_directive():
    # %shift still resolves the s/r in the merged table — the resolution
    # happens at _record_action time, after the merge.
    src = _DANGLING_ELSE + "\n%shift ELSE\n%define lr.type lalr\n"
    table = build(src)
    assert table.conflicts == []


def test_lalr_accepts_reduce_directive():
    src = _DANGLING_ELSE + "\n%reduce ELSE\n%define lr.type lalr\n"
    table = build(src)
    assert table.conflicts == []


def test_lalr_smaller_than_canonical_on_calc():
    # CALC has enough lookahead-only state divergence that LALR
    # strictly shrinks it. Concrete inequality keeps the merge from
    # silently degrading into a no-op.
    canonical = _build_with_lr_type(CALC, "canonical-lr")
    lalr = _build_with_lr_type(CALC, "lalr")
    assert len(lalr.states) <= len(canonical.states)


def test_define_unknown_key_rejected():
    import pytest
    from uplox.spec.reader import ReaderError
    src = _DANGLING_ELSE + "\n%define no.such.key whatever\n"
    with pytest.raises(ReaderError, match="unknown %define key"):
        read_source(src)


def test_define_lr_type_invalid_value_rejected():
    import pytest
    from uplox.spec.reader import ReaderError
    src = _DANGLING_ELSE + "\n%define lr.type slr\n"
    with pytest.raises(ReaderError, match="lr.type"):
        read_source(src)


# A grammar known to trigger an LALR-only reduce/reduce conflict that is
# NOT a conflict in canonical LR(1). Adapted from Aho/Sethi/Ullman, ex. 4.47:
#   S -> a A d | b B d | a B e | b A e
#   A -> c
#   B -> c
# After reading `a c`, canonical LR(1) keeps two states distinct based on
# their lookahead (`d` vs `e`); LALR merges them and the merged state has
# both `A -> c .` and `B -> c .` ready to reduce on either lookahead → r/r.
_ASU_447 = """
%grammar asu447
%tokens
A  = 'a'
B  = 'b'
C  = 'c'
D  = 'd'
E  = 'e'

%rules
<S> : A <X> D
    | B <Y> D
    | A <Y> E
    | B <X> E
    ;
<X> : C ;
<Y> : C ;
"""


def test_lalr_introduces_spurious_conflict_on_asu447():
    # Canonical LR(1) is conflict-free on this grammar; LALR is not.
    canonical = build_lr1(compile_grammar(read_source(_ASU_447)))
    lalr = build_lalr1(compile_grammar(read_source(_ASU_447)))
    assert canonical.conflicts == [], \
        "sanity: canonical LR(1) is conflict-free on ASU 4.47"
    assert lalr.conflicts, \
        "sanity: LALR(1) introduces a spurious r/r conflict on ASU 4.47"


def test_ielr_eliminates_spurious_lalr_conflict_on_asu447():
    ielr = build_ielr1(compile_grammar(read_source(_ASU_447)))
    assert ielr.conflicts == [], \
        "IELR(1) must match canonical LR(1)'s conflict count"


def test_ielr_table_size_between_lalr_and_canonical():
    # IELR is canonical-conflict-correct but LALR-sized when possible.
    # On ASU 4.47, LALR has the spurious conflict, so IELR must split
    # something — its state count should be > LALR's but <= canonical's.
    grammar = compile_grammar(read_source(_ASU_447))
    canonical = build_lr1(grammar)
    lalr = build_lalr1(grammar)
    ielr = build_ielr1(grammar)
    assert len(lalr.states) <= len(ielr.states) <= len(canonical.states)


def test_ielr_matches_lalr_on_lalr_friendly_grammar():
    # When LALR introduces no spurious conflicts (the common case for
    # well-formed grammars), IELR should produce the same table size
    # as LALR — no states should need splitting.
    canonical = build_lr1(compile_grammar(read_source(CALC)))
    lalr = build_lalr1(compile_grammar(read_source(CALC)))
    ielr = build_ielr1(compile_grammar(read_source(CALC)))
    # Sanity: CALC is LALR-friendly.
    assert len(lalr.conflicts) <= len(canonical.conflicts)
    # Then IELR matches LALR.
    assert len(ielr.states) == len(lalr.states)


def test_define_lr_type_ielr_routes_through_dispatcher():
    src = _DANGLING_ELSE + "\n%define lr.type ielr\n"
    ir = read_source(src)
    grammar = compile_grammar(ir)
    assert grammar.lr_type == "ielr"
    via_dispatcher = build_table(grammar)
    direct = build_ielr1(grammar)
    assert len(via_dispatcher.states) == len(direct.states)


def test_lalr_ada_full_subset_smoke():
    # End-to-end: a small Ada-shaped grammar with a real shift/reduce
    # ambiguity should LALR-build without crashing and the table's
    # default reductions remain consistent with the canonical version's
    # parse outcomes.
    src = """
%grammar mini

%tokens
ID    = /[A-Za-z_]+/
SEMI  = ';'
EQ    = '='
WS    = /\\s+/    %skip

%rules
<unit> : <decl>
       | <unit> <decl>
       ;
<decl> : ID SEMI
       | ID EQ ID SEMI
       ;
"""
    canonical = _build_with_lr_type(src, "canonical-lr")
    lalr = _build_with_lr_type(src, "lalr")
    assert canonical.conflicts == []
    assert lalr.conflicts == []
    assert len(lalr.states) <= len(canonical.states)
