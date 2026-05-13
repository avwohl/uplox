# Calc with AST annotations — worked prototype

A complete prototype of the [`auto_ast.md`](auto_ast.md)
annotations applied to `calc`, showing the grammar, the bundle's
new `ast` section, the generated Python module, and what a parse
returns.

The grammar is deliberately *over-annotated* — calc could get
away with less, but every annotation flavour appears at least
once so the prototype exercises the spec.

## Annotated grammar

`docs/proposals/calc_annotated.uplox` (would be this if the
annotations existed today):

    # Calc with AST annotations.
    %grammar calc

    %define lr.type lalr
    %options
    start = expr

    %tokens
    NUMBER = /[0-9]+/
    PLUS   = '+'
    MINUS  = '-'
    STAR   = '*'
    SLASH  = '/'
    LPAREN = '('
    RPAREN = ')'
    WS     = /[ \t\n]+/   %skip

    # Drop parentheses globally — they're never part of the AST.
    %ast_drop LPAREN RPAREN

    %rules
    <expr>?   : <expr>@lhs '+'@op <term>@rhs   %ast=BinOp
              | <expr>@lhs '-'@op <term>@rhs   %ast=BinOp
              | <term>
              ;

    <term>?   : <term>@lhs '*'@op <factor>@rhs %ast=BinOp
              | <term>@lhs '/'@op <factor>@rhs %ast=BinOp
              | <factor>
              ;

    <factor>? : NUMBER@value                    %ast=NumLit
              | '(' <expr> ')'
              ;

The diff against the current `examples/calc.uplox`:

* `%ast_drop LPAREN RPAREN` — one new directive.
* `?` on each of the three non-terminals' LHS — three characters.
* `%ast=BinOp` on the four binary-operator alternatives, plus
  `%ast=NumLit` on the literal alt — five annotations.
* `@lhs` / `@op` / `@rhs` on the binary-op RHS positions,
  `@value` on NUMBER — nine `@field` markers.

Five lines of new grammar source, total. The semantic actions
(`{ $$ = $1 + $3; }` etc.) are gone — the generated AST replaces
the documentation-only `$$` chatter.

## What each annotation does, line by line

`%ast_drop LPAREN RPAREN`
: Global directive. LPAREN and RPAREN never appear as children of
  any AST node. Applied automatically at AST-build time before
  any `%ast=` rule sees its children.

`<expr>?`
: Single-child lift marker on the LHS. Any production of
  `<expr>` with exactly one child (after `%ast_drop` filtering)
  passes that child through directly instead of wrapping it.
  Here that handles the `<expr> : <term>` alt — at AST level,
  there's no `expr` wrapper around `term`.

`<expr>@lhs '+'@op <term>@rhs   %ast=BinOp`
: The full annotated production:
  * `<expr>@lhs` — RHS position 0; becomes field `lhs` of type
    `node` on the produced AST.
  * `'+'@op` — RHS position 1; becomes field `op` of type
    `token` (the literal `'+'` is not dropped because it carries
    information — namely, which operator).
  * `<term>@rhs` — RHS position 2; becomes field `rhs`.
  * `%ast=BinOp` — the produced AST node has kind `BinOp`.

`<factor>? : NUMBER@value %ast=NumLit`
: One-RHS-symbol production marked `%ast=NumLit`. The `?` on
  `<factor>` would normally lift, but this production is
  explicitly marked `%ast=NumLit` so the lift doesn't apply
  (the annotation wins). The NumLit node has one field, `value`,
  holding the NUMBER token.

`<factor>? : '(' <expr> ')'`
: After `%ast_drop` strips LPAREN and RPAREN, this production
  has one child (`<expr>`). No `%ast=` annotation, so the `?`
  on `<factor>` kicks in and lifts. Result: parenthesised
  expressions vanish from the AST.

`<term>` and `<term> @lhs '*'@op <factor>@rhs %ast=BinOp`
: Identical pattern to `<expr>`. The two binary-op rules
  produce the same AST kind (`BinOp`); the operator token
  is what distinguishes them at the AST level.

## Generated bundle: the `ast` section

The full `lex` and `parse` sections of the bundle are unchanged.
The new `ast` section:

