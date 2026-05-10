"""Grammar intermediate representation.

Every reader (DSL, future YAML, etc.) targets these types. Every downstream stage
(lexer build, parser build, JSON serializer) consumes them.

The IR deliberately stores positions and original spelling so diagnostics can point
back into the source file.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass(frozen=True)
class Position:
    file: str
    line: int
    column: int

    def __str__(self) -> str:
        return f"{self.file}:{self.line}:{self.column}"


@dataclass
class TokenDecl:
    """A terminal declared in the ``%tokens`` section.

    ``pattern`` is a regex source string; the lexer pipeline lowers it to NFA/DFA.
    ``literal`` is set when the token was declared by a quoted literal in a rule;
    such tokens are matched as exact strings, not regexes.

    ``balanced_close`` marks the token as a balanced-bracket token: after the
    DFA matches the opening pattern, the scanner extends the match by counting
    nested instances of the open/close pair until depth returns to zero. The
    open delimiter is the first character of the matched text. Used for
    target-language action bodies like ``{ ... { ... } ... }`` whose body is
    not a regular language.
    """

    name: str
    pattern: Optional[str] = None
    literal: Optional[str] = None
    skip: bool = False
    balanced_close: Optional[str] = None
    position: Optional[Position] = None


@dataclass
class Symbol:
    """Right-hand-side symbol.

    ``kind`` distinguishes the three syntactic forms:

    * ``"term"``    — bare identifier; resolves to a declared terminal name
                      or a synthesised keyword token.
    * ``"nonterm"`` — ``<name>``; resolves to a rule LHS.
    * ``"literal"`` — ``'…'``; resolves to whichever declared token has that
                      literal text.

    ``name`` holds the bare identifier (for ``term``/``nonterm``) or the
    literal contents without quotes (for ``literal``).
    """

    name: str
    kind: str = "term"
    position: Optional[Position] = None


@dataclass
class Production:
    rhs: list[Symbol] = field(default_factory=list)
    action: Optional[str] = None
    hook: Optional[str] = None
    position: Optional[Position] = None


@dataclass
class Rule:
    name: str
    productions: list[Production] = field(default_factory=list)
    position: Optional[Position] = None


@dataclass
class HookDecl:
    """Declaration of a hook point name. Resolution to a callable happens in the host driver."""

    name: str
    when: str  # one of: "pre_shift", "pre_reduce", "post_reduce", "on_error"
    position: Optional[Position] = None


@dataclass
class GrammarIR:
    name: str
    start_symbol: Optional[str] = None
    tokens: list[TokenDecl] = field(default_factory=list)
    rules: list[Rule] = field(default_factory=list)
    hooks: list[HookDecl] = field(default_factory=list)
    options: dict[str, str] = field(default_factory=dict)
    source_file: Optional[str] = None
    keyword_prefix: str = ""
    # Bare-name -> synthesised token name. ``%keywords`` lists populate this so
    # bare keyword references on rule RHS resolve back to the prefixed token.
    keyword_aliases: dict[str, str] = field(default_factory=dict)
    # Terminals listed under ``%shift``. Shift/reduce conflicts on any of these
    # are silently resolved in favour of shift at LR-table-build time. Use
    # sparingly — yacc-style shift-prefer is the standard fix for dangling-else
    # and similar cases where the ε-reduction is genuinely never wanted on
    # that lookahead.
    shift_terminals: set[str] = field(default_factory=set)
