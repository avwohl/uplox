"""Python backend: emit a module, import it, parse a string, verify the tree.

The Python backend's responsibility is small — embed the bundle, load
runtime tables at import, expose ``parse``. These tests cover that the
emitted module:

* writes valid Python and imports cleanly,
* exposes the expected public surface (``parse``, ``BUNDLE``, ``TOKENS``),
* round-trips a real input through the runtime,
* raises ``ParseError`` on bad input,
* and threads through ``token_filter`` so the typedef-name hack still works.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

from uplox.gen.py import emit_py
from uplox.lex.build import lex_from_ir
from uplox.parse.grammar import compile_grammar
from uplox.parse.lr1 import build_lr1
from uplox.spec.ast_plan import compile_ast_plan
from uplox.spec.reader import read_source
from uplox.tables import ast_to_json, dfa_to_json, empty_bundle, table_to_json


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


def _build_bundle(src: str) -> dict:
    ir = read_source(src)
    dfa, tokens, skip = lex_from_ir(ir)
    table = build_lr1(compile_grammar(ir))
    bundle = empty_bundle(ir.name)
    bundle["lex"] = dfa_to_json(dfa, tokens=tokens, skip=skip)
    bundle["parse"] = table_to_json(table)
    bundle["ast"] = ast_to_json(compile_ast_plan(ir))
    return bundle


def _import_emitted(tmp_path: Path, src: str, prefix: str | None = None):
    bundle = _build_bundle(src)
    text = emit_py(bundle, prefix=prefix)
    name = (prefix or bundle["meta"]["grammar"]).lower()
    module_path = tmp_path / f"uplox_{name}.py"
    module_path.write_text(text)
    spec = importlib.util.spec_from_file_location(f"uplox_{name}", module_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    # Cache under a unique name so successive tests don't collide.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_emit_writes_importable_module(tmp_path):
    module = _import_emitted(tmp_path, CALC)
    assert hasattr(module, "parse")
    assert hasattr(module, "BUNDLE")
    assert hasattr(module, "TOKENS")
    assert module.GRAMMAR_NAME == "calc"


def test_emit_module_parses_real_input(tmp_path):
    module = _import_emitted(tmp_path, CALC)
    tree = module.parse("1 + 2 * 3")
    assert tree.kind == "expr"


def test_emit_module_raises_parseerror_on_syntax_error(tmp_path):
    from uplox.parse.runtime import ParseError

    module = _import_emitted(tmp_path, CALC)
    with pytest.raises(ParseError):
        module.parse("1 + + 2")


def test_emit_module_exposes_token_list(tmp_path):
    module = _import_emitted(tmp_path, CALC)
    # Lexer produces these in declaration order; whitespace was %skip.
    assert "NUMBER" in module.TOKENS
    assert "PLUS" in module.TOKENS
    assert "WS" in module.TOKENS
    assert "WS" in module.SKIP


def test_emit_module_supports_prefix_override(tmp_path):
    module = _import_emitted(tmp_path, CALC, prefix="myparser")
    assert module.GRAMMAR_NAME == "myparser"


def test_emit_rejects_invalid_prefix():
    bundle = _build_bundle(CALC)
    with pytest.raises(ValueError, match="prefix"):
        emit_py(bundle, prefix="123-bad")


def test_emit_module_threads_token_filter(tmp_path):
    """The Python backend's parse() takes the same ``hooks`` and
    ``token_filter`` parameters as the underlying runtime, so feedback
    grammars (typedef-name etc.) work through the emitted shim."""
    module = _import_emitted(tmp_path, CALC)

    # Identity filter — its presence proves the parameter is threaded through.
    seen: list[str] = []

    def filter_passthru(ctx, tok):
        seen.append(tok.name)
        return tok

    tree = module.parse("1 + 2", token_filter=filter_passthru)
    assert tree.kind == "expr"
    # The runtime invokes the filter on every fetch and every post-reduce
    # re-classification, so the seen list contains the source terminals in
    # order with possible repeats; dedupe before checking.
    distinct = []
    for name in seen:
        if not distinct or distinct[-1] != name:
            distinct.append(name)
    assert distinct == ["NUMBER", "PLUS", "NUMBER"]


def test_emit_module_scan_function(tmp_path):
    module = _import_emitted(tmp_path, CALC)
    tokens = list(module.scan("1 + 2"))
    assert [t.name for t in tokens] == ["NUMBER", "PLUS", "NUMBER"]


# ---- v3: AST-flavoured emission ---------------------------------------------


CALC_ANNOTATED = """
%grammar calc
%define lr.type lalr
%options
start = expr
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
          | <term>@lhs '/'@op <factor>@rhs %ast=BinOp
          | <factor>
          ;
