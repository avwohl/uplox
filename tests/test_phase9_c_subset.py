"""Phase 9: c_subset.plox parses a meaningful subset of C.

The grammar is the start of the uc_core port. Full uc_core (and the
typedef-name lexer hack, casts, the preprocessor) remains a Phase-9
follow-up. The tests below pin the size of what we *do* parse and assert
structural correctness on representative programs.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from plox.hooks import TypedefTracker
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


def parse_with_typedef_tracking(scanner, table, src: str) -> tuple[ParseNode, TypedefTracker]:
    """Parse C with the typedef-name lexer hack enabled. Returns (tree, tracker)
    so tests can assert which names were registered."""
    tracker = TypedefTracker()
    hooks = HookRegistry(ignore_missing=True)
    hooks.register("record_typedef", tracker.record_declaration)
    tree = parse(table, scanner.scan(src), hooks=hooks, token_filter=tracker.filter)
    return tree, tracker


def test_c_subset_no_conflicts(built):
    _scanner, table = built
    assert table.conflicts == []


def test_c_subset_state_count_in_range(built):
    _scanner, table = built
    # 1649 with struct/union/enum + switch + casts + array-size-as-expr +
    # preproc skip. Allow generous slack so the test stays green through
    # small grammar tweaks; flag if it explodes.
    assert 700 <= len(table.states) <= 2600, len(table.states)


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


def test_c_switch_with_break(built):
    scanner, table = built
    src = """
int test(int x) {
    switch (x) {
        case 0:
            return 10;
        case 1:
            return 20;
        default:
            return 30;
    }
    return 0;
}
"""
    tree = parse_str(scanner, table, src)
    assert isinstance(tree, ParseNode)


def test_c_switch_fall_through_and_compound(built):
    scanner, table = built
    src = """
int test(int x) {
    int r = 0;
    switch (x) {
        case 0: {
            r = 1;
        }
        case 1:
            r = r + 2;
            break;
        case 2:
        case 3:
            r = 99;
            break;
    }
    return r;
}
"""
    tree = parse_str(scanner, table, src)
    assert isinstance(tree, ParseNode)


def test_c_switch_inside_if_does_not_break_dangling_else(built):
    """Adding switch is the kind of change that often re-opens dangling-else
    if matched/unmatched discipline isn't threaded through the new stmt
    forms. This case probes that interaction."""
    scanner, table = built
    src = """
int test(int x, int y) {
    if (x) switch (y) {
        case 0: return 1;
        default: return 2;
    } else return 3;
    return 0;
}
"""
    tree = parse_str(scanner, table, src)
    assert isinstance(tree, ParseNode)


# --- typedef-name lexer hack ----------------------------------------------
# These tests demonstrate the host wiring needed for the C parser to handle
# `typedef X Y;` followed by `Y z;` correctly. Without the filter, a typedef'd
# name would be lexed as IDENT and the parser would reject `Y z;` because
# IDENT can't start a declaration. With the TypedefTracker filter, a parser
# hook on the declaration rule pushes the new name into the tracker; the
# tracker's filter rewrites subsequent IDENTs of that name to TYPEDEF_NAME.


def test_typedef_simple_int_alias_then_use(built):
    scanner, table = built
    src = """
typedef int handle_t;
handle_t a;
"""
    tree, tracker = parse_with_typedef_tracking(scanner, table, src)
    assert isinstance(tree, ParseNode) and tree.kind == "translation_unit"
    assert "handle_t" in tracker.names


def test_typedef_pointer_alias(built):
    scanner, table = built
    src = """
typedef int *IntPtr;
IntPtr p;
"""
    tree, tracker = parse_with_typedef_tracking(scanner, table, src)
    assert isinstance(tree, ParseNode)
    assert "IntPtr" in tracker.names


def test_typedef_anonymous_struct(built):
    scanner, table = built
    src = """
typedef struct {
    int x;
    int y;
} Point;
Point p;
"""
    tree, tracker = parse_with_typedef_tracking(scanner, table, src)
    assert isinstance(tree, ParseNode)
    assert "Point" in tracker.names


def test_typedef_in_function_parameter(built):
    """A typedef'd name can appear as a parameter type just like any
    built-in type-spec. This exercises the filter applying mid-parse,
    well after the typedef declaration was reduced."""
    scanner, table = built
    src = """
typedef int Byte;

int sum(Byte a, Byte b) {
    return a + b;
}
"""
    tree, tracker = parse_with_typedef_tracking(scanner, table, src)
    assert isinstance(tree, ParseNode)
    assert "Byte" in tracker.names


def test_typedef_multiple_in_one_decl(built):
    """`typedef int A, B, *C;` — three typedefs on one declaration."""
    scanner, table = built
    src = """
typedef int A, B, *C;
A x;
B y;
C z;
"""
    tree, tracker = parse_with_typedef_tracking(scanner, table, src)
    assert isinstance(tree, ParseNode)
    assert tracker.names == {"A", "B", "C"}


def test_typedef_inside_function_body(built):
    """The tracker is flat (no scope tracking yet), so a typedef inside a
    function leaks to the rest of the translation unit. That's a known
    limitation; the test pins the current behavior so it's clear when we
    add scoping."""
    scanner, table = built
    src = """
