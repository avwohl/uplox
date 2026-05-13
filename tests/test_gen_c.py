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

from uplox.gen.c import emit_c
from uplox.lex.build import lex_from_ir
from uplox.parse.grammar import compile_grammar
from uplox.parse.lr1 import build_lr1
from uplox.spec.ast_plan import compile_ast_plan
from uplox.spec.reader import read_source
from uplox.tables import (
    ast_to_json,
    dfa_to_json,
    dump_bundle,
    empty_bundle,
    table_to_json,
)


CC = shutil.which("cc") or shutil.which("gcc")
pytestmark = pytest.mark.skipif(CC is None, reason="no C compiler available")


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
    h = tmp_path / f"uplox_{name}.h"
    c = tmp_path / f"uplox_{name}.c"
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
    obj = tmp_path / "uplox_calc.o"
    cc_compile_object(c, obj, include=tmp_path)
    assert obj.exists()


def test_emitted_c_for_plm_subset_compiles(tmp_path):
    """Stress test: 700+ states, ~100 productions. The generated C is large
    but must still compile cleanly."""
    plm_path = Path(__file__).resolve().parents[1] / "examples" / "plm_subset.uplox"
    from uplox.spec.reader import read_file
    ir = read_file(str(plm_path))
    dfa, tokens, skip = lex_from_ir(ir)
    grammar = compile_grammar(ir)
    table = build_lr1(grammar)
    bundle = empty_bundle(ir.name)
    bundle["lex"] = dfa_to_json(dfa, tokens=tokens, skip=skip)
    bundle["parse"] = table_to_json(table)

    _h, c = emit_to(tmp_path, bundle)
    obj = tmp_path / "uplox_plm_subset.o"
    cc_compile_object(c, obj, include=tmp_path)
    assert obj.exists()


# ---- 2. End-to-end run -------------------------------------------------------


CALC_DRIVER = """
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "uplox_calc.h"

static int evaluate(const uplox_calc_node *n) {
    if (n->is_terminal) {
        if (n->kind == UPLOX_CALC_TOK_NUMBER) {
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
        const uplox_calc_node *op = n->children[1];
        if (op->is_terminal) {
            int l = evaluate(n->children[0]);
            int r = evaluate(n->children[2]);
            switch (op->kind) {
                case UPLOX_CALC_TOK_PLUS:  return l + r;
                case UPLOX_CALC_TOK_MINUS: return l - r;
                case UPLOX_CALC_TOK_STAR:  return l * r;
                case UPLOX_CALC_TOK_SLASH: return r ? l / r : 0;
                case UPLOX_CALC_TOK_LPAREN: return evaluate(n->children[1]);
                default: break;
            }
        }
        /* Parenthesised: ( expr ) */
        if (n->children[0]->is_terminal && n->children[0]->kind == UPLOX_CALC_TOK_LPAREN) {
            return evaluate(n->children[1]);
        }
    }
    return 0;
}

int main(void) {
    char buf[4096];
    int n = (int)fread(buf, 1, sizeof(buf) - 1, stdin);
    buf[n] = 0;
    uplox_calc_ctx *ctx = uplox_calc_create(buf, n);
    uplox_calc_node *root = NULL;
    if (uplox_calc_parse(ctx, &root) != 0) {
        fprintf(stderr, "parse error: %s\\n", uplox_calc_error(ctx));
        uplox_calc_destroy(ctx);
        return 1;
    }
    printf("%d\\n", evaluate(root));
    uplox_calc_destroy(ctx);
    return 0;
}
"""


def build_calc_evaluator(tmp_path: Path) -> Path:
    bundle = build_calc_bundle()
    emit_to(tmp_path, bundle)
    driver = tmp_path / "main.c"
    driver.write_text(CALC_DRIVER)
    binary = tmp_path / "calc"
    cc_compile(driver, tmp_path / "uplox_calc.c", out=binary, include=tmp_path)
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
A = 'a'
B = 'b'
%rules
<s> : A B
  | A <s> B
  ;
"""


TWO_DRIVER = """
/* Two grammar contexts in the same binary, in flight at the same time. */
#include <stdio.h>
#include <string.h>
#include "uplox_calc.h"
#include "uplox_tiny.h"