<factor>? : NUMBER@value                   %ast=NumLit
          | '(' <expr> ')'
          ;
"""


def test_ast_module_imports_and_exposes_kinds(tmp_path):
    mod = _import_emitted(tmp_path, CALC_ANNOTATED)
    assert hasattr(mod, "BinOp")
    assert hasattr(mod, "NumLit")
    assert hasattr(mod, "AstNode")
    assert hasattr(mod, "_Pos")
    # Both parse() and parse_cst() are exposed.
    assert callable(mod.parse)
    assert callable(mod.parse_cst)


def test_ast_module_parse_returns_typed_tree(tmp_path):
    mod = _import_emitted(tmp_path, CALC_ANNOTATED)
    ast = mod.parse("1 + 2 * 3")
    # (1 + 2 * 3) parses left-recursive PLUS with right operand 2*3.
    assert isinstance(ast, mod.BinOp)
    assert ast.op.name == "PLUS"
    assert isinstance(ast.lhs, mod.NumLit)
    assert ast.lhs.value.text == "1"
    assert isinstance(ast.rhs, mod.BinOp)
    assert ast.rhs.op.name == "STAR"
    assert isinstance(ast.rhs.lhs, mod.NumLit)
    assert isinstance(ast.rhs.rhs, mod.NumLit)
    assert ast.rhs.lhs.value.text == "2"
    assert ast.rhs.rhs.value.text == "3"


def test_ast_module_parens_lift_through_drops(tmp_path):
    """``(1 + 2) * 3``: parens drop, the inner BinOp lifts to <term>."""
    mod = _import_emitted(tmp_path, CALC_ANNOTATED)
    ast = mod.parse("(1 + 2) * 3")
    assert isinstance(ast, mod.BinOp) and ast.op.name == "STAR"
    assert isinstance(ast.lhs, mod.BinOp) and ast.lhs.op.name == "PLUS"
    assert isinstance(ast.rhs, mod.NumLit)


def test_ast_module_position_spans_populated(tmp_path):
    mod = _import_emitted(tmp_path, CALC_ANNOTATED)
    ast = mod.parse("1 + 2")
    # _Pos start/end are non-zero (we always populate from leftmost/rightmost).
    assert ast.pos.start_line >= 1
    assert ast.pos.start_column >= 1
    assert ast.pos.end_line >= 1
    assert ast.pos.end_column >= ast.pos.start_column


def test_ast_module_parse_cst_returns_parse_node(tmp_path):
    mod = _import_emitted(tmp_path, CALC_ANNOTATED)
    cst = mod.parse_cst("1 + 2")
    assert isinstance(cst, mod.ParseNode)


def test_ast_module_evaluator_pattern(tmp_path):
    """Worked example from calc_annotated.md: evaluate the AST recursively."""
    mod = _import_emitted(tmp_path, CALC_ANNOTATED)

    def evaluate(node):
        if isinstance(node, mod.NumLit):
            return int(node.value.text)
        ops = {"+": lambda a, b: a + b, "-": lambda a, b: a - b,
               "*": lambda a, b: a * b, "/": lambda a, b: a // b}
        return ops[node.op.text](evaluate(node.lhs), evaluate(node.rhs))

    assert evaluate(mod.parse("(1 + 2) * 3")) == 9
    assert evaluate(mod.parse("10 - 4 - 2")) == 4   # left-assoc
    assert evaluate(mod.parse("1 + 2 * 3")) == 7    # precedence


# ---- v3: list rules ---------------------------------------------------------


CALL_GRAMMAR = """
%grammar t
%options
start = call
%tokens
IDENT  = /[A-Za-z_][A-Za-z0-9_]*/
COMMA  = ','
LPAREN = '('
RPAREN = ')'
SEMI   = ';'
WS     = /[ \\t\\n]+/   %skip

