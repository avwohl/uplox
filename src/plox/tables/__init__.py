"""Canonical JSON bundle: schema + deterministic serializer.

The bundle is the contract between plox and every backend. See
:mod:`plox.tables.schema` for the section layout and version key. Each
section has its own (de)serialiser module — :mod:`plox.tables.lex_section`,
``parse_section`` (Phase 3), etc.
"""

from .lex_section import dfa_from_json, dfa_to_json
from .parse_section import table_from_json, table_to_json
from .schema import PLOX_SCHEMA_VERSION, dump_bundle, empty_bundle

__all__ = [
    "PLOX_SCHEMA_VERSION",
    "dump_bundle",
    "empty_bundle",
    "dfa_to_json",
    "dfa_from_json",
    "table_to_json",
    "table_from_json",
]