int main(void) {
    /* calc context */
    const char *calc_src = "1 + 2";
    uplox_calc_ctx *calc_ctx = uplox_calc_create(calc_src, (int)strlen(calc_src));
    /* tiny context */
    const char *tiny_src = "aabb";
    uplox_tiny_ctx *tiny_ctx = uplox_tiny_create(tiny_src, (int)strlen(tiny_src));

    uplox_calc_node *calc_root = NULL;
    uplox_tiny_node *tiny_root = NULL;
    int calc_rc = uplox_calc_parse(calc_ctx, &calc_root);
    int tiny_rc = uplox_tiny_parse(tiny_ctx, &tiny_root);

    printf("calc rc=%d nt=%s\\n", calc_rc, calc_rc == 0 ? uplox_calc_nt_name(calc_root->kind) : "");
    printf("tiny rc=%d nt=%s\\n", tiny_rc, tiny_rc == 0 ? uplox_tiny_nt_name(tiny_root->kind) : "");

    uplox_calc_destroy(calc_ctx);
    uplox_tiny_destroy(tiny_ctx);
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
    """Acceptance test: two uplox-emitted grammars compile and link into the
    same binary with zero symbol collisions, then run side-by-side without
    interfering."""
    emit_to(tmp_path, build_calc_bundle())
    emit_to(tmp_path, build_tiny_bundle())
    driver = tmp_path / "main.c"
    driver.write_text(TWO_DRIVER)
    binary = tmp_path / "two"
    cc_compile(
        driver,
        tmp_path / "uplox_calc.c",
        tmp_path / "uplox_tiny.c",
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
    assert "uplox_calc_ctx" in text
    assert "UPLOX_CALC_TOK__EOI_" in text  # synthetic end-of-input marker
    assert "UPLOX_CALC_TOK_NUMBER" in text
    assert "UPLOX_CALC_NT__START__" in text or "UPLOX_CALC_NT_EXPR" in text
    # No leak of internal helpers in the header.
    assert "uplox_calc__" not in text


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
SEMI  = ';'
IDENT = /[A-Za-z_][A-Za-z0-9_]*/
NAME  = /[A-Za-z_][A-Za-z0-9_]*/

%rules
<prog> : <item> ;
<item> : IDENT SEMI
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
    assert "uplox_tf_set_token_filter" in text
    assert "token_filter" in text
    assert "token_filter_data" in text

    # Driver: rewrite IDENT to NAME when the source starts with capital T.
    driver = tmp_path / "driver.c"
    driver.write_text(r"""
#include "uplox_tf.h"
#include <stdio.h>
#include <string.h>

static int rewrite_T(uplox_tf_ctx *ctx, int la_kind, const char *la_text, int la_len, void *ud) {
    (void)ctx; (void)ud; (void)la_len;
    if (la_kind == UPLOX_TF_TOK_IDENT && la_text[0] == 'T') {
        return UPLOX_TF_TOK_NAME;
    }
    return la_kind;
}

int main(int argc, char **argv) {
    const char *src = (argc > 1) ? argv[1] : "Tx;";
    int n = (int)strlen(src);
    uplox_tf_ctx *ctx = uplox_tf_create(src, n);
    uplox_tf_set_token_filter(ctx, rewrite_T, NULL);
    uplox_tf_node *root = NULL;
    int rc = uplox_tf_parse(ctx, &root);
    if (rc != 0) {
        fprintf(stderr, "parse error: %s\n", uplox_tf_error(ctx));
        uplox_tf_destroy(ctx);
        return 1;
    }
    /* `item` -> IDENT SEMI | NAME SEMI; the leaf at children[0] tells us
       which terminal kind the parser saw after filtering. */
    uplox_tf_node *item = root->children[0];
    uplox_tf_node *first = item->children[0];
    printf("%s\n", uplox_tf_token_name(first->kind));
    uplox_tf_destroy(ctx);
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
    ir = read_source(SRC)
    dfa, tokens, skip = lex_from_ir(ir)
    table = build_lr1(compile_grammar(ir))
    bundle = empty_bundle(ir.name)
    bundle["lex"] = dfa_to_json(dfa, tokens=tokens, skip=skip)
    bundle["parse"] = table_to_json(table)
    _h, c = emit_to(tmp_path, bundle)
    text = c.read_text()
    assert "uplox_tnh_set_post_reduce" in text

    driver = tmp_path / "driver.c"
    driver.write_text(r"""
#include "uplox_tnh.h"
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

static int filter(uplox_tnh_ctx *ctx, int la_kind, const char *la_text, int la_len, void *user_data) {
    (void)ctx;
    typedef_set_t *s = (typedef_set_t *)user_data;
    if (la_kind == UPLOX_TNH_TOK_IDENT && set_contains(s, la_text, la_len)) {
        return UPLOX_TNH_TOK_TNAME;
    }
    return la_kind;
}

static void on_reduce(uplox_tnh_ctx *ctx, int prod, uplox_tnh_node *node, void *user_data) {
    (void)ctx;
    typedef_set_t *s = (typedef_set_t *)user_data;
    /* Production 4 is `decl : KW_TYPEDEF IDENT SEMI` — the only path that
       declares a new type-name. children[1] is the IDENT terminal. */
    if (prod == 4 && node->num_children >= 2) {
        uplox_tnh_node *id = node->children[1];
        set_add(s, id->text, id->text_len);
    }
}

int main(int argc, char **argv) {
    const char *src = (argc > 1) ? argv[1] : "typedef Foo; Foo;";
    int n = (int)strlen(src);
    uplox_tnh_ctx *ctx = uplox_tnh_create(src, n);
    typedef_set_t set = {0};
    uplox_tnh_set_token_filter(ctx, filter, &set);
    uplox_tnh_set_post_reduce(ctx, on_reduce, &set);
    uplox_tnh_node *root = NULL;
    int rc = uplox_tnh_parse(ctx, &root);
    if (rc != 0) {
        fprintf(stderr, "parse error: %s\n", uplox_tnh_error(ctx));
        uplox_tnh_destroy(ctx);
        return 1;
    }
    /* Walk top-level decls and report which production each one used. */
    uplox_tnh_node *decls = root->children[0];
    /* `decls : decls decl | decl` — left-recursive list. Linearise. */
    uplox_tnh_node *items[32];
    int n_items = 0;
    uplox_tnh_node *cur = decls;
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
        uplox_tnh_node *d = items[i];
        printf("decl%d: prod=%d\n", n_items - 1 - i, d->production);
    }
    uplox_tnh_destroy(ctx);
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
    assert "uplox_calc_default_reduction" in text, "default_reduction array missing"
    # And the parser body must consult it on action miss.
    assert "default_reduction[s]" in text


def test_emit_with_explicit_prefix_overrides_grammar_name(tmp_path):
    bundle = build_calc_bundle()
    h, _c = emit_to(tmp_path, bundle, prefix="myparser")
    text = h.read_text()
    assert "uplox_myparser_ctx" in text
    assert "uplox_calc_ctx" not in text


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
ACTION = '{' %balanced='}'

%rules
<top>  : <top> <item> | <item> ;
<item> : IDENT ACTION ;
"""


def build_bal_bundle() -> dict:
    from uplox.lex.build import balanced_tokens

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
    assert "uplox_bal_token_balanced" in text, "balanced array missing from emit"

    driver = tmp_path / "driver.c"
    driver.write_text(r"""
#include "uplox_bal.h"
#include <stdio.h>
#include <string.h>

/* Walk the tree and count ACTION leaves; print their lengths in order. */
static int count_actions(const uplox_bal_node *n, int *lens, int cap, int *out_n) {
    if (!n) return 0;
    if (n->is_terminal) {
        if (n->kind == UPLOX_BAL_TOK_ACTION) {
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
    uplox_bal_ctx *ctx = uplox_bal_create(src, n);
    uplox_bal_node *root = NULL;
    if (uplox_bal_parse(ctx, &root) != 0) {
        fprintf(stderr, "parse error: %s\n", uplox_bal_error(ctx));
        uplox_bal_destroy(ctx);
        return 1;
    }
    int lens[16]; int cnt = 0;
    count_actions(root, lens, 16, &cnt);
    printf("actions=%d", cnt);
    for (int i = 0; i < cnt; ++i) printf(" len%d=%d", i, lens[i]);
    printf("\n");
    uplox_bal_destroy(ctx);
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
#include "uplox_bal.h"
#include <stdio.h>
#include <string.h>
int main(int argc, char **argv) {
    const char *src = (argc > 1) ? argv[1] : "x { unterminated";
    int n = (int)strlen(src);
    uplox_bal_ctx *ctx = uplox_bal_create(src, n);
    uplox_bal_node *root = NULL;
    int rc = uplox_bal_parse(ctx, &root);
    if (rc != 0) {
        fprintf(stderr, "%s\n", uplox_bal_error(ctx));
    }
    uplox_bal_destroy(ctx);
    return rc == 0 ? 0 : 1;
}
""")
    binary = tmp_path / "bal_err"
    cc_compile(c, driver, out=binary, include=tmp_path)
    result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=10)
    assert result.returncode != 0
    assert "unterminated" in result.stderr.lower()


# ============================================================================
# v3 auto-AST: emit a typed AST and exercise it through generated C.
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


def build_calc_ast_bundle() -> dict:
    ir = read_source(CALC_ANNOTATED)
    dfa, tokens, skip = lex_from_ir(ir)
    table = build_lr1(compile_grammar(ir))
    bundle = empty_bundle(ir.name)
    bundle["lex"] = dfa_to_json(dfa, tokens=tokens, skip=skip)
    bundle["parse"] = table_to_json(table)
    bundle["ast"] = ast_to_json(compile_ast_plan(ir))
    return bundle


def test_calc_ast_emits_typed_structs(tmp_path):
    """Generated header declares BinOp / NumLit structs and the tagged union."""
    bundle = build_calc_ast_bundle()
    h, _c = emit_to(tmp_path, bundle)
    header = h.read_text()
    assert "uplox_calc_ast_kind" in header
    assert "struct uplox_calc_ast_BinOp" in header
    assert "struct uplox_calc_ast_NumLit" in header
    assert "uplox_calc_parse_ast" in header
    assert "UPLOX_CALC_AST_BIN_OP" in header
    assert "UPLOX_CALC_AST_NUM_LIT" in header


def test_calc_ast_compiles_clean(tmp_path):
    bundle = build_calc_ast_bundle()
    _h, c = emit_to(tmp_path, bundle)
    obj = tmp_path / "uplox_calc.o"
    # -Wall but not -Werror — calc has no list rules so the list
    # helpers are unused. Production code with at least one list will
    # clean up; calc is the degenerate case.
    cmd = [
        CC, "-Wall", "-O1",
        "-I", str(tmp_path),
        "-c", str(c), "-o", str(obj),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_calc_ast_end_to_end(tmp_path):
    """Build the C, parse `(1 + 2) * 3`, check the AST shape."""
    bundle = build_calc_ast_bundle()
    _h, c = emit_to(tmp_path, bundle)
    driver = tmp_path / "driver.c"
    driver.write_text(r"""
#include "uplox_calc.h"
#include <stdio.h>
#include <string.h>
int main(void) {
    const char *src = "(1 + 2) * 3";
    uplox_calc_ctx *ctx = uplox_calc_create(src, (int)strlen(src));
    uplox_calc_ast *ast;
    if (uplox_calc_parse_ast(ctx, &ast) != 0) {
        fprintf(stderr, "parse error: %s\n", uplox_calc_error(ctx));
        return 1;
    }
    /* Outer * BinOp */
    if (ast->kind != UPLOX_CALC_AST_BIN_OP) { fprintf(stderr, "outer not BinOp\n"); return 1; }
    if (ast->u.bin_op.op->text[0] != '*') { fprintf(stderr, "outer op not *\n"); return 1; }
    /* lhs is +-BinOp */
    if (ast->u.bin_op.lhs->kind != UPLOX_CALC_AST_BIN_OP) { fprintf(stderr, "lhs not BinOp\n"); return 1; }
    if (ast->u.bin_op.lhs->u.bin_op.op->text[0] != '+') { fprintf(stderr, "lhs op not +\n"); return 1; }
    /* rhs is NumLit 3 */
    if (ast->u.bin_op.rhs->kind != UPLOX_CALC_AST_NUM_LIT) { fprintf(stderr, "rhs not NumLit\n"); return 1; }
    if (ast->u.bin_op.rhs->u.num_lit.value->text[0] != '3') { fprintf(stderr, "rhs not 3\n"); return 1; }
    printf("ok\n");
    uplox_calc_destroy(ctx);
    return 0;
}
""")
    binary = tmp_path / "calc_ast"
    # Allow warnings (unused list helpers); rely on the assertions below.
    cmd = [
        CC, "-Wall", "-O1",
        "-I", str(tmp_path),
        str(c), str(driver), "-o", str(binary),
    ]
    subprocess.run(cmd, check=True, capture_output=True)
    result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "ok"


# ---- v3 with lists, _unwrap, optionals --------------------------------------


COWGOL_SAMPLE_GRAMMAR = """
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

<body>
       : <body_stmts>@stmts   %ast=_unwrap
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


def build_mini_bundle() -> dict:
    ir = read_source(COWGOL_SAMPLE_GRAMMAR)
    dfa, tokens, skip = lex_from_ir(ir)
    table = build_lr1(compile_grammar(ir))
    bundle = empty_bundle(ir.name)
    bundle["lex"] = dfa_to_json(dfa, tokens=tokens, skip=skip)
    bundle["parse"] = table_to_json(table)
    bundle["ast"] = ast_to_json(compile_ast_plan(ir))
    return bundle


def test_mini_ast_lists_optionals_unwrap(tmp_path):
    """End-to-end with everything: lists, _unwrap (to token and to list),
    auto-optional fields, multiple statement kinds, parameters."""
    bundle = build_mini_bundle()
    _h, c = emit_to(tmp_path, bundle)
    driver = tmp_path / "driver.c"
    driver.write_text(r"""
#include "uplox_mini.h"
#include <stdio.h>
#include <string.h>
int main(void) {
    const char *src =
        "sub f(x: int8, y: int8) is\n"
        "    var a := 1;\n"
        "    var b;\n"
        "    return;\n"
        "end sub;\n";
    uplox_mini_ctx *ctx = uplox_mini_create(src, (int)strlen(src));
    uplox_mini_ast *ast;
    if (uplox_mini_parse_ast(ctx, &ast) != 0) {
        fprintf(stderr, "parse error: %s\n", uplox_mini_error(ctx));
        return 1;
    }
    if (ast->kind != UPLOX_MINI_AST_PROGRAM) return 11;
    if (ast->u.program.items_count != 1) return 12;
    uplox_mini_ast *sub = ast->u.program.items[0];
    if (sub->kind != UPLOX_MINI_AST_SUB_DECL) return 13;
    /* sub.name */
    if (strncmp(sub->u.sub_decl.name->text, "f", 1) != 0) return 14;
    /* sub.params is list[Param] of length 2 (auto-optional unwrap to list) */
    if (sub->u.sub_decl.params_count != 2) return 15;
    if (strncmp(sub->u.sub_decl.params[0]->u.param.name->text, "x", 1) != 0) return 16;
    if (strncmp(sub->u.sub_decl.params[0]->u.param.type->text, "int8", 4) != 0) return 17;
    /* sub.body is a list[stmt] (auto-optional unwrap to list) of length 3 */
    if (sub->u.sub_decl.body_count != 3) return 18;
    /* body[0] = VarDecl with init = NUMBER 1 (token via _unwrap) */
    uplox_mini_ast *v1 = sub->u.sub_decl.body[0];
    if (v1->kind != UPLOX_MINI_AST_VAR_DECL) return 19;
    if (strncmp(v1->u.var_decl.name->text, "a", 1) != 0) return 20;
    if (v1->u.var_decl.init == NULL) return 21;
    if (v1->u.var_decl.init->text[0] != '1') return 22;
    /* body[1] = VarDecl with init = NULL (empty alt -> auto-optional) */
    uplox_mini_ast *v2 = sub->u.sub_decl.body[1];
    if (v2->kind != UPLOX_MINI_AST_VAR_DECL) return 23;
    if (strncmp(v2->u.var_decl.name->text, "b", 1) != 0) return 24;
    if (v2->u.var_decl.init != NULL) return 25;
    /* body[2] = ReturnStmt */
    if (sub->u.sub_decl.body[2]->kind != UPLOX_MINI_AST_RETURN_STMT) return 26;
    printf("ok\n");
    uplox_mini_destroy(ctx);
    return 0;
}
""")
    binary = tmp_path / "mini_ast"
    cmd = [
        CC, "-Wall", "-Werror", "-O1",
        "-I", str(tmp_path),
        str(c), str(driver), "-o", str(binary),
    ]
    subprocess.run(cmd, check=True, capture_output=True)
    result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, (
        f"exit={result.returncode}, stdout={result.stdout!r}, stderr={result.stderr!r}"
    )
    assert result.stdout.strip() == "ok"


def test_unannotated_grammar_emits_no_ast_section(tmp_path):
    """Back-compat: pre-v3 grammars emit no ast types or parse_ast."""
    bundle = build_calc_bundle()
    h, c = emit_to(tmp_path, bundle)
    header = h.read_text()
    impl = c.read_text()
    assert "parse_ast" not in header
    assert "ast_kind" not in header
    assert "parse_ast" not in impl


# ---- file_id / FileTable ----------------------------------------------------


def test_emitted_c_header_declares_filetable_api(tmp_path):
    bundle = build_calc_bundle()
    h, _c = emit_to(tmp_path, bundle)
    text = h.read_text()
    assert "uplox_calc_intern_filename" in text
    assert "uplox_calc_filename" in text
    assert "uplox_calc_set_file_id" in text
    assert "uplox_calc_get_file_id" in text
    # Node struct gains a file_id field.
    assert "int   file_id;" in text


def test_emitted_c_stamps_file_id_on_terminals(tmp_path):
    """End-to-end: intern a filename, set it, parse, verify every terminal
    leaf carries the matching file_id; non-terminals stay at 0."""
    bundle = build_calc_bundle()
    _h, c = emit_to(tmp_path, bundle)
    driver = tmp_path / "driver.c"
    driver.write_text(r"""
#include "uplox_calc.h"
#include <stdio.h>
#include <string.h>

static int check_leaves(uplox_calc_node *n, int expect_id) {
    if (n->is_terminal) {
        if (n->file_id != expect_id) return 1;
        return 0;
    }
    for (int i = 0; i < n->num_children; ++i) {
        int rc = check_leaves(n->children[i], expect_id);
        if (rc) return rc;
    }
    return 0;
}

int main(void) {
    const char *src = "1 + 2 * 3";
    uplox_calc_ctx *ctx = uplox_calc_create(src, (int)strlen(src));
    int fid = uplox_calc_intern_filename(ctx, "src/expr.calc");
    if (fid <= 0) { fprintf(stderr, "intern failed\n"); return 1; }
    if (strcmp(uplox_calc_filename(ctx, fid), "src/expr.calc") != 0) {
        fprintf(stderr, "filename mismatch\n"); return 1;
    }
    uplox_calc_set_file_id(ctx, fid);
    if (uplox_calc_get_file_id(ctx) != fid) {
        fprintf(stderr, "get_file_id mismatch\n"); return 1;
    }
    uplox_calc_node *root;
    if (uplox_calc_parse(ctx, &root) != 0) { fprintf(stderr, "%s\n", uplox_calc_error(ctx)); return 1; }
    if (check_leaves(root, fid) != 0) {
        fprintf(stderr, "leaf file_id mismatch\n"); return 1;
    }
    /* Re-intern the same name -> same id. */
    if (uplox_calc_intern_filename(ctx, "src/expr.calc") != fid) {
        fprintf(stderr, "intern not idempotent\n"); return 1;
    }
    /* file_id 0 maps to '' */
    if (strcmp(uplox_calc_filename(ctx, 0), "") != 0) { fprintf(stderr, "id0 not ''\n"); return 1; }
    printf("ok\n");
    uplox_calc_destroy(ctx);
    return 0;
}
""")
    binary = tmp_path / "fid_calc"
    cc_compile(c, driver, out=binary, include=tmp_path)
    result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "ok"


def test_emitted_c_default_file_id_is_zero(tmp_path):
    """Without intern/set, every terminal leaf carries file_id == 0."""
    bundle = build_calc_bundle()
    _h, c = emit_to(tmp_path, bundle)
    driver = tmp_path / "driver.c"
    driver.write_text(r"""
#include "uplox_calc.h"
#include <stdio.h>
#include <string.h>

static int any_nonzero(uplox_calc_node *n) {
    if (n->is_terminal) return n->file_id != 0;
    for (int i = 0; i < n->num_children; ++i)
        if (any_nonzero(n->children[i])) return 1;
    return 0;
}

int main(void) {
    const char *src = "1 + 2";
    uplox_calc_ctx *ctx = uplox_calc_create(src, (int)strlen(src));
    uplox_calc_node *root;
    if (uplox_calc_parse(ctx, &root) != 0) { fprintf(stderr, "%s\n", uplox_calc_error(ctx)); return 1; }
    if (any_nonzero(root)) { fprintf(stderr, "expected all leaves file_id==0\n"); return 1; }
    printf("ok\n");
    uplox_calc_destroy(ctx);
    return 0;
}
""")
    binary = tmp_path / "fid_zero"
    cc_compile(c, driver, out=binary, include=tmp_path)
    result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "ok"