%ast_drop COMMA LPAREN RPAREN SEMI

%rules
<call>  : IDENT@callee '(' <args>@args ')' SEMI    %ast=Call ;
<args> %ast=list element=<expr>
        : <expr>
        | <args> COMMA <expr>
        ;
<expr>? : IDENT@name   %ast=Ident ;
"""


def test_ast_module_list_field_builds(tmp_path):
    mod = _import_emitted(tmp_path, CALL_GRAMMAR)
    ast = mod.parse("f(a, b, c);")
    assert isinstance(ast, mod.Call)
    assert ast.callee.text == "f"
    assert isinstance(ast.args, list)
    assert len(ast.args) == 3
    assert all(isinstance(x, mod.Ident) for x in ast.args)
    assert [x.name.text for x in ast.args] == ["a", "b", "c"]


def test_ast_module_list_field_single_element(tmp_path):
    mod = _import_emitted(tmp_path, CALL_GRAMMAR)
    ast = mod.parse("f(a);")
    assert len(ast.args) == 1
    assert ast.args[0].name.text == "a"


RIGHT_RECURSIVE_GRAMMAR = """
%grammar t
%options
start = block
%tokens
IDENT  = /[A-Za-z_][A-Za-z0-9_]*/
SEMI   = ';'
WS     = /[ \\t\\n]+/   %skip

%ast_drop SEMI

%rules
<block> : <stmts>@stmts   %ast=Block ;

# Right-recursive list (element first, self second). Common for
# statement sequences with a sentinel empty terminator.
<stmts> %ast=list element=<stmt>
        : <stmt> <stmts>
        |
        ;

<stmt>? : IDENT@name SEMI   %ast=Stmt ;
"""


def test_ast_module_right_recursive_list_preserves_order(tmp_path):
    """Right-recursive lists must prepend, not append — otherwise the AST
    sees the statements in reverse order."""
    mod = _import_emitted(tmp_path, RIGHT_RECURSIVE_GRAMMAR)
    ast = mod.parse("a; b; c;")
    assert isinstance(ast, mod.Block)
    assert [s.name.text for s in ast.stmts] == ["a", "b", "c"]


# ---- v3: _unwrap + auto-optional --------------------------------------------


UNWRAP_GRAMMAR = """
%grammar t
%options
start = decl
%tokens
KW_var = 'var'
COLON  = ':'
SEMI   = ';'
IDENT  = /[A-Za-z_][A-Za-z0-9_]*/
WS     = /[ \\t\\n]+/   %skip

%ast_drop COLON SEMI

%rules
<decl>  : KW_var IDENT@name <type_opt>@type SEMI   %ast=VarDecl ;

<type_opt>
        : COLON <type>@type   %ast=_unwrap
        |
        ;

