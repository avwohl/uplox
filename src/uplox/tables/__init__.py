"""Canonical JSON bundle: schema + deterministic serializer.

The bundle is the contract between uplox and every backend. See
:mod:`uplox.tables.schema` for the section layout and version key. Each
section has its own (de)serialiser module — :mod:`uplox.tables.lex_section`,
``parse_section`` (Phase 3), etc.
"""

from .ast_section import ast_from_json, ast_to_json
from .lex_section import (
    balanced_from_json,
    columns_from_json,
    columns_to_json,
    continuation_from_json,
    continuation_to_json,
    dfa_from_json,
    dfa_to_json,
    layout_from_json,
    layout_to_json,
)
from .parse_section import table_from_json, table_to_json
from .schema import UPLOX_SCHEMA_VERSION, dump_bundle, empty_bundle

__all__ = [
    "UPLOX_SCHEMA_VERSION",
    "dump_bundle",
    "empty_bundle",
    "dfa_to_json",
    "dfa_from_json",
    "balanced_from_json",
    "layout_to_json",
    "layout_from_json",
    "columns_to_json",
    "columns_from_json",
    "continuation_to_json",
    "continuation_from_json",
    "table_to_json",
    "table_from_json",
    "ast_to_json",
    "ast_from_json",
]
