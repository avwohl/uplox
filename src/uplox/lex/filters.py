"""Lexer-feedback filters that run between the raw scanner and the parser.

Three filters implement the directives described in
``docs/proposals/{layout,columns,continuation}.md``:

* :class:`LayoutFilter` — emits synthetic ``INDENT`` / ``DEDENT`` /
  ``NEWLINE`` terminals based on column tracking. Implements the
  ``%layout`` directive.
* :class:`ContinuationFilter` — collapses marker-plus-newline pairs into
  joined logical lines. Implements the ``%continuation`` directive.
* :class:`ColumnDispatcher` — column-range-aware outer loop wrapping
  the DFA scanner. Lives in ``scanner.py`` because it changes the
  scanner's I/O contract rather than just rewriting tokens.

The filters compose as documented: column dispatch happens during
scanning, then continuation, then layout, then any host-installed
token filter, then the parser.

Each filter takes an iterator of :class:`Token` and yields a
transformed iterator. They're pure functions of their config plus
the input stream — no side effects on the source text.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Iterable, Iterator, Optional

from ..spec.ir import ColumnsConfig, ContinuationConfig, LayoutConfig
from .scanner import Scanner, Token


def _make_token(name: str, line: int, column: int, file_id: int = 0) -> Token:
    """Construct a synthetic Token with empty text at a known position."""
    return Token(
        name=name,
        text="",
        line=line,
        column=column,
        offset=-1,
        file_id=file_id,
    )


@dataclass
class LayoutFilter:
    """Indentation-tracking filter — emits INDENT / DEDENT / NEWLINE.

    Algorithm (per ``docs/proposals/layout.md``):

    * Track a stack of indentation columns; start at ``[0]``.
    * At each non-blank, non-flow-bracketed line start, compare the new
      column to the stack top:
        - greater → emit ``INDENT``, push the new column.
        - less    → pop and emit one ``DEDENT`` per popped level until
                    the stack top equals (or is less than) the new
                    column.
        - equal   → emit ``NEWLINE`` separator (if we've consumed
                    content on the previous line).
    * Suspend everything inside flow brackets (configured by
      ``flow_open`` / ``flow_close`` token name lists).
    * Skip blank lines and comment-only lines (config switches
      ``blank_lines`` / ``comment_lines`` between ``"skip"`` and
      ``"emit_newline"``).
    * At EOF, emit a final ``NEWLINE`` (if content remains) plus one
      ``DEDENT`` for every remaining indent level above the initial 0.

    The filter takes a function ``is_comment(token) -> bool`` to decide
    whether a token line counts as comment-only; callers can wire this
    to a per-grammar predicate. Default is "no token is a comment", so
    only blank lines are skipped.
    """

    config: LayoutConfig
    is_comment: Optional[Callable[[Token], bool]] = None

    def filter(self, tokens: Iterable[Token]) -> Iterator[Token]:
        """Consume ``tokens`` and yield the augmented stream.

        ``tokens`` is the raw lexer output: tokens carry ``(line, column)``
        with no synthetic indentation markers yet. The output stream has
        INDENT / DEDENT / NEWLINE inserted at line boundaries.
        """
        # The flow_open / flow_close lists may carry either token
        # names (`LPAREN`) or literal text (`'('`). We match against
        # both `tok.name` and `tok.text` so either spelling works in
        # the directive.
        flow_open = set(self.config.flow_open)
        flow_close = set(self.config.flow_close)
        indent_token = self.config.indent_token
        dedent_token = self.config.dedent_token
        newline_token = self.config.newline_token
        is_comment = self.is_comment or (lambda _t: False)

        def is_flow_open(tok: Token) -> bool:
            return tok.name in flow_open or tok.text in flow_open

        def is_flow_close(tok: Token) -> bool:
            return tok.name in flow_close or tok.text in flow_close

        # Buffer one line's tokens at a time so we can decide whether the
        # line is blank / comment-only / content-bearing before emitting
        # any layout markers. The indent stack starts empty; the first
        # content-bearing line establishes the base level — no INDENT
        # is emitted for the document-root content (column 1 typically),
        # only for subsequent deeper nesting.
        stack: list[int] = []
        flow_depth = 0
        flow_depth_at_line_start = 0
        last_line: Optional[int] = None
        current_line: list[Token] = []
        had_content = False  # any prior non-blank line emitted

        def emit_dedent_to(col: int, line: int, column: int, file_id: int) -> Iterator[Token]:
            while len(stack) > 1 and stack[-1] > col:
                stack.pop()
                yield _make_token(dedent_token, line, column, file_id)

        def process_line() -> Iterator[Token]:
            """Flush one buffered physical line. Updates stack state."""
            nonlocal had_content
            if not current_line:
                return
            # Inside flow at line start: emit verbatim with no indent
            # tracking. We check the flow depth as-of line start, not
            # current — a line that opens and closes its own flow
            # mapping `{...}` ends at depth 0 but is still a "flow
            # line" with no block structure to track.
            if flow_depth_at_line_start > 0:
                yield from current_line
                return
            # Comment-only? Skip per config (default skip).
            non_skip = [t for t in current_line if not is_comment(t)]
            if not non_skip:
                # Blank-effective: per config, either drop or emit NEWLINE.
                if self.config.comment_lines == "emit_newline" and had_content:
                    yield _make_token(
                        newline_token,
                        current_line[0].line,
                        current_line[0].column,
                        current_line[0].file_id,
                    )
                return
            first = non_skip[0]
            col = first.column
            file_id = first.file_id
            if not stack:
                # First content line establishes the base level; no INDENT.
                stack.append(col)
            else:
                top = stack[-1]
                if col > top:
                    # NEWLINE before INDENT — matches CPython's tokenizer:
                    # `if x: NEWLINE INDENT body DEDENT`.
                    if had_content:
                        yield _make_token(newline_token, first.line, col, file_id)
                    stack.append(col)
                    yield _make_token(indent_token, first.line, col, file_id)
                elif col < top:
                    # NEWLINE before DEDENT — matches CPython.
                    if had_content:
                        yield _make_token(newline_token, first.line, col, file_id)
                    yield from emit_dedent_to(col, first.line, col, file_id)
                else:
                    if had_content:
                        yield _make_token(newline_token, first.line, col, file_id)
            for tok in current_line:
                yield tok
            had_content = True

        for tok in tokens:
            # Detect line change BEFORE applying ``tok``'s flow-depth
            # effect, so ``flow_depth_at_line_start`` reflects the depth
            # at the moment the new line begins (i.e. before its first
            # token's flow-bracket effect is counted).
            if last_line is not None and tok.line != last_line:
                yield from process_line()
                current_line = []
                last_line = tok.line
                flow_depth_at_line_start = flow_depth
            if last_line is None:
                last_line = tok.line
                flow_depth_at_line_start = flow_depth
            # Apply ``tok``'s effect on flow depth for subsequent tokens.
            if is_flow_open(tok):
                flow_depth += 1
            elif is_flow_close(tok) and flow_depth > 0:
                flow_depth -= 1
            current_line.append(tok)

        # End-of-input: flush remaining line, emit a trailing NEWLINE
        # so the parser sees `stmt NEWLINE stmt NEWLINE` even at EOF
        # (matches CPython's tokenizer behaviour — every Python
        # statement is terminated by NEWLINE, including the last one).
        # Then close any nested indent levels with DEDENTs.
        if current_line:
            yield from process_line()
        last_file_id = current_line[0].file_id if current_line else 0
        end_line = (last_line or 0) + 1
        if had_content:
            yield _make_token(newline_token, end_line, 1, last_file_id)
        # Close every nested level — pop down to (but not including) the
        # base level established by the first content line.
        while len(stack) > 1:
            stack.pop()
            yield _make_token(dedent_token, end_line, 1, last_file_id)


@dataclass
class ContinuationFilter:
    """Line-continuation filter — collapses marker+newline into joined lines.

    Algorithm (per ``docs/proposals/continuation.md``):

    * Buffer at most one token of lookbehind (the most recent token
      whose ``name`` matches the configured marker).
    * When a newline / line-break event arrives (here: when the next
      token's line differs from the buffered marker's line by exactly
      1), drop the marker — the join has happened.
    * Otherwise flush the marker as-is.

    Because the raw scanner doesn't emit a NEWLINE token by default,
    the filter detects line boundaries by comparing successive tokens'
    ``line`` attributes. The marker is suppressed if and only if the
    next non-marker token is on the following physical line.

    For the at_column form (COBOL's column-7 ``-``), the filter
    additionally requires the marker token's column to match
    ``config.at_column``.

    **Two semantic flavours:**

    * **`marker_char` (prospective)** — Python `\`, Fortran-F90 `&`,
      Bash `\`. The marker appears at the END of a line; we suppress
      it iff the next token is on the following line. Same-line
      occurrences pass through unchanged.
    * **`marker_token` (retroactive)** — Fortran-F77 column-6,
      COBOL column-7. The marker is a synthetic token emitted by the
      column dispatcher only at the right column / position, so it
      never has a non-continuation interpretation. Always
      suppressed.

    Brackets: by default, line continuation is implicit inside paren /
    bracket / brace nests, so the filter pauses when it sees a flow
    bracket. Set ``applies_in_brackets = true`` in the config to keep
    the marker active inside flow.
    """

    config: ContinuationConfig
    flow_open: frozenset[str] = frozenset()
    flow_close: frozenset[str] = frozenset()

    def filter(self, tokens: Iterable[Token]) -> Iterator[Token]:
        marker_char = self.config.marker_char
        marker_token = self.config.marker_token
        at_column = self.config.at_column
        applies_in_brackets = self.config.applies_in_brackets
        flow_depth = 0
        pending_marker: Optional[Token] = None
        # Token-marker form is always-suppress; char-marker form is
        # prospective (look-ahead one line).
        always_suppress = marker_token is not None
        # `join_target`: the logical line the continuation chain
        # collapses onto. `last_marker_physical`: the physical line
        # of the most recent suppressed marker — tokens on physical
        # line `last_marker_physical + 1` get rebased to join_target.
        # If a token appears past that line without an intervening
        # marker, the chain has ended.
        join_target: Optional[int] = None
        last_marker_physical: Optional[int] = None

        def is_marker(tok: Token) -> bool:
            if marker_token is not None and tok.name == marker_token:
                if at_column is not None and tok.column != at_column:
                    return False
                return True
            if marker_char is not None and tok.text == marker_char:
                if at_column is not None and tok.column != at_column:
                    return False
                return True
            return False

        def rebase(tok: Token) -> Token:
            return Token(
                name=tok.name,
                text=tok.text,
                line=join_target,  # type: ignore[arg-type]
                column=tok.column,
                offset=tok.offset,
                file_id=tok.file_id,
            )

        for tok in tokens:
            if tok.name in self.flow_open or tok.text in self.flow_open:
                flow_depth += 1
            elif (tok.name in self.flow_close or tok.text in self.flow_close) and flow_depth > 0:
                flow_depth -= 1
            in_flow = flow_depth > 0 and not applies_in_brackets
            # If we're past the continuation chain (a token on a
            # physical line beyond the last marker's physical+1),
            # clear the join.
            if (
                join_target is not None
                and last_marker_physical is not None
                and tok.line > last_marker_physical + 1
            ):
                join_target = None
                last_marker_physical = None
            if pending_marker is not None:
                # Char-marker form: suppress iff next token is on a
                # later line. Token-marker form: always suppressed
                # (handled below via the `continue` branch).
                if tok.line > pending_marker.line:
                    # Establish or extend the join chain. The chain's
                    # target line stays the FIRST marker's
                    # (rebased) line; subsequent markers within the
                    # chain push the physical-line boundary forward.
                    if join_target is None:
                        # The marker token itself was on physical
                        # line pending_marker.line. If a previous
                        # rebase had already moved it, use that
                        # rebased line; otherwise the marker's own
                        # line.
                        join_target = pending_marker.line
                    last_marker_physical = (
                        pending_marker.line
                        if last_marker_physical is None
                        else max(last_marker_physical, pending_marker.line)
                    )
                else:
                    yield pending_marker
                pending_marker = None
            if not in_flow and is_marker(tok):
                if always_suppress:
                    # Synthetic dispatcher-emitted token; drop it.
                    continue
                # For char-marker form, the marker may sit on a
                # rebased line — its `line` for chain-purpose is
                # the join_target. Capture its physical line in
                # last_marker_physical when the chain actually
                # extends (above).
                pending_marker = tok
                continue
            if join_target is not None:
                yield rebase(tok)
            else:
                yield tok
        if pending_marker is not None:
            if join_target is not None:
                yield rebase(pending_marker)
            else:
                yield pending_marker


@dataclass
class ColumnDispatcher:
    """Column-aware scanner wrapper for fixed-format / card-image grammars.

    Two responsibilities (see ``docs/proposals/columns.md``):

    1. **Source preprocessing.** Walk each physical line, apply the
       configured ``cols`` clauses to decide what each column range
       does. Blank out skip-mode ranges and comment lines so the
       inner DFA scanner doesn't see them; preserve column positions
       by substituting spaces.
    2. **Synthetic-token injection.** When a clause's
       ``continuation_if`` / ``continuation_if_nonblank`` /
       ``debug_if`` matches, emit the named token at the right
       position in the output stream.

    The dispatcher returns a normal :class:`Iterator[Token]` — the
    rest of the pipeline (continuation filter, layout filter, host
    filter, parser) is unchanged.

    Column boundaries are 1-indexed and inclusive on both ends, matching
    Fortran's traditional "column 1-5", "column 6", "column 7-72"
    convention.
    """

    config: ColumnsConfig
    scanner: Scanner

    def scan(self, source: str | bytes, *, file_id: int = 0) -> Iterator[Token]:
        data = (
            source.encode("utf-8", errors="surrogateescape")
            if isinstance(source, str) else bytes(source)
        )
        # Pass 1: compute per-line offsets so we can index by (line, col).
        line_starts = [0]
        for i, b in enumerate(data):
            if b == 0x0A:
                line_starts.append(i + 1)
        # Pass 2: build a transformed copy of the source where skip zones,
        # over-width columns, and comment lines have been blanked. Collect
        # synthetic-token injection points keyed by source offset.
        out = bytearray(data)
        injections: list[Token] = []
        width = self.config.width
        for line_no, line_start in enumerate(line_starts, start=1):
            line_end = line_starts[line_no] if line_no < len(line_starts) else len(data)
            # Drop trailing newline for column logic.
            content_end = line_end
            if content_end > line_start and data[content_end - 1] == 0x0A:
                content_end -= 1
            line_len = content_end - line_start
            # First scan: detect comment_if anywhere in the clause list. If
            # any clause's comment_if matches at its column range, blank
            # the whole line from that column to EOL.
            comment_from: Optional[int] = None
            for clause in self.config.clauses:
                if clause.comment_if is None:
                    continue
                # Check whether any byte in the clause's column range is in
                # the comment_if set. F77 only checks column 1; COBOL
                # checks column 7. We honour the configured range.
                for col in range(clause.col_start, clause.col_end + 1):
                    if col > line_len:
                        break
                    b = data[line_start + col - 1]
                    if chr(b) in clause.comment_if:
                        comment_from = line_start + col - 1
                        break
                if comment_from is not None:
                    break
            if comment_from is not None:
                # Blank from the comment marker to end of physical line.
                for k in range(comment_from, content_end):
                    out[k] = 0x20  # space
                continue
            # Second pass: per-clause column-range processing for non-comment lines.
            for clause in self.config.clauses:
                # Width clipping: columns beyond `width` are always skipped.
                col_lo = clause.col_start
                col_hi = min(clause.col_end, width)
                if col_lo > line_len:
                    continue
                if clause.continuation_if is not None:
                    # Match a literal character at col_lo.
                    if col_lo <= line_len:
                        b = data[line_start + col_lo - 1]
                        if chr(b) in clause.continuation_if:
                            if clause.continuation_token:
                                injections.append(
                                    Token(
                                        name=clause.continuation_token,
                                        text=chr(b),
                                        line=line_no,
                                        column=col_lo,
                                        offset=line_start + col_lo - 1,
                                        file_id=file_id,
                                    )
                                )
                            # Blank the marker so the DFA doesn't see it.
                            out[line_start + col_lo - 1] = 0x20
                if clause.continuation_if_nonblank:
                    if col_lo <= line_len:
                        b = data[line_start + col_lo - 1]
                        # F77: non-space, non-zero counts.
                        if b not in (0x20, 0x09, 0x30):
                            if clause.continuation_token:
                                injections.append(
                                    Token(
                                        name=clause.continuation_token,
                                        text=chr(b),
                                        line=line_no,
                                        column=col_lo,
                                        offset=line_start + col_lo - 1,
                                        file_id=file_id,
                                    )
                                )
                            out[line_start + col_lo - 1] = 0x20
                if clause.debug_if is not None:
                    if col_lo <= line_len:
                        b = data[line_start + col_lo - 1]
                        if chr(b) in clause.debug_if:
                            if clause.debug_token:
                                injections.append(
                                    Token(
                                        name=clause.debug_token,
                                        text=chr(b),
                                        line=line_no,
                                        column=col_lo,
                                        offset=line_start + col_lo - 1,
                                        file_id=file_id,
                                    )
                                )
                            out[line_start + col_lo - 1] = 0x20
                if clause.mode == "skip" or clause.mode == "label":
                    # `label` mode keeps integer labels but the host's lexer
                    # already accepts integer literals — blanking would drop
                    # them. Strategy: leave the bytes alone for label mode;
                    # the DFA will lex them as INT_LIT. For skip mode, blank.
                    if clause.mode == "skip":
                        for k in range(line_start + col_lo - 1, min(line_start + col_hi, content_end)):
                            out[k] = 0x20
            # Width clipping: columns > width are always blanked.
            if line_len > width:
                for k in range(line_start + width, content_end):
                    out[k] = 0x20
        # Pass 3: run the inner scanner over the cleaned source, then
        # merge the synthetic-token injections by offset.
        scanner_tokens = list(self.scanner.scan(bytes(out), file_id=file_id))
        merged: list[Token] = scanner_tokens + injections
        merged.sort(key=lambda t: (t.offset if t.offset >= 0 else 1 << 31, t.line, t.column))
        for tok in merged:
            yield tok


def apply_filters(
    tokens: Iterable[Token],
    *,
    continuation: Optional[ContinuationConfig] = None,
    layout: Optional[LayoutConfig] = None,
    is_comment: Optional[Callable[[Token], bool]] = None,
) -> Iterator[Token]:
    """Apply the configured filters in canonical order.

    Order matches the composition diagram in
    ``docs/proposals/continuation.md``:

    1. Continuation (collapse marker+newline pairs).
    2. Layout (emit INDENT / DEDENT / NEWLINE).

    Column dispatch happens earlier inside the scanner itself, not here
    — wrap the :class:`Scanner` in a :class:`ColumnDispatcher` before
    feeding its output through these filters.
    """
    stream: Iterable[Token] = tokens
    if continuation is not None:
        flow_open = frozenset(layout.flow_open if layout else ())
        flow_close = frozenset(layout.flow_close if layout else ())
        cf = ContinuationFilter(
            config=continuation, flow_open=flow_open, flow_close=flow_close
        )
        stream = cf.filter(stream)
    if layout is not None:
        lf = LayoutFilter(config=layout, is_comment=is_comment)
        stream = lf.filter(stream)
    return iter(stream)
