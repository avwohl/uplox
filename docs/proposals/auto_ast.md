# Proposal: declarative AST generation from `.uplox` grammars

**Status:** draft, May 2026 — design decisions locked (see "Open
questions" below); implementation not started.

**Summary.** Add three grammar-source annotations (`%ast_drop`,
`?` on LHS, `%ast=` per alternative, `@field` per RHS position)
so that `uplox build` produces a typed AST schema in the JSON
bundle, and each backend emits typed AST node types + a builder
function that turns the parse tree into AST nodes automatically.
Eliminates the hand-written "parse tree → AST" lowering pass
from every uc* consumer.

---

## Motivation

Every consumer of an uplox-generated parser today writes the
same pass by hand: walk the generic `ParseNode` / `uplox_<g>_node`
tree and produce typed AST nodes. The cost is measurable.

**Current uc* family sizes** (front-ends in `~/src/`):

    uada80/uada80/ast_nodes.py     1485 lines  — AST node dataclasses
    uada80/uada80/parser.py        4556 lines  — hand-written parser (pre-uplox)
    uada80/uada80/lowering.py      ~600 lines  — parse-tree → AST pass
    ucow/src/ast.py                 477 lines  — AST node dataclasses
    ucow/src/parser.py              942 lines  — hand-written parser
    uc80/src/codegen.py           10354 lines  — AST + codegen folded together
                                                 (no separate AST module)

uada80's `lowering.py` is exactly the pass this proposal would
eliminate. uc80 didn't even bother creating a separate AST layer
— the team chose to skip the boilerplate, with the obvious cost
that codegen now mixes concerns. ucow's `ast.py` is small but
non-trivial.

Across the four front-ends, **~3000 lines of hand-written code
exist solely to translate the grammar's parse tree into typed AST
nodes whose shape is already implicit in the grammar.** That's
the addressable surface.

### Why the pass exists at all

Two reasons the parse tree isn't usable as-is:

1. **Precedence ladders introduce wrapper nodes.** Eight-level
   PL/M expressions produce
   `OrXor(And(Not(Rel(Add(Mul(Unary(Primary(x))))))))` even when
   `x` is just an identifier. The lowering pass collapses
   single-child wrappers.
2. **Punctuation pollutes child lists.** `<if_stmt> : 'if' <expr>
   'then' <stmt> 'end' 'if' ';'` produces a 7-child node where
   children 1, 4, 5, 6 are tokens the AST doesn't care about. The
   lowering pass either indexes around them (fragile) or drops
   them by name.

Both of these are mechanically derivable from the grammar. The
generator could do them.

### Why not just keep using `build_ast` in Python?

The `uplox.ast.build_ast()` helper already does post-hoc reducer
dispatch — that's what `tests/test_ast.py` exercises. Two
limitations:

* Python-only. The C, C++, and Lua backends don't have an
  equivalent — they emit the generic parse-tree node struct
  and stop.
* Still requires a reducer per non-terminal. The wins are
  modest: ~30% less code than direct tree walking. The bulk
  of the lowering pass is the per-node-kind dispatch, not the
  tree walk.

A *declarative* AST schema in the grammar would move the
node-kind dispatch into the generator and let every backend
emit a typed builder.

---

## Goals and non-goals

### Goals

* G1. Allow grammars to declare per-production AST shapes
  using lightweight annotations.
* G2. Generate typed AST node types in each backend (Python
  dataclasses, C structs + tagged unions, C++ classes, Lua
  tables with `kind` fields).
* G3. Generate a default builder function that turns the parse
  result into AST nodes automatically.
* G4. Preserve full backwards compatibility: grammars without
  the new annotations behave exactly as today.
* G5. Keep the post-reduce hook firing point unchanged so name
  binding and scope tracking (which run *during* parse, not
  after) continue to work without changes.

### Non-goals

* N1. Breaking the JSON bundle's parse-table schema. The bundle's
  `lex` and `parse` sections stay byte-for-byte compatible so
  shipped bundles keep working; the `ast` section is purely
  additive. (We *do* expect to obsolete the host-side parse-tree
  output for grammars that opt into AST annotations — see the
  Migration section below.)
* N2. Type-checked AST construction. The schema is structural;
  it does not type-check that, e.g., the operand of a `Not`
  node is a boolean expression. That's a semantic-analysis job.
* N3. AST rewriting / tree transformation DSL. We are producing
  ASTs, not transforming them. Pattern-matching downstream is
  the consumer's problem.
* N4. GLR auto-AST. GLR produces a forest; the right shape for
  GLR + auto-AST is unclear and would force design decisions
  this proposal doesn't need. LR-only in v3.

---

## Prior art

A compact survey of how other parser generators handle this.

### Manual semantic actions (Bison, Lemon, Menhir, yacc)

User writes the AST building in `{ … }` action bodies. Maximum
flexibility, maximum tedium. uplox supports this today (via
Python `semantic_actions=` callables) but C/C++/Lua ignore the
action body — exactly the cost this proposal would remove.

### Visitor pattern (ANTLR4)

ANTLR generates a typed parse-tree hierarchy that mirrors the
grammar; the user writes a `Visitor<T>` whose `visitFoo` methods
return AST nodes. ANTLR4 added one quality-of-life feature:

    expr : expr '+' expr   # AddExpr
         | expr '*' expr   # MulExpr
         | INT             # IntLit
         ;

The `# Label` annotations give each alternative a distinct
parse-tree subclass, which the visitor dispatches on. Compared
to bare ANTLR3, this halves the visitor's switch-statement bulk.
It doesn't auto-build the AST, but it gives the visitor a clean
hook per alternative.

### Annotated CST (JJTree, Tree-sitter)

JJTree (a JavaCC preprocessor from the 90s) is the cleanest
example. Every production produces an AST node by default;
annotations modulate the default:

    void Expression() #Expr : { … }
    void Operator()   #void : { … }   /* drop entirely */
    void IfStmt()     #If(>3) : { … } /* node only if it has >3 children */

Tree-sitter (modern, used by editors and language servers) does
similar with "named" vs "anonymous" nodes. Anonymous nodes (any
literal token, or any rule whose name starts with `_`) don't
appear in the tree the consumer sees. Named nodes do. The result
is a CST that's already close to an AST for most consumers.

Both tools converge on the same insight: **the user has to mark
which nodes are real and which are plumbing**, and once they do,
typed construction is mechanical.

### Implicit shape from rule markers (Lark)

Lark is the modern Python parser generator that pushes this the
furthest. Three single-character markers replace most of an
AST-building visitor:

    ?expr  : expr "+" term -> add        /* `?` lifts single-child alts */
           | expr "-" term -> sub
           | term
           ;
    term   : NUMBER
           | "(" expr ")"
           ;
    %ignore _WS                          /* leading `_` = anonymous */

* `?rule` — if the production has exactly one child, pass it
  through (don't wrap).
* `_TOKEN` — leading underscore on a token name means "drop
  from the tree."
* `rule -> alias` — name the produced node after the alias, not
  the rule.

The combination is dense but expressive. Lark's tree builder
turns the grammar above into a tree where `(1+2)*3` is
`Mul(Add(1, 2), 3)`, no visitor needed.

### Tree-grammar / explicit AST mapping (Stratego/SDF, Eli)

Most explicit and most academic. The grammar defines concrete
syntax; a separate tree grammar defines the AST; an explicit
mapping (e.g., `{cons("If")}` per production) ties them
together. Powerful for source-to-source rewriting research,
heavy for a compiler front-end.

### The convergent insight

Every successful approach shares:

1. A way to mark **which non-terminals produce AST nodes** vs.
   which are precedence/plumbing.
2. A way to **strip punctuation** without listing it per rule.
3. A way to **name alternatives** so a non-terminal with N alts
   produces N different node kinds.
4. (Optional but valuable) a way to **name fields** so children
   are accessible by role, not just by index.

uplox doesn't currently have any of these as grammar-source
annotations. The proposal below adds them.

---

## Proposed design

Four new annotations in the `.uplox` grammar source. Each is
opt-in; grammars without them behave exactly as today.

### 1. `?` on a non-terminal's LHS — single-child lift

If a production of `<rule>?` has exactly one RHS symbol, the AST
builder returns that child directly without wrapping. The
precedence-ladder canonical case:

    <expr>?   : <expr> '+' <term>    %ast=Add
              | <expr> '-' <term>    %ast=Sub
              | <term>
              ;
    <term>?   : <term> '*' <factor>  %ast=Mul
              | <factor>
              ;
    <factor>? : NUMBER                %ast=NumLit
              | '(' <expr> ')'
              ;

For input `42`: the AST is `NumLit(42)`, not
`Expr(Term(Factor(NumLit(42))))`. The `?` markers collapse each
single-child wrapper. The `NumLit` alt is *not* single-child
(it has a `NUMBER` token child) and so produces a NumLit node.
The `'(' <expr> ')'` alt has three children — but the parens
are stripped by `%ast_drop` (see below), leaving one child, so
the lift applies and we get just the inner `<expr>`.

This is Lark's `?` marker, adopted directly.

### 2. `%ast=Name` per alternative — explicit node naming

A production marked with `%ast=Foo` produces an AST node of
kind `Foo` when built. Without this annotation, the default
node kind is the LHS non-terminal name (which is usually wrong
for multi-alt rules — `<stmt>` is rarely a useful AST kind).

    <stmt> : <if_stmt>        %ast=IfStmt
           | <while_stmt>     %ast=WhileStmt
           | <return_stmt>    %ast=ReturnStmt
           | <assignment>     %ast=Assign
           ;

This is ANTLR4's `# Label` syntax, recast to use uplox's
attribute style.

Two alternatives can share an `%ast=` name when they really
produce the same kind of node:

    <expr> : <expr> '+' <term>   %ast=BinOp
           | <expr> '-' <term>   %ast=BinOp
           ;

The operator distinguishes them at the AST level. See "Open
question 3" below for how the operator becomes a field.

### 3. `@field` per RHS position — field naming

Naming children makes the AST self-documenting and gives
backends a way to emit typed accessors. Default child names are
positional (`children[0]`, `children[1]`, …).

    <if_stmt> : 'if' <expr>@cond 'then' <stmt>@then_branch
                <else_part>@otherwise 'end' 'if' ';'
                %ast=If
              ;

The generated Python dataclass:

    @dataclass
    class If:
        cond: AstNode
        then_branch: AstNode
        otherwise: Optional[AstNode]
        position: tuple[int, int]

The generated C struct (sketch):

    struct uplox_g_ast_If {
        uplox_g_ast_node *cond;
        uplox_g_ast_node *then_branch;
        uplox_g_ast_node *otherwise;
        int line;
        int column;
    };

RHS positions without `@name` annotations don't end up as named
fields. They either survive as positional `extras` or, more
commonly, are punctuation that `%ast_drop` removed.

### 4. `%ast_drop` — token-level punctuation stripping

A top-level directive listing tokens that should never appear
in AST node child lists:

    %ast_drop SEMI COMMA LPAREN RPAREN LBRACE RBRACE COLON DOT

This is global to the grammar — `'('` is meaningful nowhere in
the AST, so it's wasted to mark it in every rule. The same set
applies whether the token is referenced via its name or its
literal form (`'('`).

Tokens not listed in `%ast_drop` stay in the child list as
`Token` leaves and can be named with `@field` if they carry
information (an operator, an identifier, a literal value).

### What this looks like in full

The calc grammar, annotated:

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

Six new tokens of grammar annotation (`?` × 3, `%ast_drop` × 1,
`%ast=` × 5, `@name` × 9). In exchange, parsing `(1+2)*3`
returns:

    BinOp(op=STAR, lhs=BinOp(op=PLUS, lhs=NumLit(value=1),
                                       rhs=NumLit(value=2)),
                   rhs=NumLit(value=3))

— in any of the four backends, with no host-side reducer code.

---

## Bundle schema additions

A new top-level `ast` section in the JSON bundle:

    {
      "meta":  {...},
      "lex":   {...},
      "parse": {...},
      "ast": {
        "drop_tokens": ["LPAREN", "RPAREN", "SEMI"],
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
          },
          {
            "name": "If",
            "fields": [
              {"name": "cond",        "type": "node"},
              {"name": "then_branch", "type": "node"},
              {"name": "otherwise",   "type": "node", "optional": true}
            ]
          },
          {
            "name": "Call",
            "fields": [
              {"name": "callee", "type": "node"},
              {"name": "args",   "type": "list", "element": "node"}
            ]
          }
        ],
        "production_actions": [
          {"production": 1, "kind": "BinOp",  "fields": [
              {"from": "rhs[0]", "to": "lhs"},
              {"from": "rhs[1]", "to": "op"},
              {"from": "rhs[2]", "to": "rhs"}
          ]},
          {"production": 3, "kind": "_lift", "from": "rhs[0]"},
          ...
        ]
      }
    }

