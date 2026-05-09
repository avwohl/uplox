"""End-to-end CLI tests: ``uplox build`` produces a loadable bundle."""

from __future__ import annotations

import json
from pathlib import Path

from uplox.cli.main import main
from uplox.lex.scanner import Scanner
from uplox.tables import dfa_from_json


def write_calc_grammar(tmp_path: Path) -> Path:
    src = tmp_path / "calc.uplox"
    src.write_text(
        "%grammar calc\n"
        "%options\n"
        "start = expr\n"
        "%tokens\n"
        "NUMBER = /[0-9]+/\n"
        "PLUS   = '+'\n"
        "MINUS  = '-'\n"
        "WS     = /[ \\t\\n]+/    %skip\n"
        "%rules\n"
        "<expr> : <expr> PLUS NUMBER | NUMBER ;\n"
    )
    return src


def test_build_writes_bundle(tmp_path, capsys):
    src = write_calc_grammar(tmp_path)
    out = tmp_path / "calc.json"
    rc = main(["build", str(src), "-o", str(out)])
    assert rc == 0
    bundle = json.loads(out.read_text())
    assert bundle["uplox_schema"] == "1"
    assert bundle["meta"]["grammar"] == "calc"
    assert bundle["lex"]["tokens"] == ["NUMBER", "PLUS", "MINUS", "WS"]
    assert bundle["lex"]["skip"] == ["WS"]


def test_built_bundle_drives_scanner(tmp_path):
    src = write_calc_grammar(tmp_path)
    out = tmp_path / "calc.json"
    rc = main(["build", str(src), "-o", str(out)])
    assert rc == 0
    bundle = json.loads(out.read_text())
    dfa, tokens, skip = dfa_from_json(bundle["lex"])
    sc = Scanner(dfa=dfa, skip_tokens=frozenset(skip))
    toks = sc.scan_all("12 + 34 - 5")
    assert [t.name for t in toks] == ["NUMBER", "PLUS", "NUMBER", "MINUS", "NUMBER"]


def test_build_to_stdout(tmp_path, capsys):
    src = write_calc_grammar(tmp_path)
    rc = main(["build", str(src)])
    assert rc == 0
    out = capsys.readouterr().out
    bundle = json.loads(out)
    assert bundle["lex"]["tokens"][0] == "NUMBER"


def test_check_passes(tmp_path, capsys):
    src = write_calc_grammar(tmp_path)
    rc = main(["check", str(src)])
    assert rc == 0
    msg = capsys.readouterr().out
    assert "calc" in msg and "4 tokens" in msg


def test_check_reports_reader_error(tmp_path, capsys):
    src = tmp_path / "bad.uplox"
    src.write_text("not a grammar directive\n")
    rc = main(["check", str(src)])
    assert rc == 1
    err = capsys.readouterr().err
    assert "expected" in err.lower()


def test_emit_target_c_writes_files(tmp_path):
    src = write_calc_grammar(tmp_path)
    bundle = tmp_path / "calc.json"
    main(["build", str(src), "-o", str(bundle)])
    out = tmp_path / "gen"
    rc = main(["emit", str(bundle), "--target", "c", "--out", str(out)])
    assert rc == 0
    assert (out / "uplox_calc.h").exists()
    assert (out / "uplox_calc.c").exists()


def test_emit_target_cpp_writes_files(tmp_path):
    src = write_calc_grammar(tmp_path)
    bundle = tmp_path / "calc.json"
    main(["build", str(src), "-o", str(bundle)])
    out = tmp_path / "gen"
    rc = main(["emit", str(bundle), "--target", "cpp", "--out", str(out)])
    assert rc == 0
    assert (out / "uplox_calc.hpp").exists()
    assert (out / "uplox_calc.cpp").exists()


def test_emit_target_lua_writes_module(tmp_path):
    src = write_calc_grammar(tmp_path)
    bundle = tmp_path / "calc.json"
    main(["build", str(src), "-o", str(bundle)])
    out = tmp_path / "gen"
    rc = main(["emit", str(bundle), "--target", "lua", "--out", str(out)])
    assert rc == 0
    assert (out / "uplox_calc.lua").exists()


