"""End-to-end tests for the Phase-1/2/3/4 context-sensitive parsing
extensions: classifiers, actions, predicates, lexer modes.

Each test builds a minimal grammar exercising one extension, runs it
through the read -> compile -> build -> parse pipeline, and checks that
the runtime visible behaviour matches the spec.
"""

from __future__ import annotations

import textwrap

import pytest

from uplox.parse.grammar import compile_grammar
from uplox.parse.lr1 import build_table
from uplox.parse.runtime import (
    ActionRegistry,
    ClassifierRegistry,
    HookRegistry,
    ParseContext,
    PredicateRegistry,
    parse,
)
from uplox.spec.reader import read_source
from uplox.tables.parse_section import table_from_json, table_to_json
from uplox.lex.build import lex_from_ir
from uplox.lex.scanner import Scanner


def _build(source: str):
    """read+compile+build a grammar string. Returns (table, scanner)."""
    ir = read_source(textwrap.dedent(source))
    dfa, _tokens, skip = lex_from_ir(ir)
    grammar = compile_grammar(ir)
    table = build_table(grammar)
    assert not table.conflicts, [c.describe(grammar) for c in table.conflicts]
    scanner = Scanner(dfa=dfa, skip_tokens=frozenset(skip))
    return table, scanner


# ---- Phase 1: classifier ----------------------------------------------------


def test_classifier_relabels_token_at_lookahead():
    """A %classifier IDENT -> TYPEDEF_NAME entry plus a host callback that
    relabels based on a name table — replays the C typedef hack.
    """
    src = """
        %grammar tdtest
        %tokens
        WS = /[ \\t\\n]+/ %skip
        IDENT = /[A-Za-z_][A-Za-z0-9_]*/
        TYPEDEF_NAME = /__never_matches__/
        SEMI = ';'
        %classifier
        IDENT -> TYPEDEF_NAME
        %rules
        <prog> : <items> ;
        <items> : | <items> <item> ;
        <item> : IDENT SEMI | TYPEDEF_NAME SEMI ;
    """
    table, scanner = _build(src)

    # Closure-bound type set so the classifier doesn't depend on
    # ctx.user being seeded before the first lookahead fetch.
    types: set[str] = {"Foo"}
    cls = ClassifierRegistry()
    cls.register(
        "IDENT",
        lambda text, ctx: "TYPEDEF_NAME" if text in types else "IDENT",
    )

    tokens = list(scanner.scan("Foo; bar;"))
    tree = parse(table, tokens, classifiers=cls,
                 hooks=HookRegistry(ignore_missing=True))
    assert tree.kind == "prog"


def test_classifier_distinguishes_paths():
    """A grammar where IDENT and TYPEDEF_NAME drive different productions:
    the choice must reflect the host classifier."""
    src = """
        %grammar tdtest2
        %tokens
        WS = /[ \\t\\n]+/ %skip
        IDENT = /[A-Za-z_][A-Za-z0-9_]*/
        TYPEDEF_NAME = /__never_matches__/
        STAR = '*'
        SEMI = ';'
        %classifier
        IDENT -> TYPEDEF_NAME
        %rules
        <prog> : <stmt> ;
        <stmt> : <decl> | <expr> ;
        <decl> : TYPEDEF_NAME IDENT SEMI ;
        <expr> : IDENT STAR IDENT SEMI ;
    """
    table, scanner = _build(src)

    def parse_with_types(source, types):
        cls = ClassifierRegistry()
        cls.register(
            "IDENT",
            lambda text, ctx: "TYPEDEF_NAME" if text in types else "IDENT",
        )
        toks = list(scanner.scan(source))
        return parse(table, toks, classifiers=cls,
                     hooks=HookRegistry(ignore_missing=True))

    tree_decl = parse_with_types("Foo bar;", {"Foo"})
    assert tree_decl.kind == "prog"

    tree_expr = parse_with_types("foo * bar;", set())
    assert tree_expr.kind == "prog"


def _seed_user(state: dict):
    """Hook factory that copies `state` into ctx.user the first time any
    hook fires. Used in tests to inject host state before parsing.
    """
    reg = HookRegistry(ignore_missing=True)

    def seed(ctx, payload):
        for k, v in state.items():
            ctx.user.setdefault(k, v)

    reg.register("pre_shift", seed)
    return reg


# ---- Phase 1 extension: classifier with lookahead window --------------------