* `drop_tokens` — global skip list at AST build time.
* `node_kinds` — typed schema, one entry per distinct
  `%ast=Name`. Field types:
  * `"node"` — single child AST node.
  * `"token"` — single `Token` leaf.
  * `"list"` with `"element"` — repeating field; produced by
    list-shaped rules (left-recursive accumulator etc.).
  * `"optional": true` on `node` or `token` — auto-derived when
    the field's source non-terminal has an empty alternative.
* `production_actions` — per-production directive saying what
  to do when this production reduces:
  * `kind: <Name>` — construct that node kind, populating the
    listed fields from the RHS positions.
  * `kind: _lift` — pass through `rhs[<i>]` unchanged (the `?`
    behavior).
  * `kind: _list_extend` — append `rhs[<i>]` onto an
    accumulator (list-shaped rules; generated automatically
    from the rule structure).
  * `kind: _list_init` — start a new accumulator (the base
    case of a list rule).
  * Grammars without annotations serialize without an `ast`
    section at all.

The schema is intentionally explicit and language-agnostic.
Each backend reads it once at codegen time and emits whatever
representation is idiomatic for the target language.

---

## Backend emission

### Python

Generated `uplox_<g>_ast.py` module:

    from dataclasses import dataclass, field
    from typing import Optional, Union
    from uplox.lex.scanner import Token

    @dataclass
    class _Pos:
        start_line: int = 0
        start_column: int = 0
        end_line: int = 0
        end_column: int = 0

    @dataclass
    class BinOp:
        lhs: "AstNode"
        op:  Token
        rhs: "AstNode"
        pos: _Pos = field(default_factory=_Pos)

    @dataclass
    class NumLit:
        value: Token
        pos: _Pos = field(default_factory=_Pos)

    @dataclass
    class Call:
        callee: "AstNode"
        args:   list["AstNode"]
        pos:    _Pos = field(default_factory=_Pos)

    AstNode = Union[BinOp, NumLit, If, Call, ...]

    def build_ast(parse_tree) -> AstNode:
        """Generated from the bundle's ast.production_actions."""
        ...

