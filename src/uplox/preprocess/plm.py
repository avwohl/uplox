"""PL/M-80 preprocessor: macro expansion and ``$``-directive handling.

Runs ``examples/plm_pre.uplox`` over a raw PL/M source string and returns
a :class:`PreprocessResult` containing the transformed source (ready to
feed ``plm_full``), the macro table, and the list of recognised
``$``-directives.

What plm_pre actually does
--------------------------

* **LITERALLY definitions** (``NAME LITERALLY 'body'``) are recorded in
  the macro table. The definition is left in the emitted source as-is —
  ``plm_full`` accepts ``IDENT LITERALLY STRING`` as a valid
  ``decl_item`` and treats the body as opaque, so no rewriting is
  needed there.
* **EQU aliases of LITERALLY**, the bootstrap pattern
  ``EQU LITERALLY 'LITERALLY'`` and any further indirection
  (``MYALIAS LITERALLY 'LITERALLY'``), are detected and from that
  point onward the alias name lexes as the keyword. Mechanically
  identical to the C typedef-name hack: a token filter rewrites the
  IDENT to ``KW_LITERALLY`` once the alias is in the live set.
* **IDENT references later in the source** that match a recorded macro
  name are replaced by the macro body in the output.
* **``$``-directive lines** (``$Q=1``, ``$INCLUDE (:F1:CPIO.PLB)``,
  …) are removed from the output and recorded in
  :attr:`PreprocessResult.directives`. They are not valid PL/M tokens
  for plm_full, so leaving them in would break parsing.

What plm_pre does *not* do (yet)
--------------------------------

* No ``$INCLUDE`` expansion — the directive is recorded but the host
  driver decides whether to splice in the included file.
* No ``#line``-style location threading. Substituting an IDENT with a
  longer body shifts column counts. Adequate for batch compilation;
  not yet adequate for a precise IDE error reporter.

Single-pass design: forward references to a LITERALLY defined later
in the source are *not* substituted. This matches real PL/M-80
compilers, which require LITERALLY definitions to precede their first
use.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from ..lex.build import balanced_tokens, lex_from_ir
from ..lex.scanner import Scanner, Token
from ..parse.grammar import compile_grammar
from ..parse.lr1 import LRTable, build_lr1
from ..parse.runtime import HookRegistry, ParseContext, ParseNode, parse
from ..spec.reader import read_file

PLM_PRE_GRAMMAR = (
    Path(__file__).resolve().parent.parent.parent.parent / "examples" / "plm_pre.uplox"
)


@dataclass
class Directive:
    """A `$`-directive line as it appeared in the source.

    ``raw`` is the entire directive token text including the leading
    ``$``, e.g. ``"$Q=1"`` or ``"$INCLUDE (:F1:CPIO.PLB)"``.  ``line``
    is the 1-based line number of the directive in the original source.
    Hosts that care about which directive is which can match on the
    leading word; the preprocessor itself does not interpret them.
    """

    raw: str
    line: int


@dataclass
class PreprocessResult:
    transformed: str
    macros: dict[str, str] = field(default_factory=dict)
    directives: list[Directive] = field(default_factory=list)


@lru_cache(maxsize=1)
def _build_plm_pre() -> tuple[Scanner, LRTable]:
    """Build the plm_pre lexer + LR table once per process. Tiny grammar
    (~30 LR states), so caching is sufficient."""
    ir = read_file(str(PLM_PRE_GRAMMAR))
    dfa, _tokens, skip = lex_from_ir(ir)
    scanner = Scanner(
        dfa=dfa,
        skip_tokens=frozenset(skip),
        balanced=balanced_tokens(ir),
    )
    table = build_lr1(compile_grammar(ir))
    return scanner, table


def preprocess(source: str) -> PreprocessResult:
    """Run plm_pre over ``source``. Returns the transformed source plus
    the macro table built during the walk and the list of recognised
    ``$``-directives."""
    # CP/M source files conventionally end with a Ctrl-Z (0x1A) — the
    # CP/M end-of-file marker — and may carry stray 0x1A bytes elsewhere.
    # Old archived PL/M sources also occasionally hold isolated non-ASCII
    # bytes from past bit-rot or encoding mishaps. PL/M-80 itself is
    # ASCII, so drop anything outside printable ASCII + standard
    # whitespace before lexing — the main parser would choke on them
    # anyway.
    if any(c == "\x1a" or ord(c) > 127 for c in source):
        source = "".join(c for c in source if ord(c) <= 127 and c != "\x1a")
    # PL/M is case-insensitive: `procedure` / `Procedure` / `PROCEDURE`
    # are the same keyword; `foo` and `FOO` are the same identifier.
    # Real PL/M-80 compilers fold to a canonical case before parsing.
    # We do the same so plm_full's uppercase grammar matches input
    # written in any case. String literals and comments are preserved.
    source = _fold_to_upper(source)
    scanner, table = _build_plm_pre()
    tokens = scanner.scan_all(source)

    # Live alias set — names that lex as IDENT but should be treated
    # as the LITERALLY keyword from this point on. Seeded with
    # "LITERALLY" itself; grows as `X LITERALLY 'LITERALLY'` lines are
    # reduced and the post_reduce hook fires.
    aliases: set[str] = {"LITERALLY"}
    macros: dict[str, str] = {}

    def token_filter(_ctx: ParseContext, tok: Token) -> Token:
        if tok.name == "IDENT" and tok.text in aliases:
            return Token(
                name="KW_LITERALLY",
                text=tok.text,
                line=tok.line,
                column=tok.column,
                offset=tok.offset,
            )
        return tok

    def on_macro(ctx: ParseContext, payload: dict) -> None:
        # `<item> : IDENT KW_LITERALLY STRING` just reduced. The
        # post_reduce payload carries the new value — a ParseNode whose
        # children are the three RHS Tokens in source order.
        if payload["when"] != "post_reduce":
            return
        node = payload["value"]
        if not isinstance(node, ParseNode):
            return
        children = node.children
        if len(children) != 3:
            return  # defensive — only fires on the 3-child item shape
        name_tok = children[0]
        body_tok = children[2]
        if not isinstance(name_tok, Token) or not isinstance(body_tok, Token):
            return
        # The body is PL/M source to splice in at substitution time.
        # PL/M-80 is case-insensitive, so uppercase the body to match
        # plm_full's expectation of uppercase keywords. This also makes
        # the EQU-bootstrap detection (`body == "LITERALLY"`) robust to
        # source like `lit literally 'literally'` where the string
        # content was preserved at its original case by the fold pass.
        body = _strip_string_quotes(body_tok.text).upper()
        macros[name_tok.text] = body
        if body == "LITERALLY":
            aliases.add(name_tok.text)

    hooks = HookRegistry()
    hooks.register("record_macro", on_macro)

    tree = parse(table, iter(tokens), hooks=hooks, token_filter=token_filter)
    assert isinstance(tree, ParseNode), "plm_pre always returns a tree"

    # Token offsets are byte offsets into the UTF-8 encoded source —
    # the scanner converts internally before scanning. Sources with
    # stray non-ASCII bytes (e.g. ancient bit-rotted CP/M files) shift
    # byte vs. character indices. Walk in bytes and decode at the end
    # so inter-token slices stay aligned.
    state = _WalkState(source_bytes=source.encode("utf-8"), macros=macros)
    _walk(tree, state)
    state.out.append(state.source_bytes[state.cursor:])
    return PreprocessResult(
        transformed=b"".join(state.out).decode("utf-8", errors="replace"),
        macros=state.macros,
        directives=state.directives,
    )


# --- Tree walker -------------------------------------------------------------
#
# The walker preserves original source layout by slicing inter-token
# whitespace and comments straight from the input string. Each leaf token
# either passes through verbatim, gets replaced by a macro body, or is
# elided (DOLLAR_DIR). The cursor tracks how far we've consumed in the
# original source so the next inter-token slice picks up correctly.
#
# The macro table is built during the parse via the post_reduce hook
# above; the walker is purely an emit pass. This split lets the token
# filter see new aliases mid-parse — by the time the walker runs, the
# parse has already enforced macro definitions appear before their use.


@dataclass
class _WalkState:
    source_bytes: bytes
    macros: dict[str, str] = field(default_factory=dict)
    out: list[bytes] = field(default_factory=list)
    directives: list[Directive] = field(default_factory=list)
    cursor: int = 0  # byte offset in source_bytes of next un-emitted byte


def _walk(root: ParseNode | Token, st: _WalkState) -> None:
    """Iterative pre-order walk. The `<items>` chain is left-recursive,
    so a recursive walker blows the Python stack on real-size sources
    (bdos.plm has ~1000+ items). We push children onto an explicit
    stack in reverse order so the leftmost child is visited next."""
    stack: list[ParseNode | Token] = [root]
    while stack:
        node = stack.pop()
        if isinstance(node, Token):
            _emit_token(node, st, replacement=None)
            continue
        if node.kind == "item" and len(node.children) == 1:
            leaf = node.children[0]
            if isinstance(leaf, Token):
                if leaf.name == "DOLLAR_DIR":
                    st.directives.append(Directive(raw=leaf.text, line=leaf.line))
                    st.out.append(st.source_bytes[st.cursor:leaf.offset])
                    st.cursor = leaf.offset + len(leaf.text.encode("utf-8"))
                    continue
                if leaf.name == "IDENT":
                    sub = st.macros.get(leaf.text)
                    _emit_token(leaf, st, replacement=sub)
                    continue
                _emit_token(leaf, st, replacement=None)
                continue
            # leaf is a ParseNode (the <other> wrapper) — recurse into it
            stack.append(leaf)
            continue
        if node.kind == "item" and len(node.children) == 3:
            # LITERALLY definition: IDENT <KW_LITERALLY-or-alias> STRING.
            # The middle token may be a literal "LITERALLY" or an alias
            # like "EQU" that the token filter rewrote to KW_LITERALLY.
            # plm_full only recognises the literal "LITERALLY" spelling,
            # so emit "LITERALLY" in the alias case so the transformed
            # source is parseable downstream.
            name_tok, kw_tok, body_tok = node.children
            assert isinstance(name_tok, Token)
            assert isinstance(kw_tok, Token)
            assert isinstance(body_tok, Token)
            _emit_token(name_tok, st, replacement=None)
            kw_replacement = None if kw_tok.text == "LITERALLY" else "LITERALLY"
            _emit_token(kw_tok, st, replacement=kw_replacement)
            _emit_token(body_tok, st, replacement=None)
            continue
        # file / items / other / unknown:
        # push children in reverse so leftmost is visited first.
        for child in reversed(node.children):
            stack.append(child)


def _emit_token(tok: Token, st: _WalkState, *, replacement: str | None) -> None:
    """Emit ``tok`` at its source position, preceded by the original
    inter-token whitespace and comments. If ``replacement`` is given,
    that text is emitted instead of ``tok.text``.

    Operates in UTF-8 byte space so token offsets (which the scanner
    reports as byte offsets) line up with source slicing on inputs
    that contain non-ASCII bytes."""
    st.out.append(st.source_bytes[st.cursor:tok.offset])
    text = replacement if replacement is not None else tok.text
    st.out.append(text.encode("utf-8"))
    st.cursor = tok.offset + len(tok.text.encode("utf-8"))


def _fold_to_upper(source: str) -> str:
    """Upper-case all PL/M source outside of string literals (``'…'``)
    and ``/* … */`` block comments. PL/M-80 is case-insensitive at the
    language level, but the lexer pattern matches literal spellings;
    folding here lets users write keywords and identifiers in any
    case. Doubled quotes inside strings (``''``) are the PL/M escape
    for a single quote and stay quoted — the simple state machine
    treats them as a length-2 quote-then-quote sequence which closes
    and reopens the string, preserving content either way."""
    out = []
    i = 0
    n = len(source)
    in_str = False
    in_cmt = False
    while i < n:
        c = source[i]
        if in_cmt:
            out.append(c)
            if c == "*" and i + 1 < n and source[i + 1] == "/":
                out.append("/")
                i += 2
                in_cmt = False
                continue
            i += 1
            continue
        if in_str:
            out.append(c)
            if c == "'":
                in_str = False
            i += 1
            continue
        if c == "/" and i + 1 < n and source[i + 1] == "*":
            out.append("/*")
            i += 2
            in_cmt = True
            continue
        if c == "'":
            out.append(c)
            i += 1
            in_str = True
            continue
        out.append(c.upper())
        i += 1
    return "".join(out)


def _strip_string_quotes(s: str) -> str:
    """Strip the surrounding ``'…'`` from a PL/M STRING token and unescape
    PL/M's ``''`` -> ``'`` doubled-quote convention. The body is what gets
    spliced into the output where macro IDENTs are referenced."""
    if len(s) >= 2 and s.startswith("'") and s.endswith("'"):
        s = s[1:-1]
    return s.replace("''", "'")
