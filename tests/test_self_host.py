"""Self-host bootstrap: plox_self.plox parses real .plox files.

The plan called for self-host once the generator was stable: the .plox
DSL described as a .plox grammar, fed back through plox to produce a
parser that reads .plox files. This file is the bootstrap of that
bootstrap — it builds plox_self.plox and parses every example .plox in
the repo (action bodies stripped, since they need balanced-brace
matching that LR can't do).

What this proves:

* The plox DSL is itself LR(1) once you carve out the action-body
  exception.
* The generator handles the same DSL it was hand-bootstrapped against —
  no irreproducible bootstrap-only quirks.
* Every committed example grammar parses cleanly through the self-host
  parser, so future DSL changes have a regression net.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from plox.lex.build import lex_from_ir
from plox.lex.scanner import Scanner
from plox.parse.grammar import compile_grammar
from plox.parse.lr1 import build_lr1
from plox.parse.runtime import HookRegistry, ParseNode, parse
from plox.spec.reader import read_file


PLOX_REPO = Path(__file__).resolve().parents[1]
PLOX_SELF = PLOX_REPO / "examples" / "plox_self.plox"


@pytest.fixture(scope="module")
def self_host():
    """Build plox_self.plox once; reuse across tests."""
    ir = read_file(str(PLOX_SELF))
    dfa, _toks, skip = lex_from_ir(ir)
    scanner = Scanner(dfa=dfa, skip_tokens=frozenset(skip))
    table = build_lr1(compile_grammar(ir))
    return scanner, table


def parse_str(scanner, table, src: str) -> ParseNode:
    return parse(table, scanner.scan(src), hooks=HookRegistry(ignore_missing=True))


def strip_actions(text: str) -> str:
    """Remove `{ ... }` action bodies from a .plox file. The self-host
    grammar doesn't model action bodies (they need balanced-brace
    matching, a non-LR construct); a real host using the self-host
    grammar runs this kind of pre-pass first."""
    out = []
    i = 0
    while i < len(text):
        c = text[i]
        # Skip comments verbatim.
        if c == "#":
            while i < len(text) and text[i] != "\n":
                out.append(text[i])
                i += 1
            continue
        # Skip string literals verbatim.
        if c == '"':
            out.append(c)
            i += 1
            while i < len(text) and text[i] != '"':
                if text[i] == "\\" and i + 1 < len(text):
                    out.append(text[i])
                    out.append(text[i + 1])
                    i += 2
                    continue
                out.append(text[i])
                i += 1
            if i < len(text):
                out.append(text[i])
                i += 1
            continue
        # Skip regex literals verbatim.
        if c == "/" and i + 1 < len(text) and text[i + 1] != "/" and text[i + 1] != "*":
            out.append(c)
            i += 1
            while i < len(text) and text[i] != "/":
                if text[i] == "\\" and i + 1 < len(text):
                    out.append(text[i])
                    out.append(text[i + 1])
                    i += 2
                    continue
                out.append(text[i])
                i += 1
            if i < len(text):
                out.append(text[i])
                i += 1
            continue
        # Strip an action body: scan forward consuming balanced braces.
        if c == "{":
            depth = 1
            i += 1
            while i < len(text) and depth > 0:
                if text[i] == "{":
                    depth += 1
                elif text[i] == "}":
                    depth -= 1
                i += 1
            continue
        out.append(c)
        i += 1
    return "".join(out)


def test_plox_self_no_conflicts(self_host):
    _scanner, table = self_host
    assert table.conflicts == []


def test_plox_self_state_count_in_range(self_host):
    """52 states as written. The DSL is small; if this explodes we
    accidentally broke conflict-freeness or pulled in a costly construct."""
    _scanner, table = self_host
    assert 30 <= len(table.states) <= 200, len(table.states)


def test_grammar_decl_only(self_host):
    """The minimal valid .plox file is `%grammar <name>`. Nothing else."""
    scanner, table = self_host
    tree = parse_str(scanner, table, "%grammar calc\n")
    assert isinstance(tree, ParseNode) and tree.kind == "file"


def test_grammar_with_options(self_host):
    scanner, table = self_host
    src = "%grammar calc\n%options\nstart = expr\n"
    tree = parse_str(scanner, table, src)
    assert isinstance(tree, ParseNode)


def test_grammar_with_tokens(self_host):
    scanner, table = self_host
    src = """
%grammar calc
%tokens
WS = /[ \\t\\n]+/  %skip
NUMBER = /[0-9]+/
PLUS = "+"
"""
    tree = parse_str(scanner, table, src)
    assert isinstance(tree, ParseNode)


def test_grammar_with_rules(self_host):
    scanner, table = self_host
    src = """
%grammar calc
%tokens
NUMBER = /[0-9]+/
PLUS = "+"
%rules
expr : expr PLUS NUMBER | NUMBER ;
"""
    tree = parse_str(scanner, table, src)
    assert isinstance(tree, ParseNode)


def test_grammar_with_hooks_section(self_host):
    scanner, table = self_host
    src = """
%grammar scoped
%hooks
block_scope pre_reduce
%tokens
LBRACE = "{"
RBRACE = "}"
%rules
block : LBRACE RBRACE ;
"""
    tree = parse_str(scanner, table, src)
    assert isinstance(tree, ParseNode)


def test_per_alternative_hook_annotation(self_host):
    scanner, table = self_host
    src = """
%grammar t
%tokens
A = "a"
SEMI = ";"
%rules
decl : A SEMI %hook=record_typedef
     | SEMI
     ;
"""
    tree = parse_str(scanner, table, src)
    assert isinstance(tree, ParseNode)


def test_empty_alternative(self_host):
    """Epsilon productions like `xs : xs x | ;`."""
    scanner, table = self_host
    src = """
%grammar t
%tokens
A = "a"
%rules
xs : xs A
   |
   ;
"""
    tree = parse_str(scanner, table, src)
    assert isinstance(tree, ParseNode)


# --- real examples ---------------------------------------------------------


EXAMPLES_TO_TEST = [
    "calc.plox",
    "ambig_expr.plox",
    "scoped.plox",
    "plm_subset.plox",
    "plm_full.plox",
    "c_subset.plox",
    "plox_self.plox",  # The grammar parses *itself*.
]


@pytest.mark.parametrize("name", EXAMPLES_TO_TEST)
def test_real_example_parses_under_self_host(self_host, name):
    """Parse every committed example .plox through the self-host parser.

    Action bodies are stripped first (the bootstrap reader handles them
    natively; this grammar doesn't, by design). If a future DSL extension
    adds new syntax, this test set will surface it as a parse failure
    until plox_self.plox is updated to match."""
    path = PLOX_REPO / "examples" / name
    if not path.exists():
        pytest.skip(f"example {name} missing")
    scanner, table = self_host
    src = strip_actions(path.read_text())
    tree = parse_str(scanner, table, src)
    assert isinstance(tree, ParseNode) and tree.kind == "file"


def test_self_host_grammar_parses_its_own_definition(self_host):
    """The most direct self-host check: does plox_self.plox parse
    plox_self.plox? If yes, the DSL is genuinely closed under its own
    description (modulo the action-body carve-out)."""
    scanner, table = self_host
    src = strip_actions(PLOX_SELF.read_text())
    tree = parse_str(scanner, table, src)
    assert isinstance(tree, ParseNode) and tree.kind == "file"