The existing `uplox.ast.AstNode` (the neutral schema) and
`build_ast()` (the reducer-based walker) stay in place, but the
*generated* module is what consumers will import.

The emitted `parse()` function gains an optional `as_ast=True`
flag that returns `AstNode` instead of `ParseNode`:

    import uplox_calc
    ast = uplox_calc.parse("1 + 2", as_ast=True)
    isinstance(ast, uplox_calc.BinOp)  # True

### C

Each `%ast=Name` becomes a struct; the union of all kinds
becomes a tagged-union root type:

    typedef enum {
        UPLOX_CALC_AST_BINOP,
        UPLOX_CALC_AST_NUMLIT,
        UPLOX_CALC_AST_IF,
        ...
    } uplox_calc_ast_kind;

    typedef struct uplox_calc_ast uplox_calc_ast;

    struct uplox_calc_ast_BinOp {
        uplox_calc_ast *lhs;
        uplox_calc_token *op;
        uplox_calc_ast *rhs;
    };

    struct uplox_calc_ast_Call {
        uplox_calc_ast  *callee;
        uplox_calc_ast **args;
        int              num_args;
    };

    /* Source span carried on every AST node; see u.* below. */
    struct uplox_calc_ast_pos {
        int start_line, start_column;
        int end_line,   end_column;
    };

    /* ... one struct per kind ... */

    struct uplox_calc_ast {
        uplox_calc_ast_kind       kind;
        struct uplox_calc_ast_pos pos;
        union {
            struct uplox_calc_ast_BinOp  binop;
            struct uplox_calc_ast_NumLit numlit;
            struct uplox_calc_ast_If     if_;
            struct uplox_calc_ast_Call   call;
            /* ... */
        } u;
    };

    int uplox_calc_parse_ast(uplox_calc_ctx *ctx, uplox_calc_ast **out_root);

