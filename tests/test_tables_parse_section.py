"""Parse-table JSON round-trip and schema-fit tests."""

from __future__ import annotations

import json

import pytest

from plox.parse.grammar import compile_grammar
from plox.parse.lr1 import (
    AcceptAction,
    ReduceAction,
    ShiftAction,
    build_lr1,
)
from plox.spec.reader import read_source
from plox.tables import (
    dump_bundle,
    empty_bundle,
    table_from_json,
    table_to_json,
)


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


def build_calc_table():
    return build_lr1(compile_grammar(read_source(CALC)))


def test_round_trip_preserves_actions():
    table = build_calc_table()
    section = table_to_json(table)
    table2 = table_from_json(section)
    assert table.action.keys() == table2.action.keys()
    for k in table.action:
        a, b = table.action[k], table2.action[k]
        assert type(a) is type(b)
        if isinstance(a, ShiftAction):
            assert a.state == b.state
        elif isinstance(a, ReduceAction):
            assert a.production == b.production
        elif isinstance(a, AcceptAction):
            pass


def test_round_trip_preserves_gotos():
    table = build_calc_table()
    section = table_to_json(table)
    table2 = table_from_json(section)
    assert table.goto == table2.goto


def test_round_trip_preserves_productions():
    table = build_calc_table()
    section = table_to_json(table)
    table2 = table_from_json(section)
    assert len(table.grammar.productions) == len(table2.grammar.productions)
    for a, b in zip(table.grammar.productions, table2.grammar.productions):
        assert a.lhs == b.lhs
        assert a.rhs == b.rhs


def test_section_fits_in_bundle():
    table = build_calc_table()
    bundle = empty_bundle("calc")
    bundle["parse"] = table_to_json(table)
    text = dump_bundle(bundle)
    parsed = json.loads(text)
    assert parsed["parse"]["kind"] == "lr1"
    assert parsed["parse"]["start_symbol"] == "expr"


def test_refuses_to_serialise_with_conflicts():
    src = """
%grammar amb
%tokens
IF = "if"
THEN = "then"
ELSE = "else"
S = "s"
%rules
stmt : IF cond THEN stmt
     | IF cond THEN stmt ELSE stmt
     | S
     ;
cond : S ;
"""
    table = build_lr1(compile_grammar(read_source(src)))
    assert table.conflicts
    with pytest.raises(ValueError, match="conflict"):
        table_to_json(table)


def test_action_codes_compact_and_unique():
    table = build_calc_table()
    section = table_to_json(table)
    seen_codes: set[str] = set()
    for state in section["states"]:
        for code in state["actions"].values():
            assert code == "acc" or code[0] in "sr"
            seen_codes.add(code[0])
    # Expect at least shift and reduce; accept lives only in the final state.
    assert "s" in seen_codes
    assert "r" in seen_codes


def test_round_trip_preserves_default_reductions():
    """Default reductions are essential for lexer-feedback grammars (e.g.
    the C typedef-name hack). They must survive the JSON round-trip; older
    bundles without the field get them recomputed at load time."""
    table = build_calc_table()
    assert table.default_reductions, "calc grammar should have some default reductions"
    section = table_to_json(table)
    table2 = table_from_json(section)
    assert table2.default_reductions == table.default_reductions


def test_default_reductions_recomputed_when_field_missing():
    """Backward compat: an older bundle without ``default_reduction`` keys
    still ends up with the same default-reductions map after load."""
    table = build_calc_table()
    section = table_to_json(table)
    for state in section["states"]:
        state.pop("default_reduction", None)
    table2 = table_from_json(section)
    assert table2.default_reductions == table.default_reductions
