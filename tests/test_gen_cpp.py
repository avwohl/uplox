"""C++ backend: emit, compile, run, verify.

Mirrors test_gen_c.py for the C++ emitter. Skipped if no C++ compiler is
available. Phase 8 acceptance: two grammars in one binary still link clean
when expressed as C++ classes (per-grammar namespace plus anonymous-namespace
tables).
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from plox.gen.cpp import emit_cpp
from plox.lex.build import lex_from_ir
from plox.parse.grammar import compile_grammar
from plox.parse.lr1 import build_lr1
from plox.spec.reader import read_source
from plox.tables import dfa_to_json, empty_bundle, table_to_json


CXX = shutil.which("c++") or shutil.which("g++")
pytestmark = pytest.mark.skipif(CXX is None, reason="no C++ compiler available")


CALC = """
%grammar calc

%tokens
NUMBER = /[0-9]+/
PLUS   = "+"
MINUS  = "-"
STAR   = "*"
SLASH  = "/"
LPAREN = "("
RPAREN = ")"
WS     = /[ \\t\\n]+/    %skip

%rules
expr  : expr PLUS term | expr MINUS term | term ;
term  : term STAR factor | term SLASH factor | factor ;
factor : NUMBER | LPAREN expr RPAREN ;
"""


TINY = """
%grammar tiny
%tokens
A = "a"
B = "b"
%rules
s : A B
  | A s B
  ;
"""


def build_bundle(src: str) -> dict:
    ir = read_source(src)
    dfa, tokens, skip = lex_from_ir(ir)
    table = build_lr1(compile_grammar(ir))
    bundle = empty_bundle(ir.name)
    bundle["lex"] = dfa_to_json(dfa, tokens=tokens, skip=skip)
    bundle["parse"] = table_to_json(table)
    return bundle


def emit_to_cpp(tmp_path: Path, bundle: dict, prefix: str | None = None) -> tuple[Path, Path]:
    header, impl = emit_cpp(bundle, prefix=prefix)
    name = (prefix or bundle["meta"]["grammar"]).lower()
    h = tmp_path / f"plox_{name}.hpp"
    c = tmp_path / f"plox_{name}.cpp"
    h.write_text(header)
    c.write_text(impl)
    return h, c


def cxx_compile_object(source: Path, out: Path, include: Path) -> None:
    cmd = [
        CXX, "-std=c++17", "-Wall", "-Werror", "-c", "-O1",
        "-I", str(include),
        str(source), "-o", str(out),
    ]
    subprocess.run(cmd, check=True, capture_output=True)


def cxx_compile(*sources: Path, out: Path, include: Path) -> None:
    cmd = [
        CXX, "-std=c++17", "-Wall", "-Werror", "-O1",
        "-I", str(include),
        *[str(s) for s in sources],
        "-o", str(out),
    ]
    subprocess.run(cmd, check=True, capture_output=True)


def test_cpp_compiles_clean(tmp_path):
    _h, c = emit_to_cpp(tmp_path, build_bundle(CALC))
    obj = tmp_path / "plox_calc.o"
    cxx_compile_object(c, obj, include=tmp_path)
    assert obj.exists()


def test_cpp_supports_token_filter_end_to_end(tmp_path):
    """Same shape as test_emitted_c_supports_token_filter: install a filter
    that rewrites IDENT to NAME for words starting with 'T', verify the
    parser sees the rewritten kind."""
    SRC = """
%grammar tf

%tokens
WS    = /[ \\t\\n]+/  %skip
SEMI  = ";"
IDENT = /[A-Za-z_][A-Za-z0-9_]*/
NAME  = /[A-Za-z_][A-Za-z0-9_]*/

%rules
prog : item ;
item : IDENT SEMI
     | NAME  SEMI
     ;
"""
    h, cpp = emit_to_cpp(tmp_path, build_bundle(SRC))
    header_text = h.read_text()
    impl_text = cpp.read_text()
    assert "set_token_filter" in header_text
    assert "TokenFilter" in header_text
    assert "token_filter_(" in impl_text

    driver = tmp_path / "driver.cpp"
    driver.write_text(r"""
#include "plox_tf.hpp"
#include <cstdio>
#include <string>

int main(int argc, char** argv) {
    std::string src = (argc > 1) ? argv[1] : "Tx;";
    plox::tf::Parser p(src);
    p.set_token_filter([](plox::tf::Parser&, int la_kind, std::string_view la_text) {
        if (la_kind == 3 /* IDENT */ && !la_text.empty() && la_text[0] == 'T') {
            return 4; /* NAME */
        }
        return la_kind;
    });
    if (!p.parse()) {
        fprintf(stderr, "parse error: %s\n", p.error().c_str());
        return 1;
    }
    auto* root = p.root();
    auto* item = root->children[0];
    auto* first = item->children[0];
    printf("%s\n", plox::tf::Parser::token_name(first->kind));
    return 0;
}
""")
    binary = tmp_path / "tf"
    cxx_compile(cpp, driver, out=binary, include=tmp_path)

    out = subprocess.run([str(binary), "Tx;"], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "NAME"

    out = subprocess.run([str(binary), "abc;"], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "IDENT"


def test_cpp_carries_default_reduction_table(tmp_path):
    _h, c = emit_to_cpp(tmp_path, build_bundle(CALC))
    text = c.read_text()
    assert "kDefaultReduction" in text, "default reduction array missing"
    assert "kDefaultReduction[s]" in text


CPP_CALC_DRIVER = """
#include <iostream>
#include <sstream>
#include <string>
#include "plox_calc.hpp"
using namespace plox::calc;

