"""Schema serialiser: shape validation and determinism."""

from __future__ import annotations

import json

import pytest

from uplox.tables import UPLOX_SCHEMA_VERSION, dump_bundle, empty_bundle


def test_empty_bundle_round_trips():
    b = empty_bundle("calc")
    text = dump_bundle(b)
    parsed = json.loads(text)
    assert parsed == b
    assert parsed["uplox_schema"] == UPLOX_SCHEMA_VERSION
    assert parsed["meta"]["grammar"] == "calc"


def test_dump_is_deterministic():
    a = empty_bundle("g")
    a["lex"]["b"] = 2
    a["lex"]["a"] = 1
    b = empty_bundle("g")
    b["lex"]["a"] = 1
    b["lex"]["b"] = 2
    assert dump_bundle(a) == dump_bundle(b)


def test_rejects_wrong_schema():
    b = empty_bundle("g")
    b["uplox_schema"] = "0"
    with pytest.raises(ValueError):
        dump_bundle(b)


def test_rejects_missing_section():
    b = empty_bundle("g")
    del b["lex"]
    with pytest.raises(ValueError):
        dump_bundle(b)


def test_rejects_non_dict_section():
    b = empty_bundle("g")
    b["parse"] = []
    with pytest.raises(TypeError):
        dump_bundle(b)
