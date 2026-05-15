"""Tests for the angle-bracket host filter."""

from __future__ import annotations

from uplox.hooks.generic_brackets import DIALECTS, rewrite_generics
from uplox.lex.scanner import Token


def _t(name: str, text: str = "", line: int = 1, col: int = 1) -> Token:
    return Token(name=name, text=text, line=line, column=col, offset=0)


def _names(tokens: list[Token]) -> list[str]:
    return [t.name for t in tokens]


def test_single_generic_typescript():
    """`Array<number>` rewrites the brackets."""
    tokens = [
        _t("IDENT", "Array"),
        _t("LT", "<"),
        _t("KW_NUMBER", "number"),
        _t("GT", ">"),
        _t("EQ", "="),
    ]
    out = rewrite_generics(tokens, dialect="typescript")
    assert _names(out) == ["IDENT", "LT_GENERIC", "KW_NUMBER", "GT_GENERIC", "EQ"]


def test_comparison_left_alone():
    """`a < b && c > d` is two comparisons, not a generic."""
    tokens = [
        _t("LPAREN", "("),
        _t("IDENT", "a"), _t("LT", "<"), _t("IDENT", "b"),
        _t("LOG_AND", "&&"),
        _t("IDENT", "c"), _t("GT", ">"), _t("IDENT", "d"),
        _t("RPAREN", ")"),
    ]
    out = rewrite_generics(tokens, dialect="typescript")
    assert all("GENERIC" not in t.name for t in out)


def test_nested_generic_splits_rshift():
    """`Map<K, Array<V>>` splits the `>>` into two GT_GENERICs."""
    tokens = [
        _t("IDENT", "m"), _t("COLON", ":"),
        _t("IDENT", "Map"),
        _t("LT", "<"),
        _t("KW_STRING", "string"),
        _t("COMMA", ","),
        _t("IDENT", "Array"),
        _t("LT", "<"),
        _t("KW_NUMBER", "number"),
        _t("RSHIFT", ">>"),
        _t("EQ", "="),
    ]
    out = rewrite_generics(tokens, dialect="typescript")
    names = _names(out)
    # The two `<` become LT_GENERIC; the `>>` becomes 2 consecutive
    # GT_GENERICs.
    assert names.count("LT_GENERIC") == 2
    assert names.count("GT_GENERIC") == 2


def test_generic_call_typescript():
    """`foo<number>(42)` — generic-instantiated call."""
    tokens = [
        _t("IDENT", "foo"),
        _t("LT", "<"),
        _t("KW_NUMBER", "number"),
        _t("GT", ">"),
        _t("LPAREN", "("),
        _t("INT_LIT", "42"),
        _t("RPAREN", ")"),
        _t("SEMI", ";"),
    ]
    out = rewrite_generics(tokens, dialect="typescript")
    assert _names(out) == [
        "IDENT", "LT_GENERIC", "KW_NUMBER", "GT_GENERIC",
        "LPAREN", "INT_LIT", "RPAREN", "SEMI",
    ]


def test_unknown_dialect_raises():
    import pytest
    with pytest.raises(KeyError):
        rewrite_generics([], dialect="cobol")


def test_all_dialects_registered():
    """All five expected dialects exist."""
    assert set(DIALECTS) == {"csharp", "java", "kotlin", "swift", "typescript"}


def test_java_urshift_split():
    """`Foo<A, Bar<Baz<C>>>` ends with `>>>` (URSHIFT) — split into 3."""
    tokens = [
        _t("IDENT", "Foo"),
        _t("LT", "<"), _t("IDENT", "A"),
        _t("COMMA", ","),
        _t("IDENT", "Bar"),
        _t("LT", "<"),
        _t("IDENT", "Baz"),
        _t("LT", "<"),
        _t("IDENT", "C"),
        _t("URSHIFT", ">>>"),
        _t("IDENT", "x"),  # IDENT is in Java follow set
        _t("SEMI", ";"),
    ]
    out = rewrite_generics(tokens, dialect="java")
    names = _names(out)
    assert names.count("LT_GENERIC") == 3
    assert names.count("GT_GENERIC") == 3
