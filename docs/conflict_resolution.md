# Conflict resolution — `%shift`, `%reduce`, and when to restructure instead

A "conflict" is the parser builder's polite way of saying: at this
state, with this lookahead, I can't tell which rule you meant. uplox
refuses to build a conflicting LR table by default, so every conflict
shows up as a build error from `uplox check`.

This doc is about diagnosing those errors and choosing the right
fix. The two yacc-style escape hatches (`%shift` and `%reduce`)
exist for a reason, but they should be the *last* tool you reach
for, not the first.

Background reading: [`cookbook.md`](cookbook.md) for the common
language shapes, [`lr_modes.md`](lr_modes.md) for what changes
when you switch construction algorithms.

## What a conflict report looks like

When `uplox check` finds conflicts, you get them itemised on stderr:

    examples/foo.uplox: 1 parser conflict(s):
    shift/reduce conflict in state N on terminal T:
      shift goes to state M
      reduce by <lhs> -> <rhs>   (foo.uplox:line)

The two pieces of information that matter most:

* **Kind** — shift/reduce or reduce/reduce.
* **Terminal** — what lookahead triggered the conflict. The
  terminal name is the key for `%shift` / `%reduce` if you end
  up using them.

Shift/reduce on ELSE means dangling-else. Reduce/reduce in LALR
mode usually means an LALR state-merge artefact (try
`%define lr.type canonical-lr` to confirm). See
[`lr_modes.md`](lr_modes.md) for the diagnostic A/B procedure.

## The decision tree

When `uplox check` reports a conflict, ask in this order:

