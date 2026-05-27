"""LR(1) shift-reduce driver consuming a token stream and an :class:`LRTable`.

This is the Phase 3 Python driver — the reference implementation that other
backends (C, C++, Lua) emit equivalent code for. The algorithm is the textbook
one; the design choices that *are* uplox-specific are:

* **Re-entrancy**: every piece of mutable state lives on a :class:`ParseContext`
  instance, never at module scope. Multiple parsers built from different
  grammars can run side-by-side without symbol collisions or leaked state.
  Hooks receive the context as their first argument.
* **Hooks fire at well-defined points**:
  - ``pre_shift``  — right before pushing a terminal value onto the stack
  - ``pre_reduce`` — right before popping a production's RHS
  - ``post_reduce`` — right after pushing the reduced LHS value onto the stack
  - ``on_error`` — when ACTION lookup fails for the current token
* **Default tree builder**: if no semantic action is registered for a
  production, the runtime constructs a generic :class:`ParseNode` whose children
  are the values from the popped RHS (terminals stay as :class:`Token`,
  non-terminals are nested ParseNodes). This makes the lexer + parser usable
  end-to-end without writing any host-language glue.

The semantic-action text from the grammar (``{ $$ = $1 + $3; }``) is *not*
interpreted here — that is the C / C++ backend's job. Python users register
real Python callables via :class:`HookRegistry`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Optional, Union

from ..lex.scanner import Token
from .grammar import END_MARKER, Grammar
from .lr1 import AcceptAction, LRTable, PredicatedActions, ReduceAction, ShiftAction


@dataclass
class ParseNode:
    """Default tree node when no per-production semantic action is registered.

    ``children`` mixes :class:`Token` and :class:`ParseNode` in source order. The
    node carries its production's LHS as :attr:`kind` and the production index
    as :attr:`production` for downstream tools (pretty-printers, AST-lowering).
    """
    kind: str
    children: list["StackValue"] = field(default_factory=list)
    production: int = -1

    def __repr__(self) -> str:  # pragma: no cover - cosmetic
        return f"ParseNode({self.kind!r}, {self.children!r})"


# A value on the parser stack is either a token (from a shift) or a tree node
# (from a reduce). Semantic-action callables can return anything; we widen to
# Any in that case.
StackValue = Union[Token, ParseNode, Any]


HookCallback = Callable[["ParseContext", dict], None]


@dataclass
class HookRegistry:
    """Map hook-name -> Python callable. Resolution happens at parse time.

    The registry is a separate object so tests and host drivers can swap
    callbacks without rebuilding the parser. By default a missing name is
    fatal — silent no-ops would mask spec/driver mismatches. Tools that
    intentionally skip hooks (smoke parsers, syntax checkers) can pass
    ``ignore_missing=True`` so unknown names are dropped silently.
    """
    callbacks: dict[str, HookCallback] = field(default_factory=dict)
    ignore_missing: bool = False

    def register(self, name: str, fn: HookCallback) -> None:
        self.callbacks[name] = fn

    def fire(self, name: str, ctx: "ParseContext", payload: dict) -> None:
        cb = self.callbacks.get(name)
        if cb is None:
            if self.ignore_missing:
                return
            raise ParseError(f"hook {name!r} fired but no callback registered")
        cb(ctx, payload)


# ---- Phase-1 / Phase-2 / Phase-3 registries ---------------------------------
#
# Three first-class registries for context-sensitive parsing:
#
#  * ClassifierRegistry — host re-labels lexer tokens at lookahead time.
#    Keyed on the source terminal name (the one the DFA produced); each
#    callback returns the final terminal name. The set of allowed
#    alternative names is fixed at grammar-build time (``%classifier``).
#
#  * ActionRegistry — host runs a callable after a production with
#    ``!{name}`` reduces. Identical semantics to a ``post_reduce`` hook
#    declared on the same production, but lifted to its own concept so
#    grammars can route mutator-style state updates (typedef table,
#    template-name table) separately from analysis-style hooks.
#
#  * PredicateRegistry — host evaluates a predicate at parse time to
#    select among predicated alternatives. The runtime consults the
#    registry when the LR table has multiple action entries gated on
#    different predicates at the same (state, lookahead).
#
# All three default to ``ignore_missing=False`` so a spec/host mismatch
# is loud. Tools that don't care (lint, smoke) pass ``True``.


ClassifyCallback = Callable[[str, "ParseContext"], str]
ActionCallback = Callable[["ParseContext", "ParseNode"], None]
PredicateCallback = Callable[[Token, "ParseContext"], bool]


@dataclass
class ClassifierRegistry:
    """Maps source-terminal names to host classifier callbacks.

    The callback receives ``(text, ctx)`` and returns the final terminal
    name (one of the grammar's declared alternatives, or the source name
    itself if no change). Empty registry behaves as the identity filter.

    Wired through :func:`parse` via the ``classifiers=`` kwarg.
    """
    callbacks: dict[str, ClassifyCallback] = field(default_factory=dict)
    ignore_missing: bool = False

    def register(self, source_token: str, fn: ClassifyCallback) -> None:
        self.callbacks[source_token] = fn

    def classify(self, ctx: "ParseContext", tok: Token) -> Token:
        cb = self.callbacks.get(tok.name)
        if cb is None:
            return tok
        new_name = cb(tok.text, ctx)
        if new_name == tok.name:
            return tok
        new_kind = 0
        # Preserve the kind int if the host runtime has a kind_map
        # stashed in ctx.user (the bundle-loader does this for emitted
        # drivers). For pure-Python parses without it, the host can
        # still compare by tok.name.
        kind_map = ctx.user.get("kind_map") if isinstance(ctx.user, dict) else None
        if isinstance(kind_map, dict):
            new_kind = kind_map.get(new_name, 0)
        return Token(
            name=new_name, text=tok.text, line=tok.line, column=tok.column,
            offset=tok.offset, file_id=tok.file_id, kind=new_kind,
        )


@dataclass
class ActionRegistry:
    """Maps action names to host callables fired after a successful reduce.

    Each callback receives ``(ctx, node)`` where ``node`` is the reduced
    ParseNode (or whatever the semantic action returned). Identical
    semantics to a ``post_reduce`` hook but routed independently for
    backend-codegen clarity.
    """
    callbacks: dict[str, ActionCallback] = field(default_factory=dict)
    ignore_missing: bool = False

    def register(self, name: str, fn: ActionCallback) -> None:
        self.callbacks[name] = fn

    def fire(self, ctx: "ParseContext", name: str, node: "ParseNode") -> None:
        cb = self.callbacks.get(name)
        if cb is None:
            if self.ignore_missing:
                return
            raise ParseError(
                f"action {name!r} referenced by a production but no callback registered"
            )
        cb(ctx, node)


@dataclass
class PredicateRegistry:
    """Maps predicate names to host callables evaluated when the parser
    has multiple predicated alternatives at the same (state, lookahead).

    Each callback receives ``(lookahead_token, ctx)`` and returns
    ``bool``. The runtime walks the predicates in their declaration
    order and selects the first action whose predicate returns
    ``True``. If no predicate matches and an unconditional default
    action exists, the parser uses that; otherwise it errors.

    Empty registry behaves as if all predicates returned ``False`` —
    i.e. only unconditional actions fire. Pass
    ``ignore_missing=False`` (the default) so a spec/host mismatch is
    fatal.
    """
    callbacks: dict[str, PredicateCallback] = field(default_factory=dict)
    ignore_missing: bool = False

    def register(self, name: str, fn: PredicateCallback) -> None:
        self.callbacks[name] = fn

    def evaluate(self, ctx: "ParseContext", name: str, tok: Token) -> bool:
        cb = self.callbacks.get(name)
        if cb is None:
            if self.ignore_missing:
                return False
            raise ParseError(
                f"predicate {name!r} referenced by the table but no callback registered"
            )
        return bool(cb(tok, ctx))


# ---- Phase 4: lexer modes ----------------------------------------------------


@dataclass
class ModeStack:
    """Per-parse mode-stack helper.

    Lexer modes (COBOL PIC clauses, TeX catcodes, Fortran format spec) are
    state the lexer and parser share. The stack lives on the parse
    context; actions push/pop it as the parse progresses; classifier
    and predicate callbacks inspect it.

    The grammar's ``%modes`` declaration fixes the set of valid mode
    names. ``modes[0]`` is the initial state. Pushing an undeclared
    mode is a host bug — assertion at debug, no enforcement otherwise.

    Notes on what this DOES and DOES NOT do today:

    * **Does**: tracks the current mode, exposes ``push``/``pop``/
      ``current``, and survives error paths because state is on the
      ParseContext.
    * **Does NOT**: switch DFAs per mode. The current Scanner runs
      one DFA. Mode-aware tokenisation for languages that genuinely
      need different lexer states (TeX catcodes, Verilog escaped
      identifiers) needs an additional scanner refactor — tracked as
      future work. The COBOL-style use case where mode determines
      which token NAME a matched text resolves to is already covered
      by the classifier registry combined with this mode stack.
    """
    modes: tuple[str, ...] = ()
    stack: list[str] = field(default_factory=list)

    def reset(self) -> None:
        self.stack = [self.modes[0]] if self.modes else []

    def current(self) -> str:
        return self.stack[-1] if self.stack else ""

    def push(self, mode: str) -> None:
        if self.modes and mode not in self.modes:
            raise ValueError(
                f"ModeStack.push({mode!r}): not in declared modes {self.modes!r}"
            )
        self.stack.append(mode)

    def pop(self) -> str:
        if len(self.stack) <= 1:
            raise RuntimeError(
                "ModeStack.pop: can't pop the initial/default mode"
            )
        return self.stack.pop()


# Semantic actions are Python callables receiving (context, rhs_values) and
# returning the value to push onto the stack for the LHS. The default action,
# used when no callable is registered for a production, builds a ParseNode.
SemanticAction = Callable[["ParseContext", list[StackValue]], StackValue]


# A token filter is the uplox equivalent of yacc/bison's "lexer feedback":
# the host can rewrite a token's terminal name based on parser-driven state.
# The classic use is the C typedef-name hack — after `typedef int Foo;` is
# reduced, subsequent ``Foo`` IDENT tokens get rewritten to TYPEDEF_NAME so
# the parser sees them as type-specs.  The runtime invokes the filter every
# time the lookahead is fetched **and** after every reduction (so a hook
# that just updated the host's name table sees its change applied to the
# pending lookahead before the next action lookup).
TokenFilter = Callable[["ParseContext", Token], Token]


@dataclass
class ParseContext:
    """All mutable state a parser run touches. Never use module globals.

    Hosts may stash their own data in :attr:`user` — the runtime never reads or
    writes it. Useful for scoped name tables, error counters, and other
    cross-cutting state that hooks need.
    """
    table: LRTable
    state_stack: list[int] = field(default_factory=list)
    value_stack: list[StackValue] = field(default_factory=list)
    semantic_actions: dict[int, SemanticAction] = field(default_factory=dict)
    hooks: HookRegistry = field(default_factory=HookRegistry)
    classifiers: ClassifierRegistry = field(default_factory=ClassifierRegistry)
    actions: ActionRegistry = field(default_factory=ActionRegistry)
    predicates: PredicateRegistry = field(default_factory=PredicateRegistry)
    mode_stack: ModeStack = field(default_factory=ModeStack)
    user: dict[str, Any] = field(default_factory=dict)
    """Free-form scratchpad for hooks and host drivers."""

    @property
    def grammar(self) -> Grammar:
        return self.table.grammar


class ParseError(Exception):
    def __init__(self, message: str, token: Optional[Token] = None):
        super().__init__(message)
        self.token = token


def _default_action(ctx: ParseContext, prod_index: int, rhs: list[StackValue]) -> ParseNode:
    """Generic tree-builder: wrap the production's RHS into a :class:`ParseNode`."""
    prod = ctx.grammar.productions[prod_index]
    return ParseNode(kind=prod.lhs, children=rhs, production=prod_index)


