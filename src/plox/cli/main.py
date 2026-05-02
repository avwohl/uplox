"""``plox`` command entry point.

Subcommands:

* ``plox version``                — print plox version and schema version.
* ``plox build <grammar.plox> -o <out.json>``
                                  — build the JSON bundle. In Phase 2 only the
                                    lex section is populated; the parse / ast /
                                    hooks sections are emitted empty so backends
                                    can already start consuming bundles.
* ``plox check <grammar.plox>``   — parse + lower without emitting JSON; reports
                                    syntax errors and lex-construction failures.
* ``plox emit  <bundle.json> --target=c|cpp|py|lua --out=<dir>``
                                  — drive a backend. Stubbed until Phase 7-8.
"""

from __future__ import annotations

import argparse
import sys

from .. import PLOX_SCHEMA_VERSION, __version__
from ..lex.build import lex_from_ir
from ..lex.scanner import Scanner
from ..parse.grammar import GrammarError, compile_grammar
from ..parse.lr1 import build_lr1
from ..parse.runtime import HookRegistry, ParseError, parse as run_parser
from ..spec.reader import ReaderError, read_file
from ..tables import (
    dfa_from_json,
    dfa_to_json,
    dump_bundle,
    empty_bundle,
    table_from_json,
    table_to_json,
)


def _cmd_version(_args: argparse.Namespace) -> int:
    print(f"plox {__version__} (schema {PLOX_SCHEMA_VERSION})")
    return 0


def _cmd_build(args: argparse.Namespace) -> int:
    try:
        ir = read_file(args.source)
    except ReaderError as e:
        print(str(e), file=sys.stderr)
        return 1
    try:
        dfa, tokens, skip = lex_from_ir(ir)
    except ValueError as e:
        print(f"{args.source}: {e}", file=sys.stderr)
        return 1

    bundle = empty_bundle(ir.name)
    bundle["lex"] = dfa_to_json(dfa, tokens=tokens, skip=skip)

    if not args.lex_only:
        try:
            grammar = compile_grammar(ir)
            table = build_lr1(grammar)
        except GrammarError as e:
            print(f"{args.source}: {e}", file=sys.stderr)
            return 1
        if table.conflicts:
            print(
                f"{args.source}: refusing to build with {len(table.conflicts)} parser conflict(s):",
                file=sys.stderr,
            )
            for c in table.conflicts:
                print(c.describe(table.grammar), file=sys.stderr)
                print("", file=sys.stderr)
            return 1
        bundle["parse"] = table_to_json(table)

    text = dump_bundle(bundle)
    if args.output == "-":
        sys.stdout.write(text)
    else:
        with open(args.output, "w", encoding="utf-8") as fh:
            fh.write(text)
    return 0


def _cmd_parse(args: argparse.Namespace) -> int:
    """Smoke-parse: load a bundle, run scanner+parser on stdin or a path, dump tree."""
    import json as _json

    with open(args.bundle, "r", encoding="utf-8") as fh:
        bundle = _json.load(fh)
    if not bundle.get("parse"):
        print(f"{args.bundle}: bundle has no parse section (was it built --lex-only?)", file=sys.stderr)
        return 1

    dfa, _tokens, skip = dfa_from_json(bundle["lex"])
    scanner = Scanner(dfa=dfa, skip_tokens=frozenset(skip))
    table = table_from_json(bundle["parse"])

    if args.input == "-":
        text = sys.stdin.read()
    else:
        with open(args.input, "r", encoding="utf-8") as fh:
            text = fh.read()

    try:
        # The CLI is a smoke tool; we don't try to resolve hooks the way a
        # real host driver would. Unknown names just no-op so any grammar
        # builds and parses end-to-end.
        tree = run_parser(table, scanner.scan(text), hooks=HookRegistry(ignore_missing=True))
    except ParseError as e:
        print(f"{args.input}: {e}", file=sys.stderr)
        return 1

    sys.stdout.write(_render_tree(tree) + "\n")
    return 0


def _render_tree(tree, indent: int = 0) -> str:
    from ..lex.scanner import Token
    from ..parse.runtime import ParseNode
    pad = "  " * indent
    if isinstance(tree, Token):
        return f"{pad}{tree.name} {tree.text!r}"
    if isinstance(tree, ParseNode):
        lines = [f"{pad}{tree.kind}"]
        for c in tree.children:
            lines.append(_render_tree(c, indent + 1))
        return "\n".join(lines)
    return f"{pad}{tree!r}"


def _cmd_check(args: argparse.Namespace) -> int:
    try:
        ir = read_file(args.source)
        lex_from_ir(ir)
    except (ReaderError, ValueError) as e:
        print(str(e), file=sys.stderr)
        return 1

    parser_summary = ""
    parser_conflicts = 0
    try:
        grammar = compile_grammar(ir)
        table = build_lr1(grammar)
        parser_conflicts = len(table.conflicts)
        if table.conflicts:
            print(f"{args.source}: {parser_conflicts} parser conflict(s):", file=sys.stderr)
            for c in table.conflicts:
                print(c.describe(table.grammar), file=sys.stderr)
                print("", file=sys.stderr)
        parser_summary = (
            f", {len(grammar.productions)} productions, "
            f"{len(table.states)} states, {parser_conflicts} conflicts"
        )
    except GrammarError as e:
        print(f"{args.source}: {e}", file=sys.stderr)
        return 1

    print(
        f"{args.source}: {ir.name} — {len(ir.tokens)} tokens, "
        f"{len(ir.hooks)} hooks{parser_summary}"
    )
    return 1 if parser_conflicts else 0


def _cmd_emit(args: argparse.Namespace) -> int:
    print(
        f"plox emit --target={args.target}: not yet implemented "
        f"(targets land in Phase 7+)",
        file=sys.stderr,
    )
    return 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="plox",
        description="Compiler front-end generator (grammar -> JSON tables + drivers)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_version = sub.add_parser("version", help="print plox and schema versions")
    p_version.set_defaults(func=_cmd_version)

    p_build = sub.add_parser("build", help="compile a .plox grammar to a JSON bundle")
    p_build.add_argument("source", help="path to .plox source file")
    p_build.add_argument(
        "-o", "--output",
        default="-",
        help="output bundle path; '-' (default) writes to stdout",
    )
    p_build.add_argument(
        "--lex-only",
        action="store_true",
        help="emit only the lex section (skip the LR table)",
    )
    p_build.set_defaults(func=_cmd_build)

    p_check = sub.add_parser("check", help="parse and validate a .plox grammar without emitting")
    p_check.add_argument("source", help="path to .plox source file")
    p_check.set_defaults(func=_cmd_check)

    p_parse = sub.add_parser(
        "parse",
        help="parse input through a built bundle and pretty-print the parse tree",
    )
    p_parse.add_argument("bundle", help="path to JSON bundle (output of `plox build`)")
    p_parse.add_argument(
        "input",
        help="path to input file; '-' reads from stdin",
        nargs="?",
        default="-",
    )
    p_parse.set_defaults(func=_cmd_parse)

    p_emit = sub.add_parser("emit", help="emit driver skeleton for a target language (Phase 7+)")
    p_emit.add_argument("bundle", help="path to JSON bundle")
    p_emit.add_argument("--target", required=True, choices=["c", "cpp", "py", "lua"])
    p_emit.add_argument("--out", required=True, help="output directory")
    p_emit.set_defaults(func=_cmd_emit)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
