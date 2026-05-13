"""End-to-end smoke test for the v3 auto-AST pipeline.

Drives ``examples/calc_ast.uplox`` through the full toolchain:

    .uplox source
        |
        v
    uplox.spec.reader  -> GrammarIR
        |
        v
    uplox.spec.ast_plan.compile_ast_plan -> AstPlan
        |
        v
    uplox.tables.ast_to_json -> bundle["ast"] section
        |
        v
    uplox.gen.py.emit_py -> generated module text
        |
        v
    import, call parse(text), assert AST shape

If any layer regresses the test fails; the calc grammar exercises
the precedence-ladder ``?``-lift, ``%ast=BinOp`` sharing, and the
``%ast_drop LPAREN RPAREN`` flow that lifts the inner expression
out of a parenthesised primary.
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
    ir = read_file(str(EXAMPLES / "calc_ast.uplox"))
    dfa, tokens, skip = lex_from_ir(ir)
    table = build_lr1(compile_grammar(ir))
    bundle = empty_bundle(ir.name)
    bundle["lex"] = dfa_to_json(dfa, tokens=tokens, skip=skip)
    bundle["parse"] = table_to_json(table)
    bundle["ast"] = ast_to_json(compile_ast_plan(ir))

    text = emit_py(bundle)
    module_path = tmp_path / "uplox_calc.py"
    module_path.write_text(text)
    spec = importlib.util.spec_from_file_location("uplox_calc_v3", module_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_calc_ast_module_has_expected_kinds(tmp_path):
    mod = _build_and_import(tmp_path)
    # The two node kinds the grammar declares.
    assert hasattr(mod, "BinOp")
    assert hasattr(mod, "NumLit")
    assert hasattr(mod, "AstNode")


def test_calc_ast_simple_number(tmp_path):
    mod = _build_and_import(tmp_path)
    ast = mod.parse("42")
    assert isinstance(ast, mod.NumLit)
    assert ast.value.text == "42"


def test_calc_ast_addition_left_associative(tmp_path):
    mod = _build_and_import(tmp_path)
    ast = mod.parse("1 + 2 + 3")
    # (1 + 2) + 3
    assert isinstance(ast, mod.BinOp) and ast.op.name == "PLUS"
    assert isinstance(ast.lhs, mod.BinOp) and ast.lhs.op.name == "PLUS"
    assert isinstance(ast.rhs, mod.NumLit) and ast.rhs.value.text == "3"


def test_calc_ast_precedence(tmp_path):
    mod = _build_and_import(tmp_path)
    ast = mod.parse("1 + 2 * 3")
    # 1 + (2 * 3): outer is PLUS with NumLit lhs and BinOp(STAR) rhs.
    assert ast.op.name == "PLUS"
    assert isinstance(ast.lhs, mod.NumLit)
    assert isinstance(ast.rhs, mod.BinOp) and ast.rhs.op.name == "STAR"


def test_calc_ast_parens_lift_through(tmp_path):
    mod = _build_and_import(tmp_path)
    ast = mod.parse("(1 + 2) * 3")
    # Outer is STAR; lhs is BinOp(PLUS, 1, 2). Parens disappear from the tree.
    assert ast.op.name == "STAR"
    assert isinstance(ast.lhs, mod.BinOp) and ast.lhs.op.name == "PLUS"
    assert ast.lhs.lhs.value.text == "1"
    assert ast.lhs.rhs.value.text == "2"


def test_calc_ast_evaluator_round_trip(tmp_path):
    """The worked evaluator from calc_annotated.md."""
    mod = _build_and_import(tmp_path)

    def ev(n):
        if isinstance(n, mod.NumLit):
            return int(n.value.text)
        ops = {
            "+": lambda a, b: a + b,
            "-": lambda a, b: a - b,
            "*": lambda a, b: a * b,
            "/": lambda a, b: a // b,
        }
        return ops[n.op.text](ev(n.lhs), ev(n.rhs))

    cases = [
        ("42", 42),
        ("1 + 2", 3),
        ("(1 + 2) * 3", 9),
        ("10 - 4 - 2", 4),     # left-associative
        ("1 + 2 * 3", 7),      # precedence
        ("(2 + 3) * (4 - 1)", 15),
    ]
    for src, expected in cases:
        assert ev(mod.parse(src)) == expected, (
            f"evaluating {src!r} returned {ev(mod.parse(src))}, expected {expected}"
        )


def test_calc_ast_position_spans(tmp_path):
    mod = _build_and_import(tmp_path)
    ast = mod.parse("12 + 34")
    # NumLit positions match where the digits land in the source.
    assert ast.lhs.value.text == "12"
    assert ast.lhs.pos.start_column == 1
    assert ast.lhs.pos.end_column == 3      # one past "12"
    assert ast.rhs.value.text == "34"
    assert ast.rhs.pos.start_column == 6    # after "12 + "


def test_calc_ast_parse_cst_still_works(tmp_path):
    mod = _build_and_import(tmp_path)
    cst = mod.parse_cst("1 + 2")
    # parse_cst returns the legacy ParseNode tree, not an AST node.
    assert isinstance(cst, mod.ParseNode)
    assert not isinstance(cst, mod.BinOp)
