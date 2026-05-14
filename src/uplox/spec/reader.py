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
from typing import Optional

from .ir import (
    ColumnClause,
    ColumnsConfig,
    ContinuationConfig,
    GrammarIR,
    HookDecl,
    LayoutConfig,
    Position,
    Production,
    Rule,
    Symbol,
    TokenDecl,
)

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


_SECTIONS = (
    "grammar",
    "options",
    "tokens",
    "hooks",
    "rules",
    "keywords",
    "shift",
    "reduce",
    "ast_drop",
    "layout",
    "columns",
    "continuation",
)
_SECTION_RE = re.compile(r"\s*%(" + "|".join(_SECTIONS) + r")\b\s*(.*)$")
_KEYWORD_PREFIX_RE = re.compile(r"\s*%keyword_prefix\s+(\S+)\s*$")
_DEFINE_RE = re.compile(r"\s*%define\s+([A-Za-z_][A-Za-z0-9_.]*)\s+(\S.*?)\s*$")

# Known %define keys and their permitted values. ``None`` means any value is
# accepted (free-form). Listed values are the canonical spelling — case matters.
_DEFINE_VALUES: dict[str, set[str] | None] = {
    "lr.type": {"canonical-lr", "lalr", "ielr"},
}


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
        elif section == "reduce":
            _parse_reduce(ir, section_buf, filename)
        elif section == "ast_drop":
            _parse_ast_drop(ir, section_buf, filename)
        elif section == "layout":
            _parse_layout(ir, section_buf, filename)
        elif section == "columns":
            _parse_columns(ir, section_buf, filename)
        elif section == "continuation":
            _parse_continuation(ir, section_buf, filename)
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

        # %define KEY VALUE — bison-style one-shot directive. Currently
        # used only for `lr.type`; the dispatch is centralised so adding
        # future keys (api.prefix, parse.error, etc.) is a one-liner.
        dm = _DEFINE_RE.match(line)
        if dm:
            flush()
            key = dm.group(1)
            value = dm.group(2).strip()
            allowed = _DEFINE_VALUES.get(key)
            if allowed is None and key not in _DEFINE_VALUES:
                raise ReaderError(
                    f"{filename}:{lineno}: unknown %define key {key!r}; "
                    f"known keys: {sorted(_DEFINE_VALUES)}"
                )
            if allowed is not None and value not in allowed:
                raise ReaderError(
                    f"{filename}:{lineno}: %define {key} value {value!r} "
                    f"not one of {sorted(allowed)}"
                )
            if key == "lr.type":
                ir.lr_type = value
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

    # %columns and %layout are mutually exclusive — fixed-column languages
    # don't have indentation-sensitive structure, and indentation-sensitive
    # languages don't have fixed-column layout. Declaring both is a
    # configuration error.
    if ir.columns is not None and ir.layout is not None:
        raise ReaderError(
            f"{filename}: `%columns` and `%layout` are mutually exclusive — "
            f"fixed-column and indentation-sensitive lexing don't compose"
        )

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


def _parse_reduce(ir: GrammarIR, lines: list[tuple[int, str]], filename: str) -> None:
    """Parse a ``%reduce`` section: dual of ``%shift``. Each listed terminal
    is added to ``ir.reduce_terminals``; shift/reduce conflicts on these
    terminals are silently resolved in favour of the (longer) reduce.

    Use when the LALR table over-eagerly offers a shift that would extend
    a different non-terminal whose state was merged into this one — the
    reduce is what the grammar actually intends. A terminal cannot appear
    in both ``%shift`` and ``%reduce``."""
    for lineno, line in lines:
        for word in line.split():
            if not _KEYWORD_NAME_RE.fullmatch(word):
                raise ReaderError(
                    f"{filename}:{lineno}: %reduce entry {word!r} is not a valid identifier"
                )
            if word in ir.shift_terminals:
                raise ReaderError(
                    f"{filename}:{lineno}: terminal {word!r} listed in both %shift and %reduce"
                )
            ir.reduce_terminals.add(word)


