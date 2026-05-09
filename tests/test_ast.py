"""AST schema and build_ast() tests."""

from __future__ import annotations

import json

from uplox.ast import AstNode, ast_from_json, ast_to_json, build_ast
from uplox.lex.build import lex_from_ir
from uplox.lex.scanner import Scanner, Token
from uplox.parse.grammar import compile_grammar
from uplox.parse.lr1 import build_lr1
from uplox.parse.runtime import ParseNode, parse
from uplox.spec.reader import read_source


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
WS     = /[ \\t\\n]+/    %skip

%rules
<expr>  : <expr> PLUS <term> | <expr> MINUS <term> | <term> ;
<term>  : <term> STAR <factor> | <term> SLASH <factor> | <factor> ;
<factor> : NUMBER | LPAREN <expr> RPAREN ;
"""


def calc_pipeline():
    ir = read_source(CALC)
    dfa, _t, skip = lex_from_ir(ir)
    return Scanner(dfa=dfa, skip_tokens=frozenset(skip)), build_lr1(compile_grammar(ir))


def calc_reducers() -> dict[str, callable]:
    """A small set of reducers that flatten the parse tree into a typical AST.

    Demonstrates the common patterns: lifting single-child wrappers, mapping
    tokens to leaf nodes, and constructing operator nodes.
    """

    def lift_single(_node, children):
        # If the production was just `x : y`, drop the wrapper.
        if len(children) == 1:
            return children[0]
        # Otherwise (shouldn't happen for these rules) keep as-is.
        return AstNode(kind="lifted", children=children)

    def expr_or_term(_node, children):
        if len(children) == 1:
            return children[0]
        # children is [lhs, op_token, rhs] after dropping nothing.
        op = children[1]
        return AstNode(
            kind="BinaryOp",
            attrs={"op": op.text if isinstance(op, Token) else str(op)},
            children=[children[0], children[2]],
        )

    def factor(_node, children):
        if len(children) == 1:
            return children[0]
        # Parenthesised expression — children = [expr] after dropping LPAREN/RPAREN.
        return children[0]

    def number(_node, children):
        # ``factor : NUMBER`` reaches here through `factor` reducer with NUMBER token.
        # But reducers dispatch by ParseNode.kind, and NUMBER is a token, not a node.
        # So this stub is not strictly needed; keeping it shows there is no
        # rule with kind "NUMBER" — terminals stay as tokens.
        return children[0]

    return {
        "expr": expr_or_term,
        "term": expr_or_term,
        "factor": factor,
    }


def test_build_ast_lifts_single_child_chains():
    scanner, table = calc_pipeline()
    parse_tree = parse(table, scanner.scan("42"))
    ast = build_ast(parse_tree, calc_reducers())
    # 42 — chain factor->term->expr should collapse to the NUMBER token.
    assert isinstance(ast, Token)
    assert ast.text == "42"


def test_build_ast_with_drop_tokens_strips_punctuation():
    scanner, table = calc_pipeline()
    parse_tree = parse(table, scanner.scan("(1)"))
    ast = build_ast(parse_tree, calc_reducers(), drop_tokens={"LPAREN", "RPAREN"})
    assert isinstance(ast, Token)
    assert ast.text == "1"


def test_build_ast_creates_binary_op_node():
    scanner, table = calc_pipeline()
    parse_tree = parse(table, scanner.scan("1 + 2"))
    ast = build_ast(parse_tree, calc_reducers())
    assert isinstance(ast, AstNode)
    assert ast.kind == "BinaryOp"
    assert ast.attrs == {"op": "+"}
    assert len(ast.children) == 2
    assert all(isinstance(c, Token) and c.name == "NUMBER" for c in ast.children)


def test_build_ast_handles_nested_expressions():
    scanner, table = calc_pipeline()
    parse_tree = parse(table, scanner.scan("(1 + 2) * 3"))
    ast = build_ast(parse_tree, calc_reducers(), drop_tokens={"LPAREN", "RPAREN"})
    assert isinstance(ast, AstNode) and ast.kind == "BinaryOp"
    assert ast.attrs == {"op": "*"}
    left, right = ast.children
    assert isinstance(left, AstNode) and left.kind == "BinaryOp"
    assert left.attrs == {"op": "+"}
    assert isinstance(right, Token) and right.text == "3"


def test_default_when_no_reducer_keeps_kind():
    scanner, table = calc_pipeline()
    parse_tree = parse(table, scanner.scan("1"))
    ast = build_ast(parse_tree, {})
    assert isinstance(ast, AstNode) and ast.kind == "expr"


def test_ast_json_round_trip():
    scanner, table = calc_pipeline()
    parse_tree = parse(table, scanner.scan("(1 + 2) * 3"))
    ast = build_ast(parse_tree, calc_reducers(), drop_tokens={"LPAREN", "RPAREN"})
    serialised = ast_to_json(ast)
    text = json.dumps(serialised, sort_keys=True)
    parsed = json.loads(text)
    rebuilt = ast_from_json(parsed)
    # Compare JSON dumps for shape equality (avoids rebuilding token __eq__).
    assert ast_to_json(rebuilt) == serialised


def test_ast_json_includes_position_when_set():
    node = AstNode(kind="X", position=(3, 7))
    out = ast_to_json(node)
    assert out["position"] == [3, 7]
    rebuilt = ast_from_json(out)
    assert isinstance(rebuilt, AstNode) and rebuilt.position == (3, 7)
