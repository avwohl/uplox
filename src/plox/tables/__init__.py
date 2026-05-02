"""Canonical JSON bundle: schema + deterministic serializer.

The bundle is the contract between plox and every backend. See
:mod:`plox.tables.schema` for the section layout and version key.
"""

from .schema import PLOX_SCHEMA_VERSION, dump_bundle, empty_bundle

__all__ = ["PLOX_SCHEMA_VERSION", "dump_bundle", "empty_bundle"]
