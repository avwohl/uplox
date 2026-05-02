"""C backend: emit, compile, run, verify.

These tests compile the emitted C with the system ``cc`` (skipped if not
available), link against a tiny test driver, and exercise the resulting
binary on real inputs. End-to-end coverage of the Phase 7 deliverable.

The re-entrancy acceptance test (two grammars in one binary, no symbol
collisions) lives in :func:`test_two_grammars_link_together`.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from plox.gen.c import emit_c
from plox.lex.build import lex_from_ir
from plox.parse.grammar import compile_grammar
from plox.parse.lr1 import build_lr1
from plox.spec.reader import read_source
from plox.tables import dfa_to_json, dump_bundle, empty_bundle, table_to_json


CC = shutil.which("cc") or shutil.which("gcc")
pytestmark = pytest.mark.skipif(CC is None, reason="no C compiler available")


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


def build_calc_bundle() -> dict:
    ir = read_source(CALC)
    dfa, tokens, skip = lex_from_ir(ir)
    grammar = compile_grammar(ir)
    table = build_lr1(grammar)
    bundle = empty_bundle(ir.name)
    bundle["lex"] = dfa_to_json(dfa, tokens=tokens, skip=skip)
    bundle["parse"] = table_to_json(table)
    return bundle


def emit_to(tmp_path: Path, bundle: dict, prefix: str | None = None) -> tuple[Path, Path]:
    header, impl = emit_c(bundle, prefix=prefix)
    name = (prefix or bundle["meta"]["grammar"]).lower()
    h = tmp_path / f"plox_{name}.h"
    c = tmp_path / f"plox_{name}.c"
    h.write_text(header)
    c.write_text(impl)
    return h, c


def cc_compile(*sources: Path, out: Path, include: Path) -> None:
    cmd = [
        CC, "-Wall", "-Werror", "-O1",
        "-I", str(include),
        *[str(s) for s in sources],
        "-o", str(out),
    ]
    subprocess.run(cmd, check=True, capture_output=True)


def cc_compile_object(source: Path, out: Path, include: Path) -> None:
    cmd = [
        CC, "-Wall", "-Werror", "-c", "-O1",
        "-I", str(include),
        str(source), "-o", str(out),
    ]
    subprocess.run(cmd, check=True, capture_output=True)


# ---- 1. Compile-only checks --------------------------------------------------


def test_emitted_c_compiles_without_warnings(tmp_path):
    bundle = build_calc_bundle()
    _h, c = emit_to(tmp_path, bundle)
    obj = tmp_path / "plox_calc.o"
    cc_compile_object(c, obj, include=tmp_path)
    assert obj.exists()


def test_emitted_c_for_plm_subset_compiles(tmp_path):
    """Stress test: 700+ states, ~100 productions. The generated C is large
    but must still compile cleanly."""
    plm_path = Path(__file__).resolve().parents[1] / "examples" / "plm_subset.plox"
    from plox.spec.reader import read_file
    ir = read_file(str(plm_path))
    dfa, tokens, skip = lex_from_ir(ir)
    grammar = compile_grammar(ir)
    table = build_lr1(grammar)
    bundle = empty_bundle(ir.name)
    bundle["lex"] = dfa_to_json(dfa, tokens=tokens, skip=skip)
    bundle["parse"] = table_to_json(table)

    _h, c = emit_to(tmp_path, bundle)
    obj = tmp_path / "plox_plm_subset.o"
    cc_compile_object(c, obj, include=tmp_path)
    assert obj.exists()


# ---- 2. End-to-end run -------------------------------------------------------


CALC_DRIVER = """
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "plox_calc.h"

