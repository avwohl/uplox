"""GrammarIR -> DFA pipeline glue.

Takes the token declarations from a parsed ``.uplox`` file and produces a
minimised DFA together with the metadata needed to fill the lex section of the
JSON bundle.

Literal tokens (``"…"``) are lowered to a regex by escaping their characters,
so the rest of the pipeline only ever sees regex source.
"""

from __future__ import annotations

import re

from ..spec.ir import GrammarIR
from .dfa import DFA, from_nfa, minimise
from .nfa import build as build_nfa
from .regex import parse as regex_parse


_REGEX_META = set(r".*+?|()[]{}\\^$")


def _literal_to_regex(literal: str) -> str:
    """Escape every regex metachar in ``literal`` so the parser sees an exact match."""
    return "".join("\\" + c if c in _REGEX_META else c for c in literal)


def lex_from_ir(ir: GrammarIR) -> tuple[DFA, list[str], list[str]]:
    """Build the lexer DFA from ``ir.tokens``.

    Returns ``(minimised_dfa, token_names_in_decl_order, skip_token_names)``.
    Token order is exactly :attr:`GrammarIR.tokens` order — that is also the
    priority used by the DFA's tie-break (earlier declaration wins).

    Balanced-bracket tokens (those with ``balanced_close``) are baked into the
    DFA only via their opening pattern; the runtime extension that consumes
    the matching close lives in :class:`uplox.lex.scanner.Scanner`. Use
    :func:`balanced_tokens` to recover the open/close map from the IR.
    """
    if not ir.tokens:
        raise ValueError(f"grammar {ir.name!r} declares no tokens")

    from .nfa import combine
    pieces = []
    for tok in ir.tokens:
        if tok.literal is not None:
            pattern = _literal_to_regex(tok.literal)
        elif tok.pattern is not None:
            pattern = tok.pattern
        else:
            raise ValueError(
                f"token {tok.name!r} has neither pattern nor literal"
            )
        nfa = build_nfa(regex_parse(pattern), token_name=tok.name)
        pieces.append((tok.name, nfa))

    combined = combine(pieces)
    dfa = minimise(from_nfa(combined))
    tokens = [t.name for t in ir.tokens]
    skip = [t.name for t in ir.tokens if t.skip]
    return dfa, tokens, skip


def balanced_tokens(ir: GrammarIR) -> dict[str, str]:
    """Map balanced-token name to its closing-delimiter character.

    The opening delimiter is the first byte of whatever the DFA matches for
    that token; the scanner reads it back from the matched text rather than
    storing it separately.
    """
    return {t.name: t.balanced_close for t in ir.tokens if t.balanced_close is not None}
