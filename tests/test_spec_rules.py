"""Rules-section parser tests."""

from __future__ import annotations

import pytest

from uplox.spec.reader import ReaderError, read_source


CALC = """
%grammar calc

%tokens
NUMBER = /[0-9]+/
PLUS   = '+'
MINUS  = '-'
STAR   = '*'
SLASH  = '/'

%rules
<expr>  : <expr> PLUS <term>     %hook=on_add  { $$ = $1 + $3; }
        | <expr> MINUS <term>                  { $$ = $1 - $3; }
        | <term>                               ;

<term>  : <term> STAR <factor>                 { $$ = $1 * $3; }
        | <term> SLASH <factor>                { $$ = $1 / $3; }
        | <factor>                             ;

<factor> : NUMBER                              { $$ = $1; }
         ;
"""


def test_basic_rule_shape():
    ir = read_source(CALC, "calc.uplox")
    rules = {r.name: r for r in ir.rules}
    assert set(rules) == {"expr", "term", "factor"}
    assert ir.start_symbol == "expr"  # default = first rule
    expr = rules["expr"]
    assert len(expr.productions) == 3
    rhs0 = [(s.kind, s.name) for s in expr.productions[0].rhs]
    assert rhs0 == [("nonterm", "expr"), ("term", "PLUS"), ("nonterm", "term")]


def test_action_text_preserved_with_braces():
    ir = read_source(
        "%grammar g\n"
        "%tokens\nA = 'a'\n"
        "%rules\n"
        "<x> : A { if (1) { return 0; } } ;\n"
    )
    [rule] = ir.rules
    [prod] = rule.productions
    assert prod.action == "if (1) { return 0; }"


def test_hook_attached_to_production():
    ir = read_source(CALC, "calc.uplox")
    expr = next(r for r in ir.rules if r.name == "expr")
    assert expr.productions[0].hook == "on_add"
    assert expr.productions[1].hook is None


def test_string_literal_in_rule_becomes_symbol():
    src = (
        "%grammar g\n"
        "%tokens\nID = /[a-z]+/\nPLUS = '+'\n"
        "%rules\n"
        "<x> : ID '+' ID ;\n"
    )
    ir = read_source(src)
    [rule] = ir.rules
    [prod] = rule.productions
    assert [(s.kind, s.name) for s in prod.rhs] == [
        ("term", "ID"),
        ("literal", "+"),
        ("term", "ID"),
    ]


def test_empty_production_via_pipe():
    src = (
        "%grammar g\n"
        "%tokens\nA = 'a'\n"
        "%rules\n"
        "<x> : A | ;\n"
    )
    ir = read_source(src)
    [rule] = ir.rules
    assert len(rule.productions) == 2
    assert [(s.kind, s.name) for s in rule.productions[0].rhs] == [("term", "A")]
    assert rule.productions[1].rhs == []


def test_explicit_start_overrides_first_rule():
    src = (
        "%grammar g\n"
        "%options\nstart = b\n"
        "%tokens\nT = 't'\n"
        "%rules\n"
        "<a> : T ;\n"
        "<b> : <a> ;\n"
    )
    ir = read_source(src)
    assert ir.start_symbol == "b"


def test_missing_colon():
    with pytest.raises(ReaderError, match="expected ':'"):
        read_source("%grammar g\n%tokens\nA = 'a'\n%rules\n<x> A ;\n")


def test_missing_semicolon():
    with pytest.raises(ReaderError, match="expected '\\|' or ';'"):
        read_source("%grammar g\n%tokens\nA = 'a'\n%rules\n<x> : A\n")


def test_unterminated_action():
    with pytest.raises(ReaderError, match="unterminated action"):
        read_source("%grammar g\n%tokens\nA = 'a'\n%rules\n<x> : A { unterminated\n")


def test_unknown_rule_directive():
    with pytest.raises(ReaderError, match="%hook"):
        read_source(
            "%grammar g\n%tokens\nA = 'a'\n"
            "%rules\n<x> : A %prec=plus ;\n"
        )


def test_position_points_into_rules_section():
    src = (
        "%grammar g\n"
        "%tokens\nA = 'a'\n"
        "%rules\n"
        "<x> : A ;\n"  # line 5
        "<y> : ! ;\n"  # line 6 - syntax error
    )
    with pytest.raises(ReaderError) as exc:
        read_source(src, "test.uplox")
    msg = str(exc.value)
    assert "test.uplox:6:" in msg
