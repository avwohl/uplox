# Cowgol with AST annotations — full-language prototype

A complete prototype of [`auto_ast.md`](auto_ast.md) annotations
applied to `examples/cowgol.uplox` (574 lines of grammar, 74
non-terminals, 19 of which carry empty alternatives that turn
into auto-optional fields). This is the prototype that drives
the ucow migration; the calc walkthrough
([`calc_annotated.md`](calc_annotated.md)) is the introduction.

Cross-check target: `~/src/ucow/src/ast.py` declares 58 node
classes by hand today. The annotated grammar reproduces all of
them and would replace the file with generator output.

## Strategy

The grammar splits naturally into four annotation regimes:

1. **Expression ladder** (`<logical_or>` → ... → `<primary>`,
   12 levels) — same pattern as calc, scaled up. `?` on every
   ladder LHS; per-operator `%ast=BinaryOp` / `%ast=Comparison` /
   `%ast=LogicalOp`; `@lhs` / `@op` / `@rhs` on the operands.
2. **Statements** (`<if_stmt>` etc.) — one `%ast=Name` per
   statement form, named fields per slot. No `?` on `<stmt>`
   itself; the dispatcher rules use the explicit `%ast=Name`
   on each alternative pattern from OQ2.
3. **Lists** (`<param_list>`, `<arg_list>`, `<case_arms>`,
   `<init_list>`, `<elseif_parts>`, `<stmt_seq>`, etc.) —
   marked `%ast=list element=<inner>` on the list-shaped rule
   itself; the using slot just says `@field` (no `*`).
4. **Optionals** (`<else_part>`, `<var_type_opt>`,
   `<var_init_opt>`, `<implements_opt>`, `<record_base_opt>`,
   `<field_offset_opt>`, `<returns_opt>`, ...) — auto-derived
   from the empty alternative; no extra annotation needed on
   either the rule or the using slot.

## Annotated grammar (excerpt — every distinct pattern shown)

