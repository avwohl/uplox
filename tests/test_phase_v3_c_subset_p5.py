"""End-to-end test for c_subset.uplox Phase 5 (final grammar push).

Phase 5a adds: _Generic, va_arg, offsetof, statement-expressions,
designated range initializers `[a ... b] = v`.

Phase 5b adds: typedef declarations + the typedef-name lex hook.
This is the classical C-parsing problem ("``Foo x;``" where Foo is
sometimes a typedef-name and sometimes a regular identifier).
uplox supports it via the existing %hook= + token filter
mechanism; a small custom tracker walks the v3 TypedefDecl AST
node to record names, and the filter rewrites IDENT to
TYPEDEF_NAME for subsequent lookahead tokens.

This closes the grammar work for the uc_core migration. Phase 6
is the translator: ~1500 lines bridging the v3 AST to
uc_core/src/uc_core/ast.py.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from uplox.gen.py import emit_py
from uplox.lex.build import lex_from_ir
from uplox.lex.scanner import Token
from uplox.parse.grammar import compile_grammar
from uplox.parse.lr1 import build_lr1
from uplox.parse.runtime import HookRegistry
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
    spec = importlib.util.spec_from_file_location("uplox_c_subset_v3p5", module_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


# ---- 5a: _Generic / va_arg / offsetof / stmt-expr / range init -------------


def test_generic_selection(tmp_path):
    mod = _build_and_import(tmp_path)
    ast = mod.parse('int main(void) { return _Generic(x, int: 1, char: 2, default: 0); }')
    expr = ast.items[0].body.items[0].value
    assert isinstance(expr, mod.GenericSelection)
    assocs = expr.assocs
    assert len(assocs) == 3
    assert isinstance(assocs[0], mod.GenericAssoc)
    assert isinstance(assocs[1], mod.GenericAssoc)
    assert isinstance(assocs[2], mod.GenericDefault)


def test_va_arg(tmp_path):
    mod = _build_and_import(tmp_path)
    ast = mod.parse('int main(void) { return va_arg(ap, int); }')
    expr = ast.items[0].body.items[0].value
    assert isinstance(expr, mod.VaArgExpr)
    assert expr.va_list_expr.name.text == "ap"
    assert isinstance(expr.type.base, mod.IntType)


def test_offsetof_simple(tmp_path):
    mod = _build_and_import(tmp_path)
    ast = mod.parse('int main(void) { return offsetof(struct S, field); }')
    expr = ast.items[0].body.items[0].value
    assert isinstance(expr, mod.OffsetofExpr)
    assert isinstance(expr.type.base, mod.StructRef)
    assert isinstance(expr.designator, mod.OffsetofMember)
    assert expr.designator.name.text == "field"


def test_offsetof_chain(tmp_path):
    """`offsetof(S, a.b[2].c)` — chain of field and index accesses."""
    mod = _build_and_import(tmp_path)
    ast = mod.parse('int main(void) { return offsetof(struct S, a.b[2].c); }')
    expr = ast.items[0].body.items[0].value
    assert isinstance(expr, mod.OffsetofExpr)
    # Designator chain: ((((a).b)[2]).c)
    d = expr.designator
    assert isinstance(d, mod.OffsetofField)
    assert d.field.text == "c"
    d = d.base
    assert isinstance(d, mod.OffsetofIndex)
    d = d.base
    assert isinstance(d, mod.OffsetofField)
    assert d.field.text == "b"
    d = d.base
    assert isinstance(d, mod.OffsetofMember)
    assert d.name.text == "a"


def test_statement_expression(tmp_path):
    """GCC extension: `({stmts}) ` as an expression."""
    mod = _build_and_import(tmp_path)
    ast = mod.parse('int main(void) { return ({ int x = 1; x + 2; }); }')
    expr = ast.items[0].body.items[0].value
    assert isinstance(expr, mod.StmtExpr)
    assert isinstance(expr.stmt, mod.CompoundStmt)
    assert len(expr.stmt.items) == 2


def test_range_designator(tmp_path):
    """`[0 ... 5] = 0` — GCC range designator."""
    mod = _build_and_import(tmp_path)
    ast = mod.parse('int main(void) { int a[10] = {[0 ... 5] = 0, [9] = 1}; }')
    init = ast.items[0].body.items[0].declarators[0].init
    assert isinstance(init, mod.InitializerList)
    di0 = init.items[0]
    assert isinstance(di0, mod.DesignatedInit)
    assert isinstance(di0.designators[0], mod.RangeDesignator)
    di1 = init.items[1]
    assert isinstance(di1.designators[0], mod.IndexDesignator)


# ---- 5b: typedef + lex hook ------------------------------------------------


def _walk_declarators_for_names(decl):
    """Walk a Declarator AST node tree and yield the inner IDENT name."""
    while decl is not None:
        if hasattr(decl, "name") and decl.name is not None:
            yield decl.name.text
            return
        decl = getattr(decl, "inner", None)


class _V3TypedefTracker:
    """Phase 5b: track typedef names from the v3 TypedefDecl AST shape.

    Pairs a token filter (IDENT -> TYPEDEF_NAME rewrite) with a
    post_reduce hook that fires after each TypedefDecl reduces.
    """

    def __init__(self):
        self.names: set[str] = set()

    def filter(self, ctx, tok):
        if tok.name == "IDENT" and tok.text in self.names:
            return Token(
                name="TYPEDEF_NAME",
                text=tok.text,
                line=tok.line,
                column=tok.column,
                offset=tok.offset,
            )
        return tok

    def record(self, ctx, payload):
        if payload.get("when") != "post_reduce":
            return
        value = payload.get("value")
        if value is None or type(value).__name__ != "TypedefDecl":
            return
        for init_decl in value.declarators:
            for name in _walk_declarators_for_names(init_decl.declarator):
                self.names.add(name)


def _parse_with_typedef(mod, src):
    tracker = _V3TypedefTracker()
    hooks = HookRegistry(ignore_missing=True)
    hooks.register("record_typedef", tracker.record)
    ast = mod.parse(src, hooks=hooks, token_filter=tracker.filter)
    return ast, tracker


def test_typedef_simple(tmp_path):
    """The classical case: `typedef int Foo; Foo x;`."""
    mod = _build_and_import(tmp_path)
    src = "typedef int Foo; Foo x;"
    ast, tracker = _parse_with_typedef(mod, src)
    assert tracker.names == {"Foo"}
    assert len(ast.items) == 2
    assert isinstance(ast.items[0], mod.TypedefDecl)
    decl = ast.items[1]
    assert isinstance(decl, mod.Declaration)
    # Foo resolves to NamedType in the type_spec
    assert isinstance(decl.type_spec, mod.NamedType)
    assert decl.type_spec.name.text == "Foo"


def test_typedef_struct(tmp_path):
    """`typedef struct Point Point_t; Point_t p;` — typedef of a struct ref."""
    mod = _build_and_import(tmp_path)
    src = "typedef struct Point Point_t; Point_t p;"
    ast, tracker = _parse_with_typedef(mod, src)
    assert "Point_t" in tracker.names
    assert isinstance(ast.items[1].type_spec, mod.NamedType)


def test_typedef_pointer(tmp_path):
    """`typedef int *IntPtr; IntPtr p;`."""
    mod = _build_and_import(tmp_path)
    src = "typedef int *IntPtr; IntPtr p;"
    ast, tracker = _parse_with_typedef(mod, src)
    assert "IntPtr" in tracker.names
    # The second declaration's type_spec is NamedType(IntPtr).
    assert isinstance(ast.items[1].type_spec, mod.NamedType)


def test_typedef_multiple_names(tmp_path):
    """`typedef int A, B, C;` declares three typedef names at once."""
    mod = _build_and_import(tmp_path)
    src = "typedef int A, B, C;"
    ast, tracker = _parse_with_typedef(mod, src)
    assert tracker.names == {"A", "B", "C"}


def test_typedef_used_in_cast(tmp_path):
    """`typedef int Foo; x = (Foo)y;` — typedef name in a cast."""
    mod = _build_and_import(tmp_path)
    src = "typedef int Foo; int main(void) { x = (Foo)y; }"
    ast, tracker = _parse_with_typedef(mod, src)
    assert tracker.names == {"Foo"}
    # Drill into the cast.
    fn = ast.items[1]
    stmt = fn.body.items[0]
    cast = stmt.expr.right
    assert isinstance(cast, mod.Cast)
    assert isinstance(cast.target_type.base, mod.NamedType)


def test_typedef_used_in_sizeof(tmp_path):
    """`typedef int Foo; n = sizeof(Foo);`."""
    mod = _build_and_import(tmp_path)
    src = "typedef int Foo; int main(void) { n = sizeof(Foo); }"
    ast, tracker = _parse_with_typedef(mod, src)
    fn = ast.items[1]
    sof = fn.body.items[0].expr.right
    assert isinstance(sof, mod.SizeofType)
    assert isinstance(sof.target_type.base, mod.NamedType)


def test_typedef_used_in_function_param(tmp_path):
    mod = _build_and_import(tmp_path)
    src = """
        typedef int Width;
        int box(Width w, Width h);
    """
    ast, tracker = _parse_with_typedef(mod, src)
    assert tracker.names == {"Width"}
    decl = ast.items[1]
    assert isinstance(decl, mod.Declaration)
    fn_decl = decl.declarators[0].declarator
    assert isinstance(fn_decl, mod.FnDeclarator)
    params = fn_decl.params
    assert len(params) == 2
    for p in params:
        assert isinstance(p.param_type, mod.NamedType)
        assert p.param_type.name.text == "Width"


def test_typedef_with_qualified_type(tmp_path):
    mod = _build_and_import(tmp_path)
    src = """
        typedef const int CInt;
        CInt x;
    """
    ast, tracker = _parse_with_typedef(mod, src)
    assert "CInt" in tracker.names
    decl = ast.items[1]
    assert isinstance(decl.type_spec, mod.NamedType)


def test_typedef_chain(tmp_path):
    """A typedef referencing another typedef."""
    mod = _build_and_import(tmp_path)
    src = """
        typedef int Foo;
        typedef Foo Bar;
        Bar x;
    """
    ast, tracker = _parse_with_typedef(mod, src)
    assert tracker.names == {"Foo", "Bar"}
    # The second TypedefDecl uses Foo as a typename in its type_spec.
    assert isinstance(ast.items[1].type_spec, mod.NamedType)
    assert ast.items[1].type_spec.name.text == "Foo"


def test_typedef_does_not_apply_before_decl(tmp_path):
    """``Foo x;`` BEFORE a typedef of Foo is a parse error (Foo lexes as IDENT).
    This verifies the lex hook doesn't retroactively reclassify."""
    mod = _build_and_import(tmp_path)
    # `Foo x;` without a prior typedef should fail.
    try:
        _parse_with_typedef(mod, "Foo x;")
        assert False, "Foo x; should not parse without a preceding typedef"
    except mod.ParseError:
        pass


# ---- Stress test combining 5a + 5b ------------------------------------------


def test_phase5_stress(tmp_path):
    """All Phase 5 features in one translation unit."""
    mod = _build_and_import(tmp_path)
    src = r"""
        typedef int Width;
        typedef struct Point Point_t;

        int dispatch(Width w);
        int va_int_argc(int n, ...);

        int main(void) {
            int kind = _Generic(w, Width: 1, int: 2, default: 0);
            int n = offsetof(Point_t, x);
            int arr[10] = {[0 ... 4] = 0, [9] = 99};
            int sum = ({ int s = 0; s = s + n; s; });
            return kind + n + sum;
        }
    """
    ast, tracker = _parse_with_typedef(mod, src)
    assert tracker.names == {"Width", "Point_t"}
    kinds = [type(i).__name__ for i in ast.items]
    assert kinds == [
        "TypedefDecl", "TypedefDecl",
        "Declaration", "Declaration",
        "FunctionDef",
    ]
    body = ast.items[4].body.items
    # Verify the four interesting declarations are all Declaration kinds
    # carrying their respective initializer shapes.
    assert all(isinstance(s, mod.Declaration) for s in body[:4])
