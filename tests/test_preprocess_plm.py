"""End-to-end tests for the PL/M preprocessor (`plox.preprocess.plm`)
and its grammar (`examples/plm_pre.plox`).

The preprocessor handles the parts of PL/M-80 that don't fit an LR core:
LITERALLY macro substitution (including the EQU-as-LITERALLY-alias
bootstrap), ``$``-directive lines, and case-insensitive folding.
Output is a transformed PL/M source string that ``plm_full`` parses
without further special-casing — the two-grammar pipeline.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from plox.lex.build import balanced_tokens, lex_from_ir
from plox.lex.scanner import Scanner
from plox.parse.grammar import compile_grammar
from plox.parse.lr1 import build_lr1
from plox.parse.runtime import HookRegistry, ParseNode, parse
from plox.preprocess.plm import preprocess
from plox.spec.reader import read_file

PLOX_REPO = Path(__file__).resolve().parents[1]
PLM_FULL = PLOX_REPO / "examples" / "plm_full.plox"


@pytest.fixture(scope="module")
def plm_full_pipeline():
    """Module-scoped: building the LR(1) table for plm_full takes a few
    seconds and shouldn't be repeated per test."""
    ir = read_file(str(PLM_FULL))
    dfa, _toks, skip = lex_from_ir(ir)
    scanner = Scanner(
        dfa=dfa, skip_tokens=frozenset(skip), balanced=balanced_tokens(ir)
    )
    table = build_lr1(compile_grammar(ir))
    return scanner, table


def parse_via_pipeline(scanner, table, src: str) -> ParseNode:
    """Run the two-stage pipeline: plm_pre, then plm_full."""
    pre = preprocess(src)
    tree = parse(
        table,
        scanner.scan(pre.transformed),
        hooks=HookRegistry(ignore_missing=True),
    )
    assert isinstance(tree, ParseNode)
    return tree


# --- plm_pre on its own ------------------------------------------------------


def test_literally_substitution_replaces_idents():
    src = """
DECLARE
    JBOOT LITERALLY '0000H',
    TBUFF LITERALLY '0080H';

START: PROCEDURE;
    CALL JBOOT;
    BUF = TBUFF;
END START;
"""
    result = preprocess(src)
    assert result.macros == {"JBOOT": "0000H", "TBUFF": "0080H"}
    assert "CALL 0000H" in result.transformed
    assert "BUF = 0080H" in result.transformed
    # The LITERALLY definitions themselves stay in the output (plm_full
    # accepts `IDENT LITERALLY STRING` as a valid decl_item).
    assert "LITERALLY '0000H'" in result.transformed


def test_dollar_directive_recorded_and_stripped():
    src = "$Q=1\n$RIGHTMARGIN=80\n\nDECLARE X BYTE;\n"
    result = preprocess(src)
    assert [d.raw for d in result.directives] == ["$Q=1", "$RIGHTMARGIN=80"]
    assert "$Q=1" not in result.transformed
    assert "$RIGHTMARGIN=80" not in result.transformed
    assert "DECLARE X BYTE" in result.transformed


def test_dollar_include_directive_recorded_not_expanded():
    """plm_pre records ``$INCLUDE`` directives but does not expand them.
    Hosts that want inclusion can splice the included file themselves."""
    src = "$INCLUDE (:F1:CPIO.PLB)\nDECLARE X BYTE;\n"
    result = preprocess(src)
    assert len(result.directives) == 1
    assert result.directives[0].raw == "$INCLUDE (:F1:CPIO.PLB)"


def test_equ_bootstrap_makes_equ_an_alias_for_literally():
    """`EQU LITERALLY 'LITERALLY'` records EQU → "LITERALLY", and from
    that point on `<NAME> EQU '<body>'` lexes-and-reduces the same way
    `<NAME> LITERALLY '<body>'` would. The mechanism mirrors the C
    typedef-name hack: a token filter rewrites the IDENT to the
    KW_LITERALLY token after each post_reduce hook updates the alias
    set."""
    src = """
DECLARE
    EQU LITERALLY 'LITERALLY',
    FOREVER EQU 'WHILE TRUE',
    TRUE EQU '1';
"""
    result = preprocess(src)
    assert result.macros == {
        "EQU": "LITERALLY",
        "FOREVER": "WHILE TRUE",
        "TRUE": "1",
    }
    # EQU references in the output get rewritten to LITERALLY so plm_full
    # parses the resulting decl_items.
    assert "FOREVER LITERALLY 'WHILE TRUE'" in result.transformed
    assert "TRUE LITERALLY '1'" in result.transformed


def test_case_insensitive_folding_outside_strings():
    src = "declare x byte; /* mixed case ok */\n"
    result = preprocess(src)
    assert "DECLARE X BYTE" in result.transformed
    # Comments are preserved as-is (the fold leaves /* ... */ alone).
    assert "/* mixed case ok */" in result.transformed