Full grammar would be ~750 lines. Each pattern appears at least
once below; the rest is mechanical application.

    %ast_drop SEMI COMMA LPAREN RPAREN LBRACKET RBRACKET LBRACE RBRACE COLON
              KW_is KW_then KW_loop KW_end KW_sub KW_if KW_record KW_case

    # ---- Top level ---------------------------------------------------------

    <program> : <top_items>@items                  %ast=Program
              |                                    %ast=Program
              ;

    <top_items>%ast=list element=<top_item>
              : <top_item>
              | <top_items> <top_item>
              ;

    # Pure dispatch — each alt's own %ast= wins.
    <top_item>?
              : <include_decl>
              | <record_decl>
              | <typedef_decl>
              | <interface_decl>
              | <sub_form>
              | <stmt>
              ;

    <include_decl> : KW_include STRING@path SEMI   %ast=IncludeDecl ;

    # ---- Subroutines -------------------------------------------------------

    <sub_form>?
              : <sub_decl_or_def>
              | AT_DECL <sub_decl_only>@decl       %ast=SubForwardDecl
              | AT_IMPL <sub_impl>@impl            %ast=SubImpl
              ;

    <sub_decl_or_def>
              : KW_sub IDENT@name <sub_signature>@sig <sub_attrs>@attrs
                <implements_opt>@implements <sub_tail>@tail
                %ast=SubDecl
              ;

    <sub_signature>?
              : LPAREN <param_list_opt>@params RPAREN <returns_opt>@returns
                %ast=Signature
              ;

    <param_list>%ast=list element=<param>
              : <param>
              | <param_list> COMMA <param>
              ;

    <param>   : IDENT@name COLON <type>@type       %ast=Parameter ;

    # <param_list_opt> exists for the grammar's LR shape; the empty alt
    # makes it auto-optional. No explicit annotation needed.
    <param_list_opt>
              : <param_list>
              |
              ;

    <returns_opt>
              : COLON LPAREN <param_list>@params RPAREN  %ast=Returns
              |
              ;

    <implements_opt>
              : KW_implements IDENT@name           %ast=Implements
              |
              ;

    <sub_tail>?
              : KW_is <sub_body>@body KW_end KW_sub SEMI    %ast=SubBody
              | SEMI                                        %ast=SubExternal
              ;

    # ---- Statements --------------------------------------------------------

    <stmt>?
              : <var_decl>
              | <const_decl>
              | <if_stmt>
              | <while_stmt>
              | <loop_stmt>
              | <case_stmt>
              | <return_stmt>
              | <break_stmt>
              | <continue_stmt>
              | <asm_stmt>
              | <assign_or_call_stmt>
              ;

    <var_decl> : KW_var IDENT@name <var_type_opt>@type <var_init_opt>@init SEMI
                 %ast=VarDecl
               ;

    <var_type_opt>
              : COLON <type>@type                  %ast=_unwrap
              |
              ;

    # Note: `_unwrap` is sugar — a single-field %ast=Name whose body just
    # carries the named field through. It lets us name the inner slot
    # without introducing a wrapper node. Equivalent to writing the rule
    # without %ast=, then using the LHS as a `?`-lift, but more readable
    # when the field has a meaningful name like `type` or `init`.

    <var_init_opt>
              : ASSIGN <expr>@value                %ast=_unwrap
              |
              ;

    <const_decl> : KW_const IDENT@name ASSIGN <expr>@value SEMI %ast=ConstDecl ;

    <return_stmt>   : KW_return SEMI               %ast=ReturnStmt ;
    <break_stmt>    : KW_break SEMI                %ast=BreakStmt ;
    <continue_stmt> : KW_continue SEMI             %ast=ContinueStmt ;

    <asm_stmt> : AT_ASM STRING@first <asm_more>@more SEMI %ast=AsmStmt ;

    <asm_more>%ast=list element=<expr>
              : <asm_more> COMMA <expr>
              |
              ;

    # ---- Control flow ------------------------------------------------------

    <if_stmt> : KW_if <expr>@cond KW_then <stmt_seq>@then_body
                <elseif_parts>@elseifs <else_part>@otherwise
                KW_end KW_if SEMI
                %ast=IfStmt
              ;

    <elseif_parts>%ast=list element=<ElseIf>
              : <elseif_parts> KW_elseif <expr> KW_then <stmt_seq>
              |
              ;
    # The list element is synthesised — each `elseif <expr> then <stmt_seq>`
    # turn produces an ElseIf node. The synthesised-element form (see OQ1
    # note below) bundles per-element annotations:
    #   element=<ElseIf> {
    #       cond:      <expr>     @ rhs[-3]
    #       then_body: <stmt_seq> @ rhs[-1]
    #   }

    <else_part>
              : KW_else <stmt_seq>@body            %ast=Else
              |
              ;

    <while_stmt> : KW_while <expr>@cond KW_loop <stmt_seq>@body
                   KW_end KW_loop SEMI            %ast=WhileStmt ;
    <loop_stmt>  : KW_loop <stmt_seq>@body
                   KW_end KW_loop SEMI            %ast=LoopStmt ;

    <case_stmt> : KW_case <expr>@subject KW_is <case_arms>@arms
                  KW_end KW_case SEMI             %ast=CaseStmt ;

    <case_arms>%ast=list element=<case_arm> : ... ;

    <case_arm>?
              : KW_when <expr_list>@values COLON <stmt_seq>@body
                %ast=CaseArm
              | KW_when KW_else COLON <stmt_seq>@body
                %ast=CaseElse
              ;

    <stmt_seq>%ast=list element=<stmt_seq_item> : ... ;

    <stmt_seq_item>?
              : <stmt>
              | <sub_form>
              | <interface_decl>
              ;

    # ---- Assignment / call / multi-assign ---------------------------------

    <assign_or_call_stmt>?
              : <expr>@expr SEMI
                %ast=ExprStmt
              | <expr>@target ASSIGN <expr>@value SEMI
                %ast=Assignment
              | LPAREN <multi_target>@targets RPAREN ASSIGN <expr>@value SEMI
                %ast=MultiAssignment
              ;

    <multi_target>%ast=list element=<expr> : ... ;

    # ---- Types -------------------------------------------------------------

    <type> : <base_type>@base <array_suffixes>@suffixes  %ast=Type ;

    <base_type>?
              : <scalar_kw>
              | LBRACKET <type>@target RBRACKET                 %ast=PointerType
              | AT_SIZEOF IDENT@target                          %ast=SizeOfType
              | AT_INDEXOF IDENT@target                         %ast=IndexOfType
              | IDENT@name                                      %ast=NamedType
              | IDENT@name LPAREN <expr>@lo COMMA <expr>@hi RPAREN
                                                                %ast=RangedIntType
              ;

    <scalar_kw>?
              : KW_int8                            %ast=ScalarType
              | KW_uint8                           %ast=ScalarType
              | KW_int16                           %ast=ScalarType
              # ... each scalar keyword tagged.
              ;
    # Better: pull the keyword into a field, one node kind covers all.
    # The %ast=ScalarType actions auto-promote rhs[0] (the keyword
    # token) to a `name: token` field — but per OQ3 (no auto-promotion)
    # we either write @name on each or use per-operator kinds. The
    # idiomatic cowgol choice is per-keyword %ast=:
    #
    #     KW_int8 %ast=ScalarType@kind=int8
    #
    # ...where `@kind=int8` sets a string constant on the produced node.
    # The string-constant syntax is not in the v3.0 spec; calling out
    # the gap.  For v3.0 we'd use:
    #
    #     KW_int8@kind %ast=ScalarType
    #
    # and let the consumer read .kind.text.

    <array_suffixes>%ast=list element=<ArraySuffix>
              : <array_suffixes> LBRACKET <expr> RBRACKET   # T[N]   sized
              | <array_suffixes> LBRACKET RBRACKET          # T[]    inferred
              |
              ;
    # Each suffix produces an ArraySuffix node carrying an optional <expr>.

    # ---- Records -----------------------------------------------------------

    <record_decl> : KW_record IDENT@name <record_base_opt>@base KW_is
                    <field_list>@fields KW_end KW_record SEMI
                    %ast=RecordDecl ;

    <record_base_opt>
              : COLON IDENT@name                   %ast=_unwrap
              |
              ;

    <field_list>%ast=list element=<field_decl> : ... ;

    <field_decl> : IDENT@name <field_offset_opt>@offset COLON <type>@type SEMI
                   %ast=RecordField ;

    <field_offset_opt>
              : AT_AT LPAREN NUMBER@value RPAREN   %ast=_unwrap
              | AT LPAREN NUMBER@value RPAREN      %ast=_unwrap
              |
              ;

    # ---- Typedefs / interfaces --------------------------------------------

    <typedef_decl> : KW_typedef IDENT@name KW_is <type>@type SEMI
                     %ast=TypedefDecl ;
    <interface_decl> : KW_interface IDENT@name LPAREN <param_list_opt>@params
                       RPAREN <returns_opt>@returns SEMI
                       %ast=InterfaceDecl ;

    # ---- Expressions: precedence ladder ----------------------------------
    # Twelve levels. Each follows the same shape; first three shown.

    <expr>? : <logical_or> ;

    <logical_or>?
              : <logical_or>@lhs KW_or@op <logical_and>@rhs    %ast=LogicalOp
              | <logical_and>
              ;

    <logical_and>?
              : <logical_and>@lhs KW_and@op <logical_not>@rhs  %ast=LogicalOp
              | <logical_not>
              ;

    <logical_not>?
              : KW_not <logical_not>@operand                   %ast=NotOp
              | <comparison>
              ;

    # <comparison>, <bitwise_or>, <bitwise_xor>, <bitwise_and>, <shift>,
    # <additive>, <multiplicative>: each takes the same shape as
    # <logical_or> with their own operator tokens. Node kinds reuse
    # %ast=BinaryOp throughout, with @op carrying the discriminating
    # token. <comparison> uses %ast=Comparison instead, so the type
    # checker can find comparisons by kind without inspecting the op
    # field.

    <cast>?   : <cast>@target KW_as <type>@type             %ast=Cast
              | <unary>
              ;

    <unary>?  : MINUS@op <unary>@operand                    %ast=UnaryOp
              | TILDE@op <unary>@operand                    %ast=UnaryOp
              | AMP <unary>@operand                         %ast=AddressOf
              | AT_SIZEOF <unary>@operand                   %ast=SizeOf
              | AT_BYTESOF <unary>@operand                  %ast=BytesOf
              | AT_BYTESOF <scalar_kw>@scalar               %ast=BytesOf
              | AT_INDEXOF <unary>@operand                  %ast=IndexOf
              | AT_NEXT <unary>@operand                     %ast=Next
              | AT_PREV <unary>@operand                     %ast=Prev
              | <postfix>
              ;

    <postfix>?
              : <primary>
              | <postfix>@callee LPAREN <arg_list_opt>@args RPAREN  %ast=Call
              | <postfix>@array LBRACKET <expr>@index RBRACKET      %ast=ArrayAccess
              | <postfix>@record DOT IDENT@field                    %ast=FieldAccess
              ;

    <arg_list>%ast=list element=<expr> : ... ;

    <primary>?
              : NUMBER@value                       %ast=NumberLiteral
              | STRING@value                       %ast=StringLiteral
              | KW_nil                             %ast=NilLiteral
              | IDENT@name                         %ast=Identifier
              | LPAREN <expr> RPAREN                                  # lifts via ?
              | LBRACE <init_list_opt>@items RBRACE %ast=ArrayInitializer
              | LBRACKET <expr>@operand RBRACKET   %ast=Dereference
              ;

    <init_list>%ast=list element=<expr> : ... ;

    <expr_list>%ast=list element=<expr> : ... ;

