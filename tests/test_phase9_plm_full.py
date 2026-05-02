"""Phase 9 progress: plm_full grammar covers more PL/M-80 constructs.

The plan's Phase 9 deliverable is "port remaining uc* front-ends; declare
v1." Full bdos.plm parsing is a meaningful step but isn't fully achievable
without an upstream LITERALLY macro expander (EQU LITERALLY 'LITERALLY'
self-modifies the lexer). The tests below cover what the plm_full grammar
*does* handle on top of plm_subset:

* Module-level labeled DO blocks (BDOS-style)
* LITERALLY / DATA / INITIAL / AT / BASED in declarations
* Iterative DO TO/BY, DO WHILE, DO CASE
* The first 162 lines of the real bdos.plm source
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


PLOX_REPO = Path(__file__).resolve().parents[1]
PLM_FULL = PLOX_REPO / "examples" / "plm_full.plox"
BDOS_PREFIX = PLOX_REPO / "tests" / "fixtures" / "bdos_prefix.plm"


def plm_full_pipeline():
    ir = read_file(str(PLM_FULL))
    dfa, _toks, skip = lex_from_ir(ir)
    scanner = Scanner(dfa=dfa, skip_tokens=frozenset(skip))
    table = build_lr1(compile_grammar(ir))
    return scanner, table


@pytest.fixture(scope="module")
def built():
    """Module-scoped pipeline; building the LR(1) table for plm_full takes
    several seconds and shouldn't be repeated per test."""
    return plm_full_pipeline()


def parse_str(scanner, table, src: str) -> ParseNode:
    return parse(table, scanner.scan(src), hooks=HookRegistry(ignore_missing=True))


def test_plm_full_no_conflicts(built):
    _scanner, table = built
    assert table.conflicts == []


def test_module_level_labeled_do_block_parses(built):
    scanner, table = built
    src = (
        "BDOS:\n"
        "DO;\n"
        "  DECLARE FUNC BYTE;\n"
        "  RETURN;\n"
        "END BDOS;\n"
    )
    tree = parse_str(scanner, table, src)
    assert isinstance(tree, ParseNode) and tree.kind == "module"


def test_literally_declaration_parses(built):
    scanner, table = built
    src = (
        "MAIN: PROCEDURE;\n"
        "  DECLARE\n"
        "    JBOOT LITERALLY '0000H',\n"
        "    JBDOS LITERALLY '0005H';\n"
        "END MAIN;\n"
    )
    tree = parse_str(scanner, table, src)
    assert isinstance(tree, ParseNode)


def test_array_initial_and_based(built):
    scanner, table = built
    src = (
        "MAIN: PROCEDURE;\n"
        "  DECLARE\n"
        "    BUFFA ADDRESS INITIAL(80H),\n"
        "    BUFF BASED BUFFA (128) BYTE,\n"
        "    DIRSET BYTE INITIAL(0);\n"
        "END MAIN;\n"
    )
    tree = parse_str(scanner, table, src)
    assert isinstance(tree, ParseNode)


def test_at_clause(built):
    scanner, table = built
    src = (
        "MAIN: PROCEDURE;\n"
        "  DECLARE MEMSIZE ADDRESS AT(0006H);\n"
        "END MAIN;\n"
    )
    tree = parse_str(scanner, table, src)
    assert isinstance(tree, ParseNode)


def test_iterative_do_to_by(built):
    scanner, table = built
    src = (
        "MAIN: PROCEDURE;\n"
        "  DECLARE I BYTE, X BYTE;\n"
        "  DO I = 1 TO 10;\n"
        "    X = I;\n"
        "  END;\n"
        "  DO I = 0 TO 100 BY 2;\n"
        "    X = I;\n"
        "  END;\n"
        "END MAIN;\n"
    )
    tree = parse_str(scanner, table, src)
    assert isinstance(tree, ParseNode)


def test_do_case(built):
    scanner, table = built
    src = (
        "MAIN: PROCEDURE;\n"
        "  DECLARE X BYTE;\n"
        "  DO CASE X;\n"
        "    X = 0;\n"
        "    X = 1;\n"
        "    X = 2;\n"
        "  END;\n"
        "END MAIN;\n"
    )
    tree = parse_str(scanner, table, src)
    assert isinstance(tree, ParseNode)


def test_do_while(built):
    scanner, table = built
    src = (
        "MAIN: PROCEDURE;\n"
        "  DECLARE X BYTE;\n"
        "  DO WHILE X < 10;\n"
        "    X = X + 1;\n"
        "  END;\n"
        "END MAIN;\n"
    )
    tree = parse_str(scanner, table, src)
    assert isinstance(tree, ParseNode)


def test_array_size_after_name(built):
    """`TRAN(32) BYTE EXTERNAL` — PL/M's preferred form for fixed-size arrays."""
    scanner, table = built
    src = (
        "MAIN: PROCEDURE;\n"
        "  DECLARE\n"
        "    TRAN(32) BYTE EXTERNAL,\n"
        "    SECSIZ BYTE EXTERNAL;\n"
        "END MAIN;\n"
    )
    tree = parse_str(scanner, table, src)
    assert isinstance(tree, ParseNode)


@pytest.mark.skipif(not BDOS_PREFIX.exists(), reason="bdos prefix fixture missing")
def test_bdos_prefix_parses_end_to_end(built):
    """The 162-line prefix of Digital Research's BDOS source — everything up
    to but not including the EQU-as-LITERALLY-alias macro pattern that
    requires upstream macro expansion."""
    scanner, table = built
    src = BDOS_PREFIX.read_text()
    tree = parse_str(scanner, table, src)
    assert isinstance(tree, ParseNode) and tree.kind == "module"


def test_plm_full_supersedes_plm_subset(built):
    """Sanity: plm_subset's calc-style examples still work under plm_full."""
    scanner, table = built
    src = (
        "MAIN: PROCEDURE;\n"
        "  DECLARE X BYTE, Y BYTE;\n"
        "  X = 1 + 2;\n"
        "  IF X > 0 THEN Y = X; ELSE Y = 0;\n"
        "END MAIN;\n"
    )
    tree = parse_str(scanner, table, src)
    assert isinstance(tree, ParseNode)
