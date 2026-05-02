"""Pluggable hooks framework.

Hooks fire at well-defined points: pre_shift, pre_reduce, post_reduce, on_error.
A hook is named in the grammar spec; the host driver resolves the name to a
callable. Hook payloads always include the parser context as the first argument
so hooks remain re-entrant.

Built-in helpers (scoped name table, type table, error-recovery sync sets) are
provided so common context-sensitive concerns do not leak into the grammar.

Phase 4.
"""
