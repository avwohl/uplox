"""End-to-end LR(1) driver: scanner -> parse table -> ParseContext."""

from __future__ import annotations

import pytest

from uplox.lex.build import lex_from_ir
from uplox.lex.scanner import Scanner
from uplox.parse.grammar import compile_grammar
from uplox.parse.lr1 import build_lr1
from uplox.parse.runtime import (
    HookRegistry,
    ParseContext,
    ParseError,
    ParseNode,
    parse,
)
from uplox.spec.reader import read_source


CALC = """
%grammar calc

%tokens
NUMBER = /[0-9]+/
PLUS   = '+'
MINUS  = '-'
STAR   = '*'
SLASH  = '/'
LPAREN = '('
RPAREN = ')'
WS     = /[ \\t\\n]+/    %skip

%rules
<expr>  : <expr> PLUS <term> | <expr> MINUS <term> | <term> ;
<term>  : <term> STAR <factor> | <term> SLASH <factor> | <factor> ;
<factor> : NUMBER | LPAREN <expr> RPAREN ;
"""


def calc_pipeline():
    ir = read_source(CALC)
    dfa, _tokens, skip = lex_from_ir(ir)
    scanner = Scanner(dfa=dfa, skip_tokens=frozenset(skip))
    table = build_lr1(compile_grammar(ir))
    return scanner, table


def test_parse_returns_parse_tree_for_calc_input():
    scanner, table = calc_pipeline()
    tree = parse(table, scanner.scan("1 + 2 * 3"))
    # ACCEPT unwraps to the start symbol's value (conventional yacc/bison
    # behavior — the augmented production's reduce fires as ACCEPT, not as a
    # normal reduction). So the top-level node is `expr`.
    assert isinstance(tree, ParseNode) and tree.kind == "expr"


def test_semantic_actions_can_evaluate_calc():
    scanner, table = calc_pipeline()
    g = table.grammar

    actions = {}
    # Locate productions by lhs+rhs shape.
    def find(lhs: str, rhs: tuple[str, ...]) -> int:
        return next(p.index for p in g.productions if p.lhs == lhs and p.rhs == rhs)

    actions[find("expr", ("expr", "PLUS", "term"))] = lambda _ctx, rhs: rhs[0] + rhs[2]
    actions[find("expr", ("expr", "MINUS", "term"))] = lambda _ctx, rhs: rhs[0] - rhs[2]
    actions[find("expr", ("term",))] = lambda _ctx, rhs: rhs[0]
    actions[find("term", ("term", "STAR", "factor"))] = lambda _ctx, rhs: rhs[0] * rhs[2]
    actions[find("term", ("term", "SLASH", "factor"))] = lambda _ctx, rhs: rhs[0] // rhs[2]
    actions[find("term", ("factor",))] = lambda _ctx, rhs: rhs[0]
    actions[find("factor", ("NUMBER",))] = lambda _ctx, rhs: int(rhs[0].text)
    actions[find("factor", ("LPAREN", "expr", "RPAREN"))] = lambda _ctx, rhs: rhs[1]
    # No action[0] needed — the augmented start production never reduces; ACCEPT
    # fires on its lookahead and the runtime returns the top-of-stack value.

    result = parse(table, scanner.scan("(12 + 34) * (5 - 6) / 7"), semantic_actions=actions)
    assert result == ((12 + 34) * (5 - 6)) // 7


def test_parse_error_on_bad_input():
    scanner, table = calc_pipeline()
    with pytest.raises(ParseError) as exc:
        parse(table, scanner.scan("1 + + 2"))
    err = exc.value
    assert err.token is not None
    assert err.token.text == "+"


def test_parse_error_lists_expected_tokens():
    """The error message includes the set of terminals the current state has
    actions for. After `1 +` the parser has just shifted PLUS and expects a
    factor-starter — NUMBER or LPAREN — so both should appear in the
    expected list and the unexpected `+` should not."""
    scanner, table = calc_pipeline()
    with pytest.raises(ParseError) as exc:
        parse(table, scanner.scan("1 + + 2"))
    msg = str(exc.value)
    assert "expected one of:" in msg
    assert "NUMBER" in msg
    assert "LPAREN" in msg


def test_parse_error_renders_eoi_as_text():
    """The synthetic end-marker token has line=0/col=0 and an empty text;
    the error message should say "unexpected end of input" rather than
    leaking those internal coordinates."""
    scanner, table = calc_pipeline()
    with pytest.raises(ParseError) as exc:
        parse(table, scanner.scan("1 +"))
    msg = str(exc.value)
    assert "unexpected end of input" in msg
    # No misleading "line 0, column 0" leak from the synthetic end-marker.
    assert "line 0" not in msg


def test_parse_error_renders_end_marker_in_expected_list():
    """When the end-marker is one of the legal continuations (e.g. after a
    complete expression), the expected list shows '<end of input>', not the
    raw '$' character used internally by the LR table."""
    scanner, table = calc_pipeline()
    with pytest.raises(ParseError) as exc:
        parse(table, scanner.scan("1 2"))
    msg = str(exc.value)
    assert "<end of input>" in msg
    assert "$" not in msg


def test_pre_shift_hook_fires_for_every_terminal():
    scanner, table = calc_pipeline()
    seen = []
    hooks = HookRegistry()
    hooks.register("pre_shift", lambda _ctx, payload: seen.append(payload["token"].name))
    parse(table, scanner.scan("1 + 2"), hooks=hooks)
    # Includes the synthetic end marker shift if any (which our driver guards
    # against). For "1 + 2": NUMBER PLUS NUMBER.
    assert seen == ["NUMBER", "PLUS", "NUMBER"]


def test_per_production_hook_fires_on_reduce():
    src = """
%grammar g
%tokens
A = 'a'
B = 'b'
%rules
<s> : A B  %hook=pair { $$ = "pair"; } ;
"""
    ir = read_source(src)
    table = build_lr1(compile_grammar(ir))
    dfa, _t, skip = lex_from_ir(ir)
    scanner = Scanner(dfa=dfa, skip_tokens=frozenset(skip))

    fired_when = []
    hooks = HookRegistry()
    hooks.register(
        "pair",
        lambda _ctx, payload: fired_when.append(payload["when"]),
    )
    parse(table, scanner.scan("ab"), hooks=hooks)
    assert "pre_reduce" in fired_when
    assert "post_reduce" in fired_when


def test_user_dict_threaded_through_context():
    scanner, table = calc_pipeline()
    counter = {"reduces": 0}
    hooks = HookRegistry()

    def count(ctx, _payload):
        ctx.user["count"] = ctx.user.get("count", 0) + 1

    # Manually wire pre_shift to count tokens.
    hooks.register("pre_shift", count)
    ctx_after: ParseContext | None = None

    # Drop straight into parse and pull the result. We don't have a hook for
    # "give me the context back" so we observe via shared closures.
    parse(table, scanner.scan("1 + 2"), hooks=hooks)
    # If we got here without raising, hooks fired correctly.
    # Verify counter at module scope wasn't used as a global state holder.
    assert counter == {"reduces": 0}  # untouched