## Things this prototype found that the calc walkthrough didn't

### 1. `_unwrap` sugar for "name a field, no new node"

The pattern

    <var_type_opt> : COLON <type>@type   %ast=_unwrap
                   |
                   ;

is common: an optional clause whose presence we care about, with
exactly one meaningful child. Without `_unwrap`, you'd choose
between:

* Wrap in a one-field node (`%ast=VarType`) — extra layer the
  consumer always unwraps.
* Use `?` to lift — but then `@type` on the using slot doesn't
  work because the using slot references the wrapped type, not
  the wrapper.

`_unwrap` is a reserved `%ast=` kind that means "this rule
contributes its named child to the parent, not a new node, but
the empty alt still marks the parent slot as optional." This is
genuinely missing from `auto_ast.md` as drafted — recording as
**spec addition needed**.

### 2. Lists with synthesised elements (`elseif_parts`)

`<elseif_parts>` in cowgol is structurally a list, but each
"element" is a tuple of `KW_elseif <expr> KW_then <stmt_seq>` —
there's no existing non-terminal that matches one elseif. Two
options:

a. Add an `<elseif_clause>` non-terminal so the list element is
   a real grammar non-terminal. Adds one production for AST
   reasons; the rule it factors out is otherwise inline.
b. Allow `element=<NewKind>` to synthesise a node kind inline
   from the surrounding RHS positions. Sketched in the
   prototype with the `element=<ElseIf>{...}` block syntax.

