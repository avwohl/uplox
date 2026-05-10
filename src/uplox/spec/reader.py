"""Recursive-descent reader for ``.uplox`` grammar source.

This is the bootstrap reader. It is hand-written so uplox can be built without
already having a working uplox-generated parser. Once the LR core is stable,
uplox's own grammar will be re-expressed as a ``.uplox`` file and parsed by a
uplox-generated front-end (the self-hosting milestone).

Syntax summary
--------------

* Token literals use single quotes: ``LPAREN = '('``.
* Non-terminals are written ``<name>`` everywhere — both LHS and RHS.
* Bare identifiers on rule RHS are terminal names (declared tokens or
  synthesised keywords).
* ``%keyword_prefix <prefix>`` sets the prefix for synthesised keyword
  tokens (default empty).
* ``%keywords`` lists keyword spellings, whitespace-separated. Each entry
  ``KW`` is synthesised as a token ``<prefix>KW`` whose literal is ``KW``;
  bare ``KW`` on rule RHS resolves to that token.
* Identifier names (rules, terminals, keywords) are not subject to a case
  rule. Case is no longer the terminal/non-terminal signal — the angle
  brackets are.

Diagnostics
-----------

The reader raises :class:`ReaderError` with ``file:line:column`` on the first
syntax error. Error messages name what was expected so a typo is obvious from
the message alone.
"""

from __future__ import annotations

import re

from .ir import GrammarIR, HookDecl, Position, Production, Rule, Symbol, TokenDecl

_HOOK_WHENS = ("pre_shift", "pre_reduce", "post_reduce", "on_error")


class ReaderError(Exception):
    pass


# ---- Tokeniser-light: line-oriented scanner over the source text -------------


def _strip_comment(line: str) -> str:
    """Strip a ``#``-introduced comment from ``line``, respecting quoted strings.

    Comments inside ``'…'`` or ``/…/`` (regex) are preserved. The DSL has no
    multi-character escape that would interact with this, so a simple flag
    suffices.
    """
    out = []
    in_str = False
    in_re = False
    i = 0
    while i < len(line):
        c = line[i]
        if c == "\\" and i + 1 < len(line):
            out.append(c)
            out.append(line[i + 1])
            i += 2
            continue
        if not in_re and c == "'":
            in_str = not in_str
        elif not in_str and c == "/":
            in_re = not in_re
        elif not in_str and not in_re and c == "#":
            break
        out.append(c)
        i += 1
    return "".join(out).rstrip()


# ---- Top-level reader --------------------------------------------------------


_SECTIONS = ("grammar", "options", "tokens", "hooks", "rules", "keywords", "shift")
_SECTION_RE = re.compile(r"\s*%(" + "|".join(_SECTIONS) + r")\b\s*(.*)$")
_KEYWORD_PREFIX_RE = re.compile(r"\s*%keyword_prefix\s+(\S+)\s*$")


def read_source(text: str, filename: str = "<source>") -> GrammarIR:
    """Parse a ``.uplox`` source string into a :class:`GrammarIR`."""
    raw_lines = text.splitlines()
    lines: list[tuple[int, str]] = []
    for lineno, raw in enumerate(raw_lines, start=1):
        stripped = _strip_comment(raw)
        if stripped.strip():
            lines.append((lineno, stripped))

    if not lines:
        raise ReaderError(f"{filename}: empty source")

    # First non-blank, non-comment line must declare the grammar name.
    head_lineno, head = lines[0]
    m = re.match(r"\s*%grammar\s+([A-Za-z_][A-Za-z0-9_]*)\s*$", head)
    if not m:
        raise ReaderError(
            f"{filename}:{head_lineno}: expected `%grammar <name>` as the first directive"
        )
    ir = GrammarIR(name=m.group(1), source_file=filename)

    # Walk remaining lines, dispatching on section markers.
    section: str | None = None
    section_buf: list[tuple[int, str]] = []

    def flush(filename: str = filename) -> None:
        nonlocal section, section_buf
        if section is None:
            return
        if section == "options":
            _parse_options(ir, section_buf, filename)
        elif section == "tokens":
            _parse_tokens(ir, section_buf, filename)
        elif section == "hooks":
            _parse_hooks(ir, section_buf, filename)
        elif section == "keywords":
            _parse_keywords(ir, section_buf, filename)
        elif section == "shift":
            _parse_shift(ir, section_buf, filename)
        elif section == "rules":
            text = "\n".join(t for _l, t in section_buf)
            ir.options["__rules_text__"] = text
            base_line = section_buf[0][0] if section_buf else 0
            _parse_rules(ir, text, base_line, filename)
        section = None
        section_buf = []

    for lineno, line in lines[1:]:
        # %keyword_prefix is a one-shot directive, not a section.
        kp = _KEYWORD_PREFIX_RE.match(line)
        if kp:
            flush()
            if ir.keyword_prefix:
                raise ReaderError(
                    f"{filename}:{lineno}: duplicate `%keyword_prefix` directive"
                )
            ir.keyword_prefix = kp.group(1)
            continue

        sm = _SECTION_RE.match(line)
        if sm:
            directive = sm.group(1)
            rest = sm.group(2).strip()
            if directive == "grammar":
                raise ReaderError(
                    f"{filename}:{lineno}: duplicate `%grammar` directive"
                )
            flush()
            section = directive
            if rest:
                section_buf.append((lineno, rest))
            continue
        if section is None:
            raise ReaderError(
                f"{filename}:{lineno}: stray text outside any section: {line.strip()!r}"
            )
        section_buf.append((lineno, line))

    flush()

    # Default start symbol = first rule's LHS, if not set in options. We do not
    # parse rules here — the LR builder fills this in once it parses the rules
    # text, but if the user set %options start = X we keep that.
    return ir


