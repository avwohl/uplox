"""End-to-end scanner tests: regex source -> NFA -> DFA -> token stream."""

from __future__ import annotations

import pytest

from uplox.lex.dfa import from_nfa, minimise
from uplox.lex.nfa import build, combine
from uplox.lex.regex import parse as re_parse
from uplox.lex.scanner import ScanError, Scanner


def make_scanner(specs: list[tuple[str, str]], skip: set[str] | None = None, minimised: bool = True) -> Scanner:
    nfa = combine([(name, build(re_parse(pat))) for name, pat in specs])
    dfa = from_nfa(nfa)
    if minimised:
        dfa = minimise(dfa)
    return Scanner(dfa=dfa, skip_tokens=frozenset(skip or set()))


def test_calc_grammar_tokenises():
    sc = make_scanner(
        [
            ("NUMBER", "[0-9]+"),
            ("PLUS", "\\+"),
            ("MINUS", "-"),
            ("STAR", "\\*"),
            ("SLASH", "/"),
            ("LPAREN", "\\("),
            ("RPAREN", "\\)"),
            ("WS", "[ \\t\\n]+"),
        ],
        skip={"WS"},
    )
    toks = sc.scan_all("12 + 34 * (5 - 6)")
    names = [t.name for t in toks]
    texts = [t.text for t in toks]
    assert names == [
        "NUMBER", "PLUS", "NUMBER", "STAR", "LPAREN", "NUMBER", "MINUS", "NUMBER", "RPAREN",
    ]
    assert texts == ["12", "+", "34", "*", "(", "5", "-", "6", ")"]


def test_keyword_beats_identifier():
    sc = make_scanner([
        ("IF", "if"),
        ("IDENT", "[a-z]+"),
        ("WS", " +"),
    ], skip={"WS"})
    toks = sc.scan_all("if when ifx")
    assert [t.name for t in toks] == ["IF", "IDENT", "IDENT"]
    assert [t.text for t in toks] == ["if", "when", "ifx"]


def test_longest_match_wins():
    # Both INT and INTLIT could accept the input; the longer one wins.
    sc = make_scanner([
        ("SHORT", "ab"),
        ("LONG", "abc"),
    ])
    toks = sc.scan_all("abc")
    assert toks[0].name == "LONG"


def test_priority_breaks_equal_length_tie():
    # Two tokens both match exactly "x". The earlier-declared one wins.
    sc = make_scanner([
        ("FIRST", "x"),
        ("SECOND", "x"),
    ])
    toks = sc.scan_all("x")
    assert toks[0].name == "FIRST"


def test_line_and_column_tracking():
    sc = make_scanner([
        ("ID", "[a-z]+"),
        ("NL", "\\n"),
    ])
    toks = sc.scan_all("foo\nbar")
    assert toks[0].name == "ID" and toks[0].line == 1 and toks[0].column == 1
    assert toks[1].name == "NL" and toks[1].line == 1 and toks[1].column == 4
    assert toks[2].name == "ID" and toks[2].line == 2 and toks[2].column == 1


def test_skip_drops_tokens_but_advances_position():
    sc = make_scanner([
        ("ID", "[a-z]+"),
        ("WS", "[ \\t]+"),
    ], skip={"WS"})
    toks = sc.scan_all("a   b")
    assert [t.name for t in toks] == ["ID", "ID"]
    assert toks[1].column == 5


def test_lex_error_reports_position():
    sc = make_scanner([("ID", "[a-z]+")])
    with pytest.raises(ScanError) as exc:
        list(sc.scan("abc?"))
    assert exc.value.line == 1
    assert exc.value.column == 4
    assert exc.value.byte == ord("?")


def test_scanner_round_trips_minimised_and_unminimised():
    specs = [
        ("IF", "if"),
        ("IDENT", "[a-z]+"),
        ("WS", " +"),
    ]
    skip = {"WS"}
    a = make_scanner(specs, skip=skip, minimised=False).scan_all("if when ifx")
    b = make_scanner(specs, skip=skip, minimised=True).scan_all("if when ifx")
    assert [(t.name, t.text) for t in a] == [(t.name, t.text) for t in b]


def test_utf8_input_passes_through_byte_classes():
    # uplox lexer is byte-oriented, so we can match any byte sequence as raw bytes.
    sc = make_scanner([
        ("LATIN", "[a-z]+"),
        ("OTHER", "[\\x80-\\xff]+"),
        ("WS", " "),
    ], skip={"WS"})
    # "café " — c, a, f are ASCII, then é = 0xc3 0xa9 (2 bytes).
    toks = sc.scan_all("caf\xc3\xa9")
    assert [t.name for t in toks] == ["LATIN", "OTHER"]
    assert toks[0].text == "caf"


# ---- FileTable + file_id --------------------------------------------------


def test_filetable_intern_returns_same_id():
    from uplox.lex.scanner import FileTable
    ft = FileTable()
    a = ft.intern("a.c")
    b = ft.intern("b.c")
    assert ft.intern("a.c") == a
    assert b != a


def test_filetable_zero_is_empty_string():
    from uplox.lex.scanner import FileTable
    ft = FileTable()
    assert ft.name(0) == ""
    # An intern of "" also returns 0 (no new slot).
    assert ft.intern("") == 0


def test_filetable_name_for_unknown_id():
    from uplox.lex.scanner import FileTable
    ft = FileTable()
    assert ft.name(999) == ""
    assert ft.name(-1) == ""


def test_scan_stamps_file_id_on_tokens():
    sc = make_scanner([("WORD", "[a-z]+"), ("WS", " ")], skip={"WS"})
    toks = sc.scan_all("hello world", file_id=42)
    assert all(t.file_id == 42 for t in toks)


def test_scan_default_file_id_is_zero():
    sc = make_scanner([("WORD", "[a-z]+"), ("WS", " ")], skip={"WS"})
    toks = sc.scan_all("hello")
    assert toks[0].file_id == 0


def test_token_default_file_id_is_zero():
    """Constructing Token without file_id keeps back-compat."""
    from uplox.lex.scanner import Token
    t = Token(name="X", text="x", line=1, column=1, offset=0)
    assert t.file_id == 0


def test_filetable_filenames_snapshot():
    from uplox.lex.scanner import FileTable
    ft = FileTable()
    ft.intern("a.c")
    ft.intern("b.c")
    snap = ft.filenames()
    assert snap == ["", "a.c", "b.c"]
    # Snapshot is a copy — mutating it doesn't affect the table.
    snap.append("c.c")
    assert len(ft) == 3