`uplox_calc_parse()` (the existing parse-tree entry) stays;
`uplox_calc_parse_ast()` is the new typed-AST entry.

### C++

Class hierarchy with a shared base:

    namespace uplox_calc {
    struct AstNode {
        int start_line, start_column;
        int end_line,   end_column;
        virtual ~AstNode() = default;
    };
    struct BinOp : AstNode {
        std::unique_ptr<AstNode> lhs;
        Token op;
        std::unique_ptr<AstNode> rhs;
    };
    struct NumLit : AstNode {
        Token value;
    };
    struct Call : AstNode {
        std::unique_ptr<AstNode>              callee;
        std::vector<std::unique_ptr<AstNode>> args;
    };
    /* ... */
    }

Standard `std::variant<>`-based discrimination would also work
and may be cleaner; that's a small backend-level choice.

### Lua

A table with a `kind` field per node, named-field access by
`.field_name`. No separate types — Lua's idiomatic.

    -- generated module:
    local M = {}
    M.kinds = {BinOp=true, NumLit=true, If=true, ...}
    function M.parse_ast(input) ... end
    return M

Consumers dispatch with `if node.kind == "BinOp" then ...`.

---

## Migration / coexistence

Two layers move at different speeds:

### The JSON bundle: fully back-compatible

The bundle's `lex` and `parse` sections are unchanged. Any host
that loads a bundle (the Python runtime, an editor plugin, an
external tool) reads the same shape it does today. The new `ast`
section is purely additive: bundles built from an
un-annotated grammar simply omit it.

