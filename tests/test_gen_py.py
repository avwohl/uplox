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

from plox.gen.py import emit_py
from plox.lex.build import lex_from_ir
from plox.parse.grammar import compile_grammar
from plox.parse.lr1 import build_lr1
from plox.spec.reader import read_source
from plox.tables import dfa_to_json, empty_bundle, table_to_json


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
    return bundle


def _import_emitted(tmp_path: Path, src: str, prefix: str | None = None):
    bundle = _build_bundle(src)
    text = emit_py(bundle, prefix=prefix)
    name = (prefix or bundle["meta"]["grammar"]).lower()
    module_path = tmp_path / f"plox_{name}.py"
    module_path.write_text(text)
    spec = importlib.util.spec_from_file_location(f"plox_{name}", module_path)
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
    from plox.parse.runtime import ParseError

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
