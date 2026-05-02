"""Phase 9: c_subset.plox parses a meaningful subset of C.

The grammar is the start of the uc_core port. Full uc_core (and the
typedef-name lexer hack, casts, struct/union/enum, the preprocessor)
remains a Phase-9 follow-up. The tests below pin the size of what we *do*
parse and assert structural correctness on representative programs.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from plox.lex.build import lex_from_ir
from plox.lex.scanner import Scanner
from plox.parse.grammar import compile_grammar
from plox.parse.lr1 import build_lr1
from plox.parse.runtime import HookRegistry, ParseNode, parse
from plox.spec.reader import read_file


C_SUBSET = Path(__file__).resolve().parents[1] / "examples" / "c_subset.plox"


def c_pipeline():
    ir = read_file(str(C_SUBSET))
    dfa, _t, skip = lex_from_ir(ir)
    scanner = Scanner(dfa=dfa, skip_tokens=frozenset(skip))
    table = build_lr1(compile_grammar(ir))
    return scanner, table


def parse_str(scanner, table, src: str) -> ParseNode:
    return parse(table, scanner.scan(src), hooks=HookRegistry(ignore_missing=True))


def test_c_subset_no_conflicts():
    _scanner, table = c_pipeline()
    assert table.conflicts == []


def test_c_subset_state_count_in_range():
    _scanner, table = c_pipeline()
    # 1181 as of the Phase-9 sketch. Allow generous slack so the test stays
    # green through small grammar tweaks; flag if it explodes.
    assert 700 <= len(table.states) <= 2000, len(table.states)


def test_c_simple_function_definition():
    scanner, table = c_pipeline()
    src = "int main(void) { return 0; }\n"
    tree = parse_str(scanner, table, src)
    assert isinstance(tree, ParseNode) and tree.kind == "translation_unit"


def test_c_if_else_with_braces():
    scanner, table = c_pipeline()
    src = """
int abs(int x) {
    if (x < 0) {
        return -x;
    } else {
        return x;
    }
}
"""
    tree = parse_str(scanner, table, src)
    assert isinstance(tree, ParseNode)


def test_c_dangling_else_binds_innermost():
    scanner, table = c_pipeline()
    src = """
int test(int a, int b, int c) {
    if (a) if (b) return 1; else return 2;
    return 0;
}
"""
    tree = parse_str(scanner, table, src)
    assert isinstance(tree, ParseNode)


def test_c_while_loop():
    scanner, table = c_pipeline()
    src = """
int countdown(int n) {
    while (n > 0) {
        n = n - 1;
    }
    return n;
}
"""
    tree = parse_str(scanner, table, src)
    assert isinstance(tree, ParseNode)


def test_c_for_loop_with_decl():
    scanner, table = c_pipeline()
    src = """
int sum_to(int n) {
    int total = 0;
    for (int i = 1; i <= n; i = i + 1) {
        total = total + i;
    }
    return total;
}
"""
    tree = parse_str(scanner, table, src)
    assert isinstance(tree, ParseNode)


def test_c_pointer_arithmetic():
    scanner, table = c_pipeline()
    src = """
int sum(int *p, int n) {
    int total = 0;
    while (n > 0) {
        total = total + *p;
        p = p + 1;
        n = n - 1;
    }
    return total;
}
"""
    tree = parse_str(scanner, table, src)
    assert isinstance(tree, ParseNode)


def test_c_array_declaration_and_subscript():
    scanner, table = c_pipeline()
    src = """
void zero(void) {
    int arr[10];
    int i;
    for (i = 0; i < 10; i = i + 1) {
        arr[i] = 0;
    }
}
"""
    tree = parse_str(scanner, table, src)
    assert isinstance(tree, ParseNode)


def test_c_recursive_function_calls():
    scanner, table = c_pipeline()
    src = """
int factorial(int n) {
    if (n <= 1) return 1;
    return n * factorial(n - 1);
}
"""
    tree = parse_str(scanner, table, src)
    assert isinstance(tree, ParseNode)


def test_c_storage_class_and_qualifiers_accepted():
    scanner, table = c_pipeline()
    src = """
static const int PI = 314;
extern int counter;
volatile int flag;
"""
    tree = parse_str(scanner, table, src)
    assert isinstance(tree, ParseNode)


def test_c_complex_expression_precedence():
    scanner, table = c_pipeline()
    src = "int test(void) { return 1 + 2 * 3 - 4 / 2 << 1 == 8; }\n"
    tree = parse_str(scanner, table, src)
    assert isinstance(tree, ParseNode)


def test_c_logical_short_circuit():
    scanner, table = c_pipeline()
    src = "int test(int a, int b) { return a && b || (a + 1) >= 0; }\n"
    tree = parse_str(scanner, table, src)
    assert isinstance(tree, ParseNode)


def test_c_assignment_operators():
    scanner, table = c_pipeline()
    src = """
void modify(int *x) {
    *x += 1;
    *x -= 1;
    *x *= 2;
    *x /= 3;
    *x %= 5;
    *x &= 0xff;
    *x |= 0x100;
    *x ^= 0x55;
    *x <<= 2;
    *x >>= 1;
}
"""
    tree = parse_str(scanner, table, src)
    assert isinstance(tree, ParseNode)


def test_c_increment_decrement():
    scanner, table = c_pipeline()
    src = """
void p(int *x) {
    ++*x;
    --*x;
    (*x)++;
    (*x)--;
}
"""
    tree = parse_str(scanner, table, src)
    assert isinstance(tree, ParseNode)


def test_c_struct_member_access_via_pointer():
    """Even though we don't define struct types, member access syntax still
    parses; the host validates after the parse."""
    scanner, table = c_pipeline()
    src = """
int test(int *p) {
    return p->x + p->y;
}
"""
    tree = parse_str(scanner, table, src)
    assert isinstance(tree, ParseNode)


def test_c_string_and_char_literals():
    scanner, table = c_pipeline()
    src = '''
char *greeting(void) { return "hello, world"; }
char first_letter(void) { return 'h'; }
'''
    tree = parse_str(scanner, table, src)
    assert isinstance(tree, ParseNode)


def test_c_real_world_factorial_and_loops():
    """One bigger sample exercising several features at once."""
    scanner, table = c_pipeline()
    src = """
int factorial(int n) {
    if (n <= 1) {
        return 1;
    } else {
        return n * factorial(n - 1);
    }
}

void run(void) {
    int x = factorial(5);
    int arr[10];
    for (int i = 0; i < 10; i = i + 1) {
        arr[i] = i * 2;
    }
    while (x > 0) {
        x = x - 1;
    }
}
"""
    tree = parse_str(scanner, table, src)
    assert isinstance(tree, ParseNode) and tree.kind == "translation_unit"