def _parse_ast_drop(ir: GrammarIR, lines: list[tuple[int, str]], filename: str) -> None:
    """Parse an ``%ast_drop`` section: whitespace-separated terminal names.

    Listed terminals are stripped from every AST node's child list at
    build time. This is the v3 AST surface's punctuation-elimination
    knob — same shape as ``%shift``/``%reduce``, different semantic
    target (the AST plan, not the LR table).

    Validation that each name resolves to a declared terminal is deferred
    to the AST plan compiler; the reader only enforces the identifier
    shape so typos like ``LPAREN ;`` are caught at read time.
    """
    for lineno, line in lines:
        for word in line.split():
            if not _KEYWORD_NAME_RE.fullmatch(word):
                raise ReaderError(
                    f"{filename}:{lineno}: %ast_drop entry {word!r} is not a valid identifier"
                )
            ir.ast_drop_tokens.add(word)


_LAYOUT_KEYS = {
    "indent_token",
    "dedent_token",
    "newline_token",
    "flow_open",
    "flow_close",
    "tab_width",
    "blank_lines",
    "comment_lines",
}
_LAYOUT_BLANK_LINES = {"skip", "emit_newline"}


def _parse_layout(ir: GrammarIR, lines: list[tuple[int, str]], filename: str) -> None:
    """Parse a ``%layout`` block: ``key = value`` lines configuring the
    indentation-tracking filter. See ``docs/proposals/layout.md``.
    """
    if ir.layout is not None:
        raise ReaderError(f"{filename}: duplicate `%layout` directive")
    cfg: dict[str, object] = {}
    first_lineno: Optional[int] = None
    for lineno, line in lines:
        if first_lineno is None:
            first_lineno = lineno
        if "=" not in line:
            raise ReaderError(
                f"{filename}:{lineno}: `%layout` line must be `key = value`"
            )
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if key not in _LAYOUT_KEYS:
            raise ReaderError(
                f"{filename}:{lineno}: unknown %layout key {key!r}; "
                f"known keys: {sorted(_LAYOUT_KEYS)}"
            )
        if key in ("flow_open", "flow_close"):
            # Space-separated list of either bare terminal names or
            # quoted literals (we keep them as-is and let the lex stage
            # resolve literals to declared token names).
            entries = _split_layout_list(value, filename, lineno, key)
            cfg[key] = entries
        elif key == "tab_width":
            try:
                cfg[key] = int(value)
            except ValueError as exc:
                raise ReaderError(
                    f"{filename}:{lineno}: %layout tab_width must be an integer; got {value!r}"
                ) from exc
        elif key in ("blank_lines", "comment_lines"):
            if value not in _LAYOUT_BLANK_LINES:
                raise ReaderError(
                    f"{filename}:{lineno}: %layout {key} must be one of "
                    f"{sorted(_LAYOUT_BLANK_LINES)}; got {value!r}"
                )
            cfg[key] = value
        else:
            cfg[key] = value
    for required in ("indent_token", "dedent_token", "newline_token"):
        if required not in cfg:
            raise ReaderError(
                f"{filename}: %layout block missing required key {required!r}"
            )
    ir.layout = LayoutConfig(
        indent_token=str(cfg["indent_token"]),
        dedent_token=str(cfg["dedent_token"]),
        newline_token=str(cfg["newline_token"]),
        flow_open=list(cfg.get("flow_open", [])),  # type: ignore[arg-type]
        flow_close=list(cfg.get("flow_close", [])),  # type: ignore[arg-type]
        tab_width=int(cfg.get("tab_width", 8)),  # type: ignore[arg-type]
        blank_lines=str(cfg.get("blank_lines", "skip")),
        comment_lines=str(cfg.get("comment_lines", "skip")),
        position=Position(filename, first_lineno or 0, 1),
    )


def _split_layout_list(
    value: str, filename: str, lineno: int, key: str
) -> list[str]:
    """Split ``"'(' '[' '{' "`` or ``"LPAREN LBRACK LBRACE"`` into a list."""
    out: list[str] = []
    i = 0
    while i < len(value):
        c = value[i]
        if c.isspace():
            i += 1
            continue
        if c == "'":
            j = i + 1
            while j < len(value) and value[j] != "'":
                if value[j] == "\\" and j + 1 < len(value):
                    j += 2
                else:
                    j += 1
            if j >= len(value):
                raise ReaderError(
                    f"{filename}:{lineno}: unterminated literal in %layout {key}"
                )
            out.append(_unquote_literal(value[i + 1 : j]))
            i = j + 1
        else:
            j = i
            while j < len(value) and not value[j].isspace():
                j += 1
            out.append(value[i:j])
            i = j
    return out


