"""LR(1) shift-reduce driver consuming a token stream and an :class:`LRTable`.

This is the Phase 3 Python driver — the reference implementation that other
backends (C, C++, Lua) emit equivalent code for. The algorithm is the textbook
one; the design choices that *are* plox-specific are:

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
from .lr1 import AcceptAction, LRTable, ReduceAction, ShiftAction


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
    callbacks without rebuilding the parser. A missing name is fatal — silent
    no-ops would mask spec/driver mismatches.
    """
    callbacks: dict[str, HookCallback] = field(default_factory=dict)

    def register(self, name: str, fn: HookCallback) -> None:
        self.callbacks[name] = fn

    def fire(self, name: str, ctx: "ParseContext", payload: dict) -> None:
        cb = self.callbacks.get(name)
        if cb is None:
            raise ParseError(f"hook {name!r} fired but no callback registered")
        cb(ctx, payload)


# Semantic actions are Python callables receiving (context, rhs_values) and
# returning the value to push onto the stack for the LHS. The default action,
# used when no callable is registered for a production, builds a ParseNode.
SemanticAction = Callable[["ParseContext", list[StackValue]], StackValue]


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
) -> StackValue:
    """Run the LR driver. Returns the value associated with the start symbol.

    ``tokens`` must end with no trailing data — the runtime appends an internal
    end-marker. If ``tokens`` is itself unterminated, the driver still terminates
    by encountering the synthetic ``$``.
    """
    ctx = ParseContext(
        table=table,
        hooks=hooks or HookRegistry(),
        semantic_actions=semantic_actions or {},
    )
    ctx.state_stack.append(table.start_state)

    # Normalise the input: append a synthetic end-of-input token at the end.
    token_iter = iter(tokens)
    end = Token(name=END_MARKER, text="", line=0, column=0, offset=-1)

    def fetch_next() -> Token:
        try:
            return next(token_iter)
        except StopIteration:
            return end

    lookahead = fetch_next()
    end_seen = lookahead is end

    while True:
        state = ctx.state_stack[-1]
        action = table.action.get((state, lookahead.name))
        if action is None:
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
            continue

        if isinstance(action, AcceptAction):
            # The augmented start production has been reduced and ACCEPT fired;
            # the value on top of the value stack is the start symbol's value.
            return ctx.value_stack[-1]

        raise ParseError(f"unknown action {action!r}")


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
    raise ParseError(
        f"unexpected token {tok.name!r} {tok.text!r} at "
        f"line {tok.line}, column {tok.column}",
        token=tok,
    )