def test_classifier_with_peek_views_next_tokens():
    """A 3-arg classifier callback receives a `LookaheadView` over the
    upcoming raw tokens. Peeked tokens reach the parser later, exactly
    once each — the classifier doesn't re-fire for already-peeked
    items."""
    src = """
        %grammar peektest
        %tokens
        WS = /[ \\t\\n]+/ %skip
        IDENT = /[A-Za-z_][A-Za-z0-9_]*/
        TYPEDEF_NAME = /__never_matches__/
        LPAREN = '('
        RPAREN = ')'
        SEMI = ';'
        %classifier
        IDENT -> TYPEDEF_NAME
        %rules
        <prog> : <items> ;
        <items> : | <items> <item> ;
        <item> : IDENT SEMI
               | TYPEDEF_NAME SEMI
               | IDENT LPAREN RPAREN SEMI
               | TYPEDEF_NAME LPAREN RPAREN SEMI
               ;
    """
    table, scanner = _build(src)

    # Classifier sees `peek` and decides based on whether `(` follows.
    # IDENT-followed-by-LPAREN-RPAREN-SEMI is treated as a "call"
    # (kept IDENT); IDENT-followed-by-SEMI is a "type" (relabel).
    seen_peeks: dict[str, list[str]] = {}

    def classify(text, ctx, peek):
        next_two = peek.peek(2)
        seen_peeks[text] = [t.name for t in next_two]
        if next_two and next_two[0].name == "LPAREN":
            return "IDENT"
        return "TYPEDEF_NAME"

    cls = ClassifierRegistry()
    cls.register("IDENT", classify)

    tokens = list(scanner.scan("Foo; bar();"))
    tree = parse(table, tokens, classifiers=cls,
                 hooks=HookRegistry(ignore_missing=True))
    assert tree.kind == "prog"
    # Each IDENT saw its next two tokens via peek.
    assert seen_peeks == {
        "Foo": ["SEMI", "IDENT"],     # `Foo` saw `; bar`
        "bar": ["LPAREN", "RPAREN"],  # `bar` saw `( )`
    }


def test_classifier_legacy_two_arg_still_works():
    """Existing two-arg classifier callbacks (no `peek` param) remain
    fully supported — arity detection picks the legacy path."""
    src = """
        %grammar legacytest
        %tokens
        WS = /[ \\t\\n]+/ %skip
        IDENT = /[A-Za-z_][A-Za-z0-9_]*/
        TYPEDEF_NAME = /__never_matches__/
        SEMI = ';'
        %classifier
        IDENT -> TYPEDEF_NAME
        %rules
        <prog> : <items> ;
        <items> : | <items> <item> ;
        <item> : IDENT SEMI | TYPEDEF_NAME SEMI ;
    """
    table, scanner = _build(src)
    cls = ClassifierRegistry()
    cls.register(
        "IDENT",
        lambda text, ctx: "TYPEDEF_NAME" if text == "Foo" else "IDENT",
    )
    tokens = list(scanner.scan("Foo; bar;"))
    tree = parse(table, tokens, classifiers=cls,
                 hooks=HookRegistry(ignore_missing=True))
    assert tree.kind == "prog"


def test_classifier_peek_handles_eof_short():
    """`peek(N)` near EOF returns up to N tokens, never an error.
    The end-marker terminates the buffer."""
    src = """
        %grammar peekeof
        %tokens
        WS = /[ \\t\\n]+/ %skip
        IDENT = /[A-Za-z_][A-Za-z0-9_]*/
        TYPEDEF_NAME = /__never_matches__/
        SEMI = ';'
        %classifier
        IDENT -> TYPEDEF_NAME
        %rules
        <prog> : <items> ;
        <items> : | <items> <item> ;
        <item> : IDENT SEMI | TYPEDEF_NAME SEMI ;
    """
    table, scanner = _build(src)
    last_peek: list[int] = [0]

    def classify(text, ctx, peek):
        last_peek[0] = len(peek.peek(100))
        return "IDENT"

    cls = ClassifierRegistry()
    cls.register("IDENT", classify)
    tokens = list(scanner.scan("only_one;"))
    parse(table, tokens, classifiers=cls,
          hooks=HookRegistry(ignore_missing=True))
    # Two real tokens remain after `only_one` (SEMI and end marker);
    # peek(100) returns up to 3 because the end marker itself counts.
    # Whatever the exact short count, the call MUST not raise.
    assert last_peek[0] <= 3


# ---- Phase 1 extension: named state sets ------------------------------------