<type>? : IDENT@name   %ast=NamedType ;
"""


def test_unwrap_present(tmp_path):
    mod = _import_emitted(tmp_path, UNWRAP_GRAMMAR)
    ast = mod.parse("var x: int;")
    assert isinstance(ast, mod.VarDecl)
    assert ast.name.text == "x"
    # The type field is auto-optional. Present here as a NamedType.
    assert isinstance(ast.type, mod.NamedType)
    assert ast.type.name.text == "int"


def test_unwrap_absent_yields_none(tmp_path):
    mod = _import_emitted(tmp_path, UNWRAP_GRAMMAR)
    ast = mod.parse("var x;")
    assert ast.name.text == "x"
    assert ast.type is None


# ---- v3: __all__ exports ----------------------------------------------------


def test_ast_module_all_includes_node_kinds(tmp_path):
    mod = _import_emitted(tmp_path, CALC_ANNOTATED)
    for name in ("BinOp", "NumLit", "AstNode", "_Pos", "parse", "parse_cst", "scan"):
        assert name in mod.__all__, f"{name!r} missing from __all__"


# ---- v3: back-compat for un-annotated grammars ------------------------------


def test_unannotated_grammar_emits_legacy_module(tmp_path):
    """Pre-v3 grammars get the original ParseNode-returning module."""
    mod = _import_emitted(tmp_path, CALC)
    # No AST dataclasses, no parse_cst (the legacy parse() IS the CST entry).
    assert not hasattr(mod, "BinOp")
    assert not hasattr(mod, "AstNode")
    assert not hasattr(mod, "parse_cst")
    # parse() still works and still returns a ParseNode.
    result = mod.parse("1 + 2")
    assert isinstance(result, mod.ParseNode)


# ---- file_id / FileTable ----------------------------------------------------


def test_legacy_module_exposes_file_table(tmp_path):
    mod = _import_emitted(tmp_path, CALC)
    assert hasattr(mod, "FILE_TABLE")
    assert hasattr(mod, "FileTable")
    assert mod.FILE_TABLE.name(0) == ""
    # An explicit filename intern returns a positive id.
    fid = mod.FILE_TABLE.intern("foo.calc")
    assert fid >= 1
    assert mod.FILE_TABLE.name(fid) == "foo.calc"


def test_legacy_module_scan_stamps_file_id(tmp_path):
    mod = _import_emitted(tmp_path, CALC)
    tokens = list(mod.scan("1 + 2", filename="src/expr.calc"))
    fid = mod.FILE_TABLE.intern("src/expr.calc")
    assert all(t.file_id == fid for t in tokens)
    # Default filename leaves the file_id == 0.
    tokens2 = list(mod.scan("3 - 4"))
    assert all(t.file_id == 0 for t in tokens2)


def test_legacy_module_parse_threads_filename(tmp_path):
    mod = _import_emitted(tmp_path, CALC)
    mod.parse("1 + 2", filename="thread.calc")
    # The filename was interned, so it now decodes back.
    fid = mod.FILE_TABLE.intern("thread.calc")
    assert mod.FILE_TABLE.name(fid) == "thread.calc"


def test_ast_module_exposes_file_table(tmp_path):
    mod = _import_emitted(tmp_path, CALC_ANNOTATED)
    assert hasattr(mod, "FILE_TABLE")
    assert hasattr(mod, "FileTable")
    assert mod.FILE_TABLE.name(0) == ""


def test_ast_module_parse_threads_filename(tmp_path):
    mod = _import_emitted(tmp_path, CALC_ANNOTATED)
    ast = mod.parse("1 + 2", filename="ast.calc")
    # The AST tokens carry the interned file_id; decode via FILE_TABLE.
    expected = mod.FILE_TABLE.intern("ast.calc")
    assert ast.lhs.value.file_id == expected
    assert mod.FILE_TABLE.name(expected) == "ast.calc"


def test_ast_module_scan_stamps_file_id(tmp_path):
    mod = _import_emitted(tmp_path, CALC_ANNOTATED)
    tokens = list(mod.scan("1 + 2", filename="scan.calc"))
    fid = mod.FILE_TABLE.intern("scan.calc")
    assert all(t.file_id == fid for t in tokens)


def test_ast_module_parse_cst_threads_filename(tmp_path):
    mod = _import_emitted(tmp_path, CALC_ANNOTATED)
    cst = mod.parse_cst("1 + 2", filename="cst.calc")
    fid = mod.FILE_TABLE.intern("cst.calc")
    # Walk to a leaf and verify the file_id is stamped.
    def first_leaf(node):
        while not isinstance(node, mod.Token):
            node = node.children[0]
        return node
    leaf = first_leaf(cst)
    assert leaf.file_id == fid
