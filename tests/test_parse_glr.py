"""GLR runtime tests.

Two angles:

* On unambiguous grammars (calc), GLR must produce the same single tree the
  LR runtime would. A regression on this would mean GLR isn't a strict
  superset.
* On classically ambiguous grammars (e+e, dangling-else without the matched/
  unmatched split) GLR must succeed where LR refuses to build, and the
  resulting forest must contain *all* valid derivations.
"""

from __future__ import annotations

import pytest

from plox.lex.build import lex_from_ir
from plox.lex.scanner import Scanner, Token
from plox.parse.glr import GLRParseError, glr_from_lr, glr_parse
from plox.parse.glr.runtime import AmbiguityNode, GLRNode
from plox.parse.grammar import compile_grammar
from plox.parse.lr1 import build_lr1
from plox.parse.runtime import parse as lr_parse
from plox.spec.reader import read_source


# ---- unambiguous regression: calc.

CALC = """
%grammar calc

%tokens
NUMBER = /[0-9]+/
PLUS   = "+"
STAR   = "*"
LPAREN = "("
RPAREN = ")"
WS     = /[ \\t\\n]+/   %skip

%rules
expr  : expr PLUS term | term ;
term  : term STAR factor | factor ;
factor : NUMBER | LPAREN expr RPAREN ;
"""


def calc_pipeline_glr():
    ir = read_source(CALC)
    dfa, _t, skip = lex_from_ir(ir)
    scanner = Scanner(dfa=dfa, skip_tokens=frozenset(skip))
    table = build_lr1(compile_grammar(ir))
    return scanner, glr_from_lr(table), table


def test_glr_on_unambiguous_grammar_produces_single_tree():
    scanner, glr_table, _lr_table = calc_pipeline_glr()
    forest = glr_parse(glr_table, scanner.scan("1 + 2 * 3"))
    assert isinstance(forest, GLRNode)
    assert forest.kind == "expr"


def test_glr_matches_lr_shape_on_calc():
    scanner, glr_table, lr_table = calc_pipeline_glr()
    glr_forest = glr_parse(glr_table, scanner.scan("(1 + 2) * 3"))
    lr_tree = lr_parse(lr_table, scanner.scan("(1 + 2) * 3"))
    # The kinds and structural shapes must match — same productions used,
    # same RHS counts at each level.
    def shape(node) -> object:
        from plox.parse.runtime import ParseNode

        if isinstance(node, Token):
            return ("T", node.name)
        if isinstance(node, GLRNode):
            return (node.kind, tuple(shape(c) for c in node.children))
        if isinstance(node, ParseNode):
            return (node.kind, tuple(shape(c) for c in node.children))
        if isinstance(node, AmbiguityNode):
            return ("AMB", tuple(shape(a) for a in node.alternatives))
        return repr(node)

    assert shape(glr_forest) == shape(lr_tree)


# ---- ambiguous grammar: e+e (no precedence)

AMBIG = """
%grammar ambig
%tokens
NUMBER = /[0-9]+/
PLUS   = "+"
STAR   = "*"
WS     = /[ \\t\\n]+/  %skip
%rules
e : e PLUS e | e STAR e | NUMBER ;
"""


def ambig_pipeline_glr():
    ir = read_source(AMBIG)
    dfa, _t, skip = lex_from_ir(ir)
    scanner = Scanner(dfa=dfa, skip_tokens=frozenset(skip))
    table = build_lr1(compile_grammar(ir))
    return scanner, glr_from_lr(table), table


def test_lr_refuses_ambiguous_grammar():
    _scanner, _glr_table, lr_table = ambig_pipeline_glr()
    # The plain ambiguous e+e grammar must produce conflicts at the LR layer.
    assert lr_table.conflicts != []


def test_glr_parses_ambiguous_input():
    scanner, glr_table, _lr_table = ambig_pipeline_glr()
    forest = glr_parse(glr_table, scanner.scan("1 + 2 * 3"))
    # The two valid derivations are (1+2)*3 and 1+(2*3) — GLR should give us
    # both, packed into an AmbiguityNode at the start symbol.
    assert isinstance(forest, AmbiguityNode)
    assert len(forest.alternatives) == 2


def test_glr_collapses_unambiguous_input_under_ambiguous_grammar():
    scanner, glr_table, _lr_table = ambig_pipeline_glr()
    # A single number has no ambiguity even under an ambiguous grammar.
    forest = glr_parse(glr_table, scanner.scan("42"))
    assert isinstance(forest, GLRNode)
    assert forest.kind == "e"


def test_glr_rejects_invalid_input_with_descriptive_error():
    scanner, glr_table, _lr_table = ambig_pipeline_glr()
    with pytest.raises(GLRParseError) as exc:
        glr_parse(glr_table, scanner.scan("1 +"))
    assert exc.value.token is not None


# ---- ambiguous grammar: dangling-else (without matched/unmatched fix)

DANGLING = """
%grammar dang
%tokens
IF   = "if"
THEN = "then"
ELSE = "else"
S    = "s"
WS   = /[ \\t\\n]+/  %skip
%rules
stmt : IF S THEN stmt
     | IF S THEN stmt ELSE stmt
     | S
     ;
"""


def test_dangling_else_glr_produces_two_parses():
    ir = read_source(DANGLING)
    dfa, _t, skip = lex_from_ir(ir)
    scanner = Scanner(dfa=dfa, skip_tokens=frozenset(skip))
    lr = build_lr1(compile_grammar(ir))
    assert lr.conflicts != []
    table = glr_from_lr(lr)
    # `if s then if s then s else s` has the classic ambiguity.
    forest = glr_parse(table, scanner.scan("if s then if s then s else s"))
    # We expect at least two derivations packed somewhere in the forest.
    def has_ambiguity(node) -> bool:
        if isinstance(node, AmbiguityNode):
            return True
        if isinstance(node, GLRNode):
            return any(has_ambiguity(c) for c in node.children)
        return False
    assert has_ambiguity(forest)
