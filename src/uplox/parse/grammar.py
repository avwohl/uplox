"""Compiled grammar form: indexed productions, terminal/non-terminal classification,
FIRST/FOLLOW sets.

The :class:`Grammar` produced here is what the LR(1) builder consumes. It owns:

* a flat ordered list of productions (with augmented start at index 0),
* the disjoint sets of terminals and non-terminals,
* the FIRST and FOLLOW sets, computed once at construction.

Resolving symbols
-----------------

The grammar IR comes from :mod:`uplox.spec` with two flavours of right-hand-side
symbol: identifiers (UPPER tokens, lower non-terminals) and string literals
(``"+"``). String literals must match a previously declared token's literal
field; mismatches are reported here, not silently treated as new terminals.

End-of-input
------------

We add a synthetic ``$`` terminal as the end-of-input marker. The augmented
start production is ``S' -> S $`` where ``S`` is the user's start symbol.
Reduction by production 0 with lookahead ``$`` is the LR accept action.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..spec.ir import GrammarIR, Symbol

# Reserved symbol names. These cannot appear in user grammars.
EPSILON = ""        # empty string token; used in FIRST sets
END_MARKER = "$"    # synthetic end-of-input
AUGMENTED_START = "$start"


@dataclass(frozen=True)
class CompiledProduction:
    """One production in the compiled grammar.

    ``index`` is the global production number; ``lhs``/``rhs`` use compiled
    symbol names (resolved from string literals to their token name).
    """
    index: int
    lhs: str
    rhs: tuple[str, ...]
    action: str | None = None
    hook: str | None = None
    user_index: int = -1
    """Index into the user's original Production list, or -1 for the augmented start."""


@dataclass
class Grammar:
    productions: list[CompiledProduction] = field(default_factory=list)
    terminals: set[str] = field(default_factory=set)
    non_terminals: set[str] = field(default_factory=set)
    start_symbol: str = ""
    augmented_start: str = AUGMENTED_START
    first_sets: dict[str, set[str]] = field(default_factory=dict)
    follow_sets: dict[str, set[str]] = field(default_factory=dict)
    productions_by_lhs: dict[str, list[int]] = field(default_factory=dict)
    first_tail: dict[tuple[int, int], frozenset[str]] = field(default_factory=dict)
    """Cache of FIRST(rhs[pos:]) per (production_index, pos). Populated by
    :func:`compile_grammar` once the grammar's FIRST map is built. Pulls
    the per-iteration ``first_of_sequence`` cost out of the hot LR(1)
    closure loop — see :mod:`uplox.parse.lr1`._closure."""
    shift_terminals: set[str] = field(default_factory=set)
    """Terminals on which a shift/reduce conflict should be silently
    resolved in favour of shift (yacc-style shift preference). Populated
    from the IR's ``%shift`` section."""
    reduce_terminals: set[str] = field(default_factory=set)
    """Terminals on which a shift/reduce conflict should be silently
    resolved in favour of reduce (the dual of ``shift_terminals``).
    Populated from the IR's ``%reduce`` section."""

    def is_terminal(self, sym: str) -> bool:
        return sym in self.terminals

    def is_non_terminal(self, sym: str) -> bool:
        return sym in self.non_terminals


class GrammarError(ValueError):
    pass


def compile_grammar(ir: GrammarIR) -> Grammar:
    """Build a :class:`Grammar` from a parsed :class:`GrammarIR`.

    Raises :class:`GrammarError` when:

    * the IR has no rules,
    * a string literal in an RHS does not correspond to any declared token,
    * a referenced non-terminal has no rule defining it,
    * the start symbol does not name a rule.
    """
    if not ir.rules:
        raise GrammarError(f"grammar {ir.name!r} has no rules")
    if ir.start_symbol is None:
        raise GrammarError(f"grammar {ir.name!r} has no start symbol")

    # Map string literals to their declared token names.
    literal_to_token: dict[str, str] = {}
    terminal_names: set[str] = {END_MARKER}
    for tok in ir.tokens:
        terminal_names.add(tok.name)
        if tok.literal is not None:
            literal_to_token[tok.literal] = tok.name

    rule_names = {r.name for r in ir.rules}
    if ir.start_symbol not in rule_names:
        raise GrammarError(
            f"start symbol {ir.start_symbol!r} is not the LHS of any rule"
        )

    keyword_aliases = ir.keyword_aliases

    def resolve(sym: Symbol) -> str:
        if sym.kind == "literal":
            tok = literal_to_token.get(sym.name)
            if tok is None:
                raise GrammarError(
                    f"string literal {sym.name!r} at {sym.position} is not "
                    f"declared as any token's literal"
                )
            return tok
        if sym.kind == "nonterm":
            if sym.name not in rule_names:
                raise GrammarError(
                    f"non-terminal <{sym.name}> at {sym.position} has no rule defining it"
                )
            return sym.name
        # kind == "term"
        if sym.name in terminal_names:
            return sym.name
        alias = keyword_aliases.get(sym.name)
        if alias is not None:
            return alias
        raise GrammarError(
            f"terminal {sym.name!r} at {sym.position} is not a declared token or keyword"
        )

    grammar = Grammar(
        start_symbol=ir.start_symbol,
        terminals=set(terminal_names),
    )
    grammar.non_terminals = set(rule_names) | {AUGMENTED_START}

    # Validate %shift terminals against the resolved terminal set. We check
    # the post-resolution names so users can list either a raw token name
    # (``IF``) or a keyword-prefix-synthesised name (``KW_if``).
    for sym in ir.shift_terminals:
        resolved = sym
        if sym not in terminal_names:
            alias = ir.keyword_aliases.get(sym)
            if alias is not None and alias in terminal_names:
                resolved = alias
            else:
                raise GrammarError(
                    f"%shift entry {sym!r} is not a declared terminal"
                )
        grammar.shift_terminals.add(resolved)

    # Validate %reduce terminals the same way (and reject any that also appear
    # in %shift, in case the keyword-alias resolution introduced an overlap).
    for sym in ir.reduce_terminals:
        resolved = sym
        if sym not in terminal_names:
            alias = ir.keyword_aliases.get(sym)
            if alias is not None and alias in terminal_names:
                resolved = alias
            else:
                raise GrammarError(
                    f"%reduce entry {sym!r} is not a declared terminal"
                )
        if resolved in grammar.shift_terminals:
            raise GrammarError(
                f"terminal {resolved!r} listed in both %shift and %reduce"
            )
        grammar.reduce_terminals.add(resolved)

    # Production 0 is always the augmented start: $start -> S
    grammar.productions.append(
        CompiledProduction(
            index=0,
            lhs=AUGMENTED_START,
            rhs=(ir.start_symbol,),
        )
    )
    user_idx = 0
    for rule in ir.rules:
        for prod in rule.productions:
            rhs_resolved = tuple(resolve(s) for s in prod.rhs)
            grammar.productions.append(
                CompiledProduction(
                    index=len(grammar.productions),
                    lhs=rule.name,
                    rhs=rhs_resolved,
                    action=prod.action,
                    hook=prod.hook,
                    user_index=user_idx,
                )
            )
            user_idx += 1

    for p in grammar.productions:
        grammar.productions_by_lhs.setdefault(p.lhs, []).append(p.index)

    grammar.first_sets = _compute_first(grammar)
    grammar.follow_sets = _compute_follow(grammar)
    _populate_first_tail(grammar)
    return grammar


