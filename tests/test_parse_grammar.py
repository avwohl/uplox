"""compile_grammar() and FIRST/FOLLOW correctness."""

from __future__ import annotations

import pytest

from plox.parse.grammar import (
    AUGMENTED_START,
    END_MARKER,
    EPSILON,
    GrammarError,
    compile_grammar,
)
from plox.spec.reader import read_source


CALC = """
%grammar calc

%tokens
NUMBER = /[0-9]+/
PLUS   = '+'
MINUS  = '-'
STAR   = '*'
SLASH  = '/'
LPAREN = '('
RPAREN = ')'
WS     = /[ \\t\\n]+/   %skip

%rules
<expr>  : <expr> PLUS <term>
      | <expr> MINUS <term>
      | <term>
      ;

<term>  : <term> STAR <factor>
      | <term> SLASH <factor>
      | <factor>
      ;

<factor> : NUMBER
       | LPAREN <expr> RPAREN
       ;
"""


def test_augmented_start_is_production_zero():
    g = compile_grammar(read_source(CALC))
    assert g.productions[0].lhs == AUGMENTED_START
    assert g.productions[0].rhs == ("expr",)


def test_terminals_and_non_terminals_disjoint():
    g = compile_grammar(read_source(CALC))
    assert g.terminals & g.non_terminals == set()
    assert "NUMBER" in g.terminals
    assert "expr" in g.non_terminals
    assert END_MARKER in g.terminals


def test_string_literal_resolves_to_token():
    src = (
        "%grammar g\n"
        "%tokens\n"
        "NUMBER = /[0-9]+/\n"
        "PLUS   = '+'\n"
        "%rules\n"
        "<sum> : NUMBER '+' NUMBER ;\n"
    )
    g = compile_grammar(read_source(src))
    [augmented, sum_prod] = g.productions
    assert sum_prod.rhs == ("NUMBER", "PLUS", "NUMBER")


def test_undeclared_literal_rejected():
    src = (
        "%grammar g\n"
        "%tokens\n"
        "NUMBER = /[0-9]+/\n"
        "%rules\n"
        "<sum> : NUMBER '?' ;\n"
    )
    with pytest.raises(GrammarError, match="not declared"):
        compile_grammar(read_source(src))


def test_undefined_symbol_rejected():
    src = (
        "%grammar g\n"
        "%tokens\nA = 'a'\n"
        "%rules\n"
        "<x> : MISSING_TOK ;\n"
    )
    with pytest.raises(GrammarError, match="not a declared token or keyword"):
        compile_grammar(read_source(src))


def test_first_of_terminals():
    g = compile_grammar(read_source(CALC))
    assert g.first_sets["NUMBER"] == {"NUMBER"}


def test_first_of_calc_non_terminals():
    g = compile_grammar(read_source(CALC))
    assert g.first_sets["expr"] == {"NUMBER", "LPAREN"}
    assert g.first_sets["term"] == {"NUMBER", "LPAREN"}
    assert g.first_sets["factor"] == {"NUMBER", "LPAREN"}


def test_follow_includes_end_marker_for_start():
    g = compile_grammar(read_source(CALC))
    assert END_MARKER in g.follow_sets["expr"]


def test_follow_of_factor():
    g = compile_grammar(read_source(CALC))
    # factor appears at end of term productions and after STAR/SLASH.
    follow = g.follow_sets["factor"]
    assert {"PLUS", "MINUS", "STAR", "SLASH", END_MARKER, "RPAREN"} <= follow


def test_epsilon_in_first_when_production_is_empty():
    src = (
        "%grammar g\n"
        "%tokens\nA = 'a'\n"
        "%rules\n"
        "<x> : A | ;\n"  # empty alternative
    )
    g = compile_grammar(read_source(src))
    assert EPSILON in g.first_sets["x"]
    assert "A" in g.first_sets["x"]


def test_first_through_nullable_prefix():
    src = (
        "%grammar g\n"
        "%tokens\nA = 'a'\nB = 'b'\n"
        "%rules\n"
        "<s> : <opt> B ;\n"
        "<opt> : A | ;\n"
    )
    g = compile_grammar(read_source(src))
    # FIRST(s) should include both A (from non-empty opt) and B (when opt is empty)
    assert g.first_sets["s"] == {"A", "B"}


def test_productions_indexed_in_order():
    g = compile_grammar(read_source(CALC))
    for i, p in enumerate(g.productions):
        assert p.index == i
