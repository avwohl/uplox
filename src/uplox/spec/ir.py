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

    ``field_name`` is set when the symbol carries an ``@field`` annotation
    in the rule source (the v3 AST surface). The reader stores the bare
    annotation; the AST plan compiler validates that the same field name
    isn't reused inside one production and that ``%ast_drop`` tokens
    aren't decorated. ``None`` means the position is unnamed (positional
    or destined to be dropped).
    """

    name: str
    kind: str = "term"
    position: Optional[Position] = None
    field_name: Optional[str] = None


@dataclass
class Production:
    """One alternative in a rule.

    ``ast_kind`` is set when the production carries an ``%ast=Name``
    annotation. Special values: ``"_unwrap"`` (reserved kind — the rule
    contributes its single ``@field``-annotated child to the parent's
    slot rather than producing a node). All other values are user-supplied
    node-kind names. ``None`` means no AST annotation on this alternative
    — the alt is then either eligible for the ``?``-lift on its rule (if
    set) or excluded from AST construction.
    """

    rhs: list[Symbol] = field(default_factory=list)
    action: Optional[str] = None
    hook: Optional[str] = None
    ast_kind: Optional[str] = None
    position: Optional[Position] = None


@dataclass
class Rule:
    """A grammar rule (one LHS, one or more alternatives).

    ``ast_lift`` mirrors a ``?`` after the LHS in the rule source. Any
    alternative without an ``%ast=`` of its own and with exactly one
    surviving child after ``%ast_drop`` filtering passes that child up
    instead of wrapping. Alternatives with an explicit ``%ast=`` win
    and the lift is silently no-op for those.

    ``ast_list_element`` mirrors a rule-level ``%ast=list element=<X>``
    annotation. When set, the generator treats this rule as a
    list-shaped accumulator producing ``list[<X>]``; ``@field``
    references to this rule's LHS from any parent become list fields.
    """

    name: str
    productions: list[Production] = field(default_factory=list)
    position: Optional[Position] = None
    ast_lift: bool = False
    ast_list_element: Optional[str] = None


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
    # Terminals listed under ``%reduce``. Shift/reduce conflicts on any of these
    # are silently resolved in favour of reduce — the dual of %shift. Use when
    # a longer reduction is genuinely the right answer and the competing shift
    # would over-extend (e.g. closing a non-terminal whose followset includes
    # the conflicting terminal, where the shift target is reachable only by
    # an LALR state-merge artifact).
    reduce_terminals: set[str] = field(default_factory=set)
    # LR construction algorithm — set by ``%define lr.type {canonical-lr|lalr}``.
    # ``canonical-lr`` (default) keeps states with different lookaheads separate;
    # ``lalr`` merges states with the same LR(0) core, producing ~10x smaller
    # tables at the cost of potentially-spurious reduce/reduce conflicts.
    lr_type: str = "canonical-lr"
    # Terminals listed under ``%ast_drop``: stripped from every AST node's
    # child list at build time. Populated by the v3 AST surface; empty for
    # grammars without any AST annotation. The set is consulted by the AST
    # plan compiler — the LR / lexer pipeline ignores it.
    ast_drop_tokens: set[str] = field(default_factory=set)