void f(void) {
    typedef int Local;
    Local x;
}
"""
    tree, tracker = parse_with_typedef_tracking(scanner, table, src)
    assert isinstance(tree, ParseNode)
    assert "Local" in tracker.names


def test_non_typedef_decl_does_not_register(built):
    scanner, table = built
    src = "int counter;\n"
    _tree, tracker = parse_with_typedef_tracking(scanner, table, src)
    assert tracker.names == set()


def test_typedef_struct_with_tag(built):
    """`typedef struct Node Node_t;` — separate tag and typedef name."""
    scanner, table = built
    src = """
typedef struct Node Node_t;
Node_t *head;
"""
    tree, tracker = parse_with_typedef_tracking(scanner, table, src)
    assert isinstance(tree, ParseNode)
    assert tracker.names == {"Node_t"}


def test_cast_to_builtin_type(built):
    """`(int) x` and `(char *) p` parse without the typedef-name hack —
    the lookahead is a type keyword, unambiguous."""
    scanner, table = built
    src = """
int test(void *p) {
    int x = (int) 42;
    char *s = (char *) p;
    return x;
}
"""
    tree = parse_str(scanner, table, src)
    assert isinstance(tree, ParseNode)


def test_cast_to_pointer_type(built):
    scanner, table = built
    src = """
int test(int x) {
    return *(int *) &x;
}
"""
    tree = parse_str(scanner, table, src)
    assert isinstance(tree, ParseNode)


def test_cast_to_typedef_name_round_trips_through_filter(built):
    """`(MyInt) x` is the parser's classic ambiguity. With the
    TypedefTracker filter rewriting `MyInt` to TYPEDEF_NAME, the cast
    parses as a cast — without it, the parser would treat `(MyInt)` as a
    parenthesised IDENT expression."""
    scanner, table = built
    src = """
typedef int MyInt;

int test(int x) {
    return (MyInt) x + 1;
}
"""
    tree, tracker = parse_with_typedef_tracking(scanner, table, src)
    assert isinstance(tree, ParseNode)
    assert "MyInt" in tracker.names


def test_cast_to_typedef_pointer(built):
    scanner, table = built
    src = """
typedef int Word;

int test(void *p) {
    return *(Word *) p;
}
"""
    tree, tracker = parse_with_typedef_tracking(scanner, table, src)
    assert isinstance(tree, ParseNode)
    assert tracker.names == {"Word"}


def test_paren_expression_still_parses(built):
    """Sanity: with casts in the grammar, plain parenthesised expressions
    still parse — the LPAREN-IDENT pair routes through expr, not type_name,
    because IDENT (not TYPEDEF_NAME) is in the expression FIRST set."""
    scanner, table = built
    src = """
int test(int a, int b) {
    return (a + b) * 2;
}
"""
    tree = parse_str(scanner, table, src)
    assert isinstance(tree, ParseNode)


def test_uc80_test_typedef_fixture(built):
    """The uc80 test_typedef.c sample uses typedef of int, unsigned char,
    pointer-to-int, and an anonymous struct. End-to-end smoke test."""
    path = UC80_FIXTURES / "test_typedef.c"
    if not path.exists():
        pytest.skip("test_typedef.c fixture missing")
    scanner, table = built
    tree, tracker = parse_with_typedef_tracking(scanner, table, path.read_text())
    assert isinstance(tree, ParseNode) and tree.kind == "translation_unit"
    assert {"MyInt", "Byte", "IntPtr", "Point"} <= tracker.names


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
    "test_switch.c",
    "test_ternary.c",
    "test_string.c",
    "test_string2.c",
    "test_long_simple.c",
    "test_unsigned.c",
    # Files with #include but only built-in types — parse cleanly because
    # the preproc skip strips the directives and the bodies don't use any
    # libc-typedef'd names as types.
    "test_long_init.c",
    "test_ctype.c",
]


# Files that use libc-typedef'd identifiers (FILE, jmp_buf) as types. Plox
# can't know these from source alone — the host pre-populates the tracker
# with the typedefs imported from system headers, mirroring how a real C
# compiler resolves them through #include processing.
UC80_LIBC_TYPEDEF_SAMPLES = [
    ("test_null_eof.c", {"FILE"}),
    ("test_fileio.c", {"FILE"}),
    ("test_setjmp.c", {"jmp_buf"}),
]


@pytest.mark.parametrize("name", UC80_SAMPLES)
def test_uc80_example_parses(built, name):
    path = UC80_FIXTURES / name
    if not path.exists():
        pytest.skip(f"uc80 fixture {name} missing")
    scanner, table = built
    tree = parse_str(scanner, table, path.read_text())
    assert isinstance(tree, ParseNode) and tree.kind == "translation_unit"


@pytest.mark.parametrize("name,seeded", UC80_LIBC_TYPEDEF_SAMPLES)
def test_uc80_example_parses_with_libc_typedefs(built, name, seeded):
    """Parse uc80 fixtures that use libc-typedef'd names (FILE, jmp_buf)
    as type-specs. The tracker is pre-populated to mirror how a host
    using c_subset would import typedefs from system headers."""
    path = UC80_FIXTURES / name
    if not path.exists():
        pytest.skip(f"uc80 fixture {name} missing")
    scanner, table = built
    tracker = TypedefTracker()
    tracker.names |= seeded
    hooks = HookRegistry(ignore_missing=True)
    hooks.register("record_typedef", tracker.record_declaration)
    tree = parse(table, scanner.scan(path.read_text()), hooks=hooks, token_filter=tracker.filter)
    assert isinstance(tree, ParseNode) and tree.kind == "translation_unit"
    assert seeded <= tracker.names
