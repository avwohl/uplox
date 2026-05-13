# What the parser passes to semantics

When an LR parser reduces a rule, every symbol on the right-hand
side has a **value** sitting on the parser's value stack. The
parser pops those values, hands them to your semantic action, and
pushes whatever the action returns. That return becomes the
right-hand-side value of any rule that uses this non-terminal.

This doc is about that hand-off: what the parser hands you, what
you can hand back, and how to use that interface to build an AST
shaped the way you want.

If you haven't built a grammar yet, start with
[`tutorial.md`](tutorial.md) and [`cookbook.md`](cookbook.md). For
the construction-mode and conflict-resolution choices that affect
when reductions fire, see [`lr_modes.md`](lr_modes.md) and
[`conflict_resolution.md`](conflict_resolution.md).

## The interface in one picture

For a production:

    <expr> : <expr> '+' <term> ;

When the parser reduces this rule, the action sees a three-element
RHS-values list:

    rhs[0]   value of <expr>     # whatever the prior <expr> action returned
    rhs[1]   the '+' Token
    rhs[2]   value of <term>     # whatever the prior <term> action returned

The action returns one value. That value becomes the new
`<expr>`'s entry on the stack — visible as `rhs[0]` (or `rhs[2]`)
to the *next* reduction that uses `<expr>`.

In the .uplox DSL, those positions are named `$1`, `$2`, `$3`
(1-indexed), and the return is `$$`:

    <expr> : <expr> '+' <term>   { $$ = $1 + $3; } ;

`$2` is the `'+'` token — usually ignored because the rule shape
already tells you which operator was matched.

## What each `$i` actually holds

* **For an RHS terminal**, the value is the `Token` that was
  matched. A `Token` carries `.name` (the token kind, e.g.
  `"PLUS"`), `.text` (the literal source bytes, e.g. `"+"`),
  `.line`, `.column`, and `.offset`.
* **For an RHS non-terminal**, the value is *whatever the action
  for that non-terminal's last reduction returned*. There is no
  built-in "node type" — you decide what each rule returns, and
  that decision propagates upward.

If you don't register any actions, the runtime uses a generic
default action: it wraps the RHS list in a `ParseNode(kind=<lhs>,
children=[...])`. That's why an out-of-the-box `uplox parse` of
`1 + 2` produces a parse-tree pretty-print rather than `3` —
nobody told the runtime what to compute.

This is the design lever for building an AST: **decide what each
rule returns**, write the action that returns it, and the AST
shape falls out automatically.

## Designing what each rule returns

Walk the calc grammar's expression ladder bottom-up. Each rule's
return type is whatever's convenient for the rule above it.

    <factor> : NUMBER             { $$ = int($1.text); }
             | '(' <expr> ')'     { $$ = $2; }
             ;

    <term>   : <term> '*' <factor> { $$ = $1 * $3; }
             | <term> '/' <factor> { $$ = $1 / $3; }
             | <factor>            { $$ = $1; }
             ;

    <expr>   : <expr> '+' <term>   { $$ = $1 + $3; }
             | <expr> '-' <term>   { $$ = $1 - $3; }
             | <term>              { $$ = $1; }
             ;