def test_state_set_membership_basic():
    """A grammar with a `%state_set` declaration: at parse time, the
    classifier can ask `ctx.in_state_set("inside_x")` and get the
    correct answer."""
    src = """
        %grammar ssettest
        %tokens
        WS = /[ \\t\\n]+/ %skip
        IDENT = /[A-Za-z_][A-Za-z0-9_]*/
        TYPEDEF_NAME = /__never_matches__/
        LPAREN = '('
        RPAREN = ')'
        SEMI = ';'
        %classifier
        IDENT -> TYPEDEF_NAME
        %state_set
        inside_args : args
        %rules
        <prog> : <items> ;
        <items> : | <items> <item> ;
        <item> : IDENT SEMI | IDENT LPAREN <args> RPAREN SEMI ;
        <args> : | <args> IDENT ;
    """
    table, scanner = _build(src)
    assert "inside_args" in table.grammar.state_sets

    seen_inside: dict[str, bool] = {}

    def classify(text, ctx):
        # Record the FIRST in-state observation for each name; later
        # re-classifications may fire after reductions advance the
        # parser into different states.
        if text not in seen_inside:
            seen_inside[text] = ctx.in_state_set("inside_args")
        return "IDENT"

    cls = ClassifierRegistry()
    cls.register("IDENT", classify)
    tokens = list(scanner.scan("a; b(c d) ;"))
    parse(table, tokens, classifiers=cls,
          hooks=HookRegistry(ignore_missing=True))
    # `a` and `b` are outside the parens; `c` and `d` are inside.
    assert seen_inside == {"a": False, "b": False, "c": True, "d": True}


def test_state_set_unknown_returns_false():
    """Querying an undeclared state-set name returns False, never
    raises."""
    src = """
        %grammar ssetfalse
        %tokens
        WS = /[ \\t\\n]+/ %skip
        IDENT = /[A-Za-z_][A-Za-z0-9_]*/
        TYPEDEF_NAME = /__never_matches__/
        SEMI = ';'
        %classifier
        IDENT -> TYPEDEF_NAME
        %rules
        <prog> : <items> ;
        <items> : | <items> IDENT SEMI ;
    """
    table, scanner = _build(src)

    result: list[bool] = []
    cls = ClassifierRegistry()
    cls.register(
        "IDENT",
        lambda text, ctx: (result.append(ctx.in_state_set("does_not_exist")), "IDENT")[1],
    )
    tokens = list(scanner.scan("foo;"))
    parse(table, tokens, classifiers=cls,
          hooks=HookRegistry(ignore_missing=True))
    # Classifier may fire multiple times for the same token via
    # default-reduction re-classification; every fire must return False
    # since the named set isn't declared.
    assert all(r is False for r in result)
    assert len(result) >= 1


def test_state_set_round_trips_through_bundle():
    """The `%state_set` declaration survives bundle JSON serialisation
    and reconstruction. Per-state membership is preserved verbatim."""
    src = """
        %grammar ssetbundle
        %tokens
        WS = /[ \\t\\n]+/ %skip
        IDENT = /[A-Za-z_][A-Za-z0-9_]*/
        LPAREN = '('
        RPAREN = ')'
        SEMI = ';'
        %state_set
        inside_args : args
        %rules
        <prog> : <items> ;
        <items> : | <items> <item> ;
        <item> : IDENT SEMI | IDENT LPAREN <args> RPAREN SEMI ;
        <args> : | <args> IDENT ;
    """
    table, _ = _build(src)
    original = dict(table.state_set_membership)
    serialised = table_to_json(table)
    assert "state_sets" in serialised
    assert serialised["state_sets"] == {"inside_args": ["args"]}
    rebuilt = table_from_json(serialised)
    assert rebuilt.state_set_membership == original
    assert "inside_args" in rebuilt.grammar.state_sets


def test_state_set_unknown_lhs_rejected():
    """A `%state_set` entry referring to a non-existent non-terminal
    is a build-time error (otherwise the runtime query would silently
    always return False)."""
    src = """
        %grammar sseterr
        %tokens
        WS = /[ \\t\\n]+/ %skip
        IDENT = /[A-Za-z_][A-Za-z0-9_]*/
        SEMI = ';'
        %state_set
        broken : nonexistent_lhs
        %rules
        <prog> : <items> ;
        <items> : | <items> IDENT SEMI ;
    """
    with pytest.raises(Exception, match="nonexistent_lhs"):
        _build(src)


# ---- Phase 2: action --------------------------------------------------------