This matters for shipped bundles in the wild (uada80, ucow, etc.
all ship pre-built JSON in CI or release tarballs) and for any
future bundle consumer outside the four blessed backends.

### The generated host code: replaces the parse-tree output

When a grammar opts into AST annotations, the generated parser
**emits the typed AST as its primary output**. The generic
parse-tree entry is removed — its only purpose was to feed
hand-written lowering passes, and those passes are exactly what
this proposal eliminates. Two states per grammar:

    Un-annotated grammar:
      Python:  parse(text) -> ParseNode          (today, unchanged)
      C:       uplox_<g>_parse(...) -> node*     (today, unchanged)
      ...

    Annotated grammar (any %ast= / ? / @field):
      Python:  parse(text) -> AstNode            (replaces ParseNode)
      C:       uplox_<g>_parse(...) -> ast*      (replaces node*)
      ...

`parse()` keeps its name across both modes — the return type is
what changes. This is intentional: a consumer migrating their
grammar to AST annotations gets a compile error at the consumer
call site (return type mismatch), not a silently-changed shape
at runtime. The compile error tells them exactly which lowering
code is now obsolete and should be deleted.

For the rare case where a consumer *also* wants the parse tree
(debug viewers, editor CST integrations), we expose it behind a
separate explicit entry — but that's a niche path, not the
default:

    Python:  parse_cst(text) -> ParseNode
    C:       uplox_<g>_parse_cst(...) -> node*
    ...

