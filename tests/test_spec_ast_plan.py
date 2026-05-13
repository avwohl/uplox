"""AST plan compiler tests.

Validates :func:`uplox.spec.ast_plan.compile_ast_plan` end-to-end:
detection (AST mode vs back-compat), drop-token resolution, per-
production action classification (named kinds, ``_unwrap``, ``_lift``,
``_list_*``), field-source classification (token/node/list with
auto-optional), and the negative paths.
"""

from __future__ import annotations

import pytest

from uplox.spec.ast_plan import (
    AstPlanError,
    NodeField,
    NodeKind,
    compile_ast_plan,
)
from uplox.spec.reader import read_source


def _by_kind(plan, name):
    for nk in plan.node_kinds:
        if nk.name == name:
            return nk
    raise AssertionError(f"node kind {name!r} not in plan")


# ---- Back-compat path -------------------------------------------------------


def test_no_annotations_returns_none():
    src = """
%grammar t
%tokens
NUMBER = /[0-9]+/
WS = /[ \\t\\n]+/   %skip
%rules
<r> : NUMBER ;
"""
    plan = compile_ast_plan(read_source(src))
    assert plan is None


# ---- Calc-style: precedence ladder with ?, %ast=, @field, %ast_drop --------


CALC_ANNOTATED = """
%grammar calc

%define lr.type lalr
%options
start = expr

%tokens
NUMBER = /[0-9]+/
PLUS   = '+'
MINUS  = '-'
STAR   = '*'
SLASH  = '/'
LPAREN = '('
RPAREN = ')'
WS     = /[ \\t\\n]+/   %skip

%ast_drop
LPAREN RPAREN

%rules
<expr>?   : <expr>@lhs '+'@op <term>@rhs   %ast=BinOp
          | <expr>@lhs '-'@op <term>@rhs   %ast=BinOp
          | <term>
          ;

<term>?   : <term>@lhs '*'@op <factor>@rhs %ast=BinOp
          | <factor>
          ;

<factor>? : NUMBER@value                   %ast=NumLit
          | '(' <expr> ')'
          ;
"""


def test_calc_drop_tokens_resolved():
    plan = compile_ast_plan(read_source(CALC_ANNOTATED))
    assert plan.drop_tokens == frozenset({"LPAREN", "RPAREN"})


def test_calc_node_kinds_schema():
    plan = compile_ast_plan(read_source(CALC_ANNOTATED))
    binop = _by_kind(plan, "BinOp")
    assert binop.fields == (
        NodeField(name="lhs", type_kind="node"),
        NodeField(name="op", type_kind="token"),
        NodeField(name="rhs", type_kind="node"),
    )
    numlit = _by_kind(plan, "NumLit")
    assert numlit.fields == (NodeField(name="value", type_kind="token"),)


def test_calc_production_actions():
    plan = compile_ast_plan(read_source(CALC_ANNOTATED))
    kinds = [a.kind for a in plan.production_actions]
    # IR order: 3 alts of <expr>, 2 alts of <term>, 2 alts of <factor> = 7.
    assert kinds == ["BinOp", "BinOp", "_lift", "BinOp", "_lift", "NumLit", "_lift"]
    # Last action: `<factor> : '(' <expr> ')'` → lift the inner <expr>
    # at position 1 (LPAREN at 0 and RPAREN at 2 are dropped).
    paren = plan.production_actions[-1]
    assert paren.source_index == 1


# ---- Lists ------------------------------------------------------------------


LIST_GRAMMAR = """
%grammar t
%tokens
IDENT  = /[A-Za-z_][A-Za-z0-9_]*/
COMMA  = ','
LPAREN = '('
RPAREN = ')'
SEMI   = ';'
WS     = /[ \\t\\n]+/   %skip

%ast_drop COMMA LPAREN RPAREN SEMI

%rules
<call>  : IDENT@callee '(' <args>@args ')' SEMI    %ast=Call ;

<args> %ast=list element=<expr>
        : <expr>
        | <args> COMMA <expr>
        ;

<expr>? : IDENT@name   %ast=Ident ;
"""