`<factor> : NUMBER` returns an `int` (the parsed value of the
NUMBER token's text). Everything upward consumes and produces
ints. That's a calculator — no AST involved.

For an AST, return AST nodes instead of ints:

    <factor> : NUMBER             { $$ = NumLit(value=int($1.text)); }
             | '(' <expr> ')'     { $$ = $2; }
             ;

    <term>   : <term> '*' <factor> { $$ = BinOp(op='*', left=$1, right=$3); }
             | <term> '/' <factor> { $$ = BinOp(op='/', left=$1, right=$3); }
             | <factor>            { $$ = $1; }
             ;

    <expr>   : <expr> '+' <term>   { $$ = BinOp(op='+', left=$1, right=$3); }
             | <expr> '-' <term>   { $$ = BinOp(op='-', left=$1, right=$3); }
             | <term>              { $$ = $1; }
             ;

Three patterns to notice:

1. **Single-child wrapper rules pass through.** `<term> : <factor>
   { $$ = $1; }` doesn't allocate a new node — it just lifts the
   child. This is how the precedence ladder doesn't show up in
   the AST.
2. **Parenthesised expressions drop the punctuation.** `<factor> :
   '(' <expr> ')' { $$ = $2; }` returns the inner `<expr>`'s
   value; the parens vanish from the tree.
3. **Binary-operator rules build one node per match.** The shape
   of the rule tells you the operator, so the action just packs
   the operands.

The same three patterns recur in every "real" grammar. Lists
return Python lists or vectors; optional clauses return
`Some(...)` / `None`; declarations return whatever shape the
host's symbol table wants.

## Three implementations of the same interface

The parser-to-semantics interface is uniform; how you *write* the
action body depends on the backend.

### Python: register real callables

In Python, the action body in `{ ... }` is **ignored** by the
runtime. You register real Python callables, keyed by production
index. Each callable receives `(ctx, rhs_values)` and returns the
new value:

    from uplox.parse.runtime import parse

    def expr_binop(ctx, rhs):
        # rhs == [left_value, op_token, right_value]
        return ("BinOp", rhs[1].text, rhs[0], rhs[2])

    def lift(ctx, rhs):
        return rhs[0]                # single-child wrapper

    def paren_expr(ctx, rhs):
        return rhs[1]                # '(' <expr> ')' -> inner

    actions = {
        1: expr_binop,    # <expr> -> <expr> '+' <term>
        2: expr_binop,    # <expr> -> <expr> '-' <term>
        3: lift,          # <expr> -> <term>
        ...
    }

    tree = parse(table, scanner.scan(text), semantic_actions=actions)

Production indices come from the grammar's compiled order — print
`grammar.productions` to see them, or look at the
`production` field on a default-built `ParseNode`. Index 0 is
always the augmented start production (`$start -> <your-start>`);
your user productions begin at 1.

You don't have to register an action for every production. Any
production without an entry falls back to the default
`ParseNode` builder. That makes it easy to wire actions only for
the rules whose shape you care about.

`ctx` is the `ParseContext`. It exposes the grammar, the parse
stacks, and the free-form `ctx.user` dict where hooks and
actions can stash cross-cutting state.

### Python: walk the parse tree post-hoc

If you don't want to write actions during parsing, let the
runtime build the default `ParseNode` tree and walk it
afterwards. `uplox.ast.build_ast` does this with per-rule
reducers keyed by the non-terminal name:

    from uplox.ast import AstNode, build_ast
    from uplox.lex.scanner import Token

    def binop(_node, children):
        # children already filtered through drop_tokens and any prior reducers
        if len(children) == 1:
            return children[0]              # lift wrapper
        return AstNode(
            kind="BinaryOp",
            attrs={"op": children[1].text}, # the '+' / '-' Token
            children=[children[0], children[2]],
        )

    reducers = {"expr": binop, "term": binop, "factor": lambda _n, c: c[0]}
    ast = build_ast(parse_tree, reducers, drop_tokens={"LPAREN", "RPAREN"})

`drop_tokens` strips listed punctuation terminals from every
node's child list before the reducer fires — so `<factor> : '('
<expr> ')'` arrives with one child, not three. Same effect as
the `$$ = $2` pattern, expressed declaratively.

The trade between the two Python approaches:

* **Semantic actions** fire during parsing. They can interact
  with hooks, read parser state, and short-circuit construction
  for huge inputs.
* **`build_ast` reducers** fire afterward on a complete tree.
  Simpler, more declarative, dispatch is by non-terminal name
  instead of production index.

The `tests/test_ast.py` file in this repo is a worked example of
the `build_ast` approach end-to-end.

### C / C++ / Lua: post-reduce hook or post-parse walk

The C, C++, and Lua backends **do not interpret** the `{ ... }`
action body in the grammar source. They emit the default tree
builder unconditionally — each rule produces a generic node
holding `kind`, `production`, and `children[]` (terminals are
leaf nodes carrying `text` / `line` / `column`).

Hosts shape that tree into their AST two ways:

1. **Post-reduce hook during parsing.** The hook fires after
   every successful reduction with the production index and the
   just-built node. Hosts can copy fields out, transform the
   node in place, or replace its `children` array — see the
   C-backend doc for the function signature and the typedef-name
   worked example.
2. **Post-parse tree walk.** Walk the returned `uplox_<g>_node`
   tree dispatching on `node->production` (or `node->kind`),
   building host AST nodes. This is what every uc* front-end
   does — uc80, uc386, uplm80 all share the same pattern: a
   single recursive descent over the parse tree producing the
   compiler's IR.

The same "decide what each rule returns" mental model applies;
the *return* just lives in the host's data structures, populated
from the parse tree, rather than being directly produced by the
parser.

The reason this design works: the parse tree is shallow and
production-tagged, so a switch on `production` covers every shape
the grammar can produce. There is no need for the parser to know
your AST schema.

## Common patterns

### Punctuation: drop or use

For tokens like `'('`, `')'`, `','`, `';'`, `':'`:

* In an action: pick out the positions you care about (`$1`,
  `$3`) and let the others lapse. `$$ = $2` is the canonical
  "drop parens" idiom.
* In `build_ast`: list them in `drop_tokens={...}` so they don't
  even appear in the children list.
* In C/C++/Lua post-parse walk: index into `node->children[]`
  using the production's shape — `children[1]` for the inner
  `<expr>` of a `'(' <expr> ')'`.

### Lists: accumulate during the reduction

A left-recursive list returns either an accumulator or a leaf:

    <items> : <items> <item>      { $$ = $1 + [$2]; }
            | <item>              { $$ = [$1]; }
            ;

In Python the action literally builds a list. In C/C++/Lua you'd
either grow a vector during the post-reduce hook or copy the
parse-tree children list during the post-parse walk.

For lists with separators, the same shape but ignore the
separator token:

    <args> : <args> ',' <expr>    { $$ = $1 + [$3]; }
           | <expr>               { $$ = [$1]; }
           ;

### Optional clauses: `None` for empty, value for present

    <else_part> : KW_ELSE <stmt_seq>   { $$ = $2; }
                |                       { $$ = None; }
                ;

The empty alternative still fires an action; in Python that
action returns `None`. The parent rule's action then sees either
the value or `None` and can branch on it.

### Single-child lifting: keep the AST shallow

When a rule exists purely for precedence (`<term> : <factor> ;`),
its action should return the child unchanged:

    <term> : <term> '*' <factor>  { $$ = BinOp('*', $1, $3); }
           | <factor>             { $$ = $1; }
           ;

Without this lift, the AST gains a wrapper node per precedence
level — `Expr(Term(Factor(Number(42))))` instead of one
`Number(42)`. A real grammar's ladder is often 8+ levels deep
(see `examples/plm_subset.uplox`); the wrapper nodes carry no
information and make every downstream pass slower.

### Source positions: copy from the leftmost child

The grammar's start position for a reduction is the start
position of its first RHS symbol. In an action:

    def binop(ctx, rhs):
        left, op, right = rhs
        return AstNode("BinaryOp", attrs={"op": op.text},
                       children=[left, right],
                       position=_position_of(left))

`_position_of` is whatever your AST shape uses — for the runtime
`AstNode`, it's a `(line, column)` tuple stored on the node.

## When semantic actions interact with hooks

Hooks fire at specific points in the parse cycle
([`grammar_format.md`](grammar_format.md#hooks) has the list).
Two interactions are worth knowing:

* **`pre_reduce` fires before the action.** The hook sees the
  RHS values as they sat on the value stack — the same values
  the action will see as `$1, $2, ...`. The hook can read them
  but should not consume them.
* **`post_reduce` fires after the action.** The hook payload
  carries the value the action returned. This is where scope
  pushes/pops, typedef-name tracking, and other state updates
  belong.

The typical division of labour: *semantic actions* shape the
data; *hooks* update cross-cutting state.

## Recap

The parser-to-semantics contract is:

1. Every reduction hands you a list of RHS values.
2. Terminals are `Token`s; non-terminals are whatever the prior
   action for that non-terminal returned (or a default
   `ParseNode` if no action is registered).
3. Your action returns one value, which becomes that
   non-terminal's stack entry for the next reduction up.

Designing an AST is really designing what each production
returns. The backend tells you whether to write that as a
callable (Python), a tree walk after the fact (C/C++/Lua), or a
hybrid using the post-reduce hook.

## See also

* [`grammar_format.md`](grammar_format.md#rules) — the `{ ... }`
  / `$$` / `$i` syntax in `%rules`.
* [`py_backend.md`](py_backend.md) — Python `parse()` signature
  including `semantic_actions=`.
* [`c_backend.md`](c_backend.md),
  [`cpp_backend.md`](cpp_backend.md),
  [`lua_backend.md`](lua_backend.md) — node layout and
  post-reduce hook for each non-Python backend.
* [`tests/test_ast.py`](../tests/test_ast.py) — worked example
  of `build_ast` reducers turning the calc parse tree into an
  AST.
