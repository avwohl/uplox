"""Run the ada_full.uplox parser over a directory of Ada source files.

Usage:
    uplox build examples/ada_full.uplox -o /tmp/ada_full.json
    python examples/ada_corpus.py /tmp/ada_full.json <corpus_dir> \\
        [--save-fails out.txt] [--save-summary out.txt]

Reports pass/fail counts and a categorized breakdown of failures based on
the offending source line (not just the error message). The bucket
heuristics target the known-remaining gaps in ada_full.uplox: T'Range
membership, aspect specs after discriminants, pipe-membership choice
lists, conditional/quantified expressions, and a handful of corpus files
that misuse Ada reserved words as identifiers.

Ada keywords are case-insensitive. The grammar's lex section keys keywords
on lowercase forms; this script lowercases identifiers that match a
reserved word before scanning.
"""
from __future__ import annotations

import json
import re
import sys
import time
from collections import Counter
from pathlib import Path

from uplox.lex.scanner import Scanner
from uplox.parse.runtime import HookRegistry, ParseError, parse as run_parser
from uplox.tables import balanced_from_json, dfa_from_json, table_from_json


ADA_KEYWORDS = {
    "abort", "abs", "abstract", "accept", "access", "aliased", "all", "and",
    "array", "at", "begin", "body", "case", "constant", "declare", "delay",
    "delta", "digits", "do", "else", "elsif", "end", "entry", "exception",
    "exit", "for", "function", "generic", "goto", "if", "in", "interface",
    "is", "limited", "loop", "mod", "new", "not", "null", "of", "or",
    "others", "out", "overriding", "package", "parallel", "pragma", "private",
    "procedure", "protected", "raise", "range", "record", "rem", "renames",
    "requeue", "return", "reverse", "select", "separate", "some", "subtype",
    "synchronized", "tagged", "task", "terminate", "then", "type", "until",
    "use", "when", "while", "with", "xor", "true", "false",
}

_TOKENIZER = re.compile(
    r"""(--[^\n]*)
      | ("(?:[^"\n]|"")*")
      | ('(?:[^'\n]|'')')
      | ([A-Za-z][A-Za-z0-9_]*)
      | (\s+)
      | (.)
    """,
    re.VERBOSE,
)


def lowercase_keywords(text: str) -> str:
    out = []
    for m in _TOKENIZER.finditer(text):
        ident = m.group(4)
        if ident is not None and ident.lower() in ADA_KEYWORDS:
            out.append(ident.lower())
        else:
            out.append(m.group(0))
    return "".join(out)


# Apostrophe disambiguation. The CHAR_LIT regex `'<char>'` greedily
# matches against `Foo'(X)` because the closing apostrophe of the
# next char literal supplies the third char of the match (so `'('a'`
# tokenizes as CHAR_LIT="(" + leftover `a'`). RM 2.4 says an
# apostrophe immediately following an identifier-completing token is
# always a TICK (attribute prefix), never a char-literal start. We
# insert a space after such an apostrophe so the lexer falls back to
# the TICK token via longest-match.
def disambiguate_apostrophe(text: str) -> str:
    # Identifier-completing chars: letters, digits, underscores, RPAREN,
    # and a closing apostrophe of a char literal or string-literal quote.
    # Excluding `>` because `>>` (RSHIFT) would be a label tail and is
    # not a valid attribute prefix in any context we care about.
    return re.sub(r"([A-Za-z0-9_)])'(?=[^\s'])", r"\1' ", text)


_ERR_LINE = re.compile(r"line (\d+), column (\d+)")
_SCAN_ERR_LINE = re.compile(r"<input>:(\d+):(\d+)")


def _line_at(src: str, lineno: int) -> str:
    lines = src.splitlines()
    if 1 <= lineno <= len(lines):
        return lines[lineno - 1]
    return ""


