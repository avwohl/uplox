"""Bootstrap reader tests for the v0 ``.plox`` DSL."""

from __future__ import annotations

import pytest

from plox.spec.reader import ReaderError, read_source

CALC_SRC = """
%grammar calc

%options
start = expr

%tokens
NUMBER = /[0-9]+/
PLUS   = "+"
MINUS  = "-"
WS     = /[ \\t\\n]+/    %skip

%hooks
log_token  pre_shift
recover    on_error

%rules
expr : expr PLUS NUMBER | NUMBER ;
"""


def test_basic_parse():
    ir = read_source(CALC_SRC, "calc.plox")
    assert ir.name == "calc"
    assert ir.start_symbol == "expr"
    assert ir.options["start"] == "expr"
    assert [t.name for t in ir.tokens] == ["NUMBER", "PLUS", "MINUS", "WS"]
    assert ir.tokens[0].pattern == "[0-9]+"
    assert ir.tokens[1].literal == "+"
    assert ir.tokens[3].skip is True
    assert {(h.name, h.when) for h in ir.hooks} == {
        ("log_token", "pre_shift"),
        ("recover", "on_error"),
    }
    assert "expr" in ir.options["__rules_text__"]


def test_comments_and_blank_lines_ignored():
    src = """
    # leading comment
    %grammar c1
    # block comment in the middle

    %tokens
    A = "a"   # trailing comment
    """
    ir = read_source(src)
    assert ir.name == "c1"
    assert [t.name for t in ir.tokens] == ["A"]


def test_missing_grammar_directive():
    with pytest.raises(ReaderError, match="expected `%grammar"):
        read_source("%tokens\nA = \"a\"\n")


def test_duplicate_grammar_directive():
    with pytest.raises(ReaderError, match="duplicate"):
        read_source("%grammar a\n%grammar b\n")


def test_unknown_option_rejected():
    with pytest.raises(ReaderError, match="unknown option"):
        read_source("%grammar g\n%options\nfooble = bar\n")


def test_malformed_token():
    with pytest.raises(ReaderError, match="malformed"):
        read_source("%grammar g\n%tokens\nFOO = 123\n")


def test_unknown_hook_when():
    with pytest.raises(ReaderError, match="unknown hook event"):
        read_source("%grammar g\n%hooks\npush postreduce\n")


def test_hash_inside_regex_is_not_a_comment():
    src = """%grammar g
%tokens
HASH = /#/
"""
    ir = read_source(src)
    assert ir.tokens[0].pattern == "#"


def test_string_escape_in_literal():
    src = """%grammar g
%tokens
TAB = "\\t"
"""
    ir = read_source(src)
    assert ir.tokens[0].literal == "\t"


def test_lex_pipeline_from_ir():
    """Reader output feeds the lexer end-to-end."""
    from plox.lex.build import lex_from_ir
    from plox.lex.scanner import Scanner

    ir = read_source(CALC_SRC, "calc.plox")
    dfa, tokens, skip = lex_from_ir(ir)
    assert tokens == ["NUMBER", "PLUS", "MINUS", "WS"]
    assert skip == ["WS"]

    sc = Scanner(dfa=dfa, skip_tokens=frozenset(skip))
    toks = sc.scan_all("12 + 34 - 5")
    assert [t.name for t in toks] == ["NUMBER", "PLUS", "NUMBER", "MINUS", "NUMBER"]
    assert [t.text for t in toks] == ["12", "+", "34", "-", "5"]
