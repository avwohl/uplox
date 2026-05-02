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
    """

    name: str
    pattern: Optional[str] = None
    literal: Optional[str] = None
    skip: bool = False
    position: Optional[Position] = None


@dataclass
class Symbol:
    """Right-hand-side symbol — either a terminal or a non-terminal reference."""

    name: str
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
