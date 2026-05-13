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

from uplox.gen.cpp import emit_cpp
from uplox.lex.build import lex_from_ir
from uplox.parse.grammar import compile_grammar
from uplox.parse.lr1 import build_lr1
from uplox.spec.ast_plan import compile_ast_plan
from uplox.spec.reader import read_source
from uplox.tables import ast_to_json, dfa_to_json, empty_bundle, table_to_json


CXX = shutil.which("c++") or shutil.which("g++")
pytestmark = pytest.mark.skipif(CXX is None, reason="no C++ compiler available")


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


TINY = """
%grammar tiny
%tokens
A = 'a'
B = 'b'
%rules
<s> : A B
  | A <s> B
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
    h = tmp_path / f"uplox_{name}.hpp"
    c = tmp_path / f"uplox_{name}.cpp"
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
    obj = tmp_path / "uplox_calc.o"
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
SEMI  = ';'
IDENT = /[A-Za-z_][A-Za-z0-9_]*/
NAME  = /[A-Za-z_][A-Za-z0-9_]*/

%rules
<prog> : <item> ;
<item> : IDENT SEMI
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
#include "uplox_tf.hpp"
#include <cstdio>
#include <string>

int main(int argc, char** argv) {
    std::string src = (argc > 1) ? argv[1] : "Tx;";
    uplox::tf::Parser p(src);
    p.set_token_filter([](uplox::tf::Parser&, int la_kind, std::string_view la_text) {
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
    printf("%s\n", uplox::tf::Parser::token_name(first->kind));
    return 0;
}
""")
    binary = tmp_path / "tf"
    cxx_compile(cpp, driver, out=binary, include=tmp_path)

    out = subprocess.run([str(binary), "Tx;"], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "NAME"

    out = subprocess.run([str(binary), "abc;"], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "IDENT"


def test_cpp_typedef_name_hack_end_to_end(tmp_path):
    """Same shape as the C-side typedef hack, expressed with std::function
    closures that capture the typedef set."""
    SRC = """
%grammar tnh

%tokens
WS         = /[ \\t\\n]+/  %skip
KW_TYPEDEF = 'typedef'
SEMI       = ';'
IDENT      = /[A-Za-z_][A-Za-z0-9_]*/
TNAME      = /[A-Za-z_][A-Za-z0-9_]*/

%rules
<prog> : <decls> ;
<decls> : <decls> <decl> | <decl> ;
<decl> : KW_TYPEDEF IDENT SEMI
     | TNAME SEMI
     ;
"""
    _h, cpp = emit_to_cpp(tmp_path, build_bundle(SRC))
    text = cpp.read_text()
    assert "post_reduce_" in text

    driver = tmp_path / "driver.cpp"
    driver.write_text(r"""
#include "uplox_tnh.hpp"
#include <cstdio>
#include <set>
#include <string>

int main(int argc, char** argv) {
    std::string src = (argc > 1) ? argv[1] : "typedef Foo; Foo;";
    uplox::tnh::Parser p(src);
    auto typedefs = std::make_shared<std::set<std::string>>();

    p.set_token_filter([typedefs](uplox::tnh::Parser&, int kind, std::string_view text) {
        // UPLOX_TNH_TOK_IDENT and TNAME aren't exposed as constants, but the
        // alphabetical / declaration order makes their indices known: $=0, WS=1,
        // KW_TYPEDEF=2, SEMI=3, IDENT=4, TNAME=5.
        if (kind == 4 /* IDENT */ && typedefs->count(std::string(text))) {
            return 5; /* TNAME */
        }
        return kind;
    });
    p.set_post_reduce([typedefs](uplox::tnh::Parser&, int prod, uplox::tnh::Node* node) {
        // Production 4: `decl : KW_TYPEDEF IDENT SEMI`.
        if (prod == 4 && node->children.size() >= 2) {
            typedefs->insert(std::string(node->children[1]->text));
        }
    });

    if (!p.parse()) {
        fprintf(stderr, "parse error: %s\n", p.error().c_str());
        return 1;
    }
    auto* root = p.root();
    auto* decls = root->children[0];
    // Linearise left-recursive `decls : decls decl | decl`.
    std::vector<uplox::tnh::Node*> items;
    uplox::tnh::Node* cur = decls;
    while (cur && !cur->is_terminal) {
        if (cur->children.size() == 2) {
            items.push_back(cur->children[1]);
            cur = cur->children[0];
        } else {
            items.push_back(cur->children[0]);
            break;
        }
    }
    for (int i = (int)items.size() - 1; i >= 0; --i) {
        printf("decl%d: prod=%d\n", (int)items.size() - 1 - i, items[i]->production);
    }
    return 0;
}
""")
    binary = tmp_path / "tnh"
    cxx_compile(cpp, driver, out=binary, include=tmp_path)

    out = subprocess.run([str(binary), "typedef Foo; Foo;"],
                         capture_output=True, text=True, check=True)
    lines = out.stdout.strip().split("\n")
    assert lines[0].endswith("prod=4"), lines
    assert lines[1].endswith("prod=5"), lines


def test_cpp_carries_default_reduction_table(tmp_path):
    _h, c = emit_to_cpp(tmp_path, build_bundle(CALC))
    text = c.read_text()
    assert "kDefaultReduction" in text, "default reduction array missing"
    assert "kDefaultReduction[s]" in text


CPP_CALC_DRIVER = """
#include <iostream>
#include <sstream>
#include <string>
#include "uplox_calc.hpp"
using namespace uplox::calc;

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
    cxx_compile(driver, tmp_path / "uplox_calc.cpp", out=binary, include=tmp_path)
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


def test_cpp_syntax_error_lists_expected_tokens(tmp_path):
    """Same shape as the Python runtime and C backend: error includes the
    expected-token list, end-of-input is rendered as text not as '$'."""
    binary = build_cpp_evaluator(tmp_path)
    rc, _out, err = run_calc(binary, "1 +")
    assert rc != 0
    assert "unexpected end of input" in err
    assert "expected one of:" in err
    assert "NUMBER" in err and "LPAREN" in err
    assert "$" not in err


CPP_TWO_DRIVER = """
#include <cstring>
#include <cstdio>
#include "uplox_calc.hpp"
#include "uplox_tiny.hpp"

int main() {
    uplox::calc::Parser calc("1 + 2");
    uplox::tiny::Parser tiny("aabb");
    bool calc_ok = calc.parse();
    bool tiny_ok = tiny.parse();
    std::printf("calc rc=%d nt=%s\\n", calc_ok ? 0 : 1, calc_ok ? uplox::calc::Parser::nt_name(calc.root()->kind) : "");
    std::printf("tiny rc=%d nt=%s\\n", tiny_ok ? 0 : 1, tiny_ok ? uplox::tiny::Parser::nt_name(tiny.root()->kind) : "");
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
        tmp_path / "uplox_calc.cpp",
        tmp_path / "uplox_tiny.cpp",
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
    assert "namespace uplox::calc" in text
    assert "class Parser" in text
    assert "enum class Token" in text
    assert "enum class NonTerminal" in text


# ---- balanced-bracket tokens (%balanced=) ----------------------------------


BAL_GRAMMAR_CPP = """
%grammar bal

%tokens
WS     = /[ \\t\\n]+/    %skip
IDENT  = /[A-Za-z_][A-Za-z0-9_]*/
ACTION = '{' %balanced='}'

%rules
<top>  : <top> <item> | <item> ;
<item> : IDENT ACTION ;
"""


def build_bal_bundle_cpp() -> dict:
    from uplox.lex.build import balanced_tokens

    ir = read_source(BAL_GRAMMAR_CPP)
    dfa, tokens, skip = lex_from_ir(ir)
    table = build_lr1(compile_grammar(ir))
    bundle = empty_bundle(ir.name)
    bundle["lex"] = dfa_to_json(
        dfa, tokens=tokens, skip=skip, balanced=balanced_tokens(ir)
    )
    bundle["parse"] = table_to_json(table)
    return bundle


def test_cpp_supports_balanced_token(tmp_path):
    """End-to-end: emit C++ for a grammar with `%balanced="}"`, compile, run.
    The scanner must consume each balanced run as a single ACTION token,
    so an action body with nested `{}` ends in one token, not several."""
    bundle = build_bal_bundle_cpp()
    _h, c = emit_to_cpp(tmp_path, bundle)
    assert "kTokenBalanced" in c.read_text()

    driver = tmp_path / "main.cpp"
    driver.write_text(r"""
#include <cstdio>
#include <cstring>
#include "uplox_bal.hpp"

using namespace uplox::bal;

static void count_actions(const Node* n, int* lens, int cap, int& cnt) {
    if (!n) return;
    if (n->is_terminal) {
        if (n->kind == static_cast<int>(Token::ACTION)) {
            if (cnt < cap) lens[cnt] = static_cast<int>(n->text.size());
            ++cnt;
        }
        return;
    }
    for (auto* c : n->children) count_actions(c, lens, cap, cnt);
}

int main(int argc, char** argv) {
    const char* src = (argc > 1) ? argv[1] : "";
    Parser p(src);
    if (!p.parse()) {
        std::fprintf(stderr, "parse error: %s\n", p.error().c_str());
        return 1;
    }
    int lens[16]; int cnt = 0;
    count_actions(p.root(), lens, 16, cnt);
    std::printf("actions=%d", cnt);
    for (int i = 0; i < cnt; ++i) std::printf(" len%d=%d", i, lens[i]);
    std::printf("\n");
    return 0;
}
""")
    binary = tmp_path / "bal"
    cxx_compile(driver, c, out=binary, include=tmp_path)

    src = "first { with { nested } stuff } second { simple }"
    out = subprocess.run([str(binary), src], capture_output=True, text=True, check=True)
    line = out.stdout.strip()
    assert line.startswith("actions=2"), line
    assert "len0=25" in line and "len1=10" in line, line


def test_cpp_balanced_unterminated_returns_error(tmp_path):
    bundle = build_bal_bundle_cpp()
    _h, c = emit_to_cpp(tmp_path, bundle)
    driver = tmp_path / "main.cpp"
    driver.write_text(r"""
#include <cstdio>
#include "uplox_bal.hpp"
int main(int argc, char** argv) {
    const char* src = (argc > 1) ? argv[1] : "x { unterminated";
    uplox::bal::Parser p(src);
    bool ok = p.parse();
    if (!ok) std::fprintf(stderr, "%s\n", p.error().c_str());
    return ok ? 0 : 1;
}
""")
    binary = tmp_path / "bal_err"
    cxx_compile(driver, c, out=binary, include=tmp_path)
    r = subprocess.run([str(binary)], capture_output=True, text=True, timeout=10)
    assert r.returncode != 0


# ============================================================================
# v3 auto-AST in C++
# ============================================================================


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


def build_ast_bundle(src: str) -> dict:
    ir = read_source(src)
    dfa, tokens, skip = lex_from_ir(ir)
    table = build_lr1(compile_grammar(ir))
    bundle = empty_bundle(ir.name)
    bundle["lex"] = dfa_to_json(dfa, tokens=tokens, skip=skip)
    bundle["parse"] = table_to_json(table)
    bundle["ast"] = ast_to_json(compile_ast_plan(ir))
    return bundle


def test_cpp_ast_header_declares_kinds(tmp_path):
    bundle = build_ast_bundle(CALC_ANNOTATED)
    h, _c = emit_to_cpp(tmp_path, bundle)
    header = h.read_text()
    assert "enum class AstKind" in header
    assert "struct BinOp : AstNode" in header
    assert "struct NumLit : AstNode" in header
    assert "bool parse_ast()" in header
    assert "ast_root()" in header


def test_cpp_ast_compiles_clean(tmp_path):
    bundle = build_ast_bundle(CALC_ANNOTATED)
    _h, c = emit_to_cpp(tmp_path, bundle)
    obj = tmp_path / "uplox_calc.o"
    # Permit -Wall (unused list helpers); calc has no list rules.
    cmd = [
        CXX, "-std=c++17", "-Wall", "-c", "-O1",
        "-I", str(tmp_path),
        str(c), "-o", str(obj),
    ]
    r = subprocess.run(cmd, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


def test_cpp_ast_end_to_end_calc(tmp_path):
    bundle = build_ast_bundle(CALC_ANNOTATED)
    _h, c = emit_to_cpp(tmp_path, bundle)
    driver = tmp_path / "main.cpp"
    driver.write_text(r"""
#include "uplox_calc.hpp"
#include <cstdio>
#include <string>
using namespace uplox::calc;
int main() {
    Parser p("(1 + 2) * 3");
    if (!p.parse_ast()) { fprintf(stderr, "parse: %s\n", p.error().c_str()); return 1; }
    auto* root = p.ast_root();
    if (root->kind != AstKind::BinOp) return 11;
    auto* outer = static_cast<const BinOp*>(root);
    if (std::string(outer->op->text) != "*") return 12;
    if (outer->lhs->kind != AstKind::BinOp) return 13;
    auto* inner = static_cast<const BinOp*>(outer->lhs);
    if (std::string(inner->op->text) != "+") return 14;
    if (outer->rhs->kind != AstKind::NumLit) return 15;
    auto* nl = static_cast<const NumLit*>(outer->rhs);
    if (std::string(nl->value->text) != "3") return 16;
    printf("ok\n");
    return 0;
}
""")
    binary = tmp_path / "calc_ast"
    cmd = [
        CXX, "-std=c++17", "-Wall", "-O1",
        "-I", str(tmp_path),
        str(c), str(driver), "-o", str(binary),
    ]
    subprocess.run(cmd, check=True, capture_output=True)
    r = subprocess.run([str(binary)], capture_output=True, text=True, timeout=10)
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == "ok"


COWGOL_MINI = """
%grammar mini
%options
start = program
%keyword_prefix KW_
%keywords
sub is end var return

%tokens
WS     = /[ \\t\\n]+/   %skip
NUMBER = /[0-9]+/
IDENT  = /[A-Za-z_][A-Za-z0-9_]*/
COLON  = ':'
COMMA  = ','
SEMI   = ';'
ASSIGN = ':='
LPAREN = '('
RPAREN = ')'

%ast_drop
LPAREN RPAREN COLON SEMI COMMA
KW_sub KW_is KW_end KW_var KW_return

%rules
<program> : <items>@items   %ast=Program
          |                  %ast=Program
          ;

<items> %ast=list element=<item>
        : <item>
        | <items> <item>
        ;

<item>? : <sub_decl>
        | <stmt>
        ;

<sub_decl> : KW_sub IDENT@name LPAREN <param_list_opt>@params RPAREN
             KW_is <body>@body KW_end KW_sub SEMI
             %ast=SubDecl
           ;

<param_list_opt> : <params>@params   %ast=_unwrap
                 |
                 ;

<params> %ast=list element=<param>
         : <param>
         | <params> COMMA <param>
         ;

<param> : IDENT@name COLON IDENT@type   %ast=Param ;

<body> : <body_stmts>@stmts   %ast=_unwrap
       |
       ;

<body_stmts> %ast=list element=<stmt>
       : <stmt>
       | <body_stmts> <stmt>
       ;

<stmt>? : <var_decl>
        | <return_stmt>
        ;

<var_decl> : KW_var IDENT@name <var_init_opt>@init SEMI   %ast=VarDecl ;

<var_init_opt> : ASSIGN NUMBER@value   %ast=_unwrap
               |
               ;

<return_stmt> : KW_return SEMI   %ast=ReturnStmt ;
"""


def test_cpp_ast_lists_optionals_unwrap(tmp_path):
    bundle = build_ast_bundle(COWGOL_MINI)
    _h, c = emit_to_cpp(tmp_path, bundle)
    driver = tmp_path / "main.cpp"
    driver.write_text(r"""
#include "uplox_mini.hpp"
#include <cstdio>
#include <string>
using namespace uplox::mini;
int main() {
    Parser p(
        "sub f(x: int8, y: int8) is\n"
        "    var a := 1;\n"
        "    var b;\n"
        "    return;\n"
        "end sub;\n"
    );
    if (!p.parse_ast()) { fprintf(stderr, "parse: %s\n", p.error().c_str()); return 1; }
    auto* prog = static_cast<const Program*>(p.ast_root());
    if (prog->items.size() != 1) return 11;
    auto* sub = static_cast<const SubDecl*>(prog->items[0]);
    if (std::string(sub->name->text) != "f") return 12;
    if (sub->params.size() != 2) return 13;
    auto* p0 = static_cast<const Param*>(sub->params[0]);
    if (std::string(p0->name->text) != "x") return 14;
    if (std::string(p0->type->text) != "int8") return 15;
    if (sub->body.size() != 3) return 16;
    auto* v1 = static_cast<const VarDecl*>(sub->body[0]);
    if (std::string(v1->name->text) != "a") return 17;
    if (v1->init == nullptr) return 18;
    if (std::string(v1->init->text) != "1") return 19;
    auto* v2 = static_cast<const VarDecl*>(sub->body[1]);
    if (std::string(v2->name->text) != "b") return 20;
    if (v2->init != nullptr) return 21;
    if (sub->body[2]->kind != AstKind::ReturnStmt) return 22;
    printf("ok\n");
    return 0;
}
""")
    binary = tmp_path / "mini_ast"
    cmd = [
        CXX, "-std=c++17", "-Wall", "-Werror", "-O1",
        "-I", str(tmp_path),
        str(c), str(driver), "-o", str(binary),
    ]
    subprocess.run(cmd, check=True, capture_output=True)
    r = subprocess.run([str(binary)], capture_output=True, text=True, timeout=10)
    assert r.returncode == 0, (
        f"exit={r.returncode}, stdout={r.stdout!r}, stderr={r.stderr!r}"
    )
    assert r.stdout.strip() == "ok"


def test_cpp_unannotated_grammar_emits_no_ast(tmp_path):
    bundle = build_bundle(CALC)  # no annotations
    h, c = emit_to_cpp(tmp_path, bundle)
    header = h.read_text()
    impl = c.read_text()
    assert "AstKind" not in header
    assert "parse_ast" not in header
    assert "parse_ast" not in impl


# ---- file_id / FileTable ----------------------------------------------------


def test_cpp_header_declares_filetable(tmp_path):
    bundle = build_bundle(CALC)
    h, _c = emit_to_cpp(tmp_path, bundle)
    text = h.read_text()
    assert "class FileTable" in text
    assert "intern_filename" in text
    assert "set_file_id" in text
    assert "int  file_id = 0;" in text


def test_cpp_stamps_file_id_on_terminals(tmp_path):
    """Intern a filename, set it, parse, walk the tree and verify every
    terminal leaf carries the matching file_id."""
    bundle = build_bundle(CALC)
    _h, c = emit_to_cpp(tmp_path, bundle)
    driver = tmp_path / "main.cpp"
    driver.write_text(r"""
#include "uplox_calc.hpp"
#include <cstdio>
using namespace uplox::calc;

static int check(const Node* n, int expect_id) {
    if (n->is_terminal) return n->file_id == expect_id ? 0 : 1;
    for (auto* c : n->children) {
        int rc = check(c, expect_id);
        if (rc) return rc;
    }
    return 0;
}

int main() {
    Parser p("1 + 2 * 3");
    int fid = p.intern_filename("src/expr.calc");
    if (fid <= 0) { fprintf(stderr, "intern failed\n"); return 1; }
    if (p.file_table().name(fid) != "src/expr.calc") {
        fprintf(stderr, "filename mismatch\n"); return 1;
    }
    p.set_file_id(fid);
    if (p.file_id() != fid) { fprintf(stderr, "get mismatch\n"); return 1; }
    if (!p.parse()) { fprintf(stderr, "parse: %s\n", p.error().c_str()); return 1; }
    if (check(p.root(), fid)) { fprintf(stderr, "leaf id mismatch\n"); return 1; }
    if (p.intern_filename("src/expr.calc") != fid) {
        fprintf(stderr, "intern not idempotent\n"); return 1;
    }
    if (p.file_table().name(0) != "") { fprintf(stderr, "id0 not ''\n"); return 1; }
    std::printf("ok\n");
    return 0;
}
""")
    binary = tmp_path / "fid_cpp"
    cxx_compile(c, driver, out=binary, include=tmp_path)
    r = subprocess.run([str(binary)], capture_output=True, text=True, timeout=10)
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == "ok"


def test_cpp_default_file_id_is_zero(tmp_path):
    bundle = build_bundle(CALC)
    _h, c = emit_to_cpp(tmp_path, bundle)
    driver = tmp_path / "main.cpp"
    driver.write_text(r"""
#include "uplox_calc.hpp"
#include <cstdio>
using namespace uplox::calc;

static int any_nonzero(const Node* n) {
    if (n->is_terminal) return n->file_id != 0;
    for (auto* c : n->children) if (any_nonzero(c)) return 1;
    return 0;
}

int main() {
    Parser p("1 + 2");
    if (!p.parse()) return 1;
    if (any_nonzero(p.root())) { fprintf(stderr, "expected file_id==0\n"); return 1; }
    std::printf("ok\n");
    return 0;
}
""")
    binary = tmp_path / "fid_zero_cpp"
    cxx_compile(c, driver, out=binary, include=tmp_path)
    r = subprocess.run([str(binary)], capture_output=True, text=True, timeout=10)
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == "ok"