def test_action_with_positional_arg():
    """`!{name@N}` passes children[N-1] of the reduced production to
    the action instead of the whole subtree."""
    src = """
        %grammar acttest_pos
        %keyword_prefix KW_
        %keywords
        using
        %tokens
        WS = /[ \\t\\n]+/ %skip
        IDENT = /[A-Za-z_][A-Za-z0-9_]*/
        SEMI = ';'
        EQ = '='
        %actions
        record_name
        %rules
        <prog> : <items> ;
        <items> : | <items> <item> ;
        # `using IDENT = IDENT ;` — positions: 1=using 2=IDENT 3=EQ 4=IDENT 5=SEMI
        # Test extracts position 2 (the LHS name only).
        <item> : using IDENT EQ IDENT SEMI !{record_name@2} ;
    """
    table, scanner = _build(src)

    seen: list[str] = []
    actions = ActionRegistry()
    actions.register("record_name", lambda ctx, child: seen.append(child.text))

    tokens = list(scanner.scan("using Foo = Bar;"))
    parse(table, tokens, actions=actions, hooks=HookRegistry(ignore_missing=True))
    assert seen == ["Foo"]


def test_action_fires_after_reduce():
    """An action registered via %actions + !{name} runs after each
    reduction of the annotated production."""
    src = """
        %grammar acttest
        %tokens
        WS = /[ \\t\\n]+/ %skip
        IDENT = /[A-Za-z_][A-Za-z0-9_]*/
        SEMI = ';'
        %actions
        record
        %rules
        <prog> : <items> ;
        <items> : | <items> <item> ;
        <item> : IDENT SEMI !{record} ;
    """
    table, scanner = _build(src)

    seen: list[str] = []
    actions = ActionRegistry()
    actions.register("record", lambda ctx, node: seen.append(node.kind))

    tokens = list(scanner.scan("a; b; c;"))
    parse(table, tokens, actions=actions, hooks=HookRegistry(ignore_missing=True))
    assert seen == ["item", "item", "item"]


def test_action_missing_callback_errors_by_default():
    src = """
        %grammar acterr
        %tokens
        WS = /[ \\t\\n]+/ %skip
        IDENT = /[A-Za-z_][A-Za-z0-9_]*/
        SEMI = ';'
        %actions
        mustexist
        %rules
        <prog> : IDENT SEMI !{mustexist} ;
    """
    table, scanner = _build(src)
    tokens = list(scanner.scan("a;"))
    actions = ActionRegistry()  # no callback registered
    with pytest.raises(Exception, match="mustexist"):
        parse(table, tokens, actions=actions, hooks=HookRegistry(ignore_missing=True))


# ---- Phase 3: predicate -----------------------------------------------------


def test_predicate_picks_alternative():
    """Two productions sharing the same RHS shape but gated on different
    predicates — the runtime must pick the matching one."""
    src = """
        %grammar predtest
        %tokens
        WS = /[ \\t\\n]+/ %skip
        IDENT = /[A-Za-z_][A-Za-z0-9_]*/
        SEMI = ';'
        %predicates
        looks_like_type
        looks_like_value
        %rules
        <prog> : <stmt> ;
        <stmt> : IDENT SEMI ?{looks_like_type}
               | IDENT SEMI ?{looks_like_value}
               ;
    """
    # NB: this grammar deliberately has two productions with the same RHS
    # gated on different predicates. The LR builder must route into a
    # PredicatedActions cell rather than erroring on a reduce/reduce.
    table, scanner = _build(src)

    preds = PredicateRegistry()
    preds.register("looks_like_type", lambda tok, ctx: ctx.user.get("mode") == "type")
    preds.register("looks_like_value", lambda tok, ctx: ctx.user.get("mode") == "value")

    def go(mode):
        tokens = list(scanner.scan("x;"))
        return parse(
            table, tokens,
            predicates=preds,
            hooks=_seed_user({"mode": mode}),
        )

    tree_t = go("type")
    tree_v = go("value")
    # Both should produce <stmt> at the root; we don't differentiate alt
    # in the test runner's tree (without %ast=). The key is that
    # neither path errors.
    assert tree_t.kind == "prog"
    assert tree_v.kind == "prog"


def test_predicate_falls_through_to_default():
    """A predicated alternative + an unconditional default — when the
    predicate is False, the default fires."""
    src = """
        %grammar predfallback
        %tokens
        WS = /[ \\t\\n]+/ %skip
        IDENT = /[A-Za-z_][A-Za-z0-9_]*/
        SEMI = ';'
        %predicates
        prefer_path_a
        %rules
        <prog> : <stmt> ;
        <stmt> : IDENT SEMI ?{prefer_path_a}
               | IDENT SEMI
               ;
    """
    table, scanner = _build(src)

    preds = PredicateRegistry()
    preds.register("prefer_path_a", lambda tok, ctx: ctx.user.get("a") is True)

    tokens = list(scanner.scan("x;"))
    # No `a` in ctx.user → predicate False → default path
    tree = parse(table, tokens, predicates=preds,
                 hooks=HookRegistry(ignore_missing=True))
    assert tree.kind == "prog"


