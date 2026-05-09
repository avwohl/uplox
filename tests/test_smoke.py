"""Sanity checks: package imports and CLI version subcommand work."""

from __future__ import annotations

import uplox
from uplox.cli.main import main


def test_version_constants_present():
    assert isinstance(uplox.__version__, str) and uplox.__version__
    assert uplox.UPLOX_SCHEMA_VERSION == "1"


def test_cli_version_subcommand(capsys):
    rc = main(["version"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "uplox" in out
    assert "schema" in out


def test_subpackages_import():
    import uplox.spec  # noqa: F401
    import uplox.lex  # noqa: F401
    import uplox.parse  # noqa: F401
    import uplox.parse.glr  # noqa: F401
    import uplox.ast  # noqa: F401
    import uplox.hooks  # noqa: F401
    import uplox.tables  # noqa: F401
    import uplox.gen  # noqa: F401
    import uplox.gen.c  # noqa: F401
    import uplox.gen.cpp  # noqa: F401
    import uplox.gen.py  # noqa: F401
    import uplox.gen.lua  # noqa: F401
    import uplox.cli  # noqa: F401