Most consumers will never call this.

### Consumer migration order

1. **ucow first** — smaller, single-target, clean syntax. Switch
   `examples/cowgol.uplox` to AST annotations; replace
   `~/src/ucow/src/ast.py` + the AST-building portion of
   `parser.py` (~1400 lines) with the generated module. Use
   this round to find rough edges in the spec.
2. **uc80 second** — gets a proper AST module it doesn't
   currently have. Largest absolute win in code clarity.
3. **uada80 last** — biggest grammar, most semantic-pass
   integration; migrate once the spec is locked.

---

## Open questions

These need decisions before implementation starts.

### OQ1. Field-typing — `node`, `token`, or anything else?

**Resolved.** The schema supports four field types: `node`,
`token`, `list`, and `optional`. Eager value parsing is *not*
included; consumers parse from `.text` when they want a typed
value.

* `list` — for list-shaped rules like
  `<args> : <args> ',' <expr> | <expr>`. The generator detects
  the list shape from the rule structure (left- or
  right-recursive with a leaf alternative) and treats a `@field`
  reference to that non-terminal as a list field. Exact grammar
  surface for marking the list-shaped rule itself is TBD during
  implementation; the schema-level commitment is firm.
* `optional` — auto-derived: any `@field` whose referenced
  non-terminal has an empty alternative is `optional: true` in
  the schema. No new grammar marker needed; the empty alt is
  the signal.
* No eager value parsing. `NUMBER@value` exposes the `Token`;
  consumers do `int(value.text)` themselves. Adding integer /
  float / unescaped-string conversion would be a v3.1
  consideration if usage justifies it.

### OQ2. Default node kind when `%ast=` is absent