1. **Is the grammar genuinely ambiguous?** Two different parse
   trees for the same input? If yes, restructure the grammar
   so it isn't, or accept the ambiguity and use GLR (`uplox
   parse --glr`).
2. **Is this dangling-else?** Use the matched/unmatched split
   ([`cookbook.md`](cookbook.md#nested-if-then-else-the-dangling-else-problem)).
   `%shift ELSE` is acceptable but lower quality.
3. **Did the conflict appear when you switched to LALR?** Run
   the same `uplox check` with `%define lr.type canonical-lr`.
   If the conflict goes away, it's a merge artefact — either
   restructure or use IELR ([`lr_modes.md`](lr_modes.md)).
4. **Can you restructure cheaply?** Splitting an empty production
   into explicit empty + non-empty halves, factoring a common
   prefix, or threading a discipline through one extra
   non-terminal — these are the typical fixes.
5. **Is restructuring expensive or invasive?** Reach for
   `%shift` or `%reduce`. Document *why*.

Restructuring beats `%shift`/`%reduce` because the disambiguation
is then visible in the rules themselves; the next reader doesn't
have to cross-reference the directive block.

## `%shift` — yacc-style shift preference

**What it does.** Listed terminals win shift/reduce conflicts by
shift. Reduce/reduce conflicts are unaffected (they still error).

    %shift
    ELSE
    KW_pragma KW_with

Whitespace separates entries; line breaks are insignificant.

### When to use `%shift`

* **Dangling-else.** The canonical case. The greedy-shift
  interpretation matches every C-family language: an `else`
  binds to the innermost `if`. LALR(1) lookahead is one token
  short to express this structurally without splitting
  matched/unmatched — `%shift ELSE` is a clean one-liner.
* **Structural shift-preferred ambiguities at unit boundaries.**
  In `examples/ada_full.uplox`, `%shift KW_pragma KW_with` resolves
  a real LR(1) ambiguity: at the boundary between two compilation
  units, the parser genuinely can't tell whether the upcoming
  `pragma` or `with` clause trails the previous unit or leads the
  next. Both parses are valid Ada; the shift-preferred choice
  ("attach to the next unit") matches the dominant style.

### When NOT to use `%shift`

* **As a substitute for an ambiguous expression grammar.** If you
  have shift/reduce conflicts on every binary operator, you have
  a one-rule expression grammar with no precedence ladder. Build
  the ladder ([`cookbook.md`](cookbook.md#operator-precedence-the-recursion-ladder)),
  don't paper over it.
* **On LALR merge artefacts that disappear under canonical-LR.**
  Either restructure or use IELR. `%shift` would be silencing a
  symptom of a state merge, not a real ambiguity.
* **When you haven't confirmed the conflict actually exists.**
  Typos in `%shift` are silent: listing a terminal that never
  participates in a shift/reduce is not an error. Land the
  change *without* the `%shift` line first to make sure the
  conflict you intend to silence is the one you're seeing.

## `%reduce` — yacc-style reduce preference

**What it does.** The dual of `%shift`. Listed terminals win
shift/reduce conflicts by *reduce* (close the current rule
rather than extending it).

    %reduce
    SEMI

Reduce/reduce conflicts are unaffected. A terminal listed in
both `%shift` and `%reduce` is rejected by the reader.

### When to use `%reduce`

`%reduce` is rarer than `%shift`. Reach for it when an
LALR/LR(1) ambiguity gives you a *spurious* shift on a terminal
that is genuinely in the follow-set of the to-be-reduced
non-terminal, and the surrounding decl needs to shift the
terminal cleanly.

The canonical example is in `examples/ada_full.uplox`. The
production `<subtype_indication> -> <name> 'range' <simple_expr>`
introduced an LALR conflict on SEMI: at `subtype X is T range Y;`
the shift on SEMI would extend a different production whose state
got merged in. `%reduce SEMI` closes the subtype indication
first, letting the outer declaration shift SEMI cleanly:

    %reduce
    SEMI

### When NOT to use `%reduce`

* **For dangling-else.** `%reduce ELSE` would associate the
  `else` with the *outer* `if`, which doesn't match any
  language's convention. Use the matched/unmatched split or
  `%shift ELSE` instead.
* **For genuine ambiguity.** If both interpretations are valid
  parses, picking one silently is at best a documentation
  failure. Either restructure the grammar to make the choice
  explicit, or use GLR.
* **Without first confirming the conflict.** Same warning as
  `%shift` — typos are silent.

## The "land it without the directive first" rule

Both `%shift` and `%reduce` silently accept terminals that don't
actually conflict. That's by design — yacc-compatible behaviour —
but it means a typo in the directive looks like it's working.

The discipline is:

1. Write the new grammar alternative *without* the
   `%shift`/`%reduce` line.
2. Run `uplox check`. Confirm the conflict you intend to
   resolve actually appears.
3. Add the directive line.
4. Run `uplox check` again. Confirm the conflict goes away.

Skipping step 1 means a typo in step 3 looks fine and you
discover the real conflict months later.

## What about reduce/reduce conflicts?

`%shift` and `%reduce` only resolve **shift/reduce** conflicts.
Reduce/reduce conflicts are never silenced and must be fixed
structurally:

* Restructure the grammar to merge or distinguish the competing
  reductions.
* If the reduce/reduce is an LALR merge artefact (run canonical-LR
  to check), switch to IELR or restructure.
* If both reductions are genuinely valid for the same input, the
  grammar is ambiguous — fix it or use GLR.

The reasoning: a shift/reduce choice usually has a natural greedy
answer (extend the current parse or close it). A reduce/reduce
choice means the parser has built two different trees that
collide at the same state on the same lookahead — there's no
greedy default that doesn't quietly change parse semantics.

## Worked example: dangling-else fixed three ways

The same dangling-else conflict, addressed three different ways
across grammars in this repo:

**1. Designed-out by a closer (`examples/cowgol.uplox`,
`examples/ada_full.uplox`):**

    <if_stmt> : KW_if <expr> KW_then <stmt_seq> <elseif_parts>
                <else_part> KW_end KW_if SEMI ;

No conflict to resolve. Best when you control the language.

**2. Split into matched/unmatched (`examples/plm_subset.uplox`,
`examples/c23.uplox`):**

    <stmt>           : <matched_stmt> | <unmatched_stmt> ;
    <matched_if>     : IF <expr> THEN <matched_stmt> ELSE <matched_stmt> ;
    <unmatched_if>   : IF <expr> THEN <stmt>
                     | IF <expr> THEN <matched_stmt> ELSE <unmatched_stmt> ;

No directive needed; disambiguation is visible in the grammar.

**3. `%shift ELSE` (none of the example grammars use this — they
prefer one of the structural fixes above):**

    %shift
    ELSE

Smallest diff, works correctly for C-family languages. Acceptable
but lower quality than the structural fixes because the
disambiguation is hidden in a directive block.

## See also

* [`lr_modes.md`](lr_modes.md) — picking `%define lr.type` and
  the canonical vs LALR conflict-count A/B.
* [`cookbook.md`](cookbook.md) — common shapes and the
  restructure recipes referenced here.
* [`grammar_format.md`](grammar_format.md#shift) — the full
  syntactic reference for `%shift` and `%reduce`.