static int evaluate(const plox_calc_node *n) {
    if (n->is_terminal) {
        if (n->kind == PLOX_CALC_TOK_NUMBER) {
            char buf[32];
            int len = n->text_len < 31 ? n->text_len : 31;
            memcpy(buf, n->text, len);
            buf[len] = 0;
            return atoi(buf);
        }
        return 0;
    }
    /* Walk down trivial wrappers. */
    if (n->num_children == 1) return evaluate(n->children[0]);
    if (n->num_children == 3) {
        const plox_calc_node *op = n->children[1];
        if (op->is_terminal) {
            int l = evaluate(n->children[0]);
            int r = evaluate(n->children[2]);
            switch (op->kind) {
                case PLOX_CALC_TOK_PLUS:  return l + r;
                case PLOX_CALC_TOK_MINUS: return l - r;
                case PLOX_CALC_TOK_STAR:  return l * r;
                case PLOX_CALC_TOK_SLASH: return r ? l / r : 0;
                case PLOX_CALC_TOK_LPAREN: return evaluate(n->children[1]);
                default: break;
            }
        }
        /* Parenthesised: ( expr ) */
        if (n->children[0]->is_terminal && n->children[0]->kind == PLOX_CALC_TOK_LPAREN) {
            return evaluate(n->children[1]);
        }
    }
    return 0;
}

int main(void) {
    char buf[4096];
    int n = (int)fread(buf, 1, sizeof(buf) - 1, stdin);
    buf[n] = 0;
    plox_calc_ctx *ctx = plox_calc_create(buf, n);
    plox_calc_node *root = NULL;
    if (plox_calc_parse(ctx, &root) != 0) {
        fprintf(stderr, "parse error: %s\\n", plox_calc_error(ctx));
        plox_calc_destroy(ctx);
        return 1;
    }
    printf("%d\\n", evaluate(root));
    plox_calc_destroy(ctx);
    return 0;
}
"""


def build_calc_evaluator(tmp_path: Path) -> Path:
    bundle = build_calc_bundle()
    emit_to(tmp_path, bundle)
    driver = tmp_path / "main.c"
    driver.write_text(CALC_DRIVER)
    binary = tmp_path / "calc"
    cc_compile(driver, tmp_path / "plox_calc.c", out=binary, include=tmp_path)
    return binary


def run_calc(binary: Path, expr: str) -> tuple[int, str, str]:
    result = subprocess.run(
        [str(binary)],
        input=expr,
        capture_output=True,
        text=True,
        timeout=10,
    )
    return result.returncode, result.stdout.strip(), result.stderr.strip()


def test_calc_addition(tmp_path):
    binary = build_calc_evaluator(tmp_path)
    rc, out, _err = run_calc(binary, "1 + 2 + 3")
    assert rc == 0 and out == "6"


def test_calc_precedence(tmp_path):
    binary = build_calc_evaluator(tmp_path)
    rc, out, _err = run_calc(binary, "1 + 2 * 3")
    assert rc == 0 and out == "7"


def test_calc_parens(tmp_path):
    binary = build_calc_evaluator(tmp_path)
    rc, out, _err = run_calc(binary, "(1 + 2) * 3")
    assert rc == 0 and out == "9"


def test_calc_division_associativity(tmp_path):
    binary = build_calc_evaluator(tmp_path)
    rc, out, _err = run_calc(binary, "100 / 5 / 2")
    assert rc == 0 and out == "10"  # left-assoc: (100/5)/2


def test_calc_syntax_error_returns_nonzero(tmp_path):
    binary = build_calc_evaluator(tmp_path)
    rc, _out, err = run_calc(binary, "1 +")
    assert rc != 0
    assert "parse error" in err.lower() or "unexpected" in err.lower()


# ---- 3. Re-entrancy acceptance test -----------------------------------------


# A second grammar to link alongside calc. Distinct enough that any
# accidentally-shared symbol would surface at link time.
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


TWO_DRIVER = """
/* Two grammar contexts in the same binary, in flight at the same time. */
#include <stdio.h>
#include <string.h>
#include "plox_calc.h"
#include "plox_tiny.h"

