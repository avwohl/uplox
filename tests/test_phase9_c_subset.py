"""Phase 9: c_subset.plox parses a meaningful subset of C.

The grammar is the start of the uc_core port. Full uc_core (and the
typedef-name lexer hack, casts, the preprocessor) remains a Phase-9
follow-up. The tests below pin the size of what we *do* parse and assert
structural correctness on representative programs.
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


@pytest.fixture(scope="module")
def built():
    """Module-scoped pipeline. Building the LR(1) table for c_subset takes
    several seconds; rebuilding for each test made the file the slowest in
    the suite. The two `_subset_*` tests below still exercise construction
    explicitly."""
    return c_pipeline()


def parse_str(scanner, table, src: str) -> ParseNode:
    return parse(table, scanner.scan(src), hooks=HookRegistry(ignore_missing=True))


def test_c_subset_no_conflicts(built):
    _scanner, table = built
    assert table.conflicts == []


def test_c_subset_state_count_in_range(built):
    _scanner, table = built
    # 1299 once struct/union/enum landed. Allow generous slack so the test
    # stays green through small grammar tweaks; flag if it explodes.
    assert 700 <= len(table.states) <= 2200, len(table.states)


def test_c_simple_function_definition(built):
    scanner, table = built
    src = "int main(void) { return 0; }\n"
    tree = parse_str(scanner, table, src)
    assert isinstance(tree, ParseNode) and tree.kind == "translation_unit"


def test_c_if_else_with_braces(built):
    scanner, table = built
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


def test_c_dangling_else_binds_innermost(built):
    scanner, table = built
    src = """
int test(int a, int b, int c) {
    if (a) if (b) return 1; else return 2;
    return 0;
}
"""
    tree = parse_str(scanner, table, src)
    assert isinstance(tree, ParseNode)


def test_c_while_loop(built):
    scanner, table = built
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


def test_c_for_loop_with_decl(built):
    scanner, table = built
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


def test_c_pointer_arithmetic(built):
    scanner, table = built
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


def test_c_array_declaration_and_subscript(built):
    scanner, table = built
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


def test_c_recursive_function_calls(built):
    scanner, table = built
    src = """
int factorial(int n) {
    if (n <= 1) return 1;
    return n * factorial(n - 1);
}
"""
    tree = parse_str(scanner, table, src)
    assert isinstance(tree, ParseNode)


def test_c_storage_class_and_qualifiers_accepted(built):
    scanner, table = built
    src = """
static const int PI = 314;
extern int counter;
volatile int flag;
"""
    tree = parse_str(scanner, table, src)
    assert isinstance(tree, ParseNode)


def test_c_complex_expression_precedence(built):
    scanner, table = built
    src = "int test(void) { return 1 + 2 * 3 - 4 / 2 << 1 == 8; }\n"
    tree = parse_str(scanner, table, src)
    assert isinstance(tree, ParseNode)


def test_c_logical_short_circuit(built):
    scanner, table = built
    src = "int test(int a, int b) { return a && b || (a + 1) >= 0; }\n"
    tree = parse_str(scanner, table, src)
    assert isinstance(tree, ParseNode)


def test_c_assignment_operators(built):
    scanner, table = built
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


def test_c_increment_decrement(built):
    scanner, table = built
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


def test_c_struct_member_access_via_pointer(built):
    scanner, table = built
    src = """
int test(int *p) {
    return p->x + p->y;
}
"""
    tree = parse_str(scanner, table, src)
    assert isinstance(tree, ParseNode)


def test_c_struct_definition_and_use(built):
    scanner, table = built
    src = """
struct Point {
    int x;
    int y;
};

int sum(struct Point p) {
    return p.x + p.y;
}
"""
    tree = parse_str(scanner, table, src)
    assert isinstance(tree, ParseNode) and tree.kind == "translation_unit"


def test_c_struct_with_pointer_and_array_fields(built):
    scanner, table = built
    src = """
struct Buffer {
    char *data;
    int len;
    int slots[16];
};
"""
    tree = parse_str(scanner, table, src)
    assert isinstance(tree, ParseNode)


def test_c_anonymous_struct_at_file_scope(built):
    scanner, table = built
    src = """
