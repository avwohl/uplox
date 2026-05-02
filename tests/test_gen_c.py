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


def test_calc_syntax_error_lists_expected_tokens(tmp_path):
    """Same shape as the Python runtime's error: the message includes
    `expected one of: ...` listing the terminals the offending state has
    actions for. After `1 +` the calc grammar expects a factor-starter
    (NUMBER or LPAREN)."""
    binary = build_calc_evaluator(tmp_path)
    rc, _out, err = run_calc(binary, "1 +")
    assert rc != 0
    # Synthetic end-of-input is rendered as "end of input", not as a stray
    # "$" token name leaked from the LR table.
    assert "unexpected end of input" in err
    assert "expected one of:" in err
    assert "NUMBER" in err and "LPAREN" in err
    assert "$" not in err


def test_calc_syntax_error_unexpected_token_includes_position(tmp_path):
    binary = build_calc_evaluator(tmp_path)
    rc, _out, err = run_calc(binary, "1 2")
    assert rc != 0
    assert "unexpected token" in err
    assert "line 1" in err and "column 3" in err
    # Right after a complete expression, end-of-input is one of the legal
    # continuations; render it as text rather than the raw "$".
    assert "<end of input>" in err


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


def test_emitted_c_supports_token_filter(tmp_path):
    """End-to-end: install a token filter from a C driver, parse input,
    confirm the filter rewrote terminals as we asked.

    Grammar shape: NAME and IDENT have the same regex (NAME wins because
    it's declared first; here we declare IDENT first so NAME is never
    produced by the lexer — only by the filter). The grammar accepts
    `prog : (NAME | IDENT) SEMI` so we can tell which terminal each input
    parsed as by inspecting the kind of the leaf in the tree."""
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
    ir = read_source(SRC)
    dfa, tokens, skip = lex_from_ir(ir)
    grammar = compile_grammar(ir)
    table = build_lr1(grammar)
    bundle = empty_bundle(ir.name)
    bundle["lex"] = dfa_to_json(dfa, tokens=tokens, skip=skip)
    bundle["parse"] = table_to_json(table)

    _h, c = emit_to(tmp_path, bundle)
    text = c.read_text()
    assert "plox_tf_set_token_filter" in text
    assert "token_filter" in text
    assert "token_filter_data" in text

    # Driver: rewrite IDENT to NAME when the source starts with capital T.
    driver = tmp_path / "driver.c"
    driver.write_text(r"""
#include "plox_tf.h"
#include <stdio.h>
#include <string.h>

static int rewrite_T(plox_tf_ctx *ctx, int la_kind, const char *la_text, int la_len, void *ud) {
    (void)ctx; (void)ud; (void)la_len;
    if (la_kind == PLOX_TF_TOK_IDENT && la_text[0] == 'T') {
        return PLOX_TF_TOK_NAME;
    }
    return la_kind;
}

int main(int argc, char **argv) {
    const char *src = (argc > 1) ? argv[1] : "Tx;";
    int n = (int)strlen(src);
    plox_tf_ctx *ctx = plox_tf_create(src, n);
    plox_tf_set_token_filter(ctx, rewrite_T, NULL);
    plox_tf_node *root = NULL;
    int rc = plox_tf_parse(ctx, &root);
    if (rc != 0) {
        fprintf(stderr, "parse error: %s\n", plox_tf_error(ctx));
        plox_tf_destroy(ctx);
        return 1;
    }
    /* `item` -> IDENT SEMI | NAME SEMI; the leaf at children[0] tells us
       which terminal kind the parser saw after filtering. */
    plox_tf_node *item = root->children[0];
    plox_tf_node *first = item->children[0];
    printf("%s\n", plox_tf_token_name(first->kind));
    plox_tf_destroy(ctx);
    return 0;
}
""")
    binary = tmp_path / "tf"
    cc_compile(c, driver, out=binary, include=tmp_path)

    # Capital-T identifier: filter rewrites to NAME.
    out = subprocess.run([str(binary), "Tx;"], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "NAME"

    # Lower-case: filter passes through; lexer's IDENT is what the parser sees.
    out = subprocess.run([str(binary), "abc;"], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "IDENT"


def test_emitted_c_typedef_name_hack_end_to_end(tmp_path):
    """The full classical typedef-name hack expressed in C: a tiny grammar
    where `typedef Foo;` registers Foo as a type-name, and a subsequent
    `Foo;` parses as a different production. Without coordinated
    post_reduce + token_filter, the second `Foo;` would lex as IDENT and
    the parser would fail. This pins the v1.x ABI end-to-end."""
    SRC = """
%grammar tnh

%tokens
WS         = /[ \\t\\n]+/  %skip
KW_TYPEDEF = "typedef"
SEMI       = ";"
IDENT      = /[A-Za-z_][A-Za-z0-9_]*/
TNAME      = /[A-Za-z_][A-Za-z0-9_]*/

%rules
prog : decls ;
decls : decls decl | decl ;
decl : KW_TYPEDEF IDENT SEMI
     | TNAME SEMI
     ;
"""
    ir = read_source(SRC)
    dfa, tokens, skip = lex_from_ir(ir)
    table = build_lr1(compile_grammar(ir))
    bundle = empty_bundle(ir.name)
    bundle["lex"] = dfa_to_json(dfa, tokens=tokens, skip=skip)
    bundle["parse"] = table_to_json(table)
    _h, c = emit_to(tmp_path, bundle)
    text = c.read_text()
    assert "plox_tnh_set_post_reduce" in text

    driver = tmp_path / "driver.c"
    driver.write_text(r"""
#include "plox_tnh.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define MAX_NAMES 32

typedef struct {
    char *names[MAX_NAMES];
    int   count;
} typedef_set_t;

static int set_contains(typedef_set_t *s, const char *text, int len) {
    for (int i = 0; i < s->count; ++i) {
        if ((int)strlen(s->names[i]) == len && memcmp(s->names[i], text, len) == 0) {
            return 1;
        }
    }
    return 0;
}

static void set_add(typedef_set_t *s, const char *text, int len) {
    if (s->count >= MAX_NAMES) return;
    char *copy = (char *)malloc(len + 1);
    memcpy(copy, text, len);
    copy[len] = '\0';
    s->names[s->count++] = copy;
}

static int filter(plox_tnh_ctx *ctx, int la_kind, const char *la_text, int la_len, void *user_data) {
    (void)ctx;
    typedef_set_t *s = (typedef_set_t *)user_data;
    if (la_kind == PLOX_TNH_TOK_IDENT && set_contains(s, la_text, la_len)) {
        return PLOX_TNH_TOK_TNAME;
    }
    return la_kind;
}

static void on_reduce(plox_tnh_ctx *ctx, int prod, plox_tnh_node *node, void *user_data) {
    (void)ctx;
    typedef_set_t *s = (typedef_set_t *)user_data;
    /* Production 4 is `decl : KW_TYPEDEF IDENT SEMI` — the only path that
       declares a new type-name. children[1] is the IDENT terminal. */
    if (prod == 4 && node->num_children >= 2) {
        plox_tnh_node *id = node->children[1];
        set_add(s, id->text, id->text_len);
    }
}

int main(int argc, char **argv) {
    const char *src = (argc > 1) ? argv[1] : "typedef Foo; Foo;";
    int n = (int)strlen(src);
    plox_tnh_ctx *ctx = plox_tnh_create(src, n);
    typedef_set_t set = {0};
    plox_tnh_set_token_filter(ctx, filter, &set);
    plox_tnh_set_post_reduce(ctx, on_reduce, &set);
    plox_tnh_node *root = NULL;
    int rc = plox_tnh_parse(ctx, &root);
    if (rc != 0) {
        fprintf(stderr, "parse error: %s\n", plox_tnh_error(ctx));
        plox_tnh_destroy(ctx);
        return 1;
    }
    /* Walk top-level decls and report which production each one used. */
    plox_tnh_node *decls = root->children[0];
    /* `decls : decls decl | decl` — left-recursive list. Linearise. */
    plox_tnh_node *items[32];
    int n_items = 0;
    plox_tnh_node *cur = decls;
    while (cur && !cur->is_terminal) {
        if (cur->num_children == 2) {
            items[n_items++] = cur->children[1];
            cur = cur->children[0];
        } else {
            items[n_items++] = cur->children[0];
            break;
        }
    }
    for (int i = n_items - 1; i >= 0; --i) {
        plox_tnh_node *d = items[i];
        printf("decl%d: prod=%d\n", n_items - 1 - i, d->production);
    }
    plox_tnh_destroy(ctx);
    return 0;
}
""")
    binary = tmp_path / "tnh"
    cc_compile(c, driver, out=binary, include=tmp_path)

    out = subprocess.run([str(binary), "typedef Foo; Foo;"],
                         capture_output=True, text=True, check=True)
    lines = out.stdout.strip().split("\n")
    # Production 4 is `decl : KW_TYPEDEF IDENT SEMI`.
    # Production 5 is `decl : TNAME SEMI`.
    assert lines[0].endswith("prod=4"), lines
    assert lines[1].endswith("prod=5"), lines


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


# ---- 5. Balanced-bracket tokens (%balanced=) --------------------------------


BAL_GRAMMAR = """
%grammar bal

%tokens
WS     = /[ \\t\\n]+/    %skip
IDENT  = /[A-Za-z_][A-Za-z0-9_]*/
ACTION = "{" %balanced="}"

%rules
top  : top item | item ;
item : IDENT ACTION ;
"""


def build_bal_bundle() -> dict:
    from plox.lex.build import balanced_tokens

    ir = read_source(BAL_GRAMMAR)
    dfa, tokens, skip = lex_from_ir(ir)
    grammar = compile_grammar(ir)
    table = build_lr1(grammar)
    bundle = empty_bundle(ir.name)
    bundle["lex"] = dfa_to_json(
        dfa, tokens=tokens, skip=skip, balanced=balanced_tokens(ir)
    )
    bundle["parse"] = table_to_json(table)
    return bundle


def test_emitted_c_supports_balanced_token(tmp_path):
    """End-to-end: emit C for a grammar with `%balanced="}"`, compile, and
    feed input where `{ ... }` action bodies contain nested braces. The
    scanner must consume each balanced run as a single ACTION token, so
    the parse tree has one ACTION per top-level item even when the body
    contains additional `{}` pairs."""
    bundle = build_bal_bundle()
    _h, c = emit_to(tmp_path, bundle)
    text = c.read_text()
    assert "plox_bal_token_balanced" in text, "balanced array missing from emit"

    driver = tmp_path / "driver.c"
    driver.write_text(r"""
#include "plox_bal.h"
#include <stdio.h>
#include <string.h>

/* Walk the tree and count ACTION leaves; print their lengths in order. */
static int count_actions(const plox_bal_node *n, int *lens, int cap, int *out_n) {
    if (!n) return 0;
    if (n->is_terminal) {
        if (n->kind == PLOX_BAL_TOK_ACTION) {
            if (*out_n < cap) lens[*out_n] = n->text_len;
            ++(*out_n);
        }
        return 0;
    }
    for (int i = 0; i < n->num_children; ++i) {
        count_actions(n->children[i], lens, cap, out_n);
    }
    return 0;
}

int main(int argc, char **argv) {
    const char *src = (argc > 1) ? argv[1] : "";
    int n = (int)strlen(src);
    plox_bal_ctx *ctx = plox_bal_create(src, n);
    plox_bal_node *root = NULL;
    if (plox_bal_parse(ctx, &root) != 0) {
        fprintf(stderr, "parse error: %s\n", plox_bal_error(ctx));
        plox_bal_destroy(ctx);
        return 1;
    }
    int lens[16]; int cnt = 0;
    count_actions(root, lens, 16, &cnt);
    printf("actions=%d", cnt);
    for (int i = 0; i < cnt; ++i) printf(" len%d=%d", i, lens[i]);
    printf("\n");
    plox_bal_destroy(ctx);
    return 0;
}
""")
    binary = tmp_path / "bal"
    cc_compile(c, driver, out=binary, include=tmp_path)

    # Two items; the first action body has a nested `{}`, the second is
    # plain. Each ACTION token's text_len includes the outer braces, so
    # the lengths are deterministic.
    src = "first { with { nested } stuff } second { simple }"
    out = subprocess.run([str(binary), src], capture_output=True, text=True, check=True)
    line = out.stdout.strip()
    assert line.startswith("actions=2"), line
    # `{ with { nested } stuff }` is 25 chars including the outer braces.
    # `{ simple }` is 10.
    assert "len0=25" in line and "len1=10" in line, line


def test_emitted_c_balanced_unterminated_returns_error(tmp_path):
    bundle = build_bal_bundle()
    _h, c = emit_to(tmp_path, bundle)
    driver = tmp_path / "driver.c"
    driver.write_text(r"""
#include "plox_bal.h"
#include <stdio.h>
#include <string.h>
int main(int argc, char **argv) {
    const char *src = (argc > 1) ? argv[1] : "x { unterminated";
    int n = (int)strlen(src);
    plox_bal_ctx *ctx = plox_bal_create(src, n);
    plox_bal_node *root = NULL;
    int rc = plox_bal_parse(ctx, &root);
    if (rc != 0) {
        fprintf(stderr, "%s\n", plox_bal_error(ctx));
    }
    plox_bal_destroy(ctx);
    return rc == 0 ? 0 : 1;
}
""")
    binary = tmp_path / "bal_err"
    cc_compile(c, driver, out=binary, include=tmp_path)
    result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=10)
    assert result.returncode != 0
    assert "unterminated" in result.stderr.lower()
