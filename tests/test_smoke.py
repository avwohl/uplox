"""Sanity checks: package imports and CLI version subcommand work."""

from __future__ import annotations

import importlib.metadata

import pytest

import uplox
from uplox.cli.main import main


def test_version_constants_present():
    assert isinstance(uplox.__version__, str) and uplox.__version__
    assert uplox.UPLOX_SCHEMA_VERSION == "1"


def test_version_matches_distribution_metadata():
    """`uplox version` prints __version__; the dist metadata must agree.

    Regression guard: 3.2.0 was once bumped in pyproject.toml only, so the
    installed dist reported 3.2.0 while `uplox version` printed 3.1.1.
    pyproject.toml now derives the version from __init__.py, so a mismatch
    here means the installed dist is stale — reinstall (``pip install -e .``).
    """
    try:
        dist_version = importlib.metadata.version("uplox")
    except importlib.metadata.PackageNotFoundError:
        pytest.skip("uplox is not installed; running from a source tree")
    assert dist_version == uplox.__version__


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
