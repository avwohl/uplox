"""Recursive-descent reader for ``.plox`` grammar source.

This is the bootstrap reader. It is hand-written so plox can be built without
already having a working plox-generated parser. Once the LR core is stable,
plox's own grammar will be re-expressed as a ``.plox`` file and parsed by a
plox-generated front-end (the self-hosting milestone).

Implementation status
---------------------

* ``%grammar``, ``%options``, ``%tokens``, ``%hooks``: implemented for v0.
* ``%rules``: stored verbatim as raw text in :attr:`GrammarIR.options` under the
  key ``__rules_text__`` and parsed in Phase 3 alongside the LR builder. This
  keeps Phase 2 deliverables (lexer end-to-end) testable from real ``.plox`` files
  without waiting for the full grammar parser.

Diagnostics
-----------

The reader raises :class:`ReaderError` with ``file:line:column`` on the first
syntax error. Error messages name what was expected so a typo is obvious from
the message alone.
"""

from __future__ import annotations

import re

from .ir import GrammarIR, HookDecl, Position, TokenDecl

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
            ir.options["__rules_text__"] = "\n".join(t for _l, t in section_buf)
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
        decl = TokenDecl(
            name=name,
            pattern=m.group("re"),
            literal=_unquote_literal(m.group("lit")) if m.group("lit") is not None else None,
            skip=m.group("skip") is not None,
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