def test_list_rule_init_and_extend():
    plan = compile_ast_plan(read_source(LIST_GRAMMAR))
    # productions: <call>, <args> alt1, <args> alt2, <expr>
    actions = plan.production_actions
    assert actions[0].kind == "Call"
    assert actions[1].kind == "_list_init"
    assert actions[1].element_indices == (0,)
    assert actions[2].kind == "_list_extend"
    assert actions[2].accumulator_index == 0
    assert actions[2].element_indices == (2,)  # <args> COMMA <expr> -> COMMA dropped, but raw index 2


def test_list_field_typed_as_list():
    plan = compile_ast_plan(read_source(LIST_GRAMMAR))
    call = _by_kind(plan, "Call")
    args = next(f for f in call.fields if f.name == "args")
    assert args.type_kind == "list"
    assert args.list_element_kind == "expr"
    assert args.optional is False  # list fields default to [], never None


def test_list_with_empty_alt_yields_list_empty_action():
    src = """
%grammar t
%tokens
IDENT = /[A-Za-z_][A-Za-z0-9_]*/
COMMA = ','
WS    = /[ \\t\\n]+/   %skip
%ast_drop COMMA
%rules
<args> %ast=list element=<expr>
        : <expr>
        | <args> COMMA <expr>
        |
        ;
<expr>?: IDENT@name   %ast=Ident ;
"""
    plan = compile_ast_plan(read_source(src))
    list_actions = [a.kind for a in plan.production_actions[:3]]
    assert list_actions == ["_list_init", "_list_extend", "_list_empty"]


def test_list_element_must_be_declared_rule():
    src = """
%grammar t
%tokens
IDENT = /[A-Za-z_][A-Za-z0-9_]*/
COMMA = ','
WS    = /[ \\t\\n]+/   %skip
%rules
<args> %ast=list element=<missing>
        : IDENT
        | <args> COMMA IDENT
        ;
"""
    with pytest.raises(AstPlanError, match="element <missing> is not a declared rule"):
        compile_ast_plan(read_source(src))


def test_list_shape_with_extra_non_drop_rejected():
    """Extra non-drop non-element non-self tokens are rejected — the
    list's element role is unambiguous, but stray tokens leave the
    grammar's intent unclear (separator? attribute? wrap?)."""
    src = """
%grammar t
%tokens
IDENT = /[A-Za-z_][A-Za-z0-9_]*/
EXTRA = 'extra'
WS    = /[ \\t\\n]+/   %skip
%rules
<things> %ast=list element=<thing>
        : <thing> EXTRA
        ;
<thing>?: IDENT@n   %ast=T ;
"""
    with pytest.raises(AstPlanError, match="shape not recognised"):
        compile_ast_plan(read_source(src))


def test_list_init_accepts_multi_element_shape():
    """``<multi_target> : <expr> COMMA <expr>`` in cowgol — the init
    alternative consumes two elements as the seed list."""
    src = """
%grammar t
%tokens
IDENT = /[A-Za-z_][A-Za-z0-9_]*/
COMMA = ','
WS    = /[ \\t\\n]+/   %skip
%ast_drop COMMA
%rules
<pair> %ast=list element=<atom>
       : <atom> COMMA <atom>
       | <pair> COMMA <atom>
       ;
<atom>?: IDENT@n   %ast=Atom ;
"""
    plan = compile_ast_plan(read_source(src))
    init = plan.production_actions[0]
    assert init.kind == "_list_init"
    assert init.element_indices == (0, 2)
    extend = plan.production_actions[1]
    assert extend.kind == "_list_extend"
    assert extend.accumulator_index == 0
    assert extend.element_indices == (2,)


# ---- _unwrap ----------------------------------------------------------------


UNWRAP_GRAMMAR = """
%grammar t
%tokens
IDENT = /[A-Za-z_][A-Za-z0-9_]*/
COLON = ':'
KW_var = 'var'
SEMI   = ';'
WS     = /[ \\t\\n]+/   %skip

%ast_drop COLON SEMI

%rules
<var_decl> : KW_var IDENT@name <type_opt>@type SEMI   %ast=VarDecl ;

<type_opt> : COLON <type>@type   %ast=_unwrap
           |
           ;

<type>?    : IDENT@name   %ast=NamedType ;
"""


def test_unwrap_action_passes_inner_position():
    plan = compile_ast_plan(read_source(UNWRAP_GRAMMAR))
    # Actions: <var_decl>, <type_opt> non-empty, <type_opt> empty, <type>.
    unwrap = plan.production_actions[1]
    assert unwrap.kind == "_unwrap"
    # COLON at index 0 is dropped, IDENT@type would be at index 1 — but
    # actually the type rule is referenced as <type>@type, so the inner
    # RHS for this production is COLON <type>; <type> is at index 1.
    assert unwrap.source_index == 1


