"""End-to-end test for c_subset.uplox Phase 4 additions.

Phase 4 adds: type qualifiers (const/volatile/restrict),
abstract declarators, <type_name>, cast expressions,
sizeof(type) / alignof(type) / sizeof expr, compound literals,
struct bit-fields, _Static_assert, range case `case A ... B:`,
asm with operand and clobber lists. Acceptance: every feature
parses to a structurally correct AST shape.

Deferred to Phase 5 (the final push): typedef + typedef-name
lex hook, _Generic / va_arg / offsetof / statement-exprs, plus
the translator into uc_core's frontend.py.
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
    spec = importlib.util.spec_from_file_location("uplox_c_subset_v3p4", module_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


# ---- Type qualifiers --------------------------------------------------------


def test_const_decl(tmp_path):
    mod = _build_and_import(tmp_path)
    ast = mod.parse("const int N = 42;")
    decl = ast.items[0]
    assert isinstance(decl, mod.Declaration)
    assert isinstance(decl.type_spec, mod.QualifiedType)
    assert len(decl.type_spec.quals) == 1
    assert isinstance(decl.type_spec.quals[0], mod.ConstQual)
    assert isinstance(decl.type_spec.base, mod.IntType)


def test_const_volatile_decl(tmp_path):
    mod = _build_and_import(tmp_path)
    ast = mod.parse("const volatile int x;")
    decl = ast.items[0]
    quals = decl.type_spec.quals
    assert len(quals) == 2
    assert isinstance(quals[0], mod.ConstQual)
    assert isinstance(quals[1], mod.VolatileQual)


def test_volatile_then_const(tmp_path):
    mod = _build_and_import(tmp_path)
    ast = mod.parse("volatile const char *p;")
    # Note: `*p` makes this a pointer-to-(volatile-const-char).
    # The qualifiers apply to the base type, not the pointer.
    decl = ast.items[0]
    assert isinstance(decl.type_spec, mod.QualifiedType)


# ---- Cast expressions -------------------------------------------------------


def test_cast_to_basic_type(tmp_path):
    mod = _build_and_import(tmp_path)
    ast = mod.parse("int main(void) { x = (int)3.14; }")
    body = ast.items[0].body.items
    expr_stmt = body[0]
    assert isinstance(expr_stmt.expr, mod.BinaryOp)
    cast = expr_stmt.expr.right
    assert isinstance(cast, mod.Cast)
    assert isinstance(cast.target_type, mod.TypeName)
    assert isinstance(cast.target_type.base, mod.IntType)
    assert isinstance(cast.expr, mod.FloatLiteral)


def test_cast_to_pointer(tmp_path):
    mod = _build_and_import(tmp_path)
    ast = mod.parse("int main(void) { p = (char *)q; }")
    expr_stmt = ast.items[0].body.items[0]
    cast = expr_stmt.expr.right
    assert isinstance(cast, mod.Cast)
    # target_type is a TypeNameWithDeclarator (char + abstract pointer)
    assert isinstance(cast.target_type, mod.TypeNameWithDeclarator)
    assert isinstance(cast.target_type.base, mod.CharType)
    assert isinstance(cast.target_type.declarator, mod.AbstractPointerTail)


def test_cast_to_pointer_to_array(tmp_path):
    mod = _build_and_import(tmp_path)
    ast = mod.parse("int main(void) { p = (int *)addr; }")
    cast = ast.items[0].body.items[0].expr.right
    assert isinstance(cast, mod.Cast)
    assert isinstance(cast.target_type.declarator, mod.AbstractPointerTail)


# ---- sizeof / alignof -------------------------------------------------------


def test_sizeof_type(tmp_path):
    mod = _build_and_import(tmp_path)
    ast = mod.parse("int main(void) { n = sizeof(int); }")
    expr_stmt = ast.items[0].body.items[0]
    sof = expr_stmt.expr.right
    assert isinstance(sof, mod.SizeofType)
    assert isinstance(sof.target_type, mod.TypeName)
    assert isinstance(sof.target_type.base, mod.IntType)


def test_sizeof_expr(tmp_path):
    mod = _build_and_import(tmp_path)
    ast = mod.parse("int main(void) { n = sizeof x; }")
    sof = ast.items[0].body.items[0].expr.right
    assert isinstance(sof, mod.SizeofExpr)
    assert isinstance(sof.operand, mod.Identifier)


def test_alignof_type(tmp_path):
    mod = _build_and_import(tmp_path)
    ast = mod.parse("int main(void) { n = alignof(double); }")
    aof = ast.items[0].body.items[0].expr.right
    assert isinstance(aof, mod.AlignofType)
    assert isinstance(aof.target_type.base, mod.DoubleType)


def test_sizeof_pointer_type(tmp_path):
    mod = _build_and_import(tmp_path)
    ast = mod.parse("int main(void) { n = sizeof(char *); }")
    sof = ast.items[0].body.items[0].expr.right
    assert isinstance(sof, mod.SizeofType)
    assert isinstance(sof.target_type, mod.TypeNameWithDeclarator)


# ---- Compound literals -----------------------------------------------------


def test_compound_literal_array(tmp_path):
    mod = _build_and_import(tmp_path)
    ast = mod.parse("int main(void) { p = (int []){1, 2, 3}; }")
    expr_stmt = ast.items[0].body.items[0]
    cl = expr_stmt.expr.right
    assert isinstance(cl, mod.CompoundLiteral)
    assert isinstance(cl.target_type, mod.TypeNameWithDeclarator)
    assert isinstance(cl.target_type.declarator, mod.AbstractArrayUnsized)
    assert len(cl.items) == 3


def test_compound_literal_struct(tmp_path):
    mod = _build_and_import(tmp_path)
    ast = mod.parse("int main(void) { p = (struct Point){.x = 1, .y = 2}; }")
    cl = ast.items[0].body.items[0].expr.right
    assert isinstance(cl, mod.CompoundLiteral)
    assert isinstance(cl.target_type, mod.TypeName)
    assert isinstance(cl.target_type.base, mod.StructRef)


def test_compound_literal_empty(tmp_path):
    mod = _build_and_import(tmp_path)
    ast = mod.parse("int main(void) { p = (int []){}; }")
    cl = ast.items[0].body.items[0].expr.right
    assert isinstance(cl, mod.CompoundLiteralEmpty)


# ---- Bit-fields ------------------------------------------------------------


def test_struct_bit_field(tmp_path):
    mod = _build_and_import(tmp_path)
    ast = mod.parse("struct F { int a : 3; int b : 5; };")
    members = ast.items[0].type_spec.members
    assert len(members) == 2
    for m in members:
        assert isinstance(m, mod.StructBitFieldMember)
        assert len(m.declarators) == 1
        bf = m.declarators[0]
        assert isinstance(bf, mod.BitFieldDecl)
        assert isinstance(bf.bit_width, mod.IntLiteral)


def test_anon_bit_field(tmp_path):
    mod = _build_and_import(tmp_path)
    ast = mod.parse("struct F { int : 8; int x : 4; };")
    members = ast.items[0].type_spec.members
    # First member is anonymous (padding); second is named
    bf0 = members[0].declarators[0]
    assert isinstance(bf0, mod.AnonBitFieldDecl)
    bf1 = members[1].declarators[0]
    assert isinstance(bf1, mod.BitFieldDecl)


# ---- _Static_assert --------------------------------------------------------


def test_static_assert_top_level(tmp_path):
    mod = _build_and_import(tmp_path)
    ast = mod.parse("static_assert(sizeof(int) >= 2);")
    sa = ast.items[0]
    assert isinstance(sa, mod.StaticAssert)
    assert isinstance(sa.condition, mod.BinaryOp)


def test_static_assert_with_message(tmp_path):
    mod = _build_and_import(tmp_path)
    ast = mod.parse('static_assert(sizeof(int) >= 2, "need 16-bit int");')
    sa = ast.items[0]
    assert isinstance(sa, mod.StaticAssertWithMessage)
    assert sa.message.text == '"need 16-bit int"'


# ---- Range case ------------------------------------------------------------


def test_range_case(tmp_path):
    mod = _build_and_import(tmp_path)
    ast = mod.parse("""
        int main(void) {
            switch (x) {
                case 1 ... 5: return 1;
                case 6: return 2;
            }
        }
    """)
    sw = ast.items[0].body.items[0]
    cases = sw.body.items
    assert isinstance(cases[0], mod.CaseRangeStmt)
    assert isinstance(cases[0].value, mod.IntLiteral)
    assert isinstance(cases[0].value_end, mod.IntLiteral)
    assert isinstance(cases[1], mod.CaseStmt)


# ---- asm with operand lists ------------------------------------------------


def test_asm_with_outputs(tmp_path):
    mod = _build_and_import(tmp_path)
    ast = mod.parse('int main(void) { asm("nop" : "=r"(out)); }')
    a = ast.items[0].body.items[0]
    assert isinstance(a, mod.AsmStmtOut)
    assert len(a.outputs) == 1
    op = a.outputs[0]
    assert isinstance(op, mod.AsmOperand)
    assert op.constraint.text == '"=r"'


def test_asm_with_outputs_inputs(tmp_path):
    mod = _build_and_import(tmp_path)
    ast = mod.parse(
        'int main(void) { asm("add %0, %1" : "=r"(out) : "r"(a), "r"(b)); }'
    )
    a = ast.items[0].body.items[0]
    assert isinstance(a, mod.AsmStmtOutIn)
    assert len(a.outputs) == 1
    assert len(a.inputs) == 2


def test_asm_with_full_form(tmp_path):
    mod = _build_and_import(tmp_path)
    ast = mod.parse(
        'int main(void) { asm("rep stosb" : : "a"(0), "c"(n) : "cc", "memory"); }'
    )
    a = ast.items[0].body.items[0]
    assert isinstance(a, mod.AsmStmtOutInClobbers)
    assert a.outputs == []
    assert len(a.inputs) == 2
    assert len(a.clobbers) == 2
    assert a.clobbers[0].value.text == '"cc"'


# ---- Integration -----------------------------------------------------------


def test_phase4_stress(tmp_path):
    """All Phase 4 features in one translation unit."""
    mod = _build_and_import(tmp_path)
    src = r"""
        const int MAX = 100;

        struct Bits { int a : 3; int b : 5; int : 8; };

        static_assert(sizeof(int) >= 2, "16-bit int required");

        int main(void) {
            int *p = (int *)0;
            int n = sizeof(int);
            int m = sizeof p;
            int sz = alignof(double);
            int arr[3] = {1, 2, 3};
            int *q = (int []){10, 20, 30};
            switch (n) {
                case 1 ... 4: return 1;
                case 8: return 2;
                default: break;
            }
            asm("nop" : : : "memory");
            return n + m + sz;
        }
    """
    ast = mod.parse(src)
    assert isinstance(ast, mod.TranslationUnit)
    kinds = [type(it).__name__ for it in ast.items]
    assert kinds == [
        "Declaration",       # const int MAX
        "TypeDeclaration",   # struct Bits
        "StaticAssertWithMessage",
        "FunctionDef",       # main
    ]
    body = ast.items[3].body.items
    body_kinds = [type(s).__name__ for s in body]
    assert "Declaration" in body_kinds
    assert "SwitchStmt" in body_kinds
    assert any(s == "AsmStmtOutInClobbers" for s in body_kinds)