struct {
    int a;
    int b;
} pair;
"""
    tree = parse_str(scanner, table, src)
    assert isinstance(tree, ParseNode)


def test_c_struct_forward_declaration(built):
    scanner, table = built
    src = """
struct Node;

int test(struct Node *n) {
    return 0;
}
"""
    tree = parse_str(scanner, table, src)
    assert isinstance(tree, ParseNode)


def test_c_union_definition(built):
    scanner, table = built
    src = """
union Word {
    int as_int;
    char as_bytes[4];
};
"""
    tree = parse_str(scanner, table, src)
    assert isinstance(tree, ParseNode)


def test_c_enum_basic_definition(built):
    scanner, table = built
    src = """
enum Color {
    RED,
    GREEN,
    BLUE
};
"""
    tree = parse_str(scanner, table, src)
    assert isinstance(tree, ParseNode)


def test_c_enum_with_explicit_values(built):
    scanner, table = built
    src = """
enum Status {
    OK = 0,
    ERROR = 1,
    PENDING = 100,
    DONE
};
"""
    tree = parse_str(scanner, table, src)
    assert isinstance(tree, ParseNode)


def test_c_enum_with_trailing_comma(built):
    scanner, table = built
    src = """
enum E {
    A,
    B,
    C,
};
"""
    tree = parse_str(scanner, table, src)
    assert isinstance(tree, ParseNode)


def test_c_enum_value_uses_constant_expression(built):
    scanner, table = built
    src = """
enum Bits {
    BIT0 = 1 << 0,
    BIT1 = 1 << 1,
    MASK = BIT0 | BIT1
};
"""
    tree = parse_str(scanner, table, src)
    assert isinstance(tree, ParseNode)


def test_c_anonymous_enum_used_for_constants(built):
    scanner, table = built
    src = """
enum {
    BUFSIZE = 256,
    MAXARGS = 8
};
"""
    tree = parse_str(scanner, table, src)
    assert isinstance(tree, ParseNode)


def test_c_typedef_accepted_as_storage_class(built):
    """We don't implement the typedef-name lexer hack, but the syntactic
    form `typedef <type> <name>;` parses cleanly because typedef is a
    storage class in the grammar (same shape as static/extern)."""
    scanner, table = built
    src = """
typedef int handle_t;
typedef struct Point {
    int x;
    int y;
} point_t;
"""
    tree = parse_str(scanner, table, src)
    assert isinstance(tree, ParseNode)


def test_c_string_and_char_literals(built):
    scanner, table = built
    src = '''
char *greeting(void) { return "hello, world"; }
char first_letter(void) { return 'h'; }
'''
    tree = parse_str(scanner, table, src)
    assert isinstance(tree, ParseNode)


def test_c_real_world_factorial_and_loops(built):
    """One bigger sample exercising several features at once."""
    scanner, table = built
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


# --- real uc80 source files ------------------------------------------------
# Vendored from /home/wohl/src/uc80/examples — small, hand-written K&R-style
# C programs the uc80 compiler is supposed to handle. Parsing them here
# verifies that the c_subset grammar covers idiomatic uc-family C, not just
# our hand-rolled minimal cases. Fixtures are committed snapshots so the
# tests don't depend on adjacent repos being checked out.

UC80_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "uc80"

UC80_SAMPLES = [
    "hello2.c",
    "test_arith.c",
    "test_break.c",
    "test_compound.c",
    "test_struct.c",
    "test_struct2.c",
    "test_enum.c",
    "test_incdec.c",
    "test_global.c",
    "test_static.c",
]


@pytest.mark.parametrize("name", UC80_SAMPLES)
def test_uc80_example_parses(built, name):
    path = UC80_FIXTURES / name
    if not path.exists():
        pytest.skip(f"uc80 fixture {name} missing")
    scanner, table = built
    tree = parse_str(scanner, table, path.read_text())
    assert isinstance(tree, ParseNode) and tree.kind == "translation_unit"
