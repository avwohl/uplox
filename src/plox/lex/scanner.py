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

from dataclasses import dataclass
from typing import Iterator

from .dfa import DEAD, DFA


@dataclass(frozen=True)
class Token:
    name: str
    text: str
    line: int
    column: int
    offset: int  # byte offset into the input

    def __repr__(self) -> str:  # pragma: no cover - cosmetic
        return f"Token({self.name!r}, {self.text!r}, line={self.line}, col={self.column})"


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
    """
    dfa: DFA
    skip_tokens: frozenset[str] = frozenset()
    filename: str = "<input>"

    def scan(self, source: str | bytes) -> Iterator[Token]:
        """Yield tokens for ``source``. ``source`` may be ``str`` (treated as UTF-8) or bytes."""
        data = source.encode("utf-8") if isinstance(source, str) else bytes(source)
        return self._scan_bytes(data)

    def scan_all(self, source: str | bytes) -> list[Token]:
        return list(self.scan(source))

    def _scan_bytes(self, data: bytes) -> Iterator[Token]:
        n = len(data)
        pos = 0
        line = 1
        col = 1
        dfa = self.dfa
        skip = self.skip_tokens

        while pos < n:
            # One maximal-munch pass starting at ``pos``.
            state = dfa.start
            last_accept_end = -1
            last_accept_name = ""

            if state in dfa.accepts:
                last_accept_end = pos
                last_accept_name = dfa.accepts[state]

            i = pos
            while i < n:
                nxt = dfa.transitions[state][data[i]]
                if nxt == DEAD:
                    break
                state = nxt
                i += 1
                if state in dfa.accepts:
                    last_accept_end = i
                    last_accept_name = dfa.accepts[state]

            if last_accept_end <= pos:
                # No progress possible. last_accept_end == pos with empty match
                # would be an infinite loop — treat as error.
                raise ScanError(
                    f"{self.filename}:{line}:{col}: lexical error at byte {data[pos]:#04x}",
                    line, col, data[pos],
                )

            text_bytes = data[pos:last_accept_end]
            if last_accept_name not in skip:
                yield Token(
                    name=last_accept_name,
                    text=text_bytes.decode("utf-8", errors="replace"),
                    line=line,
                    column=col,
                    offset=pos,
                )
            # Advance position and update line/column.
            for b in text_bytes:
                if b == 0x0A:  # \n
                    line += 1
                    col = 1
                else:
                    col += 1
            pos = last_accept_end