```json
{
  "ast": {
    "drop_tokens": ["LPAREN", "RPAREN"],
    "node_kinds": [
      {
        "name": "BinOp",
        "fields": [
          {"name": "lhs", "type": "node"},
          {"name": "op",  "type": "token"},
          {"name": "rhs", "type": "node"}
        ]
      },
      {
        "name": "NumLit",
        "fields": [
          {"name": "value", "type": "token"}
        ]
      }
    ],
    "production_actions": [
      {"production": 0, "kind": "_start"},
      {"production": 1, "kind": "BinOp",
        "fields": [
          {"from": "rhs[0]", "to": "lhs"},
          {"from": "rhs[1]", "to": "op"},
          {"from": "rhs[2]", "to": "rhs"}
        ]},
      {"production": 2, "kind": "BinOp",
        "fields": [
          {"from": "rhs[0]", "to": "lhs"},
          {"from": "rhs[1]", "to": "op"},
          {"from": "rhs[2]", "to": "rhs"}
        ]},
      {"production": 3, "kind": "_lift", "from": "rhs[0]"},
      {"production": 4, "kind": "BinOp",
        "fields": [
          {"from": "rhs[0]", "to": "lhs"},
          {"from": "rhs[1]", "to": "op"},
          {"from": "rhs[2]", "to": "rhs"}
        ]},
      {"production": 5, "kind": "BinOp",
        "fields": [
          {"from": "rhs[0]", "to": "lhs"},
          {"from": "rhs[1]", "to": "op"},
          {"from": "rhs[2]", "to": "rhs"}
        ]},
      {"production": 6, "kind": "_lift", "from": "rhs[0]"},
      {"production": 7, "kind": "NumLit",
        "fields": [
          {"from": "rhs[0]", "to": "value"}
        ]},
      {"production": 8, "kind": "_lift", "from": "rhs[1]"}
    ]
  }
}
```

Productions 1–8 mirror calc's user productions (production 0 is
the augmented start, marked `_start`). The bundle stays
language-agnostic — every backend reads this same schema.

Notice production 8 (`<factor> : '(' <expr> ')'`): after
`%ast_drop` removes LPAREN at `rhs[0]` and RPAREN at `rhs[2]`,
the surviving child is `rhs[1]`. The lift action records the
explicit index. (If we wanted a more compact schema, we could
have the runtime apply `drop_tokens` once and then lift `rhs[0]`
of the filtered list — the prototype shows the explicit-index
form for clarity.)

## Generated Python module: `uplox_calc.py`

The Python backend emits (sketched):

```python
"""Generated by uplox 3.0.0 from calc.uplox. Do not edit."""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Union
from uplox.lex.scanner import Token
from uplox.parse.runtime import parse as _parse_lr1
# ... bundle, scanner, table loading omitted ...


@dataclass
class _Pos:
    start_line:   int = 0
    start_column: int = 0
    end_line:     int = 0
    end_column:   int = 0


@dataclass
class BinOp:
    lhs: "AstNode"
    op:  Token
    rhs: "AstNode"
    pos: _Pos = field(default_factory=_Pos)


@dataclass
class NumLit:
    value: Token
    pos:   _Pos = field(default_factory=_Pos)


AstNode = Union[BinOp, NumLit]


def parse(text: str) -> AstNode:
    """Parse `text` and return the AST root."""
    parse_tree = _parse_lr1(_TABLE, _SCANNER.scan(text),
                            semantic_actions=_ACTIONS)
    return parse_tree   # semantic actions already built the AST


# Internal: per-production action table, generated from the
# bundle's ast.production_actions.
_ACTIONS = {
    1: lambda ctx, rhs: BinOp(lhs=rhs[0], op=rhs[1], rhs=rhs[2],
                               pos=_pos_span(rhs)),
    2: lambda ctx, rhs: BinOp(lhs=rhs[0], op=rhs[1], rhs=rhs[2],
                               pos=_pos_span(rhs)),
    3: lambda ctx, rhs: rhs[0],
    4: lambda ctx, rhs: BinOp(lhs=rhs[0], op=rhs[1], rhs=rhs[2],
                               pos=_pos_span(rhs)),
    5: lambda ctx, rhs: BinOp(lhs=rhs[0], op=rhs[1], rhs=rhs[2],
                               pos=_pos_span(rhs)),
    6: lambda ctx, rhs: rhs[0],
    7: lambda ctx, rhs: NumLit(value=rhs[0], pos=_pos_span(rhs)),
    # Production 8: '(' <expr> ')' — LPAREN and RPAREN dropped by
    # %ast_drop before this action runs, so rhs has one element.
    8: lambda ctx, rhs: rhs[0],
}
```

`_pos_span(rhs)` computes `_Pos` from the leftmost and rightmost
RHS elements' positions — generated boilerplate, not consumer
concern.

The notable shape change: the consumer-visible API is
`uplox_calc.parse(text) -> AstNode`. No `ParseNode`, no
`build_ast`, no reducers. Just types.

## Worked example: `(1 + 2) * 3`

Today's parse tree (from `uplox parse calc.json`):

    expr
      term
        term
          factor
            LPAREN '('
            expr
              expr
                term
                  factor
                    NUMBER '1'
              PLUS '+'
              term
                factor
                  NUMBER '2'
            RPAREN ')'
        STAR '*'
        factor
          NUMBER '3'

Sixteen nodes, mostly precedence-ladder wrappers and parens.