def read_file(path: str) -> GrammarIR:
    with open(path, "r", encoding="utf-8") as fh:
        return read_source(fh.read(), filename=path)


# ---- Section parsers ---------------------------------------------------------


_KNOWN_OPTIONS = {"start", "prefix_override"}


def _parse_options(ir: GrammarIR, lines: list[tuple[int, str]], filename: str) -> None:
    for lineno, line in lines:
        if "=" not in line:
            raise ReaderError(
                f"{filename}:{lineno}: `%options` line must be `key = value`"
            )
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if key not in _KNOWN_OPTIONS:
            raise ReaderError(
                f"{filename}:{lineno}: unknown option {key!r}; "
                f"known options: {sorted(_KNOWN_OPTIONS)}"
            )
        ir.options[key] = value
        if key == "start":
            ir.start_symbol = value


_TOKEN_LINE = re.compile(
    r"""^\s*
        (?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*=\s*
        (?:
          /(?P<re>(?:\\.|[^/\\])*)/        # /regex/
          |
          '(?P<lit>(?:\\.|[^'\\])*)'       # 'literal'
        )
        (?:\s+%balanced=
          '(?P<bal>(?:\\.|[^'\\])*)'       # %balanced='<close>'
        )?
        (?:\s+(?P<skip>%skip))?
        \s*$""",
    re.VERBOSE,
)


def _parse_tokens(ir: GrammarIR, lines: list[tuple[int, str]], filename: str) -> None:
    for lineno, line in lines:
        m = _TOKEN_LINE.match(line)
        if not m:
            raise ReaderError(
                f"{filename}:{lineno}: malformed token declaration. "
                f"Expected NAME = /regex/ or NAME = 'literal' optionally followed by %skip"
            )
        name = m.group("name")
        balanced = _unquote_literal(m.group("bal")) if m.group("bal") is not None else None
        if balanced is not None and len(balanced) != 1:
            raise ReaderError(
                f"{filename}:{lineno}: %balanced= takes a single-character close "
                f"delimiter, got {balanced!r}"
            )
        decl = TokenDecl(
            name=name,
            pattern=m.group("re"),
            literal=_unquote_literal(m.group("lit")) if m.group("lit") is not None else None,
            skip=m.group("skip") is not None,
            balanced_close=balanced,
            position=Position(filename, lineno, 1),
        )
        ir.tokens.append(decl)


def _unquote_literal(raw: str) -> str:
    """Process a quoted literal's escape sequences."""
    out = []
    i = 0
    while i < len(raw):
        c = raw[i]
        if c == "\\" and i + 1 < len(raw):
            esc = raw[i + 1]
            mapping = {"n": "\n", "t": "\t", "r": "\r", "\\": "\\", "'": "'", '"': '"'}
            if esc in mapping:
                out.append(mapping[esc])
                i += 2
                continue
        out.append(c)
        i += 1
    return "".join(out)


def _parse_hooks(ir: GrammarIR, lines: list[tuple[int, str]], filename: str) -> None:
    for lineno, line in lines:
        parts = line.split()
        if len(parts) != 2:
            raise ReaderError(
                f"{filename}:{lineno}: hook line must be `<name> <when>` (one of {_HOOK_WHENS})"
            )
        name, when = parts
        if when not in _HOOK_WHENS:
            raise ReaderError(
                f"{filename}:{lineno}: unknown hook event {when!r}; expected one of {_HOOK_WHENS}"
            )
        ir.hooks.append(HookDecl(name=name, when=when, position=Position(filename, lineno, 1)))