int main(void) {
    /* calc context */
    const char *calc_src = "1 + 2";
    plox_calc_ctx *calc_ctx = plox_calc_create(calc_src, (int)strlen(calc_src));
    /* tiny context */
    const char *tiny_src = "aabb";
    plox_tiny_ctx *tiny_ctx = plox_tiny_create(tiny_src, (int)strlen(tiny_src));

    plox_calc_node *calc_root = NULL;
    plox_tiny_node *tiny_root = NULL;
    int calc_rc = plox_calc_parse(calc_ctx, &calc_root);
    int tiny_rc = plox_tiny_parse(tiny_ctx, &tiny_root);

    printf("calc rc=%d nt=%s\\n", calc_rc, calc_rc == 0 ? plox_calc_nt_name(calc_root->kind) : "");
    printf("tiny rc=%d nt=%s\\n", tiny_rc, tiny_rc == 0 ? plox_tiny_nt_name(tiny_root->kind) : "");

    plox_calc_destroy(calc_ctx);
    plox_tiny_destroy(tiny_ctx);
    return (calc_rc == 0 && tiny_rc == 0) ? 0 : 1;
}
"""


def build_tiny_bundle() -> dict:
    ir = read_source(TINY)
    dfa, tokens, skip = lex_from_ir(ir)
    table = build_lr1(compile_grammar(ir))
    bundle = empty_bundle(ir.name)
    bundle["lex"] = dfa_to_json(dfa, tokens=tokens, skip=skip)
    bundle["parse"] = table_to_json(table)
    return bundle


def test_two_grammars_link_together(tmp_path):
    """Acceptance test: two plox-emitted grammars compile and link into the
    same binary with zero symbol collisions, then run side-by-side without
    interfering."""
    emit_to(tmp_path, build_calc_bundle())
    emit_to(tmp_path, build_tiny_bundle())
    driver = tmp_path / "main.c"
    driver.write_text(TWO_DRIVER)
    binary = tmp_path / "two"
    cc_compile(
        driver,
        tmp_path / "plox_calc.c",
        tmp_path / "plox_tiny.c",
        out=binary,
        include=tmp_path,
    )
    result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert "calc rc=0" in result.stdout
    assert "tiny rc=0" in result.stdout


# ---- 4. Header sanity --------------------------------------------------------


def test_header_uses_consistent_prefix(tmp_path):
    bundle = build_calc_bundle()
    h_path, _c_path = emit_to(tmp_path, bundle)
    text = h_path.read_text()
    assert "plox_calc_ctx" in text
    assert "PLOX_CALC_TOK__EOI_" in text  # synthetic end-of-input marker
    assert "PLOX_CALC_TOK_NUMBER" in text
    assert "PLOX_CALC_NT__START__" in text or "PLOX_CALC_NT_EXPR" in text
    # No leak of internal helpers in the header.
    assert "plox_calc__" not in text


def test_emitted_c_carries_default_reduction_table(tmp_path):
    """The C emitter writes a per-state default_reduction array. Even calc
    has populated default reductions; this test pins the wiring without
    requiring a full feedback grammar."""
    bundle = build_calc_bundle()
    _h, c = emit_to(tmp_path, bundle)
    text = c.read_text()
    assert "plox_calc_default_reduction" in text, "default_reduction array missing"
    # And the parser body must consult it on action miss.
    assert "default_reduction[s]" in text


def test_emit_with_explicit_prefix_overrides_grammar_name(tmp_path):
    bundle = build_calc_bundle()
    h, _c = emit_to(tmp_path, bundle, prefix="myparser")
    text = h.read_text()
    assert "plox_myparser_ctx" in text
    assert "plox_calc_ctx" not in text


def test_invalid_prefix_rejected(tmp_path):
    bundle = build_calc_bundle()
    with pytest.raises(ValueError):
        emit_c(bundle, prefix="bad-name")  # hyphen is not a valid C ident