With annotations, `uplox_calc.parse("(1 + 2) * 3")` returns:

    BinOp(
        op=Token('STAR', '*'),
        lhs=BinOp(
            op=Token('PLUS', '+'),
            lhs=NumLit(value=Token('NUMBER', '1')),
            rhs=NumLit(value=Token('NUMBER', '2')),
        ),
        rhs=NumLit(value=Token('NUMBER', '3')),
    )

Five nodes. Every node carries a kind and a `pos` span. No
wrapper nodes, no parens.

Evaluating it is a two-line recursive walk:

```python
def evaluate(node):
    if isinstance(node, NumLit):
        return int(node.value.text)
    return _OPS[node.op.text](evaluate(node.lhs), evaluate(node.rhs))

_OPS = {'+': operator.add, '-': operator.sub,
        '*': operator.mul, '/': operator.floordiv}
```

Compare to today's `tests/test_ast.py` — it needs roughly the
same number of lines just to *build* the equivalent AST out of
the parse tree, before any evaluation.

## What the C backend would emit

The C backend gets per-kind structs plus a tagged union, all
prefixed with `uplox_calc_`:

```c
typedef enum {
    UPLOX_CALC_AST_BINOP = 1,
    UPLOX_CALC_AST_NUMLIT,
    UPLOX_CALC_AST__COUNT__
} uplox_calc_ast_kind;

struct uplox_calc_ast_pos {
    int start_line, start_column;
    int end_line,   end_column;
};

struct uplox_calc_ast_BinOp {
    struct uplox_calc_ast *lhs;
    struct uplox_calc_token *op;
    struct uplox_calc_ast *rhs;
};

struct uplox_calc_ast_NumLit {
    struct uplox_calc_token *value;
};

struct uplox_calc_ast {
    uplox_calc_ast_kind        kind;
    struct uplox_calc_ast_pos  pos;
    union {
        struct uplox_calc_ast_BinOp  binop;
        struct uplox_calc_ast_NumLit numlit;
    } u;
};

int uplox_calc_parse(uplox_calc_ctx *ctx, struct uplox_calc_ast **out);
```

A C consumer's evaluator:

```c
int evaluate(const struct uplox_calc_ast *n) {
    switch (n->kind) {
    case UPLOX_CALC_AST_NUMLIT:
        return atoi(n->u.numlit.value->text);   /* text is NUL-padded inside ctx */
    case UPLOX_CALC_AST_BINOP: {
        int l = evaluate(n->u.binop.lhs);
        int r = evaluate(n->u.binop.rhs);
        switch (n->u.binop.op->text[0]) {
        case '+': return l + r;
        case '-': return l - r;
        case '*': return l * r;
        case '/': return l / r;
        }
    }
    }
    return 0;
}
```

That's the entire calc evaluator in C — no parse-tree walking
helpers, no manual node-kind enumeration. Today's C consumer
would write 3–4× that for the lowering pass alone before getting
to the evaluator.

## What disappears, concretely

For calc itself, almost nothing — it's a smoke test. The
addressable surface is in real grammars. Projected against
ucow's existing code:

* `~/src/ucow/src/ast.py` (477 lines of `@dataclass` AST node
  definitions) — **deleted entirely**, replaced by the generated
  `uplox_cowgol_ast.py`.
* The parse-tree → AST portion of `~/src/ucow/src/parser.py`
  (~400 of the 942 lines) — **deleted**. The remaining
  ~540 lines are the hand-written lexer and shift-reduce driver,
  which switch to uplox's generated parser as a separate
  migration.

`evaluate()` in calc is the pedagogical version of every
back-end's tree walker. Every consumer writes that walker
exactly once today; with annotations, they still write the walk
(it's domain logic), but they don't write the parse-tree-to-AST
adapter feeding it.

## Open knobs this prototype doesn't exercise

The four annotations cover calc completely; deeper grammar
features need spec extensions that the prototype defers:

* **Lists** — calc has no list-shaped rules. ucow's argument
  lists, type lists, and statement sequences will exercise the
  `list` field type.
* **Optionals** — calc has no optional clauses. PL/M's
  `<else_part>` and Ada's `<aspect_spec_opt>` will exercise the
  auto-derived `optional: true`.
* **Per-operator node kinds** — calc lumps `+`, `-`, `*`, `/`
  into `BinOp` with the operator as a Token field. The
  alternative ([`auto_ast.md`](auto_ast.md) OQ3) is per-operator
  kinds (`Add`, `Sub`, `Mul`, `Div`). Both are supported; calc
  happens to choose the field idiom.
* **Hooks interacting with AST nodes** — calc has no hooks.
  scoped.uplox and c23.uplox would be the prototypes for that
  interaction.

These get prototyped against the real grammars during the ucow
migration.