_COLS_LINE_RE = re.compile(
    r"^\s*cols\s+(?P<start>\d+)(?:\s*-\s*(?P<end>\d+))?\s+(?P<rest>.*)$"
)
_COLS_WIDTH_RE = re.compile(r"^\s*width\s*=\s*(?P<n>\d+)\s*$")
_COLS_CLAUSE_KEYS = {
    "mode",
    "comment_if",
    "continuation_if",
    "continuation_if_nonblank",
    "continuation_token",
    "debug_if",
    "debug_token",
}
_COLS_MODES = {"body", "label", "areaA", "areaB", "skip"}


def _parse_columns(ir: GrammarIR, lines: list[tuple[int, str]], filename: str) -> None:
    """Parse a ``%columns`` block. Each non-``width`` line is
    ``cols <start>[-<end>] key=value [key=value ...]``. See
    ``docs/proposals/columns.md``.
    """
    if ir.columns is not None:
        raise ReaderError(f"{filename}: duplicate `%columns` directive")
    cfg = ColumnsConfig(position=Position(filename, lines[0][0] if lines else 0, 1))
    for lineno, line in lines:
        wm = _COLS_WIDTH_RE.match(line)
        if wm:
            cfg.width = int(wm.group("n"))
            continue
        m = _COLS_LINE_RE.match(line)
        if not m:
            raise ReaderError(
                f"{filename}:{lineno}: `%columns` line must be `cols N[-M] key=value ...` "
                f"or `width = N`"
            )
        start = int(m.group("start"))
        end = int(m.group("end")) if m.group("end") else start
        rest = m.group("rest").strip()
        clause = ColumnClause(
            col_start=start,
            col_end=end,
            position=Position(filename, lineno, 1),
        )
        # rest is a sequence of key=value pairs, separated by whitespace,
        # with values being either quoted literals or bare identifiers.
        for key, value in _parse_kv_pairs(rest, filename, lineno):
            if key not in _COLS_CLAUSE_KEYS:
                raise ReaderError(
                    f"{filename}:{lineno}: unknown %columns clause key {key!r}; "
                    f"known keys: {sorted(_COLS_CLAUSE_KEYS)}"
                )
            if key == "mode":
                if value not in _COLS_MODES:
                    raise ReaderError(
                        f"{filename}:{lineno}: %columns mode {value!r} not one of "
                        f"{sorted(_COLS_MODES)}"
                    )
                clause.mode = value
            elif key == "comment_if":
                clause.comment_if = value
            elif key == "continuation_if":
                clause.continuation_if = value
            elif key == "continuation_if_nonblank":
                clause.continuation_if_nonblank = True
                clause.continuation_token = value
            elif key == "continuation_token":
                clause.continuation_token = value
            elif key == "debug_if":
                clause.debug_if = value
            elif key == "debug_token":
                clause.debug_token = value
        cfg.clauses.append(clause)
    ir.columns = cfg


def _parse_kv_pairs(
    text: str, filename: str, lineno: int
) -> list[tuple[str, str]]:
    """Parse ``key = value [key = value ...]`` with quoted-literal values
    or bare identifiers. Returns the pairs in declared order.
    """
    out: list[tuple[str, str]] = []
    i = 0
    while i < len(text):
        if text[i].isspace():
            i += 1
            continue
        # Read key.
        j = i
        while j < len(text) and (text[j].isalnum() or text[j] == "_"):
            j += 1
        if j == i:
            raise ReaderError(
                f"{filename}:{lineno}: expected key at column {i + 1} in {text!r}"
            )
        key = text[i:j]
        i = j
        while i < len(text) and text[i].isspace():
            i += 1
        if i >= len(text) or text[i] != "=":
            # Key with no value — treat as boolean flag (value = "")
            out.append((key, ""))
            continue
        i += 1  # skip '='
        while i < len(text) and text[i].isspace():
            i += 1
        if i >= len(text):
            raise ReaderError(
                f"{filename}:{lineno}: missing value for key {key!r}"
            )
        if text[i] == "'":
            j = i + 1
            while j < len(text) and text[j] != "'":
                if text[j] == "\\" and j + 1 < len(text):
                    j += 2
                else:
                    j += 1
            if j >= len(text):
                raise ReaderError(
                    f"{filename}:{lineno}: unterminated literal in value for {key!r}"
                )
            out.append((key, _unquote_literal(text[i + 1 : j])))
            i = j + 1
        else:
            j = i
            while j < len(text) and not text[j].isspace():
                j += 1
            out.append((key, text[i:j]))
            i = j
    return out


