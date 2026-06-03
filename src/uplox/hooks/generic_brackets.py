"""Reference host-filter implementations for the `<...>`-vs-comparison
ambiguity that csharp / java / kotlin / swift / typescript grammars
declare via their `LT_GENERIC` / `GT_GENERIC` synthetic terminals.

End-to-end parsing notes
------------------------

The filter handles the LT_GENERIC / GT_GENERIC pieces. Full end-to-end
parsing for each grammar may need additional filters:

* **csharp** and **java** also use an `IDENT_TYPE` synthetic terminal
  to distinguish "type-leading IDENT at statement-start" from
  "expression-leading IDENT". That filter does scan-ahead from each
  statement-start IDENT for a type-declaration shape (the equivalent
  of Mono's heuristic in `cs-tokenizer.cs`). Not yet shipped here.
* **csharp** additionally splits LPAREN into LPAREN_LAMBDA /
  LPAREN_CAST / LPAREN_TUPLE based on scan-ahead — also not shipped.

For grammars that only need angle-bracket disambiguation (kotlin,
swift, typescript), this filter is sufficient on its own.

The pattern follows Mono's `cs-tokenizer.cs`: pair each `<` with a
balanced `>` (counting nested `<` / `>` and ignoring `<` / `>` that
appear inside parens / brackets / braces) and, once the OUTERMOST
pairing closes, check whether the token after the matching `>` is in
the dialect's "disambiguating-set". If yes, rewrite the `<` and the
matching `>` to `LT_GENERIC` and `GT_GENERIC`. Multi-`>` tokens
(`>>` for two-level shift, `>>>` for unsigned shift in Java) get
split into multiple `GT_GENERIC` tokens when they close one or more
open generic contexts.

Usage
-----

The filter runs as a pre-pass over the entire token stream:

.. code-block:: python

    from uplox.hooks.generic_brackets import rewrite_generics

    tokens = list(scanner.scan(source))
    tokens = rewrite_generics(tokens, dialect="csharp")
    result = parse(table, tokens)

The function returns a new token list; the originals are not mutated.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from ..lex.scanner import Token


__all__ = ["DIALECTS", "Dialect", "rewrite_generics"]


@dataclass(frozen=True)
class Dialect:
    """Per-language configuration for the angle-bracket filter."""

    follow: frozenset[str]
    """Terminal names that may follow the OUTERMOST matching `>` for
    the brackets to be a generic-argument list."""

    rshift: str = "RSHIFT"
    """Terminal name for `>>` — split into two `GT_GENERIC` tokens
    when it closes two open generics."""

    urshift: str | None = None
    """Optional terminal name for `>>>` (Java's unsigned right
    shift). Split into three `GT_GENERIC` tokens when it closes
    three open generics."""

    lt_token: str = "LT"
    gt_token: str = "GT"
    lt_generic: str = "LT_GENERIC"
    gt_generic: str = "GT_GENERIC"

    kind_map: dict[str, int] | None = None
    """Optional name -> :class:`TokenKind` int map. When supplied,
    tokens renamed by this filter (the LT/GT -> LT_GENERIC/GT_GENERIC
    rewrites) get their ``kind`` updated to match the new name, so
    hosts that drive comparisons off ``tok.kind`` still see the
    rewritten token. Hosts that compare on ``tok.name`` (the legacy
    path) work either way."""


# Note: IDENT is included in the C# and Java follow sets so type-args
# followed by a declarator name (`Map<String, V> m;` /
# `Dictionary<K, V> dict;`) get recognised. The spec disambiguating
# sets don't include IDENT, but excluding it makes field / variable
# declarations fail. The tradeoff: comparison chains like
# `if (a < b > c)` in expression-only context get mis-rewritten as
# generic-call instantiations. Wrap such chains as
# `if (a < b && a > c)` (the boolean-and form is what real Java / C#
# comparison chains do anyway — `a < b > c` doesn't compile as a
# chain in either language, so this is a non-issue in practice).
_GENERIC_FOLLOW_CSHARP = frozenset({
    "LPAREN", "RPAREN", "RBRACK", "RBRACE", "COLON", "SEMI", "COMMA",
    "DOT", "QUESTION", "EQEQ", "NEQ", "PIPE", "CARET", "LOG_AND", "LOG_OR",
    "AMP", "LBRACK", "LBRACE", "EQ", "ARROW", "KW_IS", "KW_AS",
    "IDENT",
    "$",
})

_GENERIC_FOLLOW_JAVA = frozenset({
    "LPAREN", "RPAREN", "RBRACK", "RBRACE", "COLON", "SEMI", "COMMA",
    "DOT", "QUESTION", "EQEQ", "NEQ", "PIPE", "CARET", "LOG_AND", "LOG_OR",
    "AMP", "LBRACK", "LBRACE", "ARROW", "DCOLON",
    "IDENT",
    "$",
})

# Kotlin's `fun <T> name(...)` — generic params followed by the
# function name (IDENT). Include IDENT in the follow set to
# recognise this shape; same tradeoff as csharp / java.
_GENERIC_FOLLOW_KOTLIN = frozenset({
    "LPAREN", "RPAREN", "RBRACK", "COLON", "SEMI", "COMMA", "DOT",
    "QUESTION", "EQEQ", "NEQ", "EQ", "ARROW", "SAFE_DOT", "DCOLON",
    "IDENT",
    "$",
})

_GENERIC_FOLLOW_SWIFT = frozenset({
    "LPAREN", "RPAREN", "RBRACK", "RBRACE", "COLON", "SEMI", "COMMA",
    "DOT", "QUESTION", "EQEQ", "NEQ", "LOG_AND", "LOG_OR", "LBRACE",
    "EQ", "ARROW",
    "$",
})

_GENERIC_FOLLOW_TYPESCRIPT = frozenset({
    "LPAREN", "RPAREN", "RBRACK", "RBRACE", "COLON", "SEMI", "COMMA",
    "DOT", "QUESTION", "EQEQ", "NEQ", "STRICT_EQEQ", "STRICT_NEQ",
    "PIPE", "CARET", "LOG_AND", "LOG_OR", "LBRACK", "LBRACE", "EQ",
    "ARROW",
    "$",
})


DIALECTS: dict[str, Dialect] = {
    "csharp": Dialect(follow=_GENERIC_FOLLOW_CSHARP, rshift="RSHIFT"),
    "java": Dialect(follow=_GENERIC_FOLLOW_JAVA, rshift="RSHIFT", urshift="URSHIFT"),
    "kotlin": Dialect(follow=_GENERIC_FOLLOW_KOTLIN, rshift="RSHIFT"),
    "swift": Dialect(follow=_GENERIC_FOLLOW_SWIFT, rshift="RSHIFT"),
    "typescript": Dialect(follow=_GENERIC_FOLLOW_TYPESCRIPT, rshift="RSHIFT"),
}


# Tokens that, when seen at the same paren-depth as the outermost
# open `<`, indicate this can't be a generic-argument list. These are
# the same set across all dialects we cover.
_GENERIC_ABORT = frozenset({"SEMI", "RBRACE", "LBRACE"})


def rewrite_generics(
    tokens: Sequence[Token],
    *,
    dialect: str | Dialect = "csharp",
) -> list[Token]:
    """Return a new token list with generic-position `<` / `>` rewritten.

    ``dialect`` is either a dialect name (looked up in :data:`DIALECTS`)
    or a :class:`Dialect` instance.

    Algorithm: single forward pass with a stack of pending `<` opens.
    Each `>` / `>>` / `>>>` token pops one / two / three opens. When
    the stack drains to empty, the outermost open's matching close
    has been found; if the next token is in the dialect's follow-set,
    the entire match-group is confirmed and the involved `<` / `>` /
    `>>` / `>>>` tokens are scheduled for rewriting. Otherwise the
    group is abandoned (the `<` and `>` keep their original
    comparison-operator interpretation). Statement boundaries (`;`
    at the same paren depth as the outermost open, or `}` likewise)
    abandon any pending opens.
    """
    if isinstance(dialect, str):
        try:
            dialect = DIALECTS[dialect]
        except KeyError as exc:
            raise KeyError(
                f"unknown dialect {dialect!r}; known: {sorted(DIALECTS)}"
            ) from exc

    confirmed_opens: set[int] = set()
    confirmed_closes: dict[int, int] = {}

    # Tentative state during the current match-group.
    tentative_opens: list[int] = []
    tentative_closes: dict[int, int] = {}
    open_stack: list[int] = []
    # Paren depth INSIDE the tentative span — incremented on LPAREN
    # after the first tentative open `<`, decremented on RPAREN.
    # A RPAREN at depth 0 means we hit a RPAREN that matches an
    # LPAREN OUTSIDE the span (e.g. `if (a < b) x = (c >> d)` —
    # the `<` is inside `if(...)`, the `>>` is inside its own
    # `(...)`, and they were never meant to pair). Abandon.
    paren_depth = [0]

    def commit() -> None:
        confirmed_opens.update(tentative_opens)
        for idx, n in tentative_closes.items():
            confirmed_closes[idx] = confirmed_closes.get(idx, 0) + n
        tentative_opens.clear()
        tentative_closes.clear()
        open_stack.clear()
        open_paren_depths.clear()
        paren_depth[0] = 0

    def abandon() -> None:
        tentative_opens.clear()
        tentative_closes.clear()
        open_stack.clear()
        open_paren_depths.clear()
        paren_depth[0] = 0

    # Track each open's paren_depth at the time it was opened.
    open_paren_depths: list[int] = []

    def pop_n(close_idx: int, n: int) -> int:
        """Pop up to ``n`` open `<` indices from the stack — only
        when the topmost open is at the same paren_depth as the
        close. A `>` at paren_depth X cannot close an open `<`
        at a DIFFERENT paren_depth (a binary `>` inside `(...)`
        is not the matching close of an outer `<` that was opened
        at depth 0 — see
        `__is_core_convertible_v<decltype(F<X>() > G<Y>())>`).
        """
        actually = 0
        for _ in range(n):
            if not open_stack:
                break
            if open_paren_depths[-1] != paren_depth[0]:
                break
            open_stack.pop()
            open_paren_depths.pop()
            actually += 1
        if actually > 0:
            tentative_closes[close_idx] = (
                tentative_closes.get(close_idx, 0) + actually
            )
        return actually

    def follow_ok(idx_after_close: int) -> bool:
        if idx_after_close >= len(tokens):
            return "$" in dialect.follow
        return tokens[idx_after_close].name in dialect.follow

    for i, tok in enumerate(tokens):
        name = tok.name
        if name == dialect.lt_token:
            open_stack.append(i)
            open_paren_depths.append(paren_depth[0])
            tentative_opens.append(i)
            continue
        if name == "LPAREN" and open_stack:
            paren_depth[0] += 1
            continue
        if name == "RPAREN" and open_stack:
            if paren_depth[0] == 0:
                # RPAREN matches a LPAREN that was OUTSIDE the open
                # `<` span — we've exited the enclosing expression.
                # The pending `<` can't be a template open here.
                abandon()
                continue
            paren_depth[0] -= 1
            # Any opens at deeper paren_depths can no longer close
            # (their paren-group has ended). Abandon them. We pop
            # only opens whose depth > current paren_depth.
            while (open_paren_depths
                   and open_paren_depths[-1] > paren_depth[0]):
                open_stack.pop()
                open_paren_depths.pop()
                # Remove from tentative_opens too (so they don't
                # get committed).
                if tentative_opens:
                    tentative_opens.pop()
            continue
        if name == dialect.gt_token and open_stack:
            pop_n(i, 1)
            if not open_stack:
                if follow_ok(i + 1):
                    commit()
                else:
                    abandon()
            continue
        if name == dialect.rshift and open_stack:
            popped = pop_n(i, 2)
            if popped == 0:
                # `>>` could not close ANY open `<`: the topmost open
                # is at a SHALLOWER paren_depth, so this `>>` is nested
                # inside a paren group the open `<` is not part of —
                # it is a binary right-shift OPERATOR, not a generic
                # close. Ignore it (do NOT abandon the pending opens),
                # mirroring the single-`>` path. Without this,
                # `Foo<decltype(declval<S>() >> declval<T>())>` would
                # wrongly abandon the outer `Foo<...>`.
                continue
            if popped < 2:
                # `<...>>` where only ONE `<` is open — the `>>`
                # can't be both a template-close AND a binary
                # right-shift. Most real-world parses have the
                # `<` being binary comparison; abandon to keep
                # `>>` as RSHIFT. This loses the very rare
                # `Foo<Bar<int> >> 4` shape but breaks LOTS of
                # spurious template-pairings in expression-heavy
                # code.
                abandon()
                continue
            if not open_stack:
                if follow_ok(i + 1):
                    commit()
                else:
                    abandon()
            continue
        if dialect.urshift and name == dialect.urshift and open_stack:
            pop_n(i, 3)
            if not open_stack:
                if follow_ok(i + 1):
                    commit()
                else:
                    abandon()
            continue
        if name in _GENERIC_ABORT and open_stack:
            # LBRACE / SEMI / RBRACE inside a balanced paren group
            # (`(...{}...)`-style) is a temporary-object init or
            # statement-expr, not a statement boundary — don't
            # abandon. Only abandon when paren_depth is 0.
            if paren_depth[0] == 0:
                abandon()
            continue

    # Anything still pending at the end never closed — abandon.
    abandon()

    out: list[Token] = []
    kmap = dialect.kind_map
    for i, tok in enumerate(tokens):
        if i in confirmed_opens:
            out.append(_rename(tok, dialect.lt_generic, kmap))
        elif i in confirmed_closes:
            n = confirmed_closes[i]
            for _ in range(n):
                out.append(_rename(tok, dialect.gt_generic, kmap))
        else:
            out.append(tok)
    return out


def _rename(tok: Token, new_name: str, kind_map: dict[str, int] | None) -> Token:
    kind = kind_map.get(new_name, 0) if kind_map is not None else 0
    return Token(
        name=new_name,
        text=tok.text,
        line=tok.line,
        column=tok.column,
        offset=tok.offset,
        file_id=tok.file_id,
        kind=kind,
    )