_KEYWORD_NAME_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def _parse_shift(ir: GrammarIR, lines: list[tuple[int, str]], filename: str) -> None:
    """Parse a ``%shift`` section: whitespace-separated terminal token names,
    possibly across multiple lines. Each listed terminal is added to
    ``ir.shift_terminals``; the LR-table builder later resolves shift/reduce
    conflicts on these terminals in favour of shift, silently.

    This is the yacc-style ``%nonassoc``-with-shift-preference escape hatch
    for cases where the grammar's intent is unambiguous but LALR(1) lookahead
    is one token short — the canonical example is the dangling-else
    ambiguity, where shifting ``ELSE`` is always the right answer."""
    for lineno, line in lines:
        for word in line.split():
            if not _KEYWORD_NAME_RE.fullmatch(word):
                raise ReaderError(
                    f"{filename}:{lineno}: %shift entry {word!r} is not a valid identifier"
                )
            ir.shift_terminals.add(word)


def _parse_keywords(ir: GrammarIR, lines: list[tuple[int, str]], filename: str) -> None:
    """Parse a ``%keywords`` section: whitespace-separated bare identifiers,
    possibly across multiple lines. Synthesises one ``TokenDecl`` per name and
    records the bare->prefixed alias on the IR."""
    seen: dict[str, int] = {}
    for lineno, line in lines:
        for word in line.split():
            if not _KEYWORD_NAME_RE.fullmatch(word):
                raise ReaderError(
                    f"{filename}:{lineno}: keyword {word!r} is not a valid identifier"
                )
            if word in seen:
                raise ReaderError(
                    f"{filename}:{lineno}: keyword {word!r} already listed at line {seen[word]}"
                )
            seen[word] = lineno
            token_name = ir.keyword_prefix + word
            if any(t.name == token_name for t in ir.tokens):
                raise ReaderError(
                    f"{filename}:{lineno}: keyword {word!r} synthesises token "
                    f"{token_name!r} which collides with an existing %tokens declaration"
                )
            ir.tokens.append(
                TokenDecl(
                    name=token_name,
                    literal=word,
                    position=Position(filename, lineno, 1),
                )
            )
            ir.keyword_aliases[word] = token_name


# ---- Rules parser ------------------------------------------------------------
#
# Grammar of the rules section (informal):
#
#   rules       := rule+
#   rule        := NONTERM ':' production ('|' production)* ';'
#   production  := symbol* opt_hook opt_action
#   symbol      := IDENT | NONTERM | LITERAL
#   NONTERM     := '<' [A-Za-z_][A-Za-z0-9_]* '>'
#   IDENT       := [A-Za-z_][A-Za-z0-9_]*           (terminal name)
#   LITERAL     := '\'' (\\. | [^'\\])* '\''        (inline string literal)
#   opt_hook    := '%hook' '=' IDENT  | empty
#   opt_action  := '{' balanced '}'   | empty
#
# Action bodies are copied verbatim including embedded braces.


class _RulesLexer:
    """Single-pass tokeniser over the rules section."""

    def __init__(self, text: str, base_line: int, filename: str):
        self.src = text
        self.pos = 0
        self.line = base_line
        self.col = 1
        self.filename = filename

    def at_eof(self) -> bool:
        return self.pos >= len(self.src)

    def _advance_one(self) -> str:
        c = self.src[self.pos]
        self.pos += 1
        if c == "\n":
            self.line += 1
            self.col = 1
        else:
            self.col += 1
        return c

    def skip_ws_and_comments(self) -> None:
        while self.pos < len(self.src):
            c = self.src[self.pos]
            if c in " \t\r\n":
                self._advance_one()
            elif c == "#":
                while self.pos < len(self.src) and self.src[self.pos] != "\n":
                    self._advance_one()
            else:
                return

    def position(self) -> Position:
        return Position(self.filename, self.line, self.col)

    def peek(self) -> str:
        return self.src[self.pos] if self.pos < len(self.src) else ""

    def take(self) -> str:
        return self._advance_one()

    def take_string(self) -> tuple[str, Position]:
        """Take a single-quoted literal ``'…'``; returns the unescaped content."""
        pos = self.position()
        assert self.take() == "'"
        out = []
        while True:
            if self.at_eof():
                raise ReaderError(
                    f"{self.filename}:{pos.line}:{pos.column}: unterminated string literal"
                )
            c = self.take()
            if c == "'":
                break
            if c == "\\" and not self.at_eof():
                out.append(c)
                out.append(self.take())
                continue
            out.append(c)
        return _unquote_literal("".join(out)), pos

    def take_ident(self) -> tuple[str, Position]:
        pos = self.position()
        if not (self.peek().isalpha() or self.peek() == "_"):
            raise ReaderError(
                f"{self.filename}:{pos.line}:{pos.column}: expected identifier, "
                f"got {self.peek()!r}"
            )
        out = [self.take()]
        while self.pos < len(self.src):
            c = self.peek()
            if c.isalnum() or c == "_":
                out.append(self.take())
            else:
                break
        return "".join(out), pos

    def take_nonterm(self) -> tuple[str, Position]:
        """Take ``<ident>`` and return the bare ident."""
        pos = self.position()
        assert self.take() == "<"
        name, _ = self.take_ident()
        if self.peek() != ">":
            raise ReaderError(
                f"{self.filename}:{self.line}:{self.col}: expected '>' to close non-terminal "
                f"<{name}, got {self.peek()!r}"
            )
        self.take()
        return name, pos

    def take_balanced_braces(self) -> tuple[str, Position]:
        """Take ``{ ... }`` allowing nested braces. Strips the outer pair."""
        pos = self.position()
        assert self.take() == "{"
        depth = 1
        out = []
        while True:
            if self.at_eof():
                raise ReaderError(
                    f"{self.filename}:{pos.line}:{pos.column}: unterminated action block"
                )
            c = self.take()
            if c == "{":
                depth += 1
                out.append(c)
            elif c == "}":
                depth -= 1
                if depth == 0:
                    break
                out.append(c)
            else:
                out.append(c)
        return "".join(out).strip(), pos