_CONTINUATION_KEYS = {
    "marker",
    "marker_token",
    "at_column",
    "applies_in_brackets",
    "preserve_position",
}


def _parse_continuation(
    ir: GrammarIR, lines: list[tuple[int, str]], filename: str
) -> None:
    """Parse a ``%continuation`` block. See ``docs/proposals/continuation.md``."""
    if ir.continuation is not None:
        raise ReaderError(f"{filename}: duplicate `%continuation` directive")
    cfg = ContinuationConfig(position=Position(filename, lines[0][0] if lines else 0, 1))
    for lineno, line in lines:
        if "=" not in line:
            raise ReaderError(
                f"{filename}:{lineno}: `%continuation` line must be `key = value`"
            )
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if key not in _CONTINUATION_KEYS:
            raise ReaderError(
                f"{filename}:{lineno}: unknown %continuation key {key!r}; "
                f"known keys: {sorted(_CONTINUATION_KEYS)}"
            )
        if key == "marker":
            # Two forms: quoted single character, or the special form
            # ``'<char>' at_column <N>`` (parsed as one entry here).
            if value.startswith("'"):
                # Find closing quote and check for `at_column N` suffix.
                end_quote = value.index("'", 1)
                cfg.marker_char = _unquote_literal(value[1:end_quote])
                rest = value[end_quote + 1 :].strip()
                if rest:
                    m = re.match(r"at_column\s+(\d+)\s*$", rest)
                    if not m:
                        raise ReaderError(
                            f"{filename}:{lineno}: expected `at_column N` after "
                            f"quoted marker; got {rest!r}"
                        )
                    cfg.at_column = int(m.group(1))
            else:
                # Bare identifier — a named token.
                cfg.marker_token = value
        elif key == "marker_token":
            cfg.marker_token = value
        elif key == "at_column":
            cfg.at_column = int(value)
        elif key == "applies_in_brackets":
            cfg.applies_in_brackets = value.lower() in ("true", "yes", "1")
        elif key == "preserve_position":
            cfg.preserve_position = value.lower() in ("true", "yes", "1")
    if cfg.marker_char is None and cfg.marker_token is None:
        raise ReaderError(
            f"{filename}: %continuation block missing `marker = '<char>'` or "
            f"`marker = <TOKEN>` line"
        )
    ir.continuation = cfg


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

    # Optional v3 LHS annotations, in order: `?` then `%ast=list element=<X>`.
    # Both are no-ops without the v3 AST surface — the rest of the pipeline
    # ignores ast_lift / ast_list_element when there are no AST annotations
    # anywhere else in the grammar.
    ast_lift = False
    ast_list_element: str | None = None
    lex.skip_ws_and_comments()
    if lex.peek() == "?":
        lex.take()
        ast_lift = True
        lex.skip_ws_and_comments()
    if lex.peek() == "%":
        directive_pos = lex.position()
        lex.take()
        kw, _ = lex.take_ident()
        if kw != "ast":
            raise ReaderError(
                f"{lex.filename}:{directive_pos.line}:{directive_pos.column}: "
                f"unknown rule LHS directive %{kw}; expected %ast=list"
            )
        lex.skip_ws_and_comments()
        if lex.peek() != "=":
            raise ReaderError(
                f"{lex.filename}:{lex.line}:{lex.col}: expected '=' after %ast on rule LHS"
            )
        lex.take()
        lex.skip_ws_and_comments()
        kind, _ = lex.take_ident()
        if kind != "list":
            raise ReaderError(
                f"{lex.filename}:{lex.line}:{lex.col}: "
                f"rule LHS %ast=<kind> only accepts %ast=list (got {kind!r}); "
                f"per-alternative kinds go on each alt"
            )
        lex.skip_ws_and_comments()
        # element=<NonTerm> required.
        ek_pos = lex.position()
        ek_name, _ = lex.take_ident()
        if ek_name != "element":
            raise ReaderError(
                f"{lex.filename}:{ek_pos.line}:{ek_pos.column}: "
                f"expected `element=<NonTerm>` after %ast=list, got {ek_name!r}"
            )
        lex.skip_ws_and_comments()
        if lex.peek() != "=":
            raise ReaderError(
                f"{lex.filename}:{lex.line}:{lex.col}: expected '=' after `element`"
            )
        lex.take()
        lex.skip_ws_and_comments()
        if lex.peek() != "<":
            raise ReaderError(
                f"{lex.filename}:{lex.line}:{lex.col}: "
                f"expected `<NonTerm>` after `element=`"
            )
        ast_list_element, _ = lex.take_nonterm()
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
            return Rule(
                name=name,
                productions=productions,
                position=pos,
                ast_lift=ast_lift,
                ast_list_element=ast_list_element,
            )
        else:
            raise ReaderError(
                f"{lex.filename}:{lex.line}:{lex.col}: "
                f"expected '|' or ';' inside rule <{name}>, got {c!r}"
            )