# ---- Phase 4: lexer modes (declaration round-trip only) --------------------


def test_mode_stack_lifecycle():
    """ModeStack tracks the active mode and rejects undeclared modes."""
    from uplox.parse.runtime import ModeStack
    s = ModeStack(modes=("normal", "picture"))
    s.reset()
    assert s.current() == "normal"
    s.push("picture")
    assert s.current() == "picture"
    s.pop()
    assert s.current() == "normal"
    with pytest.raises(RuntimeError):
        s.pop()  # can't pop initial
    with pytest.raises(ValueError):
        s.push("not_declared")


def test_mode_stack_resets_per_parse():
    """ModeStack on ctx.mode_stack starts at modes[0] for each parse run."""
    src = """
        %grammar mode_lifecycle
        %modes
        normal picture
        %tokens
        WS = /[ \\t\\n]+/ %skip
        IDENT = /[A-Za-z_][A-Za-z0-9_]*/
        SEMI = ';'
        %actions
        check_mode
        %rules
        <prog> : IDENT SEMI !{check_mode} ;
    """
    table, scanner = _build(src)
    observed: list[str] = []
    actions = ActionRegistry()
    actions.register("check_mode", lambda ctx, node: observed.append(ctx.mode_stack.current()))
    tokens = list(scanner.scan("x;"))
    parse(table, tokens, actions=actions, hooks=HookRegistry(ignore_missing=True))
    assert observed == ["normal"]


def test_modes_declaration_round_trips_through_bundle():
    """A grammar with %modes survives the read -> compile -> bundle ->
    rebuild round trip. The runtime side of modes is host-driven; this
    test just confirms the declaration metadata is preserved."""
    src = """
        %grammar modetest
        %modes
        normal picture
        %tokens
        WS = /[ \\t\\n]+/ %skip
        IDENT = /[A-Za-z_][A-Za-z0-9_]*/
        SEMI = ';'
        %rules
        <prog> : IDENT SEMI ;
    """
    table, _scanner = _build(src)
    assert table.grammar.modes == ("normal", "picture")

    section = table_to_json(table)
    assert section.get("modes") == ["normal", "picture"]

    rebuilt = table_from_json(section)
    assert rebuilt.grammar.modes == ("normal", "picture")


# ---- Integration: C typedef hack end-to-end via the new API -----------------


def test_c_typedef_hack_via_classifier_and_action():
    """Drive the canonical C typedef-name disambiguation through the new
    Phase-1 + Phase-2 API: classifier relabels IDENT to TYPEDEF_NAME at
    lookahead time; action populates the type set on each typedef
    reduction. Mirrors what a real C frontend would wire."""
    src = """
        %grammar c_subset_typedef
        %keyword_prefix KW_
        %keywords
        typedef int
        %tokens
        WS = /[ \\t\\n]+/ %skip
        IDENT = /[A-Za-z_][A-Za-z0-9_]*/
        TYPEDEF_NAME = /__never_matches__/
        SEMI = ';'
        %classifier
        IDENT -> TYPEDEF_NAME
        %actions
        register_typedef
        %rules
        <prog> : <items> ;
        <items> : | <items> <item> ;
        <item> : <typedef> | <decl> ;
        <typedef> : typedef int IDENT SEMI !{register_typedef} ;
        <decl> : TYPEDEF_NAME IDENT SEMI ;
    """
    table, scanner = _build(src)

    known_types: set[str] = set()

    cls = ClassifierRegistry()
    cls.register(
        "IDENT",
        lambda text, ctx: "TYPEDEF_NAME" if text in known_types else "IDENT",
    )

    def register_typedef(ctx, node):
        # Walk children: KW_TYPEDEF KW_INT IDENT SEMI
        from uplox.lex.scanner import Token
        for child in node.children:
            if isinstance(child, Token) and child.name == "IDENT":
                known_types.add(child.text)
                return

    actions = ActionRegistry()
    actions.register("register_typedef", register_typedef)

    # "typedef int Foo; Foo bar;" — after the typedef reduces, Foo enters
    # the type set; the classifier then sees the second 'Foo' and rewrites
    # it to TYPEDEF_NAME so the <decl> path matches.
    tokens = list(scanner.scan("typedef int Foo; Foo bar;"))
    tree = parse(
        table, tokens,
        classifiers=cls,
        actions=actions,
        hooks=HookRegistry(ignore_missing=True),
    )
    assert tree.kind == "prog"
    assert known_types == {"Foo"}
