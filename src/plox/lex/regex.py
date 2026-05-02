"""Regex AST and parser for the v0 syntax described in ``docs/grammar_format.md``.

This module produces an immutable AST. Lowering to NFA is :mod:`plox.lex.nfa`;
keeping AST and NFA separate so the parser can be unit-tested without dragging
the state-machine machinery in.

Supported in v0
---------------

* Literal characters (with ``\\n \\t \\r \\\\ \\/`` and ``\\xHH`` escapes).
* ``.`` — any byte except ``\\n``.
* Character classes ``[abc]``, ``[a-z]``, ``[^abc]``.
* Closures: ``r*`` ``r+`` ``r?``.
* Alternation ``a|b`` (lower precedence than concatenation).
* Grouping ``(r)``.

Out of scope: backreferences, lookaround, named groups, ``\\d \\w \\s``,
Unicode property classes. Operates on bytes — the regex never asserts code-point
boundaries, so UTF-8 input works at the lexer level by accident-of-design.
"""

from __future__ import annotations

from dataclasses import dataclass


class RegexError(ValueError):
    """Raised on syntax errors in regex source. Message includes 1-based offset."""


# ---- AST ---------------------------------------------------------------------


@dataclass(frozen=True)
class Node:
    """Base class for regex AST nodes. Subclasses are dataclasses."""


@dataclass(frozen=True)
class Empty(Node):
    """The empty regex — matches the empty string. Used as the identity of concatenation."""


@dataclass(frozen=True)
class CharClass(Node):
    """A set of single-byte characters.

    Stored as a frozenset of ``int`` (byte values 0..255). ``.`` lowers to a
    CharClass excluding ``\\n``. Negated classes are expanded to their positive
    set at parse time so downstream code never has to special-case negation.
    """
    chars: frozenset


@dataclass(frozen=True)
class Concat(Node):
    parts: tuple  # tuple[Node, ...]


@dataclass(frozen=True)
class Alt(Node):
    branches: tuple  # tuple[Node, ...]


@dataclass(frozen=True)
class Star(Node):
    inner: Node


@dataclass(frozen=True)
class Plus(Node):
    inner: Node


@dataclass(frozen=True)
class Optional(Node):
    inner: Node


# ---- Parser ------------------------------------------------------------------

_DOT_EXCLUDED = frozenset({ord("\n")})


def parse(source: str) -> Node:
    """Parse a regex source string into a :class:`Node` tree.

    Raises :class:`RegexError` on syntax errors. Returns :class:`Empty` for the
    empty string.
    """
    p = _Parser(source)
    node = p.parse_alt()
    if p.pos != len(p.src):
        raise RegexError(f"unexpected character {p.peek()!r} at offset {p.pos + 1}")
    return node


class _Parser:
    __slots__ = ("src", "pos")

    def __init__(self, src: str):
        self.src = src
        self.pos = 0

    def peek(self) -> str:
        return self.src[self.pos] if self.pos < len(self.src) else ""

    def advance(self) -> str:
        c = self.src[self.pos]
        self.pos += 1
        return c

    def parse_alt(self) -> Node:
        first = self.parse_concat()
        if self.peek() != "|":
            return first
        branches = [first]
        while self.peek() == "|":
            self.advance()
            branches.append(self.parse_concat())
        return Alt(tuple(branches))

    def parse_concat(self) -> Node:
        parts: list[Node] = []
        while self.peek() and self.peek() not in "|)":
            parts.append(self.parse_quant())
        if not parts:
            return Empty()
        if len(parts) == 1:
            return parts[0]
        return Concat(tuple(parts))

    def parse_quant(self) -> Node:
        atom = self.parse_atom()
        c = self.peek()
        if c == "*":
            self.advance()
            return Star(atom)
        if c == "+":
            self.advance()
            return Plus(atom)
        if c == "?":
            self.advance()
            return Optional(atom)
        return atom

    def parse_atom(self) -> Node:
        c = self.peek()
        if not c:
            raise RegexError(f"unexpected end of pattern at offset {self.pos + 1}")
        if c == "(":
            self.advance()
            inner = self.parse_alt()
            if self.peek() != ")":
                raise RegexError(f"missing ')' at offset {self.pos + 1}")
            self.advance()
            return inner
        if c == "[":
            return self.parse_class()
        if c == ".":
            self.advance()
            return CharClass(frozenset(range(256)) - _DOT_EXCLUDED)
        if c == "\\":
            return CharClass(frozenset({self.parse_escape()}))
        if c in "*+?|)":
            raise RegexError(f"unexpected metachar {c!r} at offset {self.pos + 1}")
        self.advance()
        return CharClass(frozenset({ord(c)}))

    def parse_escape(self) -> int:
        # We are positioned on the backslash.
        assert self.advance() == "\\"
        if self.pos >= len(self.src):
            raise RegexError(f"trailing backslash at offset {self.pos + 1}")
        c = self.advance()
        simple = {"n": ord("\n"), "t": ord("\t"), "r": ord("\r"),
                  "\\": ord("\\"), "/": ord("/"), "[": ord("["), "]": ord("]"),
                  "(": ord("("), ")": ord(")"), "|": ord("|"),
                  "*": ord("*"), "+": ord("+"), "?": ord("?"),
                  ".": ord("."), "-": ord("-"), "^": ord("^"),
                  '"': ord('"'), "'": ord("'"), "0": 0}
        if c in simple:
            return simple[c]
        if c == "x":
            if self.pos + 2 > len(self.src):
                raise RegexError(f"truncated \\x escape at offset {self.pos + 1}")
            hex2 = self.src[self.pos:self.pos + 2]
            self.pos += 2
            try:
                return int(hex2, 16)
            except ValueError as e:
                raise RegexError(f"bad \\x escape {hex2!r} at offset {self.pos - 1}") from e
        raise RegexError(f"unknown escape \\{c} at offset {self.pos}")

    def parse_class(self) -> Node:
        assert self.advance() == "["
        negate = False
        if self.peek() == "^":
            negate = True
            self.advance()
        members: set[int] = set()
        # First char in a class is taken literally even if it's ']'.
        first = True
        while True:
            c = self.peek()
            if not c:
                raise RegexError(f"unterminated character class at offset {self.pos + 1}")
            if c == "]" and not first:
                self.advance()
                break
            first = False
            lo = self.parse_class_char()
            if self.peek() == "-" and self.pos + 1 < len(self.src) and self.src[self.pos + 1] != "]":
                self.advance()  # '-'
                hi = self.parse_class_char()
                if hi < lo:
                    raise RegexError(f"reverse range {lo}-{hi} at offset {self.pos}")
                members.update(range(lo, hi + 1))
            else:
                members.add(lo)
        if negate:
            members = set(range(256)) - members
        if not members:
            raise RegexError(f"empty character class at offset {self.pos}")
        return CharClass(frozenset(members))

    def parse_class_char(self) -> int:
        c = self.peek()
        if c == "\\":
            return self.parse_escape()
        self.advance()
        return ord(c)