def _fire_hook_if_named(ctx: ParseContext, when: str, prod_index: int, payload: dict) -> None:
    """Fire the per-production hook attached in the grammar, if any."""
    prod = ctx.grammar.productions[prod_index]
    if prod.hook:
        ctx.hooks.fire(prod.hook, ctx, {"when": when, "production": prod_index, **payload})


def parse(
    table: LRTable,
    tokens: Iterable[Token],
    *,
    hooks: HookRegistry | None = None,
    semantic_actions: dict[int, SemanticAction] | None = None,
    token_filter: TokenFilter | None = None,
    classifiers: ClassifierRegistry | None = None,
    actions: ActionRegistry | None = None,
    predicates: PredicateRegistry | None = None,
) -> StackValue:
    """Run the LR driver. Returns the value associated with the start symbol.

    ``tokens`` must end with no trailing data — the runtime appends an internal
    end-marker. If ``tokens`` is itself unterminated, the driver still terminates
    by encountering the synthetic ``$``.

    ``token_filter`` is the legacy lexer-feedback hook for grammars that
    need to re-classify tokens based on parser-driven state. New code
    should use :class:`ClassifierRegistry` via the ``classifiers=`` kwarg;
    both apply (classifier first, then ``token_filter``) for back-compat.

    ``classifiers`` is the Phase-1 token classifier registry. When the
    grammar carries ``%classifier`` declarations, host callbacks register
    here re-label tokens at lookahead-fetch time.

    ``actions`` is the Phase-2 post-reduce action registry. When a
    production carries ``!{name}``, the matching callback fires after
    the reduction completes.

    ``predicates`` is the Phase-3 predicate registry. When the LR table
    has predicated alternatives, the runtime consults this registry to
    pick the matching alternative.
    """
    ctx = ParseContext(
        table=table,
        hooks=hooks or HookRegistry(),
        semantic_actions=semantic_actions or {},
        classifiers=classifiers or ClassifierRegistry(),
        actions=actions or ActionRegistry(),
        predicates=predicates or PredicateRegistry(),
        mode_stack=ModeStack(modes=table.grammar.modes),
    )
    ctx.mode_stack.reset()
    ctx.state_stack.append(table.start_state)

    # Normalise the input: append a synthetic end-of-input token at the end.
    token_iter = iter(tokens)
    end = Token(name=END_MARKER, text="", line=0, column=0, offset=-1)

    def _apply_filters(tok: Token) -> Token:
        if tok is end:
            return tok
        # Classifier first (host-supplied name relabel), then the legacy
        # raw token_filter callable for back-compat with existing host
        # drivers (TypedefTracker etc.).
        if ctx.classifiers.callbacks:
            tok = ctx.classifiers.classify(ctx, tok)
        if token_filter is not None:
            tok = token_filter(ctx, tok)
        return tok

    def fetch_next() -> Token:
        try:
            tok = next(token_iter)
        except StopIteration:
            return end
        return _apply_filters(tok)

    lookahead = fetch_next()
    end_seen = lookahead is end

    while True:
        state = ctx.state_stack[-1]
        action = _select_action(ctx, state, lookahead)
        if action is None:
            # Default reduction fallback: a state whose only actions are all
            # reduce-X (for the same X) reduces unconditionally. Required for
            # token-filter feedback grammars (typedef-name hack) where the
            # post_reduce hook needs to fire before the next token is
            # classified — see TokenFilter docstring and TypedefTracker.
            default_prod = table.default_reductions.get(state)
            if default_prod is not None:
                _do_reduce(ctx, default_prod)
                lookahead = _apply_filters(lookahead)
                continue
            _on_error(ctx, lookahead)

        if isinstance(action, ShiftAction):
            _fire_pre_shift(ctx, lookahead)
            ctx.state_stack.append(action.state)
            ctx.value_stack.append(lookahead)
            if not end_seen:
                lookahead = fetch_next()
                end_seen = lookahead is end
            else:
                # We just shifted the end marker — that is only legal in the
                # rare grammar where $ appears explicitly. Defensive: treat as
                # an error rather than loop forever.
                raise ParseError(
                    f"shifted end-of-input in state {action.state}", lookahead
                )
            continue

        if isinstance(action, ReduceAction):
            _do_reduce(ctx, action.production)
            # A post_reduce hook or action may have updated host name
            # tables (typedef tracking, template-name tracking). Re-
            # classify so the pending lookahead reflects that new state
            # before the next action lookup.
            lookahead = _apply_filters(lookahead)
            continue

        if isinstance(action, AcceptAction):
            # The augmented start production has been reduced and ACCEPT fired;
            # the value on top of the value stack is the start symbol's value.
            return ctx.value_stack[-1]

        raise ParseError(f"unknown action {action!r}")