def test_case_fold_preserves_string_literal_contents():
    """The case-fold pass leaves string contents at their original case
    (in the transformed source). LITERALLY *bodies* are upper-cased
    when stored in the macro table — they represent PL/M source to
    splice back in, and PL/M is case-insensitive at the language
    level — so the macro table holds the canonical-case form."""
    src = "DECLARE M LITERALLY 'lower case body';\n"
    result = preprocess(src)
    assert result.macros == {"M": "LOWER CASE BODY"}
    # Transformed output preserves original-case string content.
    assert "'lower case body'" in result.transformed


def test_lowercase_keywords_folded_for_main_parser():
    """Lowercase PL/M source should preprocess into a form plm_full
    accepts. PL/M-80 is case-insensitive at the language level."""
    src = "main: procedure; declare x byte; end main;\n"
    result = preprocess(src)
    assert "MAIN: PROCEDURE" in result.transformed
    assert "DECLARE X BYTE" in result.transformed


def test_ctrl_z_eof_marker_stripped():
    """CP/M source files end with a Ctrl-Z (0x1A) EOF marker. The
    preprocessor strips them so the lexer doesn't choke."""
    src = "DECLARE X BYTE;\n\x1a"
    result = preprocess(src)
    assert "\x1a" not in result.transformed
    assert "DECLARE X BYTE" in result.transformed


# --- plm_pre + plm_full pipeline ---------------------------------------------


def test_pipeline_parses_simple_program(plm_full_pipeline):
    scanner, table = plm_full_pipeline
    src = """
DECLARE JBOOT LITERALLY '0000H';

START: PROCEDURE;
    CALL JBOOT;
END START;
"""
    tree = parse_via_pipeline(scanner, table, src)
    assert tree.kind == "module"


def test_pipeline_parses_lowercase_program(plm_full_pipeline):
    """Lowercase keywords should round-trip cleanly through case-fold."""
    scanner, table = plm_full_pipeline
    src = """
declare x byte;

main: procedure;
    x = 0;
end main;
"""
    tree = parse_via_pipeline(scanner, table, src)
    assert tree.kind == "module"


def test_pipeline_handles_equ_alias_program(plm_full_pipeline):
    """The EQU-as-LITERALLY-alias bootstrap pattern is the headline
    feature of the two-grammar split. plm_full would reject the source
    on its own; the pipeline parses cleanly."""
    scanner, table = plm_full_pipeline
    src = """
DECLARE
    EQU LITERALLY 'LITERALLY',
    FOREVER EQU 'WHILE TRUE',
    TRUE EQU '1';
"""
    tree = parse_via_pipeline(scanner, table, src)
    assert tree.kind == "module"


def test_pipeline_handles_dollar_pragmas(plm_full_pipeline):
    """`$Q=1` and similar pragmas are not valid PL/M tokens for
    plm_full but the pipeline strips them upstream."""
    scanner, table = plm_full_pipeline
    src = """
$Q=1
$RIGHTMARGIN=80

DECLARE X BYTE;
"""
    tree = parse_via_pipeline(scanner, table, src)
    assert tree.kind == "module"


# --- Real-corpus acceptance test --------------------------------------------
#
# The pipeline is the front-end for `uplm80`'s rewrite onto plox; the
# bar is "parses the .plm files that uplm80's existing test suite
# parses, plus the MP/M-II source archive (`mpm2`)." This test runs
# against the local `mpm2` checkout if present, skipping otherwise.
# Counts are pinned so future grammar regressions surface here rather
# than in `uplm80`'s integration suite.

import glob
from plox.lex.scanner import ScanError
from plox.parse.runtime import ParseError

MPM2_DIR = Path("/home/wohl/src/mpm2")


@pytest.mark.skipif(not MPM2_DIR.exists(), reason="mpm2 checkout not on this machine")
def test_mpm2_corpus_parses_majority(plm_full_pipeline):
    """At least 35/44 .PLM files in the mpm2 archive should parse end-to-end
    through plm_pre + plm_full. The shortfall is fully accounted for:

    * 7 files have non-ASCII source corruption (truncated identifiers
      like `PROCEDUR`, `STRUCTUR`) — old archived bit-rot.
    * 1 file needs `$INCLUDE` expansion (we record the directive but
      don't splice the include).
    * 1 file has a macro/variable name collision the simple-text
      substitution can't disambiguate (`M LITERALLY '20'` then
      `DECLARE (M, ...) BYTE`).

    Failing files outside those buckets is a real regression."""
    scanner, table = plm_full_pipeline
    paths = sorted(glob.glob(str(MPM2_DIR / "**" / "*.plm"), recursive=True))
    paths += sorted(glob.glob(str(MPM2_DIR / "**" / "*.PLM"), recursive=True))
    if not paths:
        pytest.skip("no .plm sources under mpm2/")
    ok = 0
    for path in paths:
        src = open(path, encoding="latin-1").read()
        try:
            tree = parse_via_pipeline(scanner, table, src)
            ok += 1
        except (ScanError, ParseError):
            pass
    # Hard floor: anything below 35 is a regression worth investigating.
    assert ok >= 35, f"only {ok}/{len(paths)} mpm2 files parse"
