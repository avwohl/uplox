"""Regex AST parser tests."""

from __future__ import annotations

import pytest

from plox.lex.regex import (
    Alt,
    CharClass,
    Concat,
    Empty,
    Optional,
    Plus,
    RegexError,
    Star,
    parse,
)


def cc(*chars: str) -> CharClass:
    return CharClass(frozenset(ord(c) for c in chars))


def test_empty():
    assert isinstance(parse(""), Empty)


def test_single_char():
    n = parse("a")
    assert isinstance(n, CharClass)
    assert n.chars == frozenset({ord("a")})


def test_concat():
    n = parse("ab")
    assert isinstance(n, Concat)
    assert len(n.parts) == 2


def test_alternation():
    n = parse("a|b")
    assert isinstance(n, Alt)
    assert len(n.branches) == 2


def test_alternation_lower_precedence_than_concat():
    n = parse("ab|cd")
    assert isinstance(n, Alt)
    # Both branches are Concat of two single chars.
    for branch in n.branches:
        assert isinstance(branch, Concat) and len(branch.parts) == 2


def test_grouping_overrides_precedence():
    n = parse("a(b|c)d")
    assert isinstance(n, Concat) and len(n.parts) == 3
    middle = n.parts[1]
    assert isinstance(middle, Alt)


def test_quantifiers():
    assert isinstance(parse("a*"), Star)
    assert isinstance(parse("a+"), Plus)
    assert isinstance(parse("a?"), Optional)


def test_dot_excludes_newline():
    n = parse(".")
    assert isinstance(n, CharClass)
    assert ord("\n") not in n.chars
    assert ord("a") in n.chars
    assert len(n.chars) == 255


def test_simple_class():
    n = parse("[abc]")
    assert n.chars == frozenset({ord("a"), ord("b"), ord("c")})


def test_class_range():
    n = parse("[a-c]")
    assert n.chars == frozenset({ord("a"), ord("b"), ord("c")})


def test_negated_class():
    n = parse("[^a]")
    assert ord("a") not in n.chars
    assert ord("b") in n.chars
    assert len(n.chars) == 255


def test_class_with_literal_dash_first():
    # First-position '-' is literal.
    n = parse("[-a]")
    assert n.chars == frozenset({ord("-"), ord("a")})


def test_escapes():
    assert parse("\\n").chars == frozenset({ord("\n")})
    assert parse("\\t").chars == frozenset({ord("\t")})
    assert parse("\\\\").chars == frozenset({ord("\\")})
    assert parse("\\x41").chars == frozenset({ord("A")})


def test_identifier_pattern():
    # The IDENT regex from the spec must parse without error and have the
    # right top-level shape: <one alpha> followed by <class>* .
    n = parse("[A-Za-z_][A-Za-z0-9_]*")
    assert isinstance(n, Concat) and len(n.parts) == 2
    assert isinstance(n.parts[0], CharClass)
    assert isinstance(n.parts[1], Star)


def test_unbalanced_paren():
    with pytest.raises(RegexError):
        parse("(a")


def test_unterminated_class():
    with pytest.raises(RegexError):
        parse("[abc")


def test_trailing_backslash():
    with pytest.raises(RegexError):
        parse("\\")


def test_empty_class_negated_full_byte_range():
    # [^] would be empty originally and then negated to all 256; v0 forbids
    # an empty positive class, so this is an error.
    with pytest.raises(RegexError):
        parse("[^]")