def _parse_production(lex: _RulesLexer) -> Production:
    rhs: list[Symbol] = []
    hook: str | None = None
    action: str | None = None
    ast_kind: str | None = None
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
            if kw == "hook":
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
            if kw == "ast":
                # %ast=Name per-alternative directive. Name is an ordinary
                # identifier (the AST kind) or the reserved spelling
                # ``_unwrap``. Validation of the name happens in the AST
                # plan compiler; the reader only enforces the syntax.
                lex.skip_ws_and_comments()
                if lex.peek() != "=":
                    raise ReaderError(
                        f"{lex.filename}:{lex.line}:{lex.col}: expected '=' after %ast"
                    )
                lex.take()
                lex.skip_ws_and_comments()
                ast_name, _ = lex.take_ident()
                if ast_kind is not None:
                    raise ReaderError(
                        f"{lex.filename}:{keyword_pos.line}:{keyword_pos.column}: "
                        f"duplicate %ast= on this alternative "
                        f"(was {ast_kind!r}, now {ast_name!r})"
                    )
                ast_kind = ast_name
                continue
            raise ReaderError(
                f"{lex.filename}:{keyword_pos.line}:{keyword_pos.column}: "
                f"unknown rule directive %{kw}; expected %hook or %ast"
            )
        if c == "{":
            action, _ = lex.take_balanced_braces()
            continue
        if c == "'":
            literal, lit_pos = lex.take_string()
            field_name = _maybe_take_field_suffix(lex)
            rhs.append(
                Symbol(name=literal, kind="literal", position=lit_pos, field_name=field_name)
            )
            continue
        if c == "<":
            name, sym_pos = lex.take_nonterm()
            field_name = _maybe_take_field_suffix(lex)
            rhs.append(
                Symbol(name=name, kind="nonterm", position=sym_pos, field_name=field_name)
            )
            continue
        if c.isalpha() or c == "_":
            name, sym_pos = lex.take_ident()
            field_name = _maybe_take_field_suffix(lex)
            rhs.append(
                Symbol(name=name, kind="term", position=sym_pos, field_name=field_name)
            )
            continue
        raise ReaderError(
            f"{lex.filename}:{lex.line}:{lex.col}: unexpected character {c!r} in production"
        )
    return Production(
        rhs=rhs, action=action, hook=hook, ast_kind=ast_kind, position=prod_pos
    )


def _maybe_take_field_suffix(lex: _RulesLexer) -> str | None:
    """If the next char is ``@``, consume it and the following identifier.

    Returns the field name, or ``None`` when no ``@`` is present. The
    ``@`` must abut the preceding symbol (no whitespace between symbol
    and ``@``) so it's unambiguous which RHS position the field belongs
    to. Spelled ``X@field`` or ``<x>@field`` or ``'+'@op``.
    """
    if lex.peek() != "@":
        return None
    at_pos = lex.position()
    lex.take()
    if not (lex.peek().isalpha() or lex.peek() == "_"):
        raise ReaderError(
            f"{lex.filename}:{at_pos.line}:{at_pos.column}: "
            f"expected identifier after '@'"
        )
    name, _ = lex.take_ident()
    return name
