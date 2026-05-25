"""Lexer runtime: drive a DFA over input bytes, yield tokens.

Used by the Python backend (Phase 2 deliverable: "Python driver") and as the
oracle for tests of the JSON-table-based backends. The C and C++ backends emit
their own scanner skeletons but follow the same algorithm:

* Maximal-munch: at each position, run the DFA forward, remembering the most
  recent accept state. When the DFA dies (or input ends), emit the longest
  remembered accept and resume scanning from one byte past it.
* Tie-break by token-priority is already baked into the DFA's accept labels —
  only one token name is associated with any given accept state, so the
  scanner does not need to know about priorities.
* ``%skip`` tokens are dropped after recognition: the scanner still consumes
  their characters, just doesn't yield them.

Errors
------

If the DFA cannot make progress and there is no remembered accept, we have a
lexical error. The scanner reports it via :class:`ScanError` with the offending
byte's line/column and the byte value. Recovery is the host driver's job; the
scanner does not skip ahead.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Iterator, Mapping, Optional

from .dfa import DEAD, DFA


@dataclass(frozen=True)
class Token:
    """A lexical token.

    ``file_id`` is an integer into a :class:`FileTable` that maps it
    back to a filename string. Tokens stay compact (an int per token
    instead of a string reference) and filenames de-dupe automatically
    across the source set. ``file_id == 0`` is the reserved "unknown
    file" slot; the FileTable always initialises with ``""`` at index
    0 so a Token constructed without a file_id still decodes to an
    empty filename via the table.

    ``kind`` is the host's :class:`TokenKind` IntEnum value for this
    token (per-host enum generated from the grammar's terminals). It
    is the fast comparison handle: ``tok.kind is K.KW_INT`` is a
    single int compare vs a string equality. ``kind == 0`` is the
    sentinel ``UNKNOWN`` value used when the scanner had no kind map
    (legacy or generic-bundle callers); in that case the host must
    fall back to ``tok.name`` string compares. The string ``name``
    is preserved alongside for error messages, debug dumps, and the
    minority of sites that look at the actual name text.
    """

    name: str
    text: str
    line: int
    column: int
    offset: int  # byte offset into the input
    file_id: int = 0
    kind: int = 0

    def __repr__(self) -> str:  # pragma: no cover - cosmetic
        return f"Token({self.name!r}, {self.text!r}, line={self.line}, col={self.column})"


def build_token_kind(tokens: list[str], *, class_name: str = "TokenKind") -> type[IntEnum]:
    """Return an :class:`IntEnum` mapping token-name strings to dense ints.

    ``tokens`` is the grammar's terminal declaration order (the same list
    that lives in the bundle's ``lex.tokens`` array). Value 0 is reserved
    for the synthetic ``UNKNOWN`` slot — every real terminal starts at 1
    — so ``Token.kind == 0`` cleanly means "no enum is in play here"
    without colliding with any grammar token.

    Hosts call this once at module load (typically right after loading
    their grammar bundle) and re-export the result as ``K`` or
    ``TokenKind`` for the parser code to import. The returned enum is
    deterministic given the input order, which is also the bundle's
    declared order — keep that order stable across builds so that
    pickles / cached parser state remain comparable.
    """
    members = [("UNKNOWN", 0)] + [(name, i + 1) for i, name in enumerate(tokens)]
    return IntEnum(class_name, members)


class FileTable:
    """Map between filename strings and small integer ``file_id``s.

    The table de-dupes filenames so each unique source file occupies
    one slot regardless of how many tokens reference it. ``file_id``
    0 is reserved for the empty/unknown filename and the table is
    seeded with that entry on construction; subsequent
    :meth:`intern` calls return monotonically-assigned ids.

    A FileTable is mutable (filenames may be added over the life of
    a parse), but is otherwise straightforward — no locking, no
    thread-safety guarantees. Consumers that want shared tables
    across threads should synchronise externally.
    """

    __slots__ = ("_files", "_index")

    def __init__(self) -> None:
        self._files: list[str] = [""]
        self._index: dict[str, int] = {"": 0}

    def intern(self, filename: str) -> int:
        """Return the ``file_id`` for ``filename``; assign one if new."""
        idx = self._index.get(filename)
        if idx is not None:
            return idx
        idx = len(self._files)
        self._files.append(filename)
        self._index[filename] = idx
        return idx

    def name(self, file_id: int) -> str:
        """Return the filename for ``file_id``, or ``""`` if out of range."""
        if 0 <= file_id < len(self._files):
            return self._files[file_id]
        return ""

    def __len__(self) -> int:
        return len(self._files)

    def filenames(self) -> list[str]:
        """Snapshot of the filename table (read-only copy)."""
        return list(self._files)


class ScanError(Exception):
    def __init__(self, message: str, line: int, column: int, byte: int):
        super().__init__(message)
        self.line = line
        self.column = column
        self.byte = byte


@dataclass(frozen=True)
class Scanner:
    """Configured scanner: a DFA plus the set of tokens to skip silently.

    Both parts are immutable. Each :func:`scan` call drives a fresh state machine
    over its input — the scanner itself holds no per-input state, so multiple
    threads or tasks can share one Scanner instance.

    ``balanced`` maps token names to their closing-delimiter characters. When
    the DFA matches a balanced token, the scanner extends the match by counting
    nested instances of (open, close) until depth returns to zero. The open
    delimiter is whatever the DFA matched (typically a single literal char like
    ``"{"``). This lets a token cover content that is not itself a regular
    language — target-language action bodies, for example.

    ``kind_map`` optionally maps each token name to its :class:`TokenKind` int
    value (see :func:`build_token_kind`). When supplied, every emitted Token
    carries ``tok.kind`` so hot-path comparisons can use ``tok.kind is K.X``
    (single int compare) instead of byte-by-byte string equality. When absent
    (generic / unbundled callers), ``tok.kind`` is ``0`` and host code must
    fall back to ``tok.name``.
    """
    dfa: DFA
    skip_tokens: frozenset[str] = frozenset()
    filename: str = "<input>"
    balanced: dict[str, str] = field(default_factory=dict)
    kind_map: Optional[Mapping[str, int]] = None

    def scan(self, source: str | bytes, *, file_id: int = 0) -> Iterator[Token]:
        """Yield tokens for ``source``. ``source`` may be ``str`` (UTF-8) or bytes.

        ``file_id`` stamps every emitted token. The caller is responsible
        for managing a :class:`FileTable` that maps the id back to a
        filename string when needed; the scanner itself only carries the
        integer.
        """
        # `errors='surrogateescape'` round-trips raw bytes that the
        # caller read with the same error handler (the C preprocessor
        # does this so it can pass through e.g. `"\xff"` literals in
        # source). For plain UTF-8 sources it's a no-op.
        data = (
            source.encode("utf-8", errors="surrogateescape")
            if isinstance(source, str) else bytes(source)
        )
        return self._scan_bytes(data, file_id)

    def scan_all(self, source: str | bytes, *, file_id: int = 0) -> list[Token]:
        return list(self.scan(source, file_id=file_id))

    def _scan_bytes(self, data: bytes, file_id: int = 0) -> Iterator[Token]:
        n = len(data)
        pos = 0
        line = 1
        col = 1
        dfa = self.dfa
        skip = self.skip_tokens
        balanced = self.balanced
        kind_map = self.kind_map

        # Intern every accept-state name once up front. The DFA's
        # ``accepts`` table maps state id -> token name string and is
        # loaded from JSON, so its string values are not interned by
        # default. After this pass each ``last_accept_name = accepts[s]``
        # below produces an interned string, which makes every downstream
        # ``tok.name == "KW_INT"`` site a pointer compare (Python's
        # string ``==`` short-circuits on ``is``). Source literals like
        # ``"KW_INT"`` in host code are auto-interned by the compiler,
        # so they end up pointing at the same object after this step.
        accepts = {s: sys.intern(name) for s, name in dfa.accepts.items()}

        while pos < n:
            # One maximal-munch pass starting at ``pos``.
            state = dfa.start
            last_accept_end = -1
            last_accept_name = ""

            if state in accepts:
                last_accept_end = pos
                last_accept_name = accepts[state]

            i = pos
            while i < n:
                nxt = dfa.transitions[state][data[i]]
                if nxt == DEAD:
                    break
                state = nxt
                i += 1
                if state in accepts:
                    last_accept_end = i
                    last_accept_name = accepts[state]

            if last_accept_end <= pos:
                # No progress possible. last_accept_end == pos with empty match
                # would be an infinite loop — treat as error.
                raise ScanError(
                    f"{self.filename}:{line}:{col}: lexical error at byte {data[pos]:#04x}",
                    line, col, data[pos],
                )

            # Extend the match to cover a balanced bracket body. The DFA only
            # recognised the opening delimiter (e.g. "{"); we count nested
            # instances of (open, close) by raw byte until depth returns to
            # zero. Open is whatever the DFA already consumed; close comes
            # from the per-token map.
            if last_accept_name in balanced:
                close_b = balanced[last_accept_name].encode("utf-8")[0]
                open_b = data[pos]
                depth = 1
                j = last_accept_end
                while j < n and depth > 0:
                    b = data[j]
                    if b == open_b:
                        depth += 1
                    elif b == close_b:
                        depth -= 1
                    j += 1
                if depth != 0:
                    raise ScanError(
                        f"{self.filename}:{line}:{col}: unterminated {last_accept_name} "
                        f"(missing {balanced[last_accept_name]!r})",
                        line, col, open_b,
                    )
                last_accept_end = j

            text_bytes = data[pos:last_accept_end]
            if last_accept_name not in skip:
                kind = kind_map.get(last_accept_name, 0) if kind_map is not None else 0
                yield Token(
                    name=last_accept_name,
                    text=text_bytes.decode("utf-8", errors="surrogateescape"),
                    line=line,
                    column=col,
                    offset=pos,
                    file_id=file_id,
                    kind=kind,
                )
            # Advance position and update line/column.
            for b in text_bytes:
                if b == 0x0A:  # \n
                    line += 1
                    col = 1
                else:
                    col += 1
            pos = last_accept_end
