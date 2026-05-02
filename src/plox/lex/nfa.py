"""Thompson's construction: regex AST -> NFA with epsilon transitions.

The NFA representation here is intentionally tiny and explicit so the DFA stage
(:mod:`plox.lex.dfa`, Phase 2 follow-up) can subset-construct it without going
through any indirection.

Representation
--------------

* States are non-negative ints, allocated by :class:`NFABuilder`.
* ``transitions[state]`` is a list of ``(label, target)`` pairs.
* ``label`` is either ``None`` (epsilon transition) or a frozenset of byte
  values 0..255 (a character class). We do not deduplicate or merge labels —
  the DFA stage owns that.
* ``start`` and ``accepts[token_name]`` give the entry point and accept set.
  Multiple regexes can be combined under one start state by adding epsilon
  transitions from the new start to each sub-NFA's start; this is how the
  lexer combines all token patterns into a single recogniser.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .regex import Alt, CharClass, Concat, Empty, Node, Optional as OptNode, Plus, Star

EPSILON = None  # sentinel for epsilon transitions


@dataclass
class NFA:
    transitions: list[list[tuple[Optional[frozenset], int]]] = field(default_factory=list)
    start: int = 0
    accepts: dict[int, str] = field(default_factory=dict)
    """Map state -> accepted token name. A state with no entry here is non-accepting."""
    token_priority: dict[str, int] = field(default_factory=dict)
    """Map token name -> declaration index. Lower wins ties at DFA accept resolution.

    Set by :func:`combine`, since priority only has meaning when multiple tokens are
    competing in the same recogniser. Single-token NFAs leave this empty; the DFA
    stage treats absent entries as priority infinity.
    """

    def state_count(self) -> int:
        return len(self.transitions)


@dataclass
class _Fragment:
    """A partial NFA with one entry and one exit state."""
    start: int
    end: int


class NFABuilder:
    """Holds the running NFA while sub-fragments are constructed and stitched."""

    def __init__(self) -> None:
        self.nfa = NFA()

    def new_state(self) -> int:
        s = len(self.nfa.transitions)
        self.nfa.transitions.append([])
        return s

    def add_edge(self, src: int, label: Optional[frozenset], dst: int) -> None:
        self.nfa.transitions[src].append((label, dst))

    def fragment_for(self, node: Node) -> _Fragment:
        if isinstance(node, Empty):
            s = self.new_state()
            e = self.new_state()
            self.add_edge(s, EPSILON, e)
            return _Fragment(s, e)

        if isinstance(node, CharClass):
            s = self.new_state()
            e = self.new_state()
            self.add_edge(s, node.chars, e)
            return _Fragment(s, e)

        if isinstance(node, Concat):
            if not node.parts:
                return self.fragment_for(Empty())
            first = self.fragment_for(node.parts[0])
            for part in node.parts[1:]:
                nxt = self.fragment_for(part)
                self.add_edge(first.end, EPSILON, nxt.start)
                first = _Fragment(first.start, nxt.end)
            return first

        if isinstance(node, Alt):
            s = self.new_state()
            e = self.new_state()
            for branch in node.branches:
                f = self.fragment_for(branch)
                self.add_edge(s, EPSILON, f.start)
                self.add_edge(f.end, EPSILON, e)
            return _Fragment(s, e)

        if isinstance(node, Star):
            s = self.new_state()
            e = self.new_state()
            inner = self.fragment_for(node.inner)
            self.add_edge(s, EPSILON, inner.start)
            self.add_edge(s, EPSILON, e)
            self.add_edge(inner.end, EPSILON, inner.start)
            self.add_edge(inner.end, EPSILON, e)
            return _Fragment(s, e)

        if isinstance(node, Plus):
            inner = self.fragment_for(node.inner)
            e = self.new_state()
            self.add_edge(inner.end, EPSILON, inner.start)
            self.add_edge(inner.end, EPSILON, e)
            return _Fragment(inner.start, e)

        if isinstance(node, OptNode):
            s = self.new_state()
            e = self.new_state()
            inner = self.fragment_for(node.inner)
            self.add_edge(s, EPSILON, inner.start)
            self.add_edge(s, EPSILON, e)
            self.add_edge(inner.end, EPSILON, e)
            return _Fragment(s, e)

        raise TypeError(f"unhandled regex node type: {type(node).__name__}")


def build(node: Node, token_name: str = "TOKEN") -> NFA:
    """Build a single-token NFA from a regex AST.

    For multi-token lexers, build each token's NFA separately and combine them
    via :func:`combine`.
    """
    b = NFABuilder()
    frag = b.fragment_for(node)
    b.nfa.start = frag.start
    b.nfa.accepts[frag.end] = token_name
    return b.nfa


def combine(nfas: list[tuple[str, NFA]]) -> NFA:
    """Merge per-token NFAs into one with a single shared start state.

    The list order *is* the priority order: the token at index 0 wins ties over
    every later token. The DFA stage uses this when multiple NFA accept states
    end up in the same DFA subset (combined with longest-match, this gives the
    classic flex/lex tie-break: longest match, then earliest-declaration).

    The per-tuple ``name`` overrides whatever the source NFA had already labelled
    its accept state with. This is the common case — callers build a single-token
    NFA with a placeholder name and assign the real one here.
    """
    if not nfas:
        raise ValueError("combine requires at least one NFA")
    combined = NFA()
    combined.start = 0
    combined.transitions.append([])  # state 0 is the new shared start
    for priority, (name, src) in enumerate(nfas):
        offset = len(combined.transitions)
        for trans in src.transitions:
            combined.transitions.append([(label, dst + offset) for (label, dst) in trans])
        combined.transitions[0].append((EPSILON, src.start + offset))
        for accept_state in src.accepts:
            combined.accepts[accept_state + offset] = name
        combined.token_priority[name] = priority
    return combined
