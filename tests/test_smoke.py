"""Sanity checks: package imports and CLI version subcommand work."""

from __future__ import annotations

import plox
from plox.cli.main import main


def test_version_constants_present():
    assert isinstance(plox.__version__, str) and plox.__version__
    assert plox.PLOX_SCHEMA_VERSION == "1"


def test_cli_version_subcommand(capsys):
    rc = main(["version"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "plox" in out
    assert "schema" in out


def test_subpackages_import():
    import plox.spec  # noqa: F401
    import plox.lex  # noqa: F401
    import plox.parse  # noqa: F401
    import plox.parse.glr  # noqa: F401
    import plox.ast  # noqa: F401
    import plox.hooks  # noqa: F401
    import plox.tables  # noqa: F401
    import plox.gen  # noqa: F401
    import plox.gen.c  # noqa: F401
    import plox.gen.cpp  # noqa: F401
    import plox.gen.py  # noqa: F401
    import plox.gen.lua  # noqa: F401
    import plox.cli  # noqa: F401
