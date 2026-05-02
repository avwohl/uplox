"""Canonical JSON bundle layout and serializer.

A plox bundle is a single JSON object with a fixed top-level shape:

::

    {
      "plox_schema": "1",
      "meta":  { ... },
      "lex":   { ... },   // lexer DFA + token table; populated in Phase 2
      "parse": { ... },   // ACTION/GOTO/productions/start state; Phase 3
      "ast":   { ... },   // node schema; Phase 4
      "hooks": { ... }    // hook point declarations; Phase 4
    }

Empty stages are emitted as ``{}`` so the top-level shape never changes between
phases. Backends always look up sections by key.

Determinism rules — every backend can rely on these:

* Keys are sorted lexicographically at every level (``json.dumps(sort_keys=True)``).
* No trailing whitespace, no platform-dependent ordering.
* Integer arrays preserve insertion order; the *order itself* is part of the contract
  (state numbering, production numbering) and must be assigned deterministically by
  the builder, never by hash iteration.
* Floats are not used. All numerics are Python ``int``.

Bumping the schema is a breaking change for backends. Every bump must be paired
with a backend update.
"""

from __future__ import annotations

import json
from typing import Any

PLOX_SCHEMA_VERSION = "1"

_SECTIONS = ("meta", "lex", "parse", "ast", "hooks")


def empty_bundle(grammar_name: str) -> dict[str, Any]:
    """Return a minimally-valid bundle skeleton for ``grammar_name``.

    Useful as the starting point for incremental builders and as a fixture for
    backends that need something to load before any real tables exist.
    """
    return {
        "plox_schema": PLOX_SCHEMA_VERSION,
        "meta": {"grammar": grammar_name},
        "lex": {},
        "parse": {},
        "ast": {},
        "hooks": {},
    }


def dump_bundle(bundle: dict[str, Any]) -> str:
    """Serialise ``bundle`` to canonical JSON text.

    Validates the top-level shape before serialising so a malformed bundle is
    rejected at the boundary instead of silently producing output a backend
    cannot load.
    """
    if not isinstance(bundle, dict):
        raise TypeError("bundle must be a dict")
    if bundle.get("plox_schema") != PLOX_SCHEMA_VERSION:
        raise ValueError(
            f"bundle.plox_schema must be {PLOX_SCHEMA_VERSION!r}, got {bundle.get('plox_schema')!r}"
        )
    for section in _SECTIONS:
        if section not in bundle:
            raise ValueError(f"bundle missing required section {section!r}")
        if not isinstance(bundle[section], dict):
            raise TypeError(f"bundle.{section} must be a dict")
    return json.dumps(bundle, sort_keys=True, indent=2, ensure_ascii=False) + "\n"