def test_unwrap_makes_parent_slot_optional():
    plan = compile_ast_plan(read_source(UNWRAP_GRAMMAR))
    decl = _by_kind(plan, "VarDecl")
    type_field = next(f for f in decl.fields if f.name == "type")
    # <type_opt> has an empty alt -> the @type slot is optional.
    assert type_field.optional is True
    assert type_field.type_kind == "node"


def test_unwrap_requires_exactly_one_field():
    """Two @-tagged positions in an _unwrap production is an error."""
    src = """
%grammar t
%tokens
IDENT = /[A-Za-z_][A-Za-z0-9_]*/
COLON = ':'
WS    = /[ \\t\\n]+/   %skip
%rules
<bad>?: IDENT@a COLON IDENT@b   %ast=_unwrap ;
"""
    with pytest.raises(AstPlanError, match="exactly one @field"):
        compile_ast_plan(read_source(src))


def test_unwrap_rejects_at_field_on_dropped_token():
    src = """
%grammar t
%tokens
IDENT = /[A-Za-z_][A-Za-z0-9_]*/
COLON = ':'
WS    = /[ \\t\\n]+/   %skip
%ast_drop COLON
%rules
<bad>?: COLON@c   %ast=_unwrap ;
"""
    with pytest.raises(AstPlanError, match="@c on a dropped token"):
        compile_ast_plan(read_source(src))


# ---- ? lift errors ----------------------------------------------------------


def test_lift_without_single_surviving_child_is_error():
    src = """
%grammar t
%tokens
IDENT = /[A-Za-z_][A-Za-z0-9_]*/
PLUS  = '+'
WS    = /[ \\t\\n]+/   %skip
%rules
<r>?  : IDENT PLUS IDENT ;
"""
    with pytest.raises(AstPlanError, match="3 surviving children"):
        compile_ast_plan(read_source(src))


def test_production_without_ast_action_is_error_in_ast_mode():
    """In AST mode, an un-annotated production of a reachable rule is rejected."""
    src = """
%grammar t
%tokens
IDENT = /[A-Za-z_][A-Za-z0-9_]*/
WS    = /[ \\t\\n]+/   %skip
%ast_drop
WS
%rules
<r>   : IDENT   %ast=Ident
      | IDENT IDENT
      ;
"""
    with pytest.raises(AstPlanError, match="no %ast="):
        compile_ast_plan(read_source(src))


# ---- Named kinds: shared %ast= across productions ---------------------------


def test_shared_kind_must_agree_on_fields():
    src = """
%grammar t
%tokens
IDENT = /[A-Za-z_][A-Za-z0-9_]*/
PLUS  = '+'
MINUS = '-'
WS    = /[ \\t\\n]+/   %skip
%rules
<expr>?  : IDENT@a PLUS@op IDENT@b    %ast=BinOp
         | IDENT@x MINUS@op IDENT@b   %ast=BinOp
         ;
"""
    with pytest.raises(AstPlanError, match="inconsistent field sets"):
        compile_ast_plan(read_source(src))


def test_shared_kind_must_agree_on_field_type_category():
    src = """
%grammar t
%tokens
IDENT = /[A-Za-z_][A-Za-z0-9_]*/
WS    = /[ \\t\\n]+/   %skip
%rules
<expr>?  : IDENT@target          %ast=Ref    # @target = token
         | <expr>@target IDENT   %ast=Ref    # @target = node
         ;
"""
    with pytest.raises(AstPlanError, match="type categories disagree"):
        compile_ast_plan(read_source(src))


def test_duplicate_field_within_one_production_rejected():
    src = """
%grammar t
%tokens
IDENT = /[A-Za-z_][A-Za-z0-9_]*/
WS    = /[ \\t\\n]+/   %skip
%rules
<r>?  : IDENT@a IDENT@a   %ast=R ;
"""
    with pytest.raises(AstPlanError, match="assigned twice"):
        compile_ast_plan(read_source(src))