def categorize(err_msg: str, src: str) -> str:
    """Bucket a parse failure by inspecting the offending source line."""
    if err_msg.startswith("ScanError"):
        m = _SCAN_ERR_LINE.search(err_msg)
        if m:
            line = _line_at(src, int(m.group(1)))
            if "##" in line:
                return "malformed-source: empty based literal '##'"
        return "malformed-source: ScanError"

    m = _ERR_LINE.search(err_msg)
    line = _line_at(src, int(m.group(1))) if m else ""

    # Reserved words used as identifiers (invalid Ada accepted by no real Ada
    # compiler). We can recognize these because the unexpected token is one
    # of {digits, delta, range, ...} appearing where an IDENT was expected.
    if re.search(r"unexpected token 'KW_(digits|delta|range|access|all|abs|rem|mod|xor|entry|false|true|null)' '(\w+)'", err_msg):
        # If the line contains "<word> :" or ".<word>" or "(<word>" the word
        # is being used as a regular identifier.
        kw = re.search(r"KW_(\w+)", err_msg).group(1)
        if re.search(rf"\b{kw}\s*[:.,(]", line, re.IGNORECASE) or re.search(
            rf"\.{kw}\b", line, re.IGNORECASE
        ):
            return f"malformed-source: '{kw}' used as identifier"

    # `X in T'Range / T'First .. T'Last`
    if re.search(r"\bin\s+[\w\.]+'(range|first|last)\b", line, re.IGNORECASE):
        return "T'Range membership"

    # `X in A | B | C`
    if "PIPE" in err_msg or re.search(r"\bin\s+[\w'\.]+\s*\|", line, re.IGNORECASE):
        return "pipe-membership"

    # Aspect spec on protected/task type after discriminants:
    #     protected type Q (...) with Aspect => Foo is ...
    if "KW_with" in err_msg and re.search(
        r"^\s*with\s+\w", line
    ):
        return "aspect spec: with after discriminants"

    # Conditional expressions `(if X then Y else Z)` — `then` shows up where
    # the parser was expecting a binary operator inside an expression.
    if re.search(r"unexpected token 'KW_(then|else|elsif)'", err_msg):
        return "conditional expression"

    # Cascade: end-of-input where a top-level decl was expected. These almost
    # always mean an earlier construct didn't close cleanly; once that earlier
    # bug is fixed these resolve too.
    if "unexpected end of input" in err_msg:
        return "cascade: EOF after earlier error"

    # Quantified expression `for all I in ... =>` / `for some`
    if re.search(r"unexpected token 'ARROW' '=>'", err_msg) and re.search(
        r"\bfor\s+(all|some)\b", line, re.IGNORECASE
    ):
        return "quantified expression"

    # `pragma X (Y, Z)` parse hiccup
    if re.search(r"unexpected token 'COMMA'", err_msg):
        return "comma in expression context"

    # Attribute designator that isn't an IDENT (T'Class, T'Address, etc.) —
    # most common: `T'Access` / `T'Class` work, but some attributes don't.
    if "TICK" in err_msg:
        return "tick / attribute"

    # `and` / `or` showing up where a binary op was expected in a member list
    # — usually fallout from T'Range.
    if re.search(r"unexpected token 'KW_(and|or|xor)'", err_msg):
        return "and/or in expression (likely T'Range fallout)"

    return "other"


def main(argv: list[str]) -> int:
    if len(argv) < 3:
        print(__doc__, file=sys.stderr)
        return 2
    bundle_path = argv[1]
    corpus_dir = argv[2]
    save_fails = save_summary = None
    args = argv[3:]
    for i, a in enumerate(args):
        if a == "--save-fails" and i + 1 < len(args):
            save_fails = args[i + 1]
        if a == "--save-summary" and i + 1 < len(args):
            save_summary = args[i + 1]

    with open(bundle_path) as fh:
        bundle = json.load(fh)
    dfa, _tokens, skip = dfa_from_json(bundle["lex"])
    scanner = Scanner(
        dfa=dfa,
        skip_tokens=frozenset(skip),
        balanced=balanced_from_json(bundle["lex"]),
    )
    table = table_from_json(bundle["parse"])

    files = sorted(Path(corpus_dir).glob("*.ad[bs]"))
    n = len(files)
    pass_n = 0
    fails: list[tuple[str, str, str]] = []
    buckets: Counter[str] = Counter()
    by_bucket: dict[str, list[str]] = {}
    t0 = time.time()
    for i, p in enumerate(files):
        text = p.read_text(encoding="utf-8", errors="replace")
        try:
            text2 = disambiguate_apostrophe(lowercase_keywords(text))
        except Exception as e:
            fails.append((str(p), f"preprocess error: {e}", ""))
            buckets["preprocess error"] += 1
            continue
        try:
            run_parser(
                table,
                scanner.scan(text2),
                hooks=HookRegistry(ignore_missing=True),
            )
            pass_n += 1
        except ParseError as e:
            err = str(e)
            bucket = categorize(err, text2)
            fails.append((str(p), err, bucket))
            buckets[bucket] += 1
            by_bucket.setdefault(bucket, []).append(p.name)
        except Exception as e:
            err = f"{type(e).__name__}: {e}"
            bucket = categorize(err, text2)
            fails.append((str(p), err, bucket))
            buckets[bucket] += 1
            by_bucket.setdefault(bucket, []).append(p.name)
        if (i + 1) % 200 == 0:
            elapsed = time.time() - t0
            print(f"  [{i+1}/{n}] pass={pass_n} fail={len(fails)} ({(i+1)/elapsed:.1f}/s)",
                  file=sys.stderr)
    elapsed = time.time() - t0
    pct = 100.0 * pass_n / n if n else 0
    lines = [
        f"corpus: {corpus_dir}",
        f"bundle: {bundle_path}",
        f"files: {n}",
        f"pass:  {pass_n} ({pct:.1f}%)",
        f"fail:  {len(fails)}",
        f"time:  {elapsed:.1f}s",
        "",
        "buckets:",
    ]
    for name, cnt in buckets.most_common():
        lines.append(f"  {cnt:4d}  {name}")
        for f in by_bucket.get(name, [])[:3]:
            lines.append(f"          e.g. {f}")
    summary = "\n".join(lines)
    print(summary)

    if save_fails:
        with open(save_fails, "w") as fh:
            for path, err, bucket in fails:
                fh.write(f"[{bucket}] {path}\n  {err}\n")
        print(f"\nwrote fail list to {save_fails}")
    if save_summary:
        with open(save_summary, "w") as fh:
            fh.write(summary + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