def _select_action(ctx: ParseContext, state: int, lookahead: Token):
    """Look up an action for (state, lookahead.name), evaluating any
    predicated alternatives in declaration order.

    For non-predicated grammars this is a single dict lookup. When the
    table has a PredicatedActions cell, walks the alternatives in order
    and picks the first whose predicate returns True; falls through to
    the default action when no predicate matches.
    """
    cell = ctx.table.action.get((state, lookahead.name))
    if cell is None:
        return None
    if isinstance(cell, PredicatedActions):
        for pred_name, act in cell.alternatives:
            if pred_name is None:
                # Unconditional default — only reached if no preceding
                # predicate matched.
                return act
            if ctx.predicates.evaluate(ctx, pred_name, lookahead):
                return act
        return cell.fallback
    return cell


def _do_reduce(ctx: ParseContext, prod_index: int) -> None:
    prod = ctx.grammar.productions[prod_index]
    rhs_len = len(prod.rhs)
    rhs_vals = ctx.value_stack[len(ctx.value_stack) - rhs_len:] if rhs_len else []

    _fire_hook_if_named(ctx, "pre_reduce", prod_index, {"rhs": rhs_vals})

    if rhs_len:
        del ctx.state_stack[-rhs_len:]
        del ctx.value_stack[-rhs_len:]

    action = ctx.semantic_actions.get(prod_index)
    if action is not None:
        new_value = action(ctx, rhs_vals)
    else:
        new_value = _default_action(ctx, prod_index, rhs_vals)

    state = ctx.state_stack[-1]
    target = ctx.table.goto.get((state, prod.lhs))
    if target is None:
        raise ParseError(
            f"no GOTO from state {state} on {prod.lhs!r}; table is malformed"
        )
    ctx.state_stack.append(target)
    ctx.value_stack.append(new_value)
    _fire_hook_if_named(ctx, "post_reduce", prod_index, {"value": new_value})
    # Phase-2 first-class action: fire the host callback registered for
    # the production's ``!{name}`` annotation, if any. Fires after the
    # post_reduce hook so a production with both gets the hook first
    # (analysis) then the action (state-mutating).
    if prod.post_action is not None:
        ctx.actions.fire(ctx, prod.post_action, new_value)


