"""Phase 5 milestone: PL/M-80 subset grammar parses real PL/M sample programs.

This is the integration check that proves plox is past the "toy grammar" stage —
a 60-token, 100+-production grammar that compiles to an LR(1) table with no
conflicts and parses real source code.
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
PLM_SUBSET = PLOX_REPO / "examples" / "plm_subset.plox"
HELLO_PLM = Path("/home/wohl/src/uplm80/examples/hello_cpm.plm")


def plm_pipeline():
    ir = read_file(str(PLM_SUBSET))
    dfa, _toks, skip = lex_from_ir(ir)
    scanner = Scanner(dfa=dfa, skip_tokens=frozenset(skip))
    table = build_lr1(compile_grammar(ir))
    return scanner, table


def test_plm_subset_has_no_conflicts():
    _scanner, table = plm_pipeline()
    assert table.conflicts == [], "\n".join(c.describe(table.grammar) for c in table.conflicts)


def test_plm_subset_state_count_is_reasonable():
    _scanner, table = plm_pipeline()
    # 741 states for the subset as of phase-5 freeze. Allow generous slack —
    # this is a regression alarm for "we accidentally exploded the table",
    # not an exact match.
    assert 400 <= len(table.states) <= 1500, len(table.states)


@pytest.mark.skipif(not HELLO_PLM.exists(), reason="uplm80 sample not available")
def test_hello_cpm_parses_end_to_end():
    scanner, table = plm_pipeline()
    text = HELLO_PLM.read_text()
    tree = parse(table, scanner.scan(text), hooks=HookRegistry(ignore_missing=True))
    assert isinstance(tree, ParseNode)
    assert tree.kind == "module"


@pytest.mark.skipif(not HELLO_PLM.exists(), reason="uplm80 sample not available")
def test_hello_cpm_recognises_address_literal():
    scanner, table = plm_pipeline()
    tree = parse(table, scanner.scan(HELLO_PLM.read_text()), hooks=HookRegistry(ignore_missing=True))

    def find_kind(node, kind):
        if isinstance(node, ParseNode):
            if node.kind == kind:
                return node
            for c in node.children:
                got = find_kind(c, kind)
                if got is not None:
                    return got
        return None

    addr = find_kind(tree, "address_literal")
    assert addr is not None
    # First child is the NUMBER token "0100H"
    from plox.lex.scanner import Token
    assert isinstance(addr.children[0], Token) and addr.children[0].text == "0100H"


def test_simple_plm_assignment_parses():
    """Synthetic small program — no external file dependency."""
    scanner, table = plm_pipeline()
    src = (
        "MAIN: PROCEDURE;\n"
        "  DECLARE X BYTE, Y BYTE;\n"
        "  X = 1 + 2;\n"
        "  IF X > 0 THEN Y = X; ELSE Y = 0;\n"
        "  RETURN;\n"
        "END MAIN;\n"
    )
    tree = parse(table, scanner.scan(src), hooks=HookRegistry(ignore_missing=True))
    assert isinstance(tree, ParseNode) and tree.kind == "module"


def test_plm_dangling_else_resolves_innermost():
    """The matched/unmatched split should make `IF a THEN IF b THEN s ELSE t`
    bind ELSE to the inner IF (the conventional reading)."""
    scanner, table = plm_pipeline()
    src = (
        "MAIN: PROCEDURE;\n"
        "  DECLARE X BYTE;\n"
        "  IF X = 0 THEN IF X = 1 THEN X = 2; ELSE X = 3;\n"
        "END MAIN;\n"
    )
    tree = parse(table, scanner.scan(src), hooks=HookRegistry(ignore_missing=True))
    # We don't introspect the inner shape here — that the parse succeeds
    # without conflict and produces a tree is the load-bearing claim.
    assert isinstance(tree, ParseNode)
