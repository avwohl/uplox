"""End-to-end test for the v3 AST pilot Phase 2 (C subset).

Builds ``examples/c_subset.uplox`` and drives uc80's hello.c-style
C programs through the full toolchain. Phase 2 adds simple
declarations, statements, basic types, and pointer declarators on
top of Phase 1's expression-only grammar.

If this test passes, the v3 surface can carry uc_core's expression
+ statement + simple-declaration AST shape; the remaining work for
the full uc_core migration is Phase 3 (struct/union/enum, typedef,
typedef-name lexer hook, full declarators, casts, sizeof(type)).
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
    ir = read_file(str(EXAMPLES / "c_subset.uplox"))
    dfa, tokens, skip = lex_from_ir(ir)
    table = build_lr1(compile_grammar(ir))
    bundle = empty_bundle(ir.name)
    bundle["lex"] = dfa_to_json(dfa, tokens=tokens, skip=skip)
    bundle["parse"] = table_to_json(table)
    bundle["ast"] = ast_to_json(compile_ast_plan(ir))

    text = emit_py(bundle)
    module_path = tmp_path / "uplox_c_subset.py"
    module_path.write_text(text)
    spec = importlib.util.spec_from_file_location("uplox_c_subset_v3", module_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


# ---- Single-function programs -----------------------------------------------


def test_empty_function(tmp_path):
    mod = _build_and_import(tmp_path)
    ast = mod.parse("void foo(void) { }")
    fn = ast.items[0]
    assert isinstance(fn, mod.FunctionDef)
    assert isinstance(fn.return_type, mod.VoidType)
    assert fn.declarator.name.text == "foo"
    assert isinstance(fn.body, mod.CompoundStmt)
    assert fn.body.items == []


def test_function_with_int_param(tmp_path):
    mod = _build_and_import(tmp_path)
    ast = mod.parse("int sq(int x) { return x * x; }")
    fn = ast.items[0]
    assert isinstance(fn.return_type, mod.IntType)
    assert fn.declarator.name.text == "sq"
    params = fn.declarator.params
    assert len(params) == 1
    assert isinstance(params[0].param_type, mod.IntType)
    assert params[0].declarator.name.text == "x"
    body = fn.body.items
    assert len(body) == 1
    assert isinstance(body[0], mod.ReturnStmt)
    assert isinstance(body[0].value, mod.BinaryOp)
    assert body[0].value.op.text == "*"


def test_function_with_pointer_param(tmp_path):
    """`void puts(char *s)` — char* exercises PointerDeclarator."""
    mod = _build_and_import(tmp_path)
    ast = mod.parse("void puts(char *s);")
    decl = ast.items[0]
    assert isinstance(decl, mod.Declaration)
    # The single init_declarator is a function prototype.
    inner = decl.declarators[0]
    fn_decl = inner.declarator
    assert isinstance(fn_decl, mod.FnDeclarator)
    assert fn_decl.name.text == "puts"
    # Param: char *s
    p = fn_decl.params[0]
    assert isinstance(p.param_type, mod.CharType)
    assert isinstance(p.declarator, mod.PointerDeclarator)
    # The inner declarator carries the name.
    assert p.declarator.inner.name.text == "s"


# ---- uc80's actual hello.c programs ----------------------------------------


def test_hello_c_parses(tmp_path):
    """uc80/examples/hello.c: forward decl + main + call."""
    mod = _build_and_import(tmp_path)
    src = open("/Users/wohl/src/uc80/examples/hello.c").read()
    ast = mod.parse(src)
    assert isinstance(ast, mod.TranslationUnit)
    assert len(ast.items) == 2
    # Item 0: void puts(char *s);
    decl = ast.items[0]
    assert isinstance(decl, mod.Declaration)
    # Item 1: int main(void) { ... }
    main_fn = ast.items[1]
    assert isinstance(main_fn, mod.FunctionDef)
    assert main_fn.declarator.name.text == "main"
    assert isinstance(main_fn.return_type, mod.IntType)
    # Body has 2 stmts: puts(...); return 0;
    body = main_fn.body.items
    assert len(body) == 2
    assert isinstance(body[0], mod.ExpressionStmt)
    assert isinstance(body[0].expr, mod.Call)
    assert body[0].expr.func.name.text == "puts"
    assert isinstance(body[1], mod.ReturnStmt)
    assert isinstance(body[1].value, mod.IntLiteral)


def test_hello2_c_parses(tmp_path):
    """uc80/examples/hello2.c: while loop, pointer var decl, pointer deref."""
    mod = _build_and_import(tmp_path)
    src = open("/Users/wohl/src/uc80/examples/hello2.c").read()
    ast = mod.parse(src)
    assert isinstance(ast, mod.TranslationUnit)
    # Item 0: int putchar(int c);
    decl = ast.items[0]
    assert isinstance(decl, mod.Declaration)
    # Item 1: int main(void) { ... }
    main_fn = ast.items[1]
    assert isinstance(main_fn, mod.FunctionDef)
    body = main_fn.body.items
    # body: Declaration (char *s = "..."), WhileStmt, ReturnStmt
    assert len(body) == 3
    assert isinstance(body[0], mod.Declaration)
    assert isinstance(body[1], mod.WhileStmt)
    assert isinstance(body[2], mod.ReturnStmt)
    # The while condition: *s — UnaryOp(*, Identifier(s))
    cond = body[1].condition
    assert isinstance(cond, mod.UnaryOp) and cond.op.text == "*"
    assert isinstance(cond.operand, mod.Identifier) and cond.operand.name.text == "s"


# ---- Statement coverage -----------------------------------------------------


def test_if_else(tmp_path):
    mod = _build_and_import(tmp_path)
    ast = mod.parse("int f(int n) { if (n == 0) return 1; else return 0; }")
    fn = ast.items[0]
    stmt = fn.body.items[0]
    assert isinstance(stmt, mod.IfStmtElse)
    assert isinstance(stmt.condition, mod.BinaryOp)
    assert isinstance(stmt.then_branch, mod.ReturnStmt)
    assert isinstance(stmt.else_branch, mod.ReturnStmt)


def test_if_without_else(tmp_path):
    mod = _build_and_import(tmp_path)
    ast = mod.parse("int f(int n) { if (n > 0) return 1; return 0; }")
    body = ast.items[0].body.items
    assert isinstance(body[0], mod.IfStmt)
    assert isinstance(body[1], mod.ReturnStmt)


def test_while_loop_with_break_continue(tmp_path):
    mod = _build_and_import(tmp_path)
    ast = mod.parse("""
        int f(int n) {
            while (n > 0) {
                if (n == 5) break;
                if (n == 3) continue;
                n = n - 1;
            }
            return n;
        }
    """)
    fn = ast.items[0]
    w = fn.body.items[0]
    assert isinstance(w, mod.WhileStmt)
    inner = w.body.items
    assert isinstance(inner[0], mod.IfStmt)
    assert isinstance(inner[0].then_branch, mod.BreakStmt)
    assert isinstance(inner[1].then_branch, mod.ContinueStmt)


def test_local_var_decl(tmp_path):
    mod = _build_and_import(tmp_path)
    ast = mod.parse("int f(void) { int x = 42; return x; }")
    body = ast.items[0].body.items
    assert isinstance(body[0], mod.Declaration)
    init_decl = body[0].declarators[0]
    assert isinstance(init_decl, mod.InitDeclaratorWithInit)
    assert init_decl.declarator.name.text == "x"
    assert isinstance(init_decl.init, mod.IntLiteral)


def test_pointer_chain(tmp_path):
    """`int **pp` — two levels of PointerDeclarator."""
    mod = _build_and_import(tmp_path)
    ast = mod.parse("int **pp;")
    init_decl = ast.items[0].declarators[0]
    d = init_decl.declarator
    assert isinstance(d, mod.PointerDeclarator)
    assert isinstance(d.inner, mod.PointerDeclarator)
    assert d.inner.inner.name.text == "pp"


def test_multiple_init_declarators(tmp_path):
    """`int a, b = 0;` — multiple declarators in one Declaration."""
    mod = _build_and_import(tmp_path)
    ast = mod.parse("int a, b = 0;")
    decls = ast.items[0].declarators
    assert len(decls) == 2
    assert isinstance(decls[0], mod.InitDeclarator)
    assert decls[0].declarator.name.text == "a"
    assert isinstance(decls[1], mod.InitDeclaratorWithInit)
    assert decls[1].declarator.name.text == "b"
