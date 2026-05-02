"""``plox`` command entry point.

Subcommands (planned):

* ``plox build <grammar.plox>``   — build the JSON bundle.
* ``plox emit  <bundle.json> --target=c|cpp|py|lua --out=<dir>`` — drive a backend.
* ``plox check <grammar.plox>``   — parse the grammar and report conflicts only.
* ``plox version``                — print plox version and schema version.

Only ``version`` is wired up so far; everything else is a Phase-2+ deliverable
and is intentionally a stub that prints "not yet implemented" so that the
``plox`` script remains callable.
"""

from __future__ import annotations

import argparse
import sys

from .. import PLOX_SCHEMA_VERSION, __version__


def _cmd_version(_args: argparse.Namespace) -> int:
    print(f"plox {__version__} (schema {PLOX_SCHEMA_VERSION})")
    return 0


def _cmd_not_implemented(args: argparse.Namespace) -> int:
    print(f"plox {args.command}: not yet implemented", file=sys.stderr)
    return 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="plox",
        description="Compiler front-end generator (grammar -> JSON tables + drivers)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_version = sub.add_parser("version", help="print plox and schema versions")
    p_version.set_defaults(func=_cmd_version)

    for stub in ("build", "emit", "check"):
        p = sub.add_parser(stub, help=f"{stub} — not yet implemented")
        p.set_defaults(func=_cmd_not_implemented)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
