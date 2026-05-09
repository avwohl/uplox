"""%keyword_prefix and %keywords tests."""

from __future__ import annotations

import pytest

from uplox.parse.grammar import compile_grammar
from uplox.spec.reader import ReaderError, read_source


def test_default_prefix_is_empty():
    src = """%grammar g
%keywords
IF WHILE
%tokens
IDENT = /[A-Za-z]+/
%rules
<stmt> : IF IDENT | WHILE IDENT ;
"""
    ir = read_source(src)
    assert ir.keyword_prefix == ""
    assert ir.keyword_aliases == {"IF": "IF", "WHILE": "WHILE"}
    names = [t.name for t in ir.tokens]
    assert names == ["IF", "WHILE", "IDENT"]
    assert all(t.literal == t.name for t in ir.tokens if t.name in ("IF", "WHILE"))


def test_kw_prefix_synthesises_token_names():
    src = """%grammar g
%keyword_prefix KW_
%keywords
if while
%tokens
IDENT = /[A-Za-z]+/
%rules
<stmt> : if IDENT | while IDENT ;
"""
    ir = read_source(src)
    assert ir.keyword_prefix == "KW_"
    assert ir.keyword_aliases == {"if": "KW_if", "while": "KW_while"}
    g = compile_grammar(ir)
    # The bare keywords resolved to the prefixed token names.
    rhs_kinds = [tuple(p.rhs) for p in g.productions[1:]]
    assert ("KW_if", "IDENT") in rhs_kinds
    assert ("KW_while", "IDENT") in rhs_kinds


def test_keyword_listed_before_prefix_uses_empty_prefix():
    """%keyword_prefix must come before %keywords; an absent prefix is empty."""
    src = """%grammar g
%keywords
HALT
%tokens
IDENT = /[A-Za-z]+/
%rules
<stmt> : HALT ;
"""
    ir = read_source(src)
    assert ir.keyword_aliases == {"HALT": "HALT"}


def test_duplicate_keyword_prefix_directive_rejected():
    src = """%grammar g
%keyword_prefix KW_
%keyword_prefix OTHER_
"""
    with pytest.raises(ReaderError, match="duplicate `%keyword_prefix"):
        read_source(src)


def test_duplicate_keyword_in_list_rejected():
    src = """%grammar g
%keywords
HALT HALT
"""
    with pytest.raises(ReaderError, match="already listed"):
        read_source(src)


def test_keyword_collides_with_existing_token_name():
    """If `%keyword_prefix KW_` synthesises `KW_IF` but `%tokens` already
    declared `KW_IF`, the reader rejects rather than silently overriding."""
    src = """%grammar g
%keyword_prefix KW_
%tokens
KW_IF = 'if'
%keywords
IF
"""
    with pytest.raises(ReaderError, match="collides"):
        read_source(src)


def test_invalid_keyword_identifier_rejected():
    src = """%grammar g
%keywords
HALT 1BAD
"""
    with pytest.raises(ReaderError, match="not a valid identifier"):
        read_source(src)


def test_keyword_resolved_via_prefixed_name_too():
    """Either bare or prefixed spelling on RHS resolves to the same token."""
    src = """%grammar g
%keyword_prefix KW_
%keywords
HALT
%tokens
IDENT = /[A-Za-z]+/
%rules
<stmt> : HALT
       | KW_HALT
       ;
"""
    g = compile_grammar(read_source(src))
    user_prods = g.productions[1:]
    assert all(p.rhs == ("KW_HALT",) for p in user_prods)


def test_keyword_section_can_span_multiple_lines():
    src = """%grammar g
%keyword_prefix KW_
%keywords
   if while
   then else
%tokens
IDENT = /[A-Za-z]+/
%rules
<stmt> : if IDENT then IDENT else IDENT
       | while IDENT
       ;
"""
    ir = read_source(src)
    assert set(ir.keyword_aliases) == {"if", "while", "then", "else"}
