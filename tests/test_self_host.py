"""Self-host bootstrap: plox_self.plox parses real .plox files.

The plan called for self-host once the generator was stable: the .plox
DSL described as a .plox grammar, fed back through plox to produce a
parser that reads .plox files. This file is the bootstrap of that
bootstrap — it builds plox_self.plox and parses every example .plox in
the repo, action bodies and all.

Action bodies are not a regular language, so the lexer DFA can't match
them on its own. plox_self.plox uses the lexer's ``%balanced=...``
extension: the ACTION_BODY token's DFA matches just the opening ``{``,
and the runtime extends the match by counting nested ``{``/``}`` until
depth returns to zero.

What this proves:

* The plox DSL is itself LR(1).
* The generator handles the same DSL it was hand-bootstrapped against —
  no irreproducible bootstrap-only quirks.
* Every committed example grammar parses cleanly through the self-host
  parser, so future DSL changes have a regression net.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from plox.lex.build import balanced_tokens, lex_from_ir
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
    scanner = Scanner(
        dfa=dfa, skip_tokens=frozenset(skip), balanced=balanced_tokens(ir)
    )
    table = build_lr1(compile_grammar(ir))
    return scanner, table


def parse_str(scanner, table, src: str) -> ParseNode:
    return parse(table, scanner.scan(src), hooks=HookRegistry(ignore_missing=True))


def test_plox_self_no_conflicts(self_host):
    _scanner, table = self_host
    assert table.conflicts == []


def test_plox_self_state_count_in_range(self_host):
    """54 states as written (52 before ACTION_BODY landed). The DSL is
    small; if this explodes we accidentally broke conflict-freeness or
    pulled in a costly construct."""
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
    """Parse every committed example .plox through the self-host parser,
    verbatim — action bodies and all. If a future DSL extension adds new
    syntax, this test set will surface it as a parse failure until
    plox_self.plox is updated to match."""
    path = PLOX_REPO / "examples" / name
    if not path.exists():
        pytest.skip(f"example {name} missing")
    scanner, table = self_host
    tree = parse_str(scanner, table, path.read_text())
    assert isinstance(tree, ParseNode) and tree.kind == "file"


def test_self_host_grammar_parses_its_own_definition(self_host):
    """The most direct self-host check: does plox_self.plox parse
    plox_self.plox? If yes, the DSL is genuinely closed under its own
    description, action bodies included."""
    scanner, table = self_host
    tree = parse_str(scanner, table, PLOX_SELF.read_text())
    assert isinstance(tree, ParseNode) and tree.kind == "file"


def test_action_body_with_nested_braces(self_host):
    """ACTION_BODY consumes balanced `{` / `}` runs. A nested `{}` inside
    the body must not terminate the outer match early."""
    scanner, table = self_host
    src = """
%grammar t
%tokens
A = "a"
%rules
xs : A { if (cond) { do_thing(); } }
   ;
"""
    tree = parse_str(scanner, table, src)
    assert isinstance(tree, ParseNode)


def test_action_body_with_multi_line_content(self_host):
    """Action bodies can span lines; the DFA's `%balanced` extension
    consumes raw bytes, newlines included."""
    scanner, table = self_host
    src = """
%grammar t
%tokens
A = "a"
%rules
xs : A {
        $$ = $1;
        log("got it");
     }
   ;
"""
    tree = parse_str(scanner, table, src)
    assert isinstance(tree, ParseNode)


def test_unterminated_action_body_is_lex_error(self_host):
    """If a `{` opens an action body and the closing `}` never appears,
    the scanner reports a lexical error (not a parse error)."""
    from plox.lex.scanner import ScanError

    scanner, table = self_host
    src = """
%grammar t
%tokens
A = "a"
%rules
xs : A { unterminated
   ;
"""
    with pytest.raises(ScanError):
        parse_str(scanner, table, src)
