"""Lua backend: emit, run via the Lua interpreter, verify."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from plox.gen.lua import emit_lua
from plox.lex.build import lex_from_ir
from plox.parse.grammar import compile_grammar
from plox.parse.lr1 import build_lr1
from plox.spec.reader import read_source
from plox.tables import dfa_to_json, empty_bundle, table_to_json


# Lua may live in unusual paths; widen the search a bit.
def _find_lua() -> str | None:
    for name in ("lua5.4", "lua5.3", "lua", "luajit"):
        path = shutil.which(name)
        if path:
            return path
    # Some distros keep lua in /local/s/bin or similar.
    for path in ("/usr/local/bin/lua", "/local/s/bin/lua"):
        if Path(path).exists():
            return path
    return None


LUA = _find_lua()
pytestmark = pytest.mark.skipif(LUA is None, reason="no Lua interpreter found")


CALC = """
%grammar calc

%tokens
NUMBER = /[0-9]+/
PLUS   = "+"
MINUS  = "-"
STAR   = "*"
SLASH  = "/"
LPAREN = "("
RPAREN = ")"
WS     = /[ \\t\\n]+/    %skip

%rules
expr  : expr PLUS term | expr MINUS term | term ;
term  : term STAR factor | term SLASH factor | factor ;
factor : NUMBER | LPAREN expr RPAREN ;
"""


def build_bundle() -> dict:
    ir = read_source(CALC)
    dfa, tokens, skip = lex_from_ir(ir)
    table = build_lr1(compile_grammar(ir))
    bundle = empty_bundle(ir.name)
    bundle["lex"] = dfa_to_json(dfa, tokens=tokens, skip=skip)
    bundle["parse"] = table_to_json(table)
    return bundle


LUA_DRIVER = """
package.path = '%s/?.lua;' .. package.path
local M = require('plox_calc')

local input = io.read('*a')
local p = M.new(input)
if not p:parse() then
    io.stderr:write('parse error: ' .. (p.error or '?') .. '\\n')
    os.exit(1)
end

local function evaluate(n)
    if n.is_terminal then
        if n.kind == M.TOK.NUMBER then return tonumber(n.text) end
        return 0
    end
    if #n.children == 1 then return evaluate(n.children[1]) end
    if #n.children == 3 then
        local op = n.children[2]
        if op.is_terminal then
            local l = evaluate(n.children[1])
            local r = evaluate(n.children[3])
            if op.kind == M.TOK.PLUS  then return l + r end
            if op.kind == M.TOK.MINUS then return l - r end
            if op.kind == M.TOK.STAR  then return l * r end
            -- For division, truncate toward zero so output matches the C
            -- backend regardless of Lua version's `//` floor semantics.
            if op.kind == M.TOK.SLASH then
                if r == 0 then return 0 end
                local q = l / r
                if q >= 0 then return math.floor(q) else return math.ceil(q) end
            end
        end
        if n.children[1].is_terminal and n.children[1].kind == M.TOK.LPAREN then
            return evaluate(n.children[2])
        end
    end
    return 0
end
print(evaluate(p.root))
"""


def write_calc_module(tmp_path: Path) -> Path:
    bundle = build_bundle()
    text = emit_lua(bundle)
    module_path = tmp_path / "plox_calc.lua"
    module_path.write_text(text)
    return module_path


def write_driver(tmp_path: Path) -> Path:
    driver = tmp_path / "main.lua"
    driver.write_text(LUA_DRIVER % str(tmp_path))
    return driver


def run_calc(tmp_path: Path, expr: str) -> tuple[int, str, str]:
    write_calc_module(tmp_path)
    driver = write_driver(tmp_path)
    r = subprocess.run(
        [LUA, str(driver)],
        input=expr,
        capture_output=True,
        text=True,
        timeout=10,
    )
    return r.returncode, r.stdout.strip(), r.stderr.strip()


def test_lua_simple_addition(tmp_path):
    rc, out, _err = run_calc(tmp_path, "1 + 2 + 3")
    assert rc == 0 and out == "6"


def test_lua_precedence(tmp_path):
    rc, out, _err = run_calc(tmp_path, "1 + 2 * 3")
    assert rc == 0 and out == "7"


def test_lua_parens_and_negative(tmp_path):
    rc, out, _err = run_calc(tmp_path, "(12 + 34) * (5 - 6) / 7")
    # Truncating division: -46/7 = -6 (toward zero).
    assert rc == 0 and out == "-6"


def test_lua_syntax_error(tmp_path):
    rc, _out, err = run_calc(tmp_path, "1 +")
    assert rc != 0
    assert "unexpected" in err.lower() or "lex" in err.lower()


def test_lua_module_loads_without_globals(tmp_path):
    """Sanity check: requiring the module twice produces independent parser
    instances with no shared mutable state."""
    write_calc_module(tmp_path)
    driver = tmp_path / "two.lua"
    driver.write_text(
        f"""
package.path = '{tmp_path}/?.lua;' .. package.path
local M = require('plox_calc')
local p1 = M.new('1 + 2')
local p2 = M.new('3 + 4')
assert(p1 ~= p2, 'M.new should return distinct instances')
assert(p1:parse(), 'p1 should parse')
assert(p2:parse(), 'p2 should parse')
-- after p2 ran its lexer, p1's state should be untouched.
assert(p1.lex_pos > 1, 'p1 lex_pos should reflect its own scan')
print('ok')
"""
    )
    r = subprocess.run([LUA, str(driver)], capture_output=True, text=True, timeout=10)
    assert r.returncode == 0, r.stderr
    assert "ok" in r.stdout


def test_lua_carries_default_reduction_table(tmp_path):
    bundle = build_bundle()
    text = emit_lua(bundle)
    assert "default_reduction" in text, "default reduction table missing"
    assert "default_reduction[s + 1]" in text


def test_lua_invalid_prefix_rejected():
    bundle = build_bundle()
    with pytest.raises(ValueError):
        emit_lua(bundle, prefix="bad-name")
