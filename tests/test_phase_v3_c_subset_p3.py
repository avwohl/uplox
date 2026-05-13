"""End-to-end test for c_subset.uplox Phase 3 additions.

Phase 3 adds: for / do-while / switch / case / default / goto /
labels / asm statements, struct / union / enum types, initializer
lists (with designated initializers), variadic parameters, array
declarators (1D and N-D). Acceptance target: a program exercising
all of the above parses to a structurally correct AST.

Deferred to Phase 4: const/volatile composition, casts and
sizeof(type), typedef + typedef-name lexer hook, compound
literals, _Generic, va_arg, offsetof, bit-fields.
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
    spec = importlib.util.spec_from_file_location("uplox_c_subset_v3p3", module_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


# ---- New control flow -------------------------------------------------------


def test_for_loop_with_init_decl(tmp_path):
    mod = _build_and_import(tmp_path)
    ast = mod.parse("int main(void) { for (int i = 0; i < 10; i = i + 1) sum = sum + i; }")
    body = ast.items[0].body.items
    stmt = body[0]
    assert isinstance(stmt, mod.ForStmt)
    assert isinstance(stmt.init, mod.ForDeclInit)
    assert isinstance(stmt.condition, mod.ForCond)
    assert isinstance(stmt.update, mod.ForUpdate)


def test_for_loop_empty_pieces(tmp_path):
    mod = _build_and_import(tmp_path)
    ast = mod.parse("int main(void) { for (;;) break; }")
    stmt = ast.items[0].body.items[0]
    assert isinstance(stmt, mod.ForStmt)
    assert isinstance(stmt.init, mod.EmptyForInit)
    assert isinstance(stmt.condition, mod.EmptyForCond)
    assert isinstance(stmt.update, mod.EmptyForUpdate)


def test_do_while(tmp_path):
    mod = _build_and_import(tmp_path)
    ast = mod.parse("int main(void) { do { x = x - 1; } while (x > 0); }")
    stmt = ast.items[0].body.items[0]
    assert isinstance(stmt, mod.DoWhileStmt)
    assert isinstance(stmt.body, mod.CompoundStmt)
    assert isinstance(stmt.condition, mod.BinaryOp)


def test_switch_with_cases(tmp_path):
    mod = _build_and_import(tmp_path)
    ast = mod.parse("""
        int main(void) {
            switch (x) {
                case 1: return 1;
                case 2: return 2;
                default: return 0;
            }
        }
    """)
    body = ast.items[0].body.items
    sw = body[0]
    assert isinstance(sw, mod.SwitchStmt)
    inner = sw.body.items
    assert isinstance(inner[0], mod.CaseStmt)
    assert isinstance(inner[0].value, mod.IntLiteral)
    assert isinstance(inner[1], mod.CaseStmt)
    assert isinstance(inner[2], mod.DefaultStmt)


def test_label_and_goto(tmp_path):
    mod = _build_and_import(tmp_path)
    ast = mod.parse("int main(void) { goto end; x = 1; end: return 0; }")
    body = ast.items[0].body.items
    assert isinstance(body[0], mod.GotoStmt)
    assert body[0].label.text == "end"
    assert isinstance(body[2], mod.LabelStmt)
    assert body[2].label.text == "end"


def test_asm_stmt(tmp_path):
    mod = _build_and_import(tmp_path)
    ast = mod.parse('int main(void) { asm("nop"); return 0; }')
    body = ast.items[0].body.items
    assert isinstance(body[0], mod.AsmStmt)
    assert body[0].template.text == '"nop"'


# ---- Structs / unions / enums ----------------------------------------------


def test_struct_decl(tmp_path):
    mod = _build_and_import(tmp_path)
    ast = mod.parse("struct Point { int x; int y; };")
    decl = ast.items[0]
    assert isinstance(decl, mod.TypeDeclaration)
    assert isinstance(decl.type_spec, mod.StructDef)
    assert decl.type_spec.name.text == "Point"
    assert len(decl.type_spec.members) == 2
    for m in decl.type_spec.members:
        assert isinstance(m, mod.StructMember)


def test_struct_reference(tmp_path):
    mod = _build_and_import(tmp_path)
    ast = mod.parse("struct Point p;")
    decl = ast.items[0]
    assert isinstance(decl.type_spec, mod.StructRef)
    assert decl.type_spec.name.text == "Point"


def test_union_decl(tmp_path):
    mod = _build_and_import(tmp_path)
    ast = mod.parse("union U { int i; float f; };")
    decl = ast.items[0]
    assert isinstance(decl.type_spec, mod.UnionDef)
    assert len(decl.type_spec.members) == 2


def test_enum_with_values(tmp_path):
    mod = _build_and_import(tmp_path)
    ast = mod.parse("enum Color { RED, GREEN = 5, BLUE };")
    decl = ast.items[0]
    assert isinstance(decl.type_spec, mod.EnumDef)
    assert decl.type_spec.name.text == "Color"
    assert len(decl.type_spec.values) == 3
    # First and third: EnumValue (no init); middle: EnumValueWithInit (= 5)
    assert isinstance(decl.type_spec.values[0], mod.EnumValue)
    assert decl.type_spec.values[0].name.text == "RED"
    assert isinstance(decl.type_spec.values[1], mod.EnumValueWithInit)
    assert decl.type_spec.values[1].name.text == "GREEN"


def test_enum_trailing_comma(tmp_path):
    mod = _build_and_import(tmp_path)
    ast = mod.parse("enum Status { OK, ERR, };")
    decl = ast.items[0]
    assert isinstance(decl.type_spec, mod.EnumDef)
    assert len(decl.type_spec.values) == 2


# ---- Initializer lists -----------------------------------------------------


def test_array_initializer(tmp_path):
    mod = _build_and_import(tmp_path)
    ast = mod.parse("int main(void) { int arr[3] = {10, 20, 30}; }")
    decl = ast.items[0].body.items[0]
    init_decl = decl.declarators[0]
    assert isinstance(init_decl, mod.InitDeclaratorWithInit)
    assert isinstance(init_decl.init, mod.InitializerList)
    assert len(init_decl.init.items) == 3
    assert all(isinstance(x, mod.IntLiteral) for x in init_decl.init.items)


def test_designated_initializer(tmp_path):
    mod = _build_and_import(tmp_path)
    ast = mod.parse("int main(void) { struct Point p = {.x = 1, .y = 2}; }")
    decl = ast.items[0].body.items[0]
    init = decl.declarators[0].init
    assert isinstance(init, mod.InitializerList)
    assert len(init.items) == 2
    di = init.items[0]
    assert isinstance(di, mod.DesignatedInit)
    assert isinstance(di.designators[0], mod.FieldDesignator)
    assert di.designators[0].field.text == "x"


def test_indexed_designated_initializer(tmp_path):
    mod = _build_and_import(tmp_path)
    ast = mod.parse("int main(void) { int a[4] = {[2] = 99}; }")
    di = ast.items[0].body.items[0].declarators[0].init.items[0]
    assert isinstance(di, mod.DesignatedInit)
    assert isinstance(di.designators[0], mod.IndexDesignator)


def test_empty_initializer(tmp_path):
    mod = _build_and_import(tmp_path)
    ast = mod.parse("int main(void) { int a[1] = {}; }")
    init = ast.items[0].body.items[0].declarators[0].init
    assert isinstance(init, mod.InitializerList)
    assert init.items == []


# ---- Variadic params -------------------------------------------------------


def test_variadic_function(tmp_path):
    mod = _build_and_import(tmp_path)
    ast = mod.parse("int printf(char *fmt, ...);")
    decl = ast.items[0]
    assert isinstance(decl, mod.Declaration)
    fn_decl = decl.declarators[0].declarator
    # FnDeclarator with VariadicParams wrapping the regular params list
    assert isinstance(fn_decl, mod.FnDeclarator)
    assert isinstance(fn_decl.params, mod.VariadicParams)
    assert len(fn_decl.params.params) == 1


# ---- Array declarators -----------------------------------------------------


def test_1d_array(tmp_path):
    mod = _build_and_import(tmp_path)
    ast = mod.parse("int arr[10];")
    init_decl = ast.items[0].declarators[0]
    assert isinstance(init_decl.declarator, mod.ArrayDeclarator)
    assert init_decl.declarator.name.text == "arr"


def test_2d_array(tmp_path):
    mod = _build_and_import(tmp_path)
    ast = mod.parse("int matrix[3][4];")
    init_decl = ast.items[0].declarators[0]
    assert isinstance(init_decl.declarator, mod.ArrayDeclaratorNd)
    assert len(init_decl.declarator.more) == 1


def test_unsized_array(tmp_path):
    """`int arr[] = {1, 2, 3}` — size inferred from initializer."""
    mod = _build_and_import(tmp_path)
    ast = mod.parse("int arr[] = {1, 2, 3};")
    init_decl = ast.items[0].declarators[0]
    assert isinstance(init_decl.declarator, mod.ArrayDeclaratorUnsized)


# ---- Big integration test --------------------------------------------------


def test_complex_program(tmp_path):
    """All Phase 3 features in one program."""
    mod = _build_and_import(tmp_path)
    src = """
        struct Point { int x; int y; };

        enum Color { RED, GREEN = 5, BLUE };

        int printf(char *fmt, ...);

        int main(void) {
            struct Point p = {.x = 1, .y = 2};
            int arr[3] = {10, 20, 30};
            int sum = 0;
            for (int i = 0; i < 3; i = i + 1) {
                sum = sum + arr[i];
            }
            do {
                sum = sum - 1;
            } while (sum > 0);
            switch (sum) {
                case 0:
                    goto done;
                case 1:
                    break;
                default:
                    sum = -1;
            }
        done:
            return sum;
        }
    """
    ast = mod.parse(src)
    assert isinstance(ast, mod.TranslationUnit)
    assert len(ast.items) == 4
    assert isinstance(ast.items[0], mod.TypeDeclaration)   # struct
    assert isinstance(ast.items[1], mod.TypeDeclaration)   # enum
    assert isinstance(ast.items[2], mod.Declaration)        # printf decl
    assert isinstance(ast.items[3], mod.FunctionDef)        # main
    body = ast.items[3].body.items
    # main has: 3 local decls (Point, arr, sum), for, do-while, switch, label
    kinds = [type(s).__name__ for s in body]
    assert kinds == [
        "Declaration", "Declaration", "Declaration",
        "ForStmt", "DoWhileStmt", "SwitchStmt", "LabelStmt",
    ]
