"""Bootstrap reader tests for the ``.uplox`` DSL."""

from __future__ import annotations

import pytest

from uplox.spec.reader import ReaderError, read_source

CALC_SRC = """
%grammar calc

%options
start = expr

%tokens
NUMBER = /[0-9]+/
PLUS   = '+'
MINUS  = '-'
WS     = /[ \\t\\n]+/    %skip

%hooks
log_token  pre_shift
recover    on_error

%rules
<expr> : <expr> PLUS NUMBER | NUMBER ;
"""


def test_basic_parse():
    ir = read_source(CALC_SRC, "calc.uplox")
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
    A = 'a'   # trailing comment
    """
    ir = read_source(src)
    assert ir.name == "c1"
    assert [t.name for t in ir.tokens] == ["A"]


def test_missing_grammar_directive():
    with pytest.raises(ReaderError, match="expected `%grammar"):
        read_source("%tokens\nA = 'a'\n")


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
TAB = '\\t'
"""
    ir = read_source(src)
    assert ir.tokens[0].literal == "\t"


# ---- v3 AST-annotation surface ----------------------------------------------


_AST_SRC = """
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

%ast_drop
LPAREN RPAREN

%rules
<expr>?   : <expr>@lhs '+'@op <term>@rhs   %ast=BinOp
          | <expr>@lhs '-'@op <term>@rhs   %ast=BinOp
          | <term>
          ;

<term>?   : <term>@lhs '*'@op <factor>@rhs %ast=BinOp
          | <factor>
          ;

<factor>? : NUMBER@value                   %ast=NumLit
          | '(' <expr> ')'
          ;
"""


def test_ast_drop_section():
    ir = read_source(_AST_SRC, "calc.uplox")
    assert ir.ast_drop_tokens == {"LPAREN", "RPAREN"}


def test_ast_lift_marker_on_lhs():
    ir = read_source(_AST_SRC, "calc.uplox")
    by_name = {r.name: r for r in ir.rules}
    assert by_name["expr"].ast_lift is True
    assert by_name["term"].ast_lift is True
    assert by_name["factor"].ast_lift is True


def test_ast_kind_per_alternative():
    ir = read_source(_AST_SRC, "calc.uplox")
    by_name = {r.name: r for r in ir.rules}
    expr = by_name["expr"]
    assert [p.ast_kind for p in expr.productions] == ["BinOp", "BinOp", None]
    factor = by_name["factor"]
    assert [p.ast_kind for p in factor.productions] == ["NumLit", None]


def test_field_suffix_on_rhs_symbols():
    ir = read_source(_AST_SRC, "calc.uplox")
    by_name = {r.name: r for r in ir.rules}
    binop = by_name["expr"].productions[0]
    assert [(s.name, s.kind, s.field_name) for s in binop.rhs] == [
        ("expr", "nonterm", "lhs"),
        ("+", "literal", "op"),
        ("term", "nonterm", "rhs"),
    ]
    numlit = by_name["factor"].productions[0]
    assert numlit.rhs[0].field_name == "value"
    # Bare reference without @ has no field name.
    plain = by_name["expr"].productions[2].rhs[0]
    assert plain.field_name is None


def test_list_rule_lhs_annotation():
    src = """
%grammar t
%tokens
IDENT = /[A-Za-z_][A-Za-z0-9_]*/
COMMA = ','
WS    = /[ \\t\\n]+/   %skip
%rules
<args> %ast=list element=<expr>
       : <expr>
       | <args> COMMA <expr>
       ;
<expr>?: IDENT@name   %ast=Ident ;
"""
    ir = read_source(src)
    args = next(r for r in ir.rules if r.name == "args")
    assert args.ast_list_element == "expr"
    assert args.ast_lift is False


def test_ast_unwrap_reserved_kind_parses():
    """Reader accepts %ast=_unwrap; the semantic check that the production
    has exactly one @field-annotated RHS is the AST plan compiler's job."""
    src = """
%grammar t
%tokens
IDENT = /[A-Za-z_][A-Za-z0-9_]*/
COLON = ':'
WS    = /[ \\t\\n]+/   %skip
%rules
<type_opt> : COLON IDENT@name   %ast=_unwrap
           |
           ;
"""
    ir = read_source(src)
    p = ir.rules[0].productions[0]
    assert p.ast_kind == "_unwrap"


def test_duplicate_ast_on_alt_rejected():
    src = """
%grammar t
%tokens
IDENT = /[A-Za-z_][A-Za-z0-9_]*/
WS    = /[ \\t\\n]+/   %skip
%rules
<r> : IDENT@name   %ast=A %ast=B ;
"""
    with pytest.raises(ReaderError, match="duplicate %ast"):
        read_source(src)


def test_at_without_identifier_rejected():
    src = """
%grammar t
%tokens
IDENT = /[A-Za-z_][A-Za-z0-9_]*/
WS    = /[ \\t\\n]+/   %skip
%rules
<r> : IDENT@   %ast=R ;
"""
    with pytest.raises(ReaderError, match="expected identifier after '@'"):
        read_source(src)


def test_lhs_ast_rejects_non_list_kind():
    src = """
%grammar t
%tokens
IDENT = /[A-Za-z_][A-Za-z0-9_]*/
WS    = /[ \\t\\n]+/   %skip
%rules
<r> %ast=Foo : IDENT@name %ast=Foo ;
"""
    with pytest.raises(ReaderError, match="only accepts %ast=list"):
        read_source(src)


def test_ast_drop_rejects_non_identifier():
    src = """
%grammar t
%tokens
WS = /[ \\t\\n]+/   %skip
%ast_drop
+
%rules
<r> : WS ;
"""
    with pytest.raises(ReaderError, match="not a valid identifier"):
        read_source(src)


def test_grammar_without_ast_annotations_has_empty_ast_state():
    """Pure back-compat — a grammar with no v3 surface gets defaults."""
    ir = read_source(CALC_SRC, "calc.uplox")
    assert ir.ast_drop_tokens == set()
    for r in ir.rules:
        assert r.ast_lift is False
        assert r.ast_list_element is None
        for p in r.productions:
            assert p.ast_kind is None
            for s in p.rhs:
                assert s.field_name is None


def test_lex_pipeline_from_ir():
    """Reader output feeds the lexer end-to-end."""
    from uplox.lex.build import lex_from_ir
    from uplox.lex.scanner import Scanner

    ir = read_source(CALC_SRC, "calc.uplox")
    dfa, tokens, skip = lex_from_ir(ir)
    assert tokens == ["NUMBER", "PLUS", "MINUS", "WS"]
    assert skip == ["WS"]

    sc = Scanner(dfa=dfa, skip_tokens=frozenset(skip))
    toks = sc.scan_all("12 + 34 - 5")
    assert [t.name for t in toks] == ["NUMBER", "PLUS", "NUMBER", "MINUS", "NUMBER"]
    assert [t.text for t in toks] == ["12", "+", "34", "-", "5"]