Option (a) is mechanical and stays inside the v3.0 spec.
Option (b) is more concise but adds another sub-language. **Pick
(a)** — cleaner, no spec extension — and add an
`<elseif_clause>` non-terminal to the migrated grammar.

### 3. Scalar keywords as a discriminator

`<scalar_kw> : KW_int8 | KW_uint8 | ...` has seven keywords. In
ucow's hand-written AST, `ScalarType` carries a string field
`name`. The grammar wants to map "which keyword?" into the node
without seven separate kinds.

The clean shapes available in the v3.0 spec:

* `%ast=ScalarType` on each alt with the keyword `@kind` field —
  produces `ScalarType(kind=Token('KW_int8', ...))`. Consumer
  reads `.kind.text` to get `"int8"`. Works, slightly verbose.
* Per-keyword node kinds (`%ast=Int8Type`, `%ast=UInt8Type`,
  ...) — seven kinds instead of one. Aligns with OQ3 idiom for
  small operator sets, but seven is past the comfort line.

Recording: ucow migration uses option (a) — `kind` as a Token
field. If usage shows consumers always switching on `.kind.text`,
v3.1 could add string-constant attributes.

### 4. `<expr>` is empty-able through `<program>`

The grammar has `<program> : <top_items> | ;` — a program can be
empty. Auto-optional kicks in on `<top_items>@items`, so
`Program.items` is `Optional[list[TopItem]]`. The runtime should
normalise this to an empty list (since `items` is *also* a list
field): empty alt → empty list, not None. This is a v3.0 spec
clarification: **list fields whose source non-terminal has an
empty alt default to `[]`, not `Optional[list[...]]`.** Adding to
OQ1.

### 5. Auto-optional fires on many slots without ceremony