def _fire_pre_shift(ctx: ParseContext, tok: Token) -> None:
    # Per-token hook lookup happens via the per-production hook on production
    # zero — rare in practice, but we expose the firing point so user-registered
    # global hooks can inspect every shifted token.
    cb = ctx.hooks.callbacks.get("pre_shift")
    if cb is not None:
        cb(ctx, {"when": "pre_shift", "token": tok})


def _on_error(ctx: ParseContext, tok: Token) -> None:
    cb = ctx.hooks.callbacks.get("on_error")
    if cb is not None:
        cb(ctx, {"when": "on_error", "token": tok, "state_stack": list(ctx.state_stack)})
    state = ctx.state_stack[-1]
    # Render the synthetic end-marker as "<end of input>" in the expected
    # list; that's friendlier than the raw "$" the LR table uses internally.
    raw_expected = sorted(t for (s, t) in ctx.table.action if s == state)
    expected = ["<end of input>" if t == END_MARKER else t for t in raw_expected]
    if expected:
        # Cap the displayed list: in expression contexts the FOLLOW set can be
        # large enough that the message becomes a wall of token names; the
        # first dozen are usually enough to figure out what shape was wanted.
        shown = expected[:12]
        suffix = "" if len(expected) <= 12 else f", ... +{len(expected) - 12} more"
        expect_msg = f"; expected one of: {', '.join(shown)}{suffix}"
    else:
        expect_msg = ""
    # The synthetic end-of-input token carries offset=-1, line=0, column=0;
    # render it as "end of input" rather than a misleading line:col pair.
    if tok.offset < 0:
        message = f"unexpected end of input{expect_msg}"
    else:
        message = (
            f"unexpected token {tok.name!r} {tok.text!r} at "
            f"line {tok.line}, column {tok.column}{expect_msg}"
        )
    raise ParseError(message, token=tok)