def _parse_rules(ir: GrammarIR, text: str, base_line: int, filename: str) -> None:
    lex = _RulesLexer(text, base_line, filename)
    while True:
        lex.skip_ws_and_comments()
        if lex.at_eof():
            break
        rule = _parse_one_rule(lex)
        ir.rules.append(rule)
    if ir.start_symbol is None and ir.rules:
        ir.start_symbol = ir.rules[0].name


def _parse_one_rule(lex: _RulesLexer) -> Rule:
    lex.skip_ws_and_comments()
    if lex.peek() != "<":
        raise ReaderError(
            f"{lex.filename}:{lex.line}:{lex.col}: "
            f"expected `<name>` to start a rule, got {lex.peek()!r}"
        )
    name, pos = lex.take_nonterm()
    lex.skip_ws_and_comments()
    if lex.peek() != ":":
        raise ReaderError(
            f"{lex.filename}:{lex.line}:{lex.col}: expected ':' after rule name <{name}>"
        )
    lex.take()
    productions = [_parse_production(lex)]
    while True:
        lex.skip_ws_and_comments()
        c = lex.peek()
        if c == "|":
            lex.take()
            productions.append(_parse_production(lex))
        elif c == ";":
            lex.take()
            return Rule(name=name, productions=productions, position=pos)
        else:
            raise ReaderError(
                f"{lex.filename}:{lex.line}:{lex.col}: "
                f"expected '|' or ';' inside rule <{name}>, got {c!r}"
            )


def _parse_production(lex: _RulesLexer) -> Production:
    rhs: list[Symbol] = []
    hook: str | None = None
    action: str | None = None
    prod_pos = lex.position()
    while True:
        lex.skip_ws_and_comments()
        c = lex.peek()
        if not c or c in "|;":
            break
        if c == "%":
            keyword_pos = lex.position()
            lex.take()
            kw, _kw_pos = lex.take_ident()
            if kw != "hook":
                raise ReaderError(
                    f"{lex.filename}:{keyword_pos.line}:{keyword_pos.column}: "
                    f"unknown rule directive %{kw}; only %hook is supported"
                )
            lex.skip_ws_and_comments()
            if lex.peek() != "=":
                raise ReaderError(
                    f"{lex.filename}:{lex.line}:{lex.col}: expected '=' after %hook"
                )
            lex.take()
            lex.skip_ws_and_comments()
            hook_name, _ = lex.take_ident()
            hook = hook_name
            continue
        if c == "{":
            action, _ = lex.take_balanced_braces()
            continue
        if c == "'":
            literal, lit_pos = lex.take_string()
            rhs.append(Symbol(name=literal, kind="literal", position=lit_pos))
            continue
        if c == "<":
            name, sym_pos = lex.take_nonterm()
            rhs.append(Symbol(name=name, kind="nonterm", position=sym_pos))
            continue
        if c.isalpha() or c == "_":
            name, sym_pos = lex.take_ident()
            rhs.append(Symbol(name=name, kind="term", position=sym_pos))
            continue
        raise ReaderError(
            f"{lex.filename}:{lex.line}:{lex.col}: unexpected character {c!r} in production"
        )
    return Production(rhs=rhs, action=action, hook=hook, position=prod_pos)