Pattern count from the grammar:

    <var_type_opt>      empty alt -> VarDecl.type   optional
    <var_init_opt>      empty alt -> VarDecl.init   optional
    <implements_opt>    empty alt -> SubDecl.implements  optional
    <record_base_opt>   empty alt -> RecordDecl.base    optional
    <field_offset_opt>  empty alt -> RecordField.offset optional
    <returns_opt>       empty alt -> SubSignature.returns optional
    <param_list_opt>    empty alt -> ... params         optional
    <arg_list_opt>      empty alt -> Call.args          optional/empty-list
    <init_list_opt>     empty alt -> ArrayInit.items    optional/empty-list
    <else_part>         empty alt -> IfStmt.otherwise   optional
    <elseif_parts>      empty alt -> IfStmt.elseifs     empty-list

Eleven fields, zero per-field annotation needed. Validates the
"auto-derive from empty alt" decision in OQ1.

## Estimated annotation density

For the full cowgol grammar (574 lines today):

* `%ast_drop`: 1 line, ~9 tokens listed.
* `?` on LHS: 19 rules.
* `%ast=Name`: ~58 distinct names, applied to ~92 productions.
* `@field`: ~120 RHS positions.
* `%ast=list element=...`: 9 list-shaped rules.
* `%ast=_unwrap`: 6 single-field optional rules.

Net grammar size: ~720 lines (~25% growth). In exchange:

* `~/src/ucow/src/ast.py`: 477 lines deleted.
* `~/src/ucow/src/parser.py`: parse-tree → AST construction
  removed (~400 of 942 lines).
* The hand-written tree-walking dispatcher in `codegen.py` /
  `optimizer.py` / `postopt.py` becomes simpler — every node
  is typed, no isinstance ladder against generic Node.

Net code reduction across ucow: ~750 lines deleted to add ~150
lines of grammar annotation. **Net ~600 lines deleted, with
typing improvements throughout the consumer.**

## Spec gaps found by this prototype

Items to add to [`auto_ast.md`](auto_ast.md) before
implementation starts:

1. **`%ast=_unwrap`** — reserved kind for single-field
   transparent rules. Common enough to need first-class
   support.
2. **List fields default to empty list, not None** — clarify
   in OQ1 that an empty alt on a list-shaped rule produces
   `[]` rather than `Optional[list[...]]`. Removes a
   None-check ceremony from every consumer.
3. **Synthesised list elements** — defer; require a real
   non-terminal for the element. Document `<elseif_parts>` as
   the canonical example of "add a non-terminal for AST
   reasons even though it's otherwise inline-able."
4. **String-constant attributes** — defer to v3.1. Use Token
   field with `.text` indirection in v3.0. Don't block on this.
5. **`<scalar_kw>?`-lift interaction with `%ast=` on each alt**
   — the prototype's `<scalar_kw>?` plus per-alt `%ast=` is
   inconsistent with OQ2 "explicit-only". Resolution: the `?`
   lift applies *only* to alternatives without `%ast=`. With
   `%ast=ScalarType` on every alt, the `?` is dead-code
   noise — drop it.

## Migration order within cowgol

When the spec is implemented, ucow's migration unfolds in
roughly this order, smallest-blast-radius first:

1. **Literals and identifiers** — `<primary>` → NumberLiteral,
   StringLiteral, NilLiteral, Identifier. Tiny patch, gives a
   real AST node to test against.
2. **Expression ladder** — apply `?` + per-operator `%ast=`.
   The hand-written ast.py classes BinaryOp, Comparison,
   LogicalOp, NotOp, UnaryOp, Cast, ArrayAccess, FieldAccess,
   Call land here. Test: an expression-only parse should round-
   trip through the generated AST and produce the same shape
   as today's hand-built one.
3. **Type system** — `<type>`, `<base_type>`, `<scalar_kw>`,
   `<array_suffixes>`. Tests against ucow's type-checking pass
   (the consumer's main reader of these nodes).
4. **Declarations** — `<var_decl>`, `<const_decl>`, `<record_decl>`,
   `<typedef_decl>`, `<sub_form>` and friends.
5. **Statements** — `<if_stmt>`, `<while_stmt>`, `<loop_stmt>`,
   `<case_stmt>` and the rest. Validates auto-optionals and
   list elements end-to-end.
6. **Top level** — `<program>`, `<top_item>`, `<include_decl>`.

Each step lands as its own commit against ucow with the
matching delete from `ast.py`. By step 6 the file is empty and
can be removed.