static int evaluate(const Node *n) {
    if (n->is_terminal) {
        if (n->kind == static_cast<int>(Token::NUMBER)) {
            return std::stoi(std::string(n->text));
        }
        return 0;
    }
    if (n->children.size() == 1) return evaluate(n->children[0]);
    if (n->children.size() == 3) {
        const Node *op = n->children[1];
        if (op->is_terminal) {
            int l = evaluate(n->children[0]);
            int r = evaluate(n->children[2]);
            switch (static_cast<Token>(op->kind)) {
                case Token::PLUS:  return l + r;
                case Token::MINUS: return l - r;
                case Token::STAR:  return l * r;
                case Token::SLASH: return r ? l / r : 0;
                default: break;
            }
        }
        if (n->children[0]->is_terminal && static_cast<Token>(n->children[0]->kind) == Token::LPAREN) {
            return evaluate(n->children[1]);
        }
    }
    return 0;
}

int main() {
    std::ostringstream os;
    os << std::cin.rdbuf();
    std::string buf = os.str();
    Parser p(buf);
    if (!p.parse()) { std::cerr << "parse error: " << p.error() << "\\n"; return 1; }
    std::cout << evaluate(p.root()) << "\\n";
    return 0;
}
"""


def build_cpp_evaluator(tmp_path: Path) -> Path:
    bundle = build_bundle(CALC)
    emit_to_cpp(tmp_path, bundle)
    driver = tmp_path / "main.cpp"
    driver.write_text(CPP_CALC_DRIVER)
    binary = tmp_path / "calc_cpp"
    cxx_compile(driver, tmp_path / "plox_calc.cpp", out=binary, include=tmp_path)
    return binary


def run_calc(binary: Path, expr: str) -> tuple[int, str, str]:
    r = subprocess.run([str(binary)], input=expr, capture_output=True, text=True, timeout=10)
    return r.returncode, r.stdout.strip(), r.stderr.strip()


def test_cpp_evaluates_simple(tmp_path):
    binary = build_cpp_evaluator(tmp_path)
    rc, out, _err = run_calc(binary, "1 + 2 + 3")
    assert rc == 0 and out == "6"


def test_cpp_precedence(tmp_path):
    binary = build_cpp_evaluator(tmp_path)
    rc, out, _err = run_calc(binary, "1 + 2 * 3")
    assert rc == 0 and out == "7"


def test_cpp_parens(tmp_path):
    binary = build_cpp_evaluator(tmp_path)
    rc, out, _err = run_calc(binary, "(1 + 2) * 3")
    assert rc == 0 and out == "9"


def test_cpp_syntax_error_returns_nonzero(tmp_path):
    binary = build_cpp_evaluator(tmp_path)
    rc, _out, err = run_calc(binary, "1 +")
    assert rc != 0
    assert "parse error" in err.lower() or "unexpected" in err.lower()


CPP_TWO_DRIVER = """
#include <cstring>
#include <cstdio>
#include "plox_calc.hpp"
#include "plox_tiny.hpp"

int main() {
    plox::calc::Parser calc("1 + 2");
    plox::tiny::Parser tiny("aabb");
    bool calc_ok = calc.parse();
    bool tiny_ok = tiny.parse();
    std::printf("calc rc=%d nt=%s\\n", calc_ok ? 0 : 1, calc_ok ? plox::calc::Parser::nt_name(calc.root()->kind) : "");
    std::printf("tiny rc=%d nt=%s\\n", tiny_ok ? 0 : 1, tiny_ok ? plox::tiny::Parser::nt_name(tiny.root()->kind) : "");
    return (calc_ok && tiny_ok) ? 0 : 1;
}
"""


def test_cpp_two_grammars_link_together(tmp_path):
    """Re-entrancy acceptance test for the C++ backend: two grammars in two
    namespaces compile and link with zero collisions, and parse simultaneously."""
    emit_to_cpp(tmp_path, build_bundle(CALC))
    emit_to_cpp(tmp_path, build_bundle(TINY))
    driver = tmp_path / "main.cpp"
    driver.write_text(CPP_TWO_DRIVER)
    binary = tmp_path / "two"
    cxx_compile(
        driver,
        tmp_path / "plox_calc.cpp",
        tmp_path / "plox_tiny.cpp",
        out=binary, include=tmp_path,
    )
    r = subprocess.run([str(binary)], capture_output=True, text=True, timeout=10)
    assert r.returncode == 0, r.stderr
    assert "calc rc=0" in r.stdout
    assert "tiny rc=0" in r.stdout


def test_cpp_invalid_prefix_rejected():
    bundle = build_bundle(CALC)
    with pytest.raises(ValueError):
        emit_cpp(bundle, prefix="bad-name")


def test_cpp_header_uses_namespace(tmp_path):
    h, _c = emit_to_cpp(tmp_path, build_bundle(CALC))
    text = h.read_text()
    assert "namespace plox::calc" in text
    assert "class Parser" in text
    assert "enum class Token" in text
    assert "enum class NonTerminal" in text