def test_emit_target_py_writes_module(tmp_path):
    src = write_calc_grammar(tmp_path)
    bundle = tmp_path / "calc.json"
    main(["build", str(src), "-o", str(bundle)])
    out = tmp_path / "gen"
    rc = main(["emit", str(bundle), "--target", "py", "--out", str(out)])
    assert rc == 0
    assert (out / "uplox_calc.py").exists()


def test_build_emits_parse_section(tmp_path):
    src = write_calc_grammar(tmp_path)
    out = tmp_path / "calc.json"
    rc = main(["build", str(src), "-o", str(out)])
    assert rc == 0
    bundle = json.loads(out.read_text())
    assert bundle["parse"]["kind"] == "lr1"
    assert bundle["parse"]["start_symbol"] == "expr"
    assert any(p["lhs"] == "$start" for p in bundle["parse"]["productions"])
    assert len(bundle["parse"]["states"]) > 0


def test_lex_only_skips_parse_section(tmp_path):
    src = write_calc_grammar(tmp_path)
    out = tmp_path / "calc.json"
    rc = main(["build", str(src), "--lex-only", "-o", str(out)])
    assert rc == 0
    bundle = json.loads(out.read_text())
    assert bundle["parse"] == {}


def test_build_refuses_conflicts(tmp_path, capsys):
    src = tmp_path / "amb.uplox"
    src.write_text(
        "%grammar amb\n"
        "%tokens\n"
        "IF   = 'if'\n"
        "THEN = 'then'\n"
        "ELSE = 'else'\n"
        "S    = 's'\n"
        "%rules\n"
        "<stmt> : IF <cond> THEN <stmt>\n"
        "       | IF <cond> THEN <stmt> ELSE <stmt>\n"
        "       | S\n"
        "       ;\n"
        "<cond> : S ;\n"
    )
    out = tmp_path / "amb.json"
    rc = main(["build", str(src), "-o", str(out)])
    assert rc == 1
    err = capsys.readouterr().err
    assert "conflict" in err.lower()
    assert not out.exists()


def test_parse_command_round_trips(tmp_path, capsys):
    src = write_calc_grammar(tmp_path)
    bundle = tmp_path / "calc.json"
    main(["build", str(src), "-o", str(bundle)])
    inp = tmp_path / "input.txt"
    # Fixture rule: `expr : expr PLUS NUMBER | NUMBER`. Stick to that shape.
    inp.write_text("1 + 2 + 3\n")
    rc = main(["parse", str(bundle), str(inp)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "expr" in out
    assert "NUMBER '1'" in out
    assert "NUMBER '2'" in out
    assert "NUMBER '3'" in out


def test_parse_command_reports_syntax_error(tmp_path, capsys):
    src = write_calc_grammar(tmp_path)
    bundle = tmp_path / "calc.json"
    main(["build", str(src), "-o", str(bundle)])
    inp = tmp_path / "input.txt"
    inp.write_text("1 + + 2\n")
    rc = main(["parse", str(bundle), str(inp)])
    assert rc == 1
    err = capsys.readouterr().err
    assert "unexpected" in err.lower()


def test_parse_command_rejects_lex_only_bundle(tmp_path, capsys):
    src = write_calc_grammar(tmp_path)
    bundle = tmp_path / "calc.json"
    main(["build", str(src), "--lex-only", "-o", str(bundle)])
    inp = tmp_path / "input.txt"
    inp.write_text("1\n")
    rc = main(["parse", str(bundle), str(inp)])
    assert rc == 1
    err = capsys.readouterr().err
    assert "no parse section" in err.lower()


def test_check_reports_conflicts(tmp_path, capsys):
    src = tmp_path / "amb.uplox"
    src.write_text(
        "%grammar amb\n"
        "%tokens\n"
        "IF   = 'if'\n"
        "THEN = 'then'\n"
        "ELSE = 'else'\n"
        "S    = 's'\n"
        "%rules\n"
        "<stmt> : IF <cond> THEN <stmt>\n"
        "       | IF <cond> THEN <stmt> ELSE <stmt>\n"
        "       | S\n"
        "       ;\n"
        "<cond> : S ;\n"
    )
    rc = main(["check", str(src)])
    assert rc == 1
    err = capsys.readouterr().err
    assert "conflict" in err.lower()
    assert "ELSE" in err
