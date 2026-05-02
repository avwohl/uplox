"""Backends. Each subpackage emits a driver skeleton in one target language.

Backends consume only the JSON bundle described in :mod:`plox.tables.schema`.
They never read the grammar source directly — the JSON is the contract.
"""