**Resolved: explicit-only.** Every production that contributes
to the AST must carry `%ast=Name` (or be a single-child lift
under a `?`-marked LHS, which doesn't need its own name). No
implicit "use the LHS name" default. Rationale: the LHS name is
usually a dispatch concept (`<stmt>`, `<expr>`, `<decl_item>`)
rather than an AST kind, so a silent default would produce
useless wrapper nodes that consumers would just have to
filter back out.

The codegen error message must be specific: name the
production, name the missing annotation, and suggest the
likely fix.

### OQ3. Operator tokens as fields

**Resolved.** Two supported idioms; pick per grammar:

* **Explicit `@op`** — `<expr>@lhs '+'@op <term>@rhs %ast=BinOp`.
  The grammar names a slot for the operator token; the AST node
  carries it as a `Token` field. Use this when several
  operators share a node kind.
* **Per-operator node kind** — `<expr> '+' <term> %ast=Add | <expr>
  '-' <term> %ast=Sub`. Each operator gets its own AST kind; the
  operator is encoded in `kind` rather than in a field. Cleanest
  for languages with few operators (e.g. Cowgol's casts) or
  where each operator's semantics differ enough to warrant a
  distinct visitor case.

Auto-promoting literal tokens to a string attribute was
considered and rejected — too implicit, and conflicts with the
"explicit-only" stance from OQ2.

### OQ4. AST builder hook points

**Resolved: no AST-specific hooks in v3.0.** The existing
`pre_shift` / `pre_reduce` / `post_reduce` / `on_error` hooks
keep firing at exactly the same moments, against parser state
(stack contents, production index). Name binding,
typedef-tracker, scope-stack tracking all keep working
unchanged — they operate on parser state, not on AST node
shapes.

The AST builder runs *after* the parse-time hooks have already
done their work. If a v3.1 use case emerges for hooks that
fire on typed AST nodes (e.g. a name-binding pass that wants
to walk only `Decl` nodes), we add them then.

### OQ5. Partial AST annotation — error or fall back?

**Resolved: strict for v3.0.** With OQ2 locked to
explicit-only, a grammar with *any* AST annotation
(`%ast=` / `?` / `@field`) puts the whole grammar in AST mode,
and every production reachable from the start symbol must
participate (carry `%ast=` or be `?`-lifted). Codegen errors on
the first un-annotated reachable production; the error message
names the production and the likely fix.

Strict mode runs through the ucow migration. If the experience
shows partial annotation has legitimate value (e.g., scaffolding
during incremental migration), v3.1 can relax to "un-annotated
productions are transparent" or similar.

### OQ6. Source positions

**Resolved.** Every AST node carries both a start and end
position: `start_line`, `start_column`, `end_line`, `end_column`
(four ints). Start = the leftmost RHS symbol's start position;
end = one past the rightmost RHS symbol's last consumed
position. For a `?`-lifted node, both positions pass through
unchanged.

End positions enable editor squiggle underlines, error-region
highlighting, and source maps without per-consumer recomputation.
The cost is four ints per node — trivial.

---

## Cost / risk assessment

### What this lands

* Spec: 4 new annotations, ~200 lines added to
  `grammar_format.md`.
* Compiler: per-production AST plan, new `AstPlan` in
  `src/uplox/spec/`. ~500 lines.
* Bundle: new `ast` section, additions to `src/uplox/tables/`.
  ~200 lines + schema doc.
* Python backend: emit `uplox_<g>_ast.py` per grammar. ~400 lines.
* C backend: emit struct + builder. ~600 lines.
* C++ backend: emit classes + builder. ~500 lines.
* Lua backend: emit module + builder. ~250 lines.
* Tests: end-to-end for each backend. ~800 lines.

Total: ~3500 lines of generator work. ~2-3 weeks at full focus,
~6 weeks part-time.

### What this removes

Once landed and consumers migrate:

* uada80 `lowering.py` → ~80% reduction (~480 lines deleted).
* ucow `ast.py` + `parser.py` AST-building portion → ~60%
  reduction (~600 lines deleted).
* uc80 — gets an AST module it doesn't currently have (no
  deletion, but new capability).
* Future consumers — never write the lowering pass.

Net deletions over time: ~1500 lines, plus prevented future
writes.

### Risks

* **Spec creep.** Each open question above can spawn a sub-feature
  (lists, optionals, attribute promotion, AST-specific hooks).
  Discipline: ship v3.0 with the four annotations only; defer
  list types, eager value parsing, and AST hooks to v3.1.
* **Coexistence cost.** Two parse entries per backend doubles
  the test matrix. Mitigation: factor backends so the AST
  builder is a thin shim on top of the parse-tree path.
* **Grammar-source noise.** Every annotation adds visual weight.
  Lark survives this; ANTLR's labels are widely accepted. The
  uplox style guide should encourage compact annotation
  (`@x` short names, group related `%ast_drop`s).

---

## Recommendation

Land this as **uplox v3.0**. The cost is ~3-6 weeks of generator
work; the addressable surface is ~3000 lines of currently
hand-written code across the uc* family; and every future
consumer benefits without doing anything special.

Sequence:

1. Spec and prototype against `examples/calc.uplox` —
   verify the four-annotation surface produces a clean AST.
2. Implement the Python backend first (smallest, fastest
   iteration). Land the bundle schema and the `uplox_<g>_ast`
   module emission together.
3. Migrate `examples/cowgol.uplox` end-to-end and replace
   ucow's hand-written `ast.py` + lowering. Use that experience
   to refine the spec.
4. Add C, C++, Lua backends in that order.
5. Migrate uc80, then uada80, when the consumer-side wins are
   demonstrated.

This proposal is `v0.1`. Specific design decisions in "Open
questions" above need to be locked before implementation
starts.

## See also

* [`docs/semantics.md`](../semantics.md) — current
  parser-to-semantics interface this proposal extends.
* [`docs/grammar_format.md`](../grammar_format.md) — the DSL
  surface this proposal would extend.
* [`src/uplox/ast/__init__.py`](../../src/uplox/ast/__init__.py)
  — the existing `build_ast()` / `AstNode` reducer-based
  approach, which stays in place as the "manual" path.
