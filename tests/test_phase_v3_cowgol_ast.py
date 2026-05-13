"""End-to-end test for the v3 auto-AST pipeline at cowgol scale.

Validates that the full Cowgol grammar (74 non-terminals, 12-level
expression ladder, 11+ auto-optional clauses, 13 list-shaped rules)
flows cleanly through the v3 surface and produces a typed AST.

If this test passes, the v3 spec covers everything a real-language
front-end consumer (ucow) needs from the parser layer; the
consumer-side migration becomes "replace ast.py + the parse-tree
walker with this generated module."
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
    ir = read_file(str(EXAMPLES / "cowgol_ast.uplox"))
    dfa, tokens, skip = lex_from_ir(ir)
    table = build_lr1(compile_grammar(ir))
    bundle = empty_bundle(ir.name)
    bundle["lex"] = dfa_to_json(dfa, tokens=tokens, skip=skip)
    bundle["parse"] = table_to_json(table)
    bundle["ast"] = ast_to_json(compile_ast_plan(ir))

    text = emit_py(bundle)
    module_path = tmp_path / "uplox_cowgol.py"
    module_path.write_text(text)
    spec = importlib.util.spec_from_file_location("uplox_cowgol_v3", module_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_cowgol_program_top_level(tmp_path):
    cg = _build_and_import(tmp_path)
    ast = cg.parse('include "stdio.coh"; sub main() is end sub;')
    assert isinstance(ast, cg.Program)
    assert len(ast.items) == 2
    assert isinstance(ast.items[0], cg.IncludeDecl)
    assert ast.items[0].path.text == '"stdio.coh"'
    assert isinstance(ast.items[1], cg.SubDecl)
    assert ast.items[1].name.text == "main"


def test_cowgol_sub_with_statements(tmp_path):
    cg = _build_and_import(tmp_path)
    src = """
        sub main() is
            var i: int8 := 0;
            while i < 10 loop
                i := i + 1;
            end loop;
            return;
        end sub;
    """
    ast = cg.parse(src)
    sub = ast.items[0]
    assert isinstance(sub, cg.SubDecl)
    # tail is a SubBody whose body is a list[sub_body_item]
    assert isinstance(sub.tail, cg.SubBody)
    body = sub.tail.body
    assert isinstance(body, list)
    assert len(body) == 3
    var_decl, while_stmt, return_stmt = body
    assert isinstance(var_decl, cg.VarDecl)
    assert var_decl.name.text == "i"
    # var_decl.type unwraps to a Type node (with base + suffixes)
    assert isinstance(var_decl.type, cg.Type)
    assert isinstance(var_decl.type.base, cg.Int8Type)
    # var_decl.init unwraps to an expression
    assert isinstance(var_decl.init, cg.NumberLiteral)
    assert var_decl.init.value.text == "0"
    assert isinstance(while_stmt, cg.WhileStmt)
    assert isinstance(return_stmt, cg.ReturnStmt)


def test_cowgol_expression_precedence(tmp_path):
    cg = _build_and_import(tmp_path)
    # 1 + 2 * 3 → Type checker / runtime would evaluate to 7 but here
    # we just verify the AST shape matches operator precedence.
    src = "sub main() is var x: int32 := 1 + 2 * 3; end sub;"
    ast = cg.parse(src)
    var = ast.items[0].tail.body[0]
    init = var.init
    assert isinstance(init, cg.BinaryOp) and init.op.text == "+"
    assert isinstance(init.lhs, cg.NumberLiteral) and init.lhs.value.text == "1"
    assert isinstance(init.rhs, cg.BinaryOp) and init.rhs.op.text == "*"


def test_cowgol_comparison_kind_distinct(tmp_path):
    cg = _build_and_import(tmp_path)
    src = "sub main() is while a == b loop end loop; end sub;"
    ast = cg.parse(src)
    while_stmt = ast.items[0].tail.body[0]
    cond = while_stmt.cond
    # Comparisons are their own kind, not BinaryOp.
    assert isinstance(cond, cg.Comparison)
    assert cond.op.text == "=="
    assert isinstance(cond.lhs, cg.Identifier)
    assert isinstance(cond.rhs, cg.Identifier)


def test_cowgol_if_with_elseif_chain(tmp_path):
    cg = _build_and_import(tmp_path)
    src = """
        sub main() is
            if a == 1 then
                x := 1;
            elseif a == 2 then
                x := 2;
            elseif a == 3 then
                x := 3;
            else
                x := 0;
            end if;
        end sub;
    """
    ast = cg.parse(src)
    if_stmt = ast.items[0].tail.body[0]
    assert isinstance(if_stmt, cg.IfStmt)
    assert isinstance(if_stmt.cond, cg.Comparison)
    assert len(if_stmt.then_body) == 1
    # elseifs is a list[ElseIf]; this program has two elseif clauses.
    assert isinstance(if_stmt.elseifs, list)
    assert len(if_stmt.elseifs) == 2
    for clause in if_stmt.elseifs:
        assert isinstance(clause, cg.ElseIf)
        assert isinstance(clause.cond, cg.Comparison)
    # otherwise unwraps to the else body (a list)
    assert isinstance(if_stmt.otherwise, list)
    assert len(if_stmt.otherwise) == 1


def test_cowgol_record_decl(tmp_path):
    cg = _build_and_import(tmp_path)
    src = """
        record Point is
            x: int16;
            y: int16;
        end record;
    """
    ast = cg.parse(src)
    rec = ast.items[0]
    assert isinstance(rec, cg.RecordDecl)
    assert rec.name.text == "Point"
    assert isinstance(rec.fields, list)
    assert len(rec.fields) == 2
    for field in rec.fields:
        assert isinstance(field, cg.RecordField)
    assert rec.fields[0].name.text == "x"
    assert rec.fields[1].name.text == "y"


def test_cowgol_call_with_args(tmp_path):
    cg = _build_and_import(tmp_path)
    src = "sub main() is f(a, b, c); end sub;"
    ast = cg.parse(src)
    stmt = ast.items[0].tail.body[0]
    assert isinstance(stmt, cg.ExprStmt)
    call = stmt.expr
    assert isinstance(call, cg.Call)
    # args is the list of expressions
    assert isinstance(call.args, list)
    assert len(call.args) == 3
    assert all(isinstance(a, cg.Identifier) for a in call.args)
    assert [a.name.text for a in call.args] == ["a", "b", "c"]


def test_cowgol_multi_assignment(tmp_path):
    cg = _build_and_import(tmp_path)
    src = "sub main() is (a, b) := f(); end sub;"
    ast = cg.parse(src)
    stmt = ast.items[0].tail.body[0]
    assert isinstance(stmt, cg.MultiAssignment)
    # targets is a list[expr] with at least two entries
    assert isinstance(stmt.targets, list)
    assert len(stmt.targets) == 2
    assert [t.name.text for t in stmt.targets] == ["a", "b"]


def test_cowgol_pointer_and_array_types(tmp_path):
    cg = _build_and_import(tmp_path)
    src = "sub main() is var p: [int8][5]; end sub;"
    ast = cg.parse(src)
    var = ast.items[0].tail.body[0]
    assert isinstance(var.type, cg.Type)
    # base is a PointerType to int8
    assert isinstance(var.type.base, cg.PointerType)
    # suffixes carries the [5] sized-array suffix
    assert isinstance(var.type.suffixes, list)
    assert len(var.type.suffixes) == 1
    assert isinstance(var.type.suffixes[0], cg.ArraySized)
