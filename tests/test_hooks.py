"""Hook-helper tests: scoped name tables, type tables, sync-set recovery."""

from __future__ import annotations

import pytest

from uplox.hooks import (
    HookRegistry,
    ScopedNameTable,
    SyncSetRecovery,
    TypeTable,
    scoped_table_hooks,
)
from uplox.lex.build import lex_from_ir
from uplox.lex.scanner import Scanner
from uplox.parse.grammar import compile_grammar
from uplox.parse.lr1 import build_lr1
from uplox.parse.runtime import ParseContext, parse
from uplox.spec.reader import read_source


# ---- ScopedNameTable in isolation --------------------------------------------


def test_push_and_pop_basic():
    nt = ScopedNameTable()
    nt.bind("x", 1)
    nt.push()
    nt.bind("x", 2)
    assert nt.lookup("x") == 2
    nt.pop()
    assert nt.lookup("x") == 1


def test_lookup_misses_return_none():
    nt = ScopedNameTable()
    assert nt.lookup("missing") is None


def test_pop_at_global_level_is_error():
    nt = ScopedNameTable()
    with pytest.raises(IndexError):
        nt.pop()


def test_bind_unique_detects_redefinition():
    nt = ScopedNameTable()
    nt.bind_unique("x", 1)
    with pytest.raises(NameError):
        nt.bind_unique("x", 2)
    # After pushing a new scope, the same name is allowed again.
    nt.push()
    nt.bind_unique("x", 3)
    assert nt.lookup("x") == 3


def test_depth_tracks_pushes():
    nt = ScopedNameTable()
    assert nt.depth() == 1
    nt.push()
    nt.push()
    assert nt.depth() == 3
    nt.pop()
    assert nt.depth() == 2


# ---- scoped_table_hooks driving an actual parse ------------------------------


SCOPED_GRAMMAR = """
%grammar scoped

%tokens
LBRACE = '{'
RBRACE = '}'
ID     = /[a-z]+/
SEMI   = ';'
WS     = /[ \\t\\n]+/   %skip

%rules
<prog>  : <block> ;
<block> : LBRACE <stmts> RBRACE  %hook=block_scope ;
<stmts> : <stmts> <stmt> | ;
<stmt>  : ID SEMI | <block> ;
"""


def parse_with_scoping(input_text: str):
    """Drive a parse with scope hooks; return the post-parse ScopedNameTable + a
    list of (when, depth) snapshots taken inside the hook for inspection.
    """
    ir = read_source(SCOPED_GRAMMAR)
    dfa, _t, skip = lex_from_ir(ir)
    scanner = Scanner(dfa=dfa, skip_tokens=frozenset(skip))
    table = build_lr1(compile_grammar(ir))
    assert table.conflicts == []

    registry = HookRegistry()
    scoped_table_hooks(registry, scope_on={"block_scope"})
    name_table = ScopedNameTable()
    snapshots: list[tuple[str, int]] = []

    # Wrap block_scope so we also record depth at each fire.
    inner = registry.callbacks["block_scope"]

    def wrapped(ctx: ParseContext, payload: dict):
        inner(ctx, payload)
        snapshots.append((payload.get("when"), ctx.user["scope"].depth()))

    registry.callbacks["block_scope"] = wrapped

    # Seed ctx.user["scope"] with the name table on first hook call.
    seeded = {"done": False}

    def seeder(ctx: ParseContext, _payload):
        if not seeded["done"]:
            ctx.user["scope"] = name_table
            seeded["done"] = True

    registry.register("pre_shift", seeder)

    parse(table, scanner.scan(input_text), hooks=registry)
    return name_table, snapshots


def test_scoped_hooks_balance_pushes_and_pops():
    # Three nested blocks: each push must be matched with a pop.
    nt, _snapshots = parse_with_scoping("{ a; { b; { c; } } }")
    assert nt.depth() == 1  # back to global


def test_scoped_hooks_record_pre_and_post():
    nt, snapshots = parse_with_scoping("{ a; }")
    # One block reduce -> 1 pre_reduce (push) + 1 post_reduce (pop).
    whens = [w for w, _d in snapshots]
    assert whens == ["pre_reduce", "post_reduce"]
    # Depth at pre_reduce is 2 (just pushed), at post_reduce is 1 (just popped).
    depths = [d for _w, d in snapshots]
    assert depths == [2, 1]


def test_scoped_hooks_run_inside_out_in_lr():
    # Bottom-up LR reduces innermost blocks first, so each block's pre/post
    # hook pair completes before its parent block reduces. We see one pair per
    # block, never overlapping.
    _nt, snapshots = parse_with_scoping("{ { { a; } } }")
    whens = [w for w, _d in snapshots]
    assert whens == [
        "pre_reduce", "post_reduce",   # innermost {a;}
        "pre_reduce", "post_reduce",   # middle  {{a;}}
        "pre_reduce", "post_reduce",   # outer   {{{a;}}}
    ]


# ---- TypeTable ---------------------------------------------------------------


def test_type_table_declare_and_lookup():
    tt = TypeTable()
    tt.declare("x", "int")
    assert tt.type_of("x") == "int"


def test_type_table_redeclaration_default_forbidden():
    tt = TypeTable()
    tt.declare("x", "int")
    with pytest.raises(NameError):
        tt.declare("x", "string")


def test_type_table_redeclaration_with_flag():
    tt = TypeTable()
    tt.declare("x", "int")
    tt.declare("x", "string", allow_redeclaration=True)
    assert tt.type_of("x") == "string"


# ---- SyncSetRecovery ---------------------------------------------------------


def test_sync_recovery_records_errors_up_to_threshold():
    rec = SyncSetRecovery(sync_terminals=frozenset({"SEMI"}), max_errors=2)
    assert rec.record({"line": 1}) is True
    assert rec.record({"line": 2}) is False  # threshold reached
    assert len(rec.errors) == 2


def test_sync_recovery_membership():
    rec = SyncSetRecovery(sync_terminals=frozenset({"SEMI", "RBRACE"}))
    assert rec.is_sync("SEMI")
    assert not rec.is_sync("PLUS")
