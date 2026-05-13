"""AST section serializer: shape, round-trip, determinism."""

from __future__ import annotations

import json

from uplox.spec.ast_plan import (
    AstPlan,
    FieldAssignment,
    NodeField,
    NodeKind,
    ProductionAction,
    compile_ast_plan,
)
from uplox.spec.reader import read_source
from uplox.tables import ast_from_json, ast_to_json


# ---- None <-> {} ------------------------------------------------------------


def test_none_plan_serialises_to_empty_dict():
    assert ast_to_json(None) == {}


def test_empty_dict_deserialises_to_none():
    assert ast_from_json({}) is None


# ---- Calc-style end-to-end --------------------------------------------------


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


def test_calc_section_round_trip():
    plan = compile_ast_plan(read_source(CALC_ANNOTATED))
    section = ast_to_json(plan)
    reloaded = ast_from_json(section)
    assert reloaded == plan


def test_calc_section_shape():
    plan = compile_ast_plan(read_source(CALC_ANNOTATED))
    section = ast_to_json(plan)
    assert section["drop_tokens"] == ["LPAREN", "RPAREN"]
    kind_names = [k["name"] for k in section["node_kinds"]]
    assert kind_names == ["BinOp", "NumLit"]
    # First production's action is a BinOp construction with three fields.
    first = section["production_actions"][0]
    assert first == {
        "user_index": 0,
        "kind": "BinOp",
        "fields": [
            {"from": 0, "to": "lhs"},
            {"from": 1, "to": "op"},
            {"from": 2, "to": "rhs"},
        ],
    }
    # The parenthesised-expr lift action.
    lift = [a for a in section["production_actions"] if a["kind"] == "_lift"]
    assert any(a.get("source_index") == 1 for a in lift)


def test_section_is_json_serialisable():
    plan = compile_ast_plan(read_source(CALC_ANNOTATED))
    section = ast_to_json(plan)
    # Round-trip through json.dumps to catch any non-serialisable values.
    text = json.dumps(section, sort_keys=True)
    reparsed = json.loads(text)
    assert ast_from_json(reparsed) == plan


# ---- Lists, _unwrap ---------------------------------------------------------


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


def test_list_action_round_trip():
    plan = compile_ast_plan(read_source(LIST_GRAMMAR))
    section = ast_to_json(plan)
    init = next(a for a in section["production_actions"] if a["kind"] == "_list_init")
    extend = next(a for a in section["production_actions"] if a["kind"] == "_list_extend")
    assert init["element_indices"] == [0]
    assert extend["accumulator_index"] == 0
    assert extend["element_indices"] == [2]
    assert ast_from_json(section) == plan


def test_list_field_carries_element_type():
    plan = compile_ast_plan(read_source(LIST_GRAMMAR))
    section = ast_to_json(plan)
    call = next(k for k in section["node_kinds"] if k["name"] == "Call")
    args_field = next(f for f in call["fields"] if f["name"] == "args")
    assert args_field == {"name": "args", "type": "list", "element": "expr"}


def test_list_empty_action_has_no_extra_data():
    src = """
%grammar t
%tokens
IDENT = /[A-Za-z_][A-Za-z0-9_]*/
COMMA = ','
WS    = /[ \\t\\n]+/   %skip
%ast_drop COMMA
%rules
<items> %ast=list element=<item>
       : <item>
       | <items> COMMA <item>
       |
       ;
<item>? : IDENT@n   %ast=Item ;
"""
    plan = compile_ast_plan(read_source(src))
    section = ast_to_json(plan)
    empty = next(a for a in section["production_actions"] if a["kind"] == "_list_empty")
    # Only user_index + kind, no other keys.
    assert set(empty.keys()) == {"user_index", "kind"}


def test_unwrap_serialises_source_index():
    src = """
%grammar t
%tokens
IDENT = /[A-Za-z_][A-Za-z0-9_]*/
COLON = ':'
WS    = /[ \\t\\n]+/   %skip
%ast_drop COLON
%rules
<decl>?   : IDENT@name <type_opt>@type   %ast=Decl ;
<type_opt>: COLON IDENT@n   %ast=_unwrap
          |
          ;
"""
    plan = compile_ast_plan(read_source(src))
    section = ast_to_json(plan)
    unwrap = next(a for a in section["production_actions"] if a["kind"] == "_unwrap")
    assert unwrap["source_index"] == 1
    # Decl.type field is auto-optional because <type_opt> has an empty
    # alt. The type category is "token" because the _unwrap look-through
    # resolves to the inner IDENT@n, which is a Token.
    decl = next(k for k in section["node_kinds"] if k["name"] == "Decl")
    type_field = next(f for f in decl["fields"] if f["name"] == "type")
    assert type_field == {"name": "type", "type": "token", "optional": True}


# ---- Optional flag is omitted when false (compact bundles) ------------------


def test_optional_false_is_omitted_from_field_dict():
    plan = compile_ast_plan(read_source(CALC_ANNOTATED))
    section = ast_to_json(plan)
    for kind in section["node_kinds"]:
        for f in kind["fields"]:
            # None of calc's fields reference optional non-terminals.
            assert "optional" not in f


# ---- Determinism -----------------------------------------------------------


def test_drop_tokens_sorted_deterministically():
    plan = compile_ast_plan(read_source(CALC_ANNOTATED))
    section = ast_to_json(plan)
    assert section["drop_tokens"] == sorted(section["drop_tokens"])
