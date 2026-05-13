"""End-to-end test for the v3 auto-AST pipeline against C expressions.

Drives ``examples/c_expr.uplox`` through the full toolchain
(read source -> compile_ast_plan -> ast_to_json -> emit_py -> import
-> parse) and validates the resulting AST matches uc_core-style
expression node shapes. This is the pilot for the eventual full C
grammar (uc_core's frontend.py is ~2200 lines); the expression
hierarchy alone exercises the operator precedence ladder,
ternary, call, index, member access, postfix ops, and the
string-literal concatenation pattern.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from uplox.gen.py import emit_py
from uplox.lex.build import lex_from_ir
from uplox.parse.grammar import compile_grammar
from uplox.parse.lr1 import build_lr1
from uplox.spec.ast_plan import compile_ast_plan
from uplox.spec.reader import read_file
from uplox.tables import ast_to_json, dfa_to_json, empty_bundle, table_to_json


EXAMPLES = Path(__file__).resolve().parent.parent / "examples"


def _build_and_import(tmp_path: Path):
    ir = read_file(str(EXAMPLES / "c_expr.uplox"))
    dfa, tokens, skip = lex_from_ir(ir)
    table = build_lr1(compile_grammar(ir))
    bundle = empty_bundle(ir.name)
    bundle["lex"] = dfa_to_json(dfa, tokens=tokens, skip=skip)
    bundle["parse"] = table_to_json(table)
    bundle["ast"] = ast_to_json(compile_ast_plan(ir))

    text = emit_py(bundle)
    module_path = tmp_path / "uplox_c_expr.py"
    module_path.write_text(text)
    spec = importlib.util.spec_from_file_location("uplox_c_expr_v3", module_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_precedence_addition_then_multiplication(tmp_path):
    mod = _build_and_import(tmp_path)
    # 1 + 2 * 3 -> BinaryOp(+, 1, BinaryOp(*, 2, 3))
    ast = mod.parse("1 + 2 * 3")
    assert isinstance(ast, mod.BinaryOp)
    assert ast.op.text == "+"
    assert isinstance(ast.left, mod.IntLiteral)
    assert isinstance(ast.right, mod.BinaryOp)
    assert ast.right.op.text == "*"


def test_addition_is_left_associative(tmp_path):
    mod = _build_and_import(tmp_path)
    # a + b + c -> BinaryOp(+, BinaryOp(+, a, b), c)
    ast = mod.parse("a + b + c")
    assert ast.op.text == "+"
    assert isinstance(ast.left, mod.BinaryOp)
    assert ast.left.op.text == "+"
    assert ast.left.left.name.text == "a"
    assert ast.left.right.name.text == "b"
    assert ast.right.name.text == "c"


def test_assignment_is_right_associative(tmp_path):
    mod = _build_and_import(tmp_path)
    # a = b = c -> BinaryOp(=, a, BinaryOp(=, b, c))
    ast = mod.parse("a = b = c")
    assert ast.op.text == "="
    assert isinstance(ast.left, mod.Identifier)
    assert isinstance(ast.right, mod.BinaryOp)
    assert ast.right.op.text == "="


def test_compound_assignment(tmp_path):
    mod = _build_and_import(tmp_path)
    ast = mod.parse("x += y * 2")
    assert ast.op.text == "+="
    assert isinstance(ast.right, mod.BinaryOp) and ast.right.op.text == "*"


def test_ternary(tmp_path):
    mod = _build_and_import(tmp_path)
    ast = mod.parse("a < b ? x : y")
    assert isinstance(ast, mod.TernaryOp)
    assert isinstance(ast.condition, mod.BinaryOp) and ast.condition.op.text == "<"
    assert isinstance(ast.true_expr, mod.Identifier)
    assert isinstance(ast.false_expr, mod.Identifier)


def test_call_with_args(tmp_path):
    mod = _build_and_import(tmp_path)
    ast = mod.parse("f(a, b, c)")
    assert isinstance(ast, mod.Call)
    assert isinstance(ast.func, mod.Identifier) and ast.func.name.text == "f"
    assert [a.name.text for a in ast.args] == ["a", "b", "c"]


def test_call_empty_args(tmp_path):
    mod = _build_and_import(tmp_path)
    ast = mod.parse("f()")
    assert isinstance(ast, mod.Call)
    assert ast.args == []


def test_array_index(tmp_path):
    mod = _build_and_import(tmp_path)
    ast = mod.parse("arr[i + 1]")
    assert isinstance(ast, mod.Index)
    assert isinstance(ast.array, mod.Identifier) and ast.array.name.text == "arr"
    assert isinstance(ast.index, mod.BinaryOp) and ast.index.op.text == "+"


def test_dot_member(tmp_path):
    mod = _build_and_import(tmp_path)
    ast = mod.parse("obj.field")
    assert isinstance(ast, mod.Member)
    assert isinstance(ast.obj, mod.Identifier)
    assert ast.member.text == "field"


def test_arrow_member(tmp_path):
    """ucow's translator will set is_arrow=True based on AST kind name."""
    mod = _build_and_import(tmp_path)
    ast = mod.parse("p->field")
    assert isinstance(ast, mod.ArrowMember)
    assert ast.member.text == "field"


