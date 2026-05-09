# uplox C backend

The C backend emits a **self-contained** `.c` / `.h` pair for each grammar.
There is no shared runtime library — every generated file embeds its own
DFA tables, ACTION/GOTO tables, scanner, and shift-reduce driver. This
keeps the integration story trivial: drop the two files into your project,
add them to your build, and you're done.

## Re-entrancy guarantee

Every piece of mutable state lives on a `uplox_<grammar>_ctx`. The generator
verifies that:

* No file-scope mutable variables are emitted. Tables are `static const`.
* Every function takes a `ctx` pointer (or returns one).
* All identifier names that escape the translation unit are prefixed with
  `uplox_<grammar>_` or `UPLOX_<GRAMMAR>_`.

Two grammars built with uplox can therefore link into the same binary with
zero collisions. This is the **acceptance test** for the C backend, not a
nice-to-have — uc80 and uc386 are designed to share the uc_core front-end
and link side-by-side, and the build setup uses both compilers from the
same shell.

## Public API

For a grammar named `calc`:

```c
typedef struct uplox_calc_ctx uplox_calc_ctx;

typedef enum {
    UPLOX_CALC_TOK__EOI_ = 0,        /* synthetic end-of-input marker */
    UPLOX_CALC_TOK_NUMBER,
    UPLOX_CALC_TOK_PLUS,
    /* ... one entry per declared token */
    UPLOX_CALC_TOK__COUNT__
} uplox_calc_token_kind;

typedef enum {
    UPLOX_CALC_NT__START__ = 0,      /* augmented start non-terminal */
    UPLOX_CALC_NT_EXPR,
    UPLOX_CALC_NT_TERM,
    /* ... one entry per declared rule */
    UPLOX_CALC_NT__COUNT__
} uplox_calc_nt_kind;

typedef struct uplox_calc_node uplox_calc_node;
struct uplox_calc_node {
    int          kind;          /* token kind for leaves, non-terminal kind otherwise */
    int          is_terminal;   /* nonzero for token leaves */
    int          production;    /* -1 for terminals; production index otherwise */
    int          line;
    int          column;
    const char  *text;          /* leaves: lexeme (into input buffer); non-NUL-terminated */
    int          text_len;
    uplox_calc_node **children;
    int          num_children;
};

/* Lifecycle */
uplox_calc_ctx *uplox_calc_create(const char *input, int input_len);
void           uplox_calc_destroy(uplox_calc_ctx *ctx);
int            uplox_calc_parse(uplox_calc_ctx *ctx, uplox_calc_node **out_root);
const char    *uplox_calc_error(const uplox_calc_ctx *ctx);

/* Symbolic names for tokens and non-terminals (debugging / pretty-printing) */
const char *uplox_calc_token_name(int token_kind);
const char *uplox_calc_nt_name(int nt_kind);
```

* `uplox_calc_create` allocates a ctx and stores a pointer into the input
  buffer. The caller owns the input buffer; it must outlive the ctx.
* `uplox_calc_parse` returns 0 on success. The output tree is owned by
  the ctx and freed by `uplox_calc_destroy`.
* On parse error, `uplox_calc_error` returns a stable C string describing
  the first error; the parser stops at the first unrecoverable error.

## Lexer feedback (since 1.1.0)

For grammars that need parser state to influence terminal classification —
the C typedef-name hack is the canonical case — the emitted parser
exposes two callbacks. They fire in the same order the Python runtime
uses, so a host that prototyped the logic in Python ports without
surprises.

### Token filter

```c
typedef int (*uplox_calc_token_filter_fn)(
    uplox_calc_ctx *ctx,
    int            la_kind,
    const char    *la_text,    /* into input buffer; not NUL-terminated */
    int            la_len,
    void          *user_data);

void uplox_calc_set_token_filter(
    uplox_calc_ctx               *ctx,
    uplox_calc_token_filter_fn    fn,
    void                        *user_data);
```

The runtime invokes the filter on every freshly fetched lookahead and
again after every reduction. Returning a different kind reroutes the
parser's next ACTION lookup; returning `la_kind` unchanged is a no-op.

### Post-reduce hook

```c
typedef void (*uplox_calc_post_reduce_fn)(
    uplox_calc_ctx  *ctx,
    int             prod_index,
    uplox_calc_node *node,
    void           *user_data);

void uplox_calc_set_post_reduce(
    uplox_calc_ctx             *ctx,
    uplox_calc_post_reduce_fn   fn,
    void                      *user_data);
```

Fires after every successful reduction (the augmented-start production
is internal and not surfaced). The hook runs **before** the token filter
re-runs on the pending lookahead, so any state change is visible by the
next ACTION lookup.

### Pattern: typedef-name in C

```c
static int filter(uplox_calc_ctx *ctx, int la_kind, const char *t, int len, void *ud) {
    typedef_set_t *s = ud;
    if (la_kind == UPLOX_CALC_TOK_IDENT && set_contains(s, t, len)) {
        return UPLOX_CALC_TOK_TYPEDEF_NAME;
    }
    return la_kind;
}

static void on_reduce(uplox_calc_ctx *ctx, int prod, uplox_calc_node *n, void *ud) {
    typedef_set_t *s = ud;
    if (prod == TYPEDEF_DECL_PROD && n->num_children >= 2) {
        uplox_calc_node *id = n->children[1];
        set_add(s, id->text, id->text_len);
    }
}

uplox_calc_set_token_filter(ctx, filter, &my_typedefs);
uplox_calc_set_post_reduce(ctx, on_reduce, &my_typedefs);
```

A complete worked example is in
[`tests/test_gen_c.py::test_emitted_c_typedef_name_hack_end_to_end`](../tests/test_gen_c.py).

## Balanced-bracket tokens (since 1.3.0)

Tokens declared with `%balanced='<close>'` in the grammar source come
through to the emitted scanner via a per-grammar `uplox_<g>_token_balanced[]`
array (one entry per token, holding the close-delimiter byte or 0). After
the DFA matches a balanced token, the scanner extends the match by
counting nested open/close pairs in the input until depth returns to
zero — the open byte is whatever the DFA already consumed at the start
of the token. An unmatched close raises an "unterminated balanced token"
error via the same `uplox_<g>_error` path as a lexical error.

The feature is what lets non-regular bodies (target-language action
bodies, Lua-style long brackets, etc.) be lexed as a single token.
The Python runtime had it since 1.2.0; the C backend gained it in 1.3.0.

## Default reductions

The emitted ACTION table is sparse; a state whose only valid actions are
all reduce-X for the same production X gets a `-1`-default entry in
`uplox_<g>_default_reduction[]`. The driver consults that on ACTION miss
before erroring. This is what makes the typedef-name hack work in
canonical LR(1) — without default reductions, the parser errors before
the post-reduce hook can update the host's typedef set.

## What's still out of scope

* Custom semantic actions. The grammar's `{ ... }` action text is copied
  into the JSON bundle but ignored by the C emitter. Hosts that want
  per-production semantic actions either walk the parse tree post-parse
  or use the post-reduce hook described above (which carries the
  production index and the just-built node — most "semantic action" use
  cases reduce to that).
* Error recovery. The driver stops at the first error.

These restrictions match what the uc* front-ends actually need from a
parse-only port and don't compromise the re-entrancy guarantee. Name
resolution, type checking, and similar concerns happen on the resulting
tree using the host's existing logic — exactly the integration the
front-ends were written against.