def test_field_on_dropped_token_in_named_kind_rejected():
    src = """
%grammar t
%tokens
IDENT = /[A-Za-z_][A-Za-z0-9_]*/
SEMI  = ';'
WS    = /[ \\t\\n]+/   %skip
%ast_drop SEMI
%rules
<r>?  : IDENT@a SEMI@s   %ast=R ;
"""
    with pytest.raises(AstPlanError, match=r"is on a %ast_drop"):
        compile_ast_plan(read_source(src))


# ---- Auto-optional ----------------------------------------------------------


def test_field_referencing_optional_rule_marked_optional():
    src = """
%grammar t
%tokens
IDENT = /[A-Za-z_][A-Za-z0-9_]*/
COLON = ':'
WS    = /[ \\t\\n]+/   %skip
%ast_drop COLON
%rules
<decl>?: IDENT@name <type_opt>@type   %ast=Decl ;

<type_opt> : COLON IDENT@n   %ast=_unwrap
           |
           ;
"""
    plan = compile_ast_plan(read_source(src))
    decl = _by_kind(plan, "Decl")
    type_field = next(f for f in decl.fields if f.name == "type")
    assert type_field.optional is True
    name_field = next(f for f in decl.fields if f.name == "name")
    assert name_field.optional is False


def test_field_referencing_non_optional_rule_not_optional():
    src = """
%grammar t
%tokens
IDENT = /[A-Za-z_][A-Za-z0-9_]*/
WS    = /[ \\t\\n]+/   %skip
%rules
<decl>?: <name>@n   %ast=Decl ;
<name>?: IDENT@v    %ast=Name ;
"""
    plan = compile_ast_plan(read_source(src))
    decl = _by_kind(plan, "Decl")
    n = next(f for f in decl.fields if f.name == "n")
    assert n.optional is False
    assert n.type_kind == "node"


# ---- Drop token resolution --------------------------------------------------


def test_drop_token_must_be_declared():
    src = """
%grammar t
%tokens
IDENT = /[A-Za-z_][A-Za-z0-9_]*/
WS    = /[ \\t\\n]+/   %skip
%ast_drop NOPE
%rules
<r>?  : IDENT@n   %ast=R ;
"""
    with pytest.raises(AstPlanError, match=r"NOPE.*not a declared terminal"):
        compile_ast_plan(read_source(src))


def test_drop_token_accepts_keyword_alias():
    src = """
%grammar t
%keyword_prefix KW_
%keywords
return
%tokens
IDENT = /[A-Za-z_][A-Za-z0-9_]*/
WS    = /[ \\t\\n]+/   %skip
%ast_drop return
%rules
<r>?  : return IDENT@n   %ast=R ;
"""
    plan = compile_ast_plan(read_source(src))
    assert plan.drop_tokens == frozenset({"KW_return"})


# ---- Reachability -----------------------------------------------------------


def test_unreachable_rules_skip_validation():
    """A rule unreachable from the start symbol can have whatever annotation
    set the user wrote — validation is permissive for dead code so the grammar
    compiler's own unreachable-rule warning is the single source of truth."""
    src = """
%grammar t
%options
start = main
%tokens
IDENT = /[A-Za-z_][A-Za-z0-9_]*/
PLUS  = '+'
WS    = /[ \\t\\n]+/   %skip
%rules
<main>?  : IDENT@n   %ast=Main ;
<dead>?  : IDENT IDENT IDENT ;
"""
    # <dead> has 3 surviving children and would be a hard error if reachable;
    # being unreachable, it gets a silent placeholder action.
    plan = compile_ast_plan(read_source(src))
    assert plan is not None
    # main is the only kind because <dead> never assigns one.
    assert {nk.name for nk in plan.node_kinds} == {"Main"}


# ---- Whole-grammar smoke ----------------------------------------------------


def test_calc_full_plan_round_trip():
    """End-to-end: every production has an action; node-kind count == 2."""
    plan = compile_ast_plan(read_source(CALC_ANNOTATED))
    assert len(plan.production_actions) == 7
    assert {nk.name for nk in plan.node_kinds} == {"BinOp", "NumLit"}
    # Every action references a valid kind or runtime tag.
    valid = {nk.name for nk in plan.node_kinds} | {
        "_lift", "_unwrap", "_list_init", "_list_extend", "_list_empty"
    }
    for a in plan.production_actions:
        assert a.kind in valid, f"unknown action kind {a.kind!r}"
