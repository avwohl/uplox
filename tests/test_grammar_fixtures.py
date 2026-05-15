"""Real-world parse fixtures.

For each grammar, this test walks `tests/fixtures/<lang>/` and parses
every source file through the matching grammar, asserting that parsing
succeeds. Grammars that need a host filter for generics get the filter
applied automatically.

To add coverage for a new grammar, drop one or more source files into
`tests/fixtures/<grammar-name>/` and (if the grammar needs the
generic-bracket filter) add an entry to :data:`GENERIC_DIALECTS`.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from uplox.hooks.generic_brackets import rewrite_generics
from uplox.lex.filters import ColumnDispatcher, apply_filters
from uplox.lex.scanner import Scanner
from uplox.parse.runtime import HookRegistry, parse as run_parser
from uplox.tables import (
    balanced_from_json,
    columns_from_json,
    continuation_from_json,
    dfa_from_json,
    layout_from_json,
    table_from_json,
)


REPO_ROOT = Path(__file__).resolve().parent.parent
EXAMPLES_DIR = REPO_ROOT / "examples"
FIXTURES_DIR = REPO_ROOT / "tests" / "fixtures"


# Map from fixtures-subdirectory name to the matching grammar file
# stem. Defaults to the same name (`python` → `python.uplox`).
GRAMMAR_OVERRIDE: dict[str, str] = {}

# Grammars whose fixtures should be passed through
# `rewrite_generics` before parsing — these are the ones declaring
# LT_GENERIC / GT_GENERIC synthetic terminals. The value is the
# dialect name from :data:`uplox.hooks.generic_brackets.DIALECTS`.
GENERIC_DIALECTS: dict[str, str] = {
    "kotlin": "kotlin",
    "swift": "swift",
    "typescript": "typescript",
}


def _grammar_for(lang: str) -> Path:
    stem = GRAMMAR_OVERRIDE.get(lang, lang)
    return EXAMPLES_DIR / f"{stem}.uplox"


def _bundle_for(tmp_path: Path, lang: str) -> dict:
    """Build the grammar to a temp JSON bundle and load it."""
    grammar = _grammar_for(lang)
    bundle_path = tmp_path / f"{lang}.json"
    import subprocess
    subprocess.run(
        ["uplox", "build", str(grammar), "-o", str(bundle_path)],
        check=True, capture_output=True,
    )
    with open(bundle_path) as fh:
        return json.load(fh)


def _collect_fixture_files() -> list[tuple[str, Path]]:
    """Walk fixtures/<lang>/ subdirs and yield (lang, file) pairs."""
    out: list[tuple[str, Path]] = []
    if not FIXTURES_DIR.exists():
        return out
    for lang_dir in sorted(FIXTURES_DIR.iterdir()):
        if not lang_dir.is_dir():
            continue
        grammar = _grammar_for(lang_dir.name)
        if not grammar.exists():
            # Skip directories that don't correspond to a grammar.
            continue
        for src in sorted(lang_dir.iterdir()):
            if src.is_file() and not src.name.startswith("."):
                out.append((lang_dir.name, src))
    return out


FIXTURE_PAIRS = _collect_fixture_files()


@pytest.fixture(scope="session")
def grammar_bundles(tmp_path_factory):
    """Build each grammar bundle once per test session."""
    bundle_dir = tmp_path_factory.mktemp("grammars")
    bundles: dict[str, dict] = {}
    seen_langs = {lang for lang, _ in FIXTURE_PAIRS}
    for lang in sorted(seen_langs):
        bundles[lang] = _bundle_for(bundle_dir, lang)
    return bundles


@pytest.mark.parametrize(
    "lang,src_path",
    FIXTURE_PAIRS,
    ids=[f"{lang}-{p.name}" for lang, p in FIXTURE_PAIRS],
)
def test_fixture_parses(lang: str, src_path: Path, grammar_bundles):
    bundle = grammar_bundles[lang]
    dfa, _, skip = dfa_from_json(bundle["lex"])
    scanner = Scanner(
        dfa=dfa,
        skip_tokens=frozenset(skip),
        balanced=balanced_from_json(bundle["lex"]),
    )
    columns_cfg = columns_from_json(bundle["lex"])
    layout_cfg = layout_from_json(bundle["lex"])
    continuation_cfg = continuation_from_json(bundle["lex"])
    table = table_from_json(bundle["parse"])

    text = src_path.read_text()
    if columns_cfg is not None:
        raw = ColumnDispatcher(config=columns_cfg, scanner=scanner).scan(text)
    else:
        raw = scanner.scan(text)
    filtered = apply_filters(
        raw, continuation=continuation_cfg, layout=layout_cfg
    )
    tokens = list(filtered)
    if lang in GENERIC_DIALECTS:
        tokens = rewrite_generics(tokens, dialect=GENERIC_DIALECTS[lang])

    run_parser(table, tokens, hooks=HookRegistry(ignore_missing=True))