def _populate_first_tail(g: Grammar) -> None:
    """Pre-compute FIRST(rhs[pos:]) for every (production, pos) pair.

    The canonical LR(1) closure repeatedly asks for FIRST(β · la) where β
    is a tail of some RHS. With this cache, the closure looks up FIRST(β)
    in O(1) and only adds the la dependency on the EPSILON test, instead
    of re-iterating β's symbols each call. For c_subset (1.6k states) this
    cuts canonical-LR(1) build time roughly in half.
    """
    for prod in g.productions:
        n = len(prod.rhs)
        for pos in range(n + 1):
            g.first_tail[(prod.index, pos)] = frozenset(
                first_of_sequence(prod.rhs[pos:], g.first_sets)
            )


# ---- FIRST -------------------------------------------------------------------


def _compute_first(g: Grammar) -> dict[str, set[str]]:
    """Closed-form FIRST: smallest map such that

    * for every terminal t, FIRST(t) = {t};
    * for every production A -> X1...Xn, FIRST(A) ⊇ FIRST(X1...Xn) where
      FIRST of a sequence is the union of FIRST(Xi) (minus epsilon) for the
      longest prefix that derives epsilon, plus epsilon if the whole prefix does.

    Computed by fixed-point iteration. Empty productions add EPSILON to the LHS.
    """
    first: dict[str, set[str]] = {t: {t} for t in g.terminals}
    for nt in g.non_terminals:
        first[nt] = set()

    changed = True
    while changed:
        changed = False
        for prod in g.productions:
            before = len(first[prod.lhs])
            first[prod.lhs] |= first_of_sequence(prod.rhs, first)
            if len(first[prod.lhs]) != before:
                changed = True
    return first


def first_of_sequence(seq: tuple[str, ...], first: dict[str, set[str]]) -> set[str]:
    """FIRST of a symbol sequence given a FIRST map for individual symbols.

    Iterates through ``seq`` accumulating non-epsilon members of FIRST(Xi)
    for as long as the prefix can derive epsilon; adds EPSILON if every
    symbol in the sequence does. ``first`` must contain entries for every
    symbol that appears.
    """
    out: set[str] = set()
    if not seq:
        out.add(EPSILON)
        return out
    for sym in seq:
        sym_first = first[sym]
        out |= sym_first - {EPSILON}
        if EPSILON not in sym_first:
            return out
    out.add(EPSILON)
    return out


# ---- FOLLOW ------------------------------------------------------------------


def _compute_follow(g: Grammar) -> dict[str, set[str]]:
    """Standard FOLLOW computation:

    * FOLLOW(start_augmented) ⊇ {$}
    * For A -> α B β: FOLLOW(B) ⊇ FIRST(β) − {ε}
    * If β derives ε (or is empty): FOLLOW(B) ⊇ FOLLOW(A)

    Useful for SLR/LALR diagnostics; the LR(1) builder computes its own
    lookaheads from items rather than using FOLLOW.
    """
    follow: dict[str, set[str]] = {nt: set() for nt in g.non_terminals}
    follow[AUGMENTED_START].add(END_MARKER)

    changed = True
    while changed:
        changed = False
        for prod in g.productions:
            for i, sym in enumerate(prod.rhs):
                if sym not in g.non_terminals:
                    continue
                trailer = first_of_sequence(prod.rhs[i + 1:], g.first_sets)
                before = len(follow[sym])
                follow[sym] |= trailer - {EPSILON}
                if EPSILON in trailer:
                    follow[sym] |= follow[prod.lhs]
                if len(follow[sym]) != before:
                    changed = True
    return follow
