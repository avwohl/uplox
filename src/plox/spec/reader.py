"""Recursive-descent reader for ``.plox`` grammar source.

This is the bootstrap reader. It is hand-written so plox can be built without
already having a working plox-generated parser. Once the LR core is stable,
plox's own grammar will be re-expressed as a ``.plox`` file and parsed by a
plox-generated front-end (the self-hosting milestone).

Implementation status
---------------------

All v0 sections are implemented: ``%grammar``, ``%options``, ``%tokens``,
``%hooks``, ``%rules``. The raw rules text remains accessible via the
``__rules_text__`` key in :attr:`GrammarIR.options` for diagnostics that want to
quote the source verbatim.

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

    Comments inside ``"…"`` or ``/…/`` (regex) are preserved. The DSL has no
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
        if not in_re and c == '"':
            in_str = not in_str
        elif not in_str and c == "/":
            in_re = not in_re
        elif not in_str and not in_re and c == "#":
            break
        out.append(c)
        i += 1
    return "".join(out).rstrip()


# ---- Top-level reader --------------------------------------------------------


def read_source(text: str, filename: str = "<source>") -> GrammarIR:
    """Parse a ``.plox`` source string into a :class:`GrammarIR`."""
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
        elif section == "rules":
            text = "\n".join(t for _l, t in section_buf)
            ir.options["__rules_text__"] = text
            base_line = section_buf[0][0] if section_buf else 0
            _parse_rules(ir, text, base_line, filename)
        section = None
        section_buf = []

    for lineno, line in lines[1:]:
        sm = re.match(r"\s*%(grammar|options|tokens|hooks|rules)\b\s*(.*)$", line)
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
        (?P<name>[A-Z][A-Z0-9_]*)\s*=\s*
        (?:
          /(?P<re>(?:\\.|[^/\\])*)/        # /regex/
          |
          "(?P<lit>(?:\\.|[^"\\])*)"       # "literal"
        )
        (?:\s+%balanced=
          "(?P<bal>(?:\\.|[^"\\])*)"       # %balanced="<close>"
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
                f"Expected NAME = /regex/ or NAME = \"literal\" optionally followed by %skip"
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
    """Process a quoted literal's escape sequences. Mirrors regex.py escapes."""
    out = []
    i = 0
    while i < len(raw):
        c = raw[i]
        if c == "\\" and i + 1 < len(raw):
            esc = raw[i + 1]
            mapping = {"n": "\n", "t": "\t", "r": "\r", "\\": "\\", '"': '"', "'": "'"}
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


# ---- Rules parser ------------------------------------------------------------
#
# Grammar of the rules section (informal):
#
#   rules       := rule+
#   rule        := IDENT ':' production ('|' production)* ';'
#   production  := symbol* opt_hook opt_action
#   symbol      := IDENT | UPPER | STRING_LITERAL
#   opt_hook    := '%hook' '=' IDENT  | empty
#   opt_action  := '{' balanced '}'   | empty
#   STRING_LITERAL := '"' (\\. | [^"\\])* '"'
#
# String literals are anonymous tokens; the IR records them as Symbols whose
# name is the same as the literal (resolution against `%tokens` happens later).
# Action bodies are copied verbatim including embedded braces.


class _RulesLexer:
    """Single-pass tokeniser over the rules section.

    Tracks line/column so error messages can point inside the rules block.
    ``base_line`` is the source-file line of the first character — set by the
    section-flush logic so positions are absolute, not section-local.
    """

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
        pos = self.position()
        assert self.take() == '"'
        out = []
        while True:
            if self.at_eof():
                raise ReaderError(
                    f"{self.filename}:{pos.line}:{pos.column}: unterminated string literal"
                )
            c = self.take()
            if c == '"':
                break
            if c == "\\" and not self.at_eof():
                out.append(c)
                out.append(self.take())
                continue
            out.append(c)
        return "".join(out), pos

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
    name, pos = lex.take_ident()
    lex.skip_ws_and_comments()
    if lex.peek() != ":":
        raise ReaderError(
            f"{lex.filename}:{lex.line}:{lex.col}: expected ':' after rule name {name!r}"
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
                f"expected '|' or ';' inside rule {name!r}, got {c!r}"
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
            # Either %hook or %prec. Only %hook in v0.
            keyword_pos = lex.position()
            lex.take()
            kw, _kw_pos = lex.take_ident()
            if kw != "hook":
                raise ReaderError(
                    f"{lex.filename}:{keyword_pos.line}:{keyword_pos.column}: "
                    f"unknown rule directive %{kw}; v0 supports only %hook"
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
        if c == '"':
            literal, lit_pos = lex.take_string()
            rhs.append(Symbol(name='"' + literal + '"', position=lit_pos))
            continue
        if c.isalpha() or c == "_":
            name, sym_pos = lex.take_ident()
            rhs.append(Symbol(name=name, position=sym_pos))
            continue
        raise ReaderError(
            f"{lex.filename}:{lex.line}:{lex.col}: unexpected character {c!r} in production"
        )
    return Production(rhs=rhs, action=action, hook=hook, position=prod_pos)
