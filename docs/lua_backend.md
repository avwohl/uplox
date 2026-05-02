# plox Lua backend

The Lua backend emits a single `plox_<grammar>.lua` module per grammar.
Targets Lua 5.3 and later (uses integer division and bitops where useful;
no LuaJIT-specific syntax).

## Public API

For a grammar named `calc`:

```lua
local M = require('plox_calc')

-- Construct a parser over an input string.
local parser = M.new(input)

-- Parse. Returns true on success; sets parser.root, parser.error.
if not parser:parse() then
    error(parser.error)
end

local root = parser.root          -- table with .kind, .is_terminal, .children, …

-- Symbolic enums (token / non-terminal indices).
M.TOK.NUMBER         -- integer token kind
M.NT.EXPR            -- integer non-terminal kind
M.token_name(kind)   -- string
M.nt_name(kind)      -- string
```

Each `Node` is a plain Lua table with these fields:

```lua
{
    is_terminal = true|false,
    kind        = <integer>,
    production  = <integer or -1>,
    line, column,
    text        = '<lexeme>',  -- terminals only
    children    = { ... },     -- 1-indexed list of child nodes
}
```

## Lexer feedback (since 1.1.0)

```lua
parser:set_token_filter(function(parser, kind, text, len)
    -- Return a (possibly rewritten) terminal kind.
    return kind
end)

parser:set_post_reduce(function(parser, prod, node)
    -- Update host state visible to the next filter call.
end)
```

The runtime invokes the filter on every freshly fetched lookahead and
again after every reduction; the post-reduce hook fires before the
filter re-runs. Same ordering and contract as the C and C++ backends.

## Worked example

[`tests/test_gen_lua.py::test_lua_typedef_name_hack_end_to_end`](../tests/test_gen_lua.py)
implements the C typedef-name hack in Lua with closures over a shared
typedef table. Use it as the template for any feedback-style grammar.

## Re-entrancy

`M.new(input)` returns a fresh parser table. There is no module-level
state mutated during parsing. Two grammars produce two distinct modules
under different `require` paths.

## Status of out-of-scope items

Same as C / C++: no semantic-action injection beyond the post-reduce
hook, no error recovery. Walk the parse tree.