def test_postfix_increment(tmp_path):
    mod = _build_and_import(tmp_path)
    ast = mod.parse("x++")
    assert isinstance(ast, mod.PostfixOp)
    assert ast.op.text == "++"
    assert isinstance(ast.operand, mod.Identifier)


def test_prefix_operators(tmp_path):
    mod = _build_and_import(tmp_path)
    for src, op in [("-x", "-"), ("!flag", "!"), ("~bits", "~"), ("*p", "*"),
                    ("&var", "&"), ("++i", "++"), ("--n", "--")]:
        ast = mod.parse(src)
        assert isinstance(ast, mod.UnaryOp), f"{src!r}: expected UnaryOp"
        assert ast.op.text == op, f"{src!r}: op={ast.op.text!r}"


def test_string_literal_concatenation(tmp_path):
    """Adjacent string literals: each STRING_LIT becomes one StringLiteral;
    they aggregate into the list. Consumer-side concatenation is the
    next pass's job."""
    mod = _build_and_import(tmp_path)
    ast = mod.parse('"hello, " "world"')
    # <string_literal> is a list rule; lifting gives back the raw list.
    assert isinstance(ast, list)
    assert len(ast) == 2
    assert all(isinstance(x, mod.StringLiteral) for x in ast)


def test_literal_kinds(tmp_path):
    mod = _build_and_import(tmp_path)
    for src, expected in [
        ("42", mod.IntLiteral),
        ("3.14", mod.FloatLiteral),
        ("'x'", mod.CharLiteral),
        ("nullptr", mod.NullptrLiteral),
        ("true", mod.BoolLiteral),
        ("false", mod.BoolLiteral),
        ("name", mod.Identifier),
    ]:
        ast = mod.parse(src)
        assert isinstance(ast, expected), f"{src!r}: got {type(ast).__name__}"


def test_complex_expression(tmp_path):
    """End-to-end stress: nested call inside a ternary inside an
    assignment. Shape ought to be:
        BinaryOp(=
            Identifier(result),
            TernaryOp(
                cond=Call(check, [x, y]),
                true=BinaryOp(+, a, b),
                false=BinaryOp(-, a, b)
            ))
    """
    mod = _build_and_import(tmp_path)
    ast = mod.parse("result = check(x, y) ? a + b : a - b")
    assert isinstance(ast, mod.BinaryOp) and ast.op.text == "="
    tern = ast.right
    assert isinstance(tern, mod.TernaryOp)
    assert isinstance(tern.condition, mod.Call)
    assert tern.condition.func.name.text == "check"
    assert isinstance(tern.true_expr, mod.BinaryOp) and tern.true_expr.op.text == "+"
    assert isinstance(tern.false_expr, mod.BinaryOp) and tern.false_expr.op.text == "-"
