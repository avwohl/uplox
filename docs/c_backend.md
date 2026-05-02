# plox C backend

The C backend emits a **self-contained** `.c` / `.h` pair for each grammar.
There is no shared runtime library — every generated file embeds its own
DFA tables, ACTION/GOTO tables, scanner, and shift-reduce driver. This
keeps the integration story trivial: drop the two files into your project,
add them to your build, and you're done.

## Re-entrancy guarantee

Every piece of mutable state lives on a `plox_<grammar>_ctx`. The generator
verifies that:

* No file-scope mutable variables are emitted. Tables are `static const`.
* Every function takes a `ctx` pointer (or returns one).
* All identifier names that escape the translation unit are prefixed with
  `plox_<grammar>_` or `PLOX_<GRAMMAR>_`.

Two grammars built with plox can therefore link into the same binary with
zero collisions. This is the **acceptance test** for the C backend, not a
nice-to-have — uc80 and uc386 are designed to share the uc_core front-end
and link side-by-side, and the build setup uses both compilers from the
same shell.

## Public API

For grammar named `calc`:

```c
typedef struct plox_calc_ctx plox_calc_ctx;

typedef enum {
    PLOX_CALC_TOK_END = 0,           /* synthetic end-of-input */
    PLOX_CALC_TOK_NUMBER,
    PLOX_CALC_TOK_PLUS,
    /* ... one entry per declared token */
} plox_calc_token_kind;

typedef enum {
    PLOX_CALC_NT__START__ = 0,      /* augmented start */
    PLOX_CALC_NT_EXPR,
    PLOX_CALC_NT_TERM,
    /* ... one entry per declared rule */
} plox_calc_nt_kind;

typedef struct {
    int          kind;          /* token kind for leaves, nonterminal kind otherwise */
    int          is_terminal;   /* nonzero for token leaves */
    int          production;    /* -1 for terminals */
    int          line;          /* both forms carry source position */
    int          column;
    const char  *text;          /* leaves: lexeme (into input buffer); else NULL */
    int          text_len;
    struct plox_calc_node **children;
    int          num_children;
} plox_calc_node;

plox_calc_ctx *plox_calc_create(const char *input, int input_len);
void           plox_calc_destroy(plox_calc_ctx *ctx);
int            plox_calc_parse(plox_calc_ctx *ctx, plox_calc_node **out_root);
const char    *plox_calc_error(const plox_calc_ctx *ctx);

/* Symbolic names for tokens and non-terminals (debugging / pretty-printing) */
const char *plox_calc_token_name(int token_kind);
const char *plox_calc_nt_name(int nt_kind);
```

* `plox_calc_create` allocates a ctx and copies a pointer into the input
  buffer. The caller owns the input buffer; it must outlive the ctx.
* `plox_calc_parse` returns 0 on success. The output tree is owned by
  the ctx and freed by `plox_calc_destroy`.
* On parse error, `plox_calc_error` returns a stable C string describing
  the first error; the parser stops at the first unrecoverable error.

## What's *not* in v0

* Hook callbacks. The Python runtime resolves named hooks to callables;
  for C, the equivalent would be a registry-of-function-pointers. Phase 7
  defers this — generate the parse tree, let the host walk it. The
  callback registry lands in a Phase 7 follow-up.
* Custom semantic actions. The grammar's `{ ... }` action text is copied
  into the bundle but ignored by the C emitter. Hosts that want
  per-production semantic actions either walk the tree post-parse or wait
  for the action-injection feature.
* Error recovery. The driver stops at the first error.

These restrictions match what the uc* front-ends actually need from a
parse-only port and don't compromise the re-entrancy guarantee. They
cover everything except hand-written name resolution that the existing
front-ends can keep doing on the resulting tree.
