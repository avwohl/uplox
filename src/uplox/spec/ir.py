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

    ``predicate`` is the name of a ``%predicates`` entry (set when the
    production carries a ``?{name}`` annotation). At parser-build time,
    productions sharing the same RHS shape but differing in their
    predicate are allowed to share an LR action cell; the runtime
    consults the predicates in declaration order. ``None`` means the
    production is the unconditional default for its (state, lookahead).

    ``post_action`` is the name of an ``%actions`` entry (set when the
    production carries a ``!{name}`` annotation). The runtime fires it
    after the reduction; semantically equivalent to a ``post_reduce``
    hook but lifted to its own first-class concept so backend codegen
    can route actions and hooks separately.
    """

    rhs: list[Symbol] = field(default_factory=list)
    action: Optional[str] = None
    hook: Optional[str] = None
    ast_kind: Optional[str] = None
    position: Optional[Position] = None
    predicate: Optional[str] = None
    post_action: Optional[str] = None
    post_action_arg_pos: Optional[int] = None
    """1-indexed RHS position to pass to ``post_action`` instead of the
    full reduced subtree. Set by the ``!{name@N}`` grammar syntax. ``None``
    means the action receives the whole ParseNode (legacy behaviour)."""


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
class ClassifierDecl:
    """Declares a token whose final terminal name is decided by a host
    callback at scan / lookahead-fetch time.

    The classifier receives the matched ``(text, ctx)`` and returns one of
    ``alt_names`` — the alternative terminal names the grammar declares
    upfront. ``source_name`` is the token the DFA actually matched
    (e.g. ``IDENT``); the alternatives are other terminals the host may
    redirect to (``TYPEDEF_NAME``, ``TEMPLATE_NAME``, …). All
    alternatives must be declared as ordinary terminals via ``%tokens``;
    the classifier section only wires the lookup.

    The grammar-side declaration exists so:

    1. The set of classifiable tokens is known statically (backend codegen
       emits a single dispatch point).
    2. The alternative names are validated against the terminal set at
       grammar-build time, not at runtime.
    3. Bundles round-trip the wiring without a host-side registry.

    Host wires actual callables via :class:`ClassifierRegistry` at parse
    setup. Names referenced in the grammar without a registered callback
    are fatal at parse time (the registry has an ``ignore_missing`` flag
    for tooling that wants to parse without classification).
    """

    source_name: str
    alt_names: list[str] = field(default_factory=list)
    position: Optional[Position] = None


@dataclass
class LayoutConfig:
    """``%layout`` directive — runtime INDENT/DEDENT/NEWLINE emission.

    Names the three synthetic terminals the layout filter will emit, plus
    the flow-bracket tokens that suspend indentation tracking. See
    ``docs/proposals/layout.md`` for the algorithm.
    """

    indent_token: str
    dedent_token: str
    newline_token: str
    flow_open: list[str] = field(default_factory=list)
    flow_close: list[str] = field(default_factory=list)
    tab_width: int = 8
    blank_lines: str = "skip"        # "skip" | "emit_newline"
    comment_lines: str = "skip"      # "skip" | "emit_newline"
    position: Optional[Position] = None


@dataclass
class ColumnClause:
    """One ``cols <range>`` clause within a ``%columns`` block."""

    col_start: int                    # 1-indexed, inclusive
    col_end: int                      # 1-indexed, inclusive (col_start for single column)
    mode: str = "body"                # body | label | areaA | areaB | skip
    comment_if: Optional[str] = None  # characters that trigger comment-mode
    continuation_if: Optional[str] = None        # exact characters
    continuation_if_nonblank: bool = False       # any non-space, non-zero
    continuation_token: Optional[str] = None     # token name to emit on match
    debug_if: Optional[str] = None
    debug_token: Optional[str] = None
    position: Optional[Position] = None


@dataclass
class ColumnsConfig:
    """``%columns`` directive — column-range dispatch for fixed-format
    grammars (F77, COBOL). See ``docs/proposals/columns.md``.
    """

    width: int = 80
    clauses: list[ColumnClause] = field(default_factory=list)
    position: Optional[Position] = None


@dataclass
class ContinuationConfig:
    """``%continuation`` directive — line-continuation marker handling.
    See ``docs/proposals/continuation.md``.
    """

    # Exactly one of ``marker_char`` or ``marker_token`` is set.
    marker_char: Optional[str] = None
    marker_token: Optional[str] = None
    at_column: Optional[int] = None
    applies_in_brackets: bool = False
    preserve_position: bool = True
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
    # Lexer-feedback configs for context-sensitive tokenisation. All three
    # are optional and default to None. See ``docs/proposals/{layout,
    # columns,continuation}.md`` for the directive specs.
    layout: Optional[LayoutConfig] = None
    columns: Optional[ColumnsConfig] = None
    continuation: Optional[ContinuationConfig] = None
    # Per-token classifier declarations. Each entry binds one source terminal
    # (the one the DFA actually matches) to a fixed set of alternative
    # terminal names the host classifier may redirect to. Empty by default;
    # grammars opt in with ``%classifier`` sections.
    classifiers: list[ClassifierDecl] = field(default_factory=list)
    # Per-LR-state predicates. Each predicate is a host-supplied callable
    # consulted at runtime when the LR table has multiple actions gated on
    # different predicates. Empty by default; grammars opt in by attaching
    # ``?{name}`` annotations on productions.
    predicates: list[str] = field(default_factory=list)
    # Per-production action declarations. Each entry names a host-side
    # callable invoked after a successful reduction with the reduced
    # subtree + context. Identical mechanism to ``%hook=post_reduce``
    # but the spec lifts it to a first-class concept. Empty by default;
    # grammars opt in via ``!{name}`` annotations.
    actions: list[str] = field(default_factory=list)
    # Lexer-mode declarations (Phase 4). When non-empty, the lexer
    # maintains a mode stack starting with ``modes[0]``; mode-switch
    # actions push/pop the stack. ``modes[0]`` is the default; bundle
    # serialisation emits one DFA per mode.
    modes: list[str] = field(default_factory=list)
    # Named LR-state sets (Phase-1 extension). Each entry binds a
    # name to a list of non-terminal LHSs; at build time the LR
    # builder computes which LR states have an in-progress item for
    # any of those non-terminals, and the runtime exposes
    # ``ctx.in_state_set(name)`` so host classifiers/predicates can
    # answer parse-state questions like "am I currently inside a
    # declarator?" without inspecting raw state numbers.
    state_sets: list["StateSetDecl"] = field(default_factory=list)


@dataclass
class StateSetDecl:
    """A ``%state_set`` directive: a named group of non-terminal LHSs.

    A state is "in" the set if any of its LR(0) items has an in-
    progress (dot not at end) production whose LHS is listed here.
    Computed at build time, queried at runtime via
    :meth:`ParseContext.in_state_set`.
    """

    name: str
    lhss: list[str]
    position: Optional[Position] = None
