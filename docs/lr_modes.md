# Picking `%define lr.type` — canonical-LR(1), LALR(1), IELR(1)

uplox can build parse tables three different ways:

    %define lr.type canonical-lr   # default
    %define lr.type lalr
    %define lr.type ielr

The choice trades **table size** against **conflict honesty**. This
doc tells you which one to pick and why, with concrete cost numbers
from the example grammars in this repo.

If you're brand new to uplox, read [`tutorial.md`](tutorial.md)
first, then come back here when your first non-trivial grammar
starts taking too long to build.

## TL;DR

* **Default to `canonical-lr`.** Every conflict it reports is a
  real LR(1) conflict, and the diagnostics aren't muddied by
  state-merging artefacts. For grammars under ~200 productions
  this is fine.
* **Switch to `lalr` when build time or bundle size start to hurt.**
  ~10× smaller tables, ~10× faster builds, and most real grammars
  are LALR-friendly with no restructuring. Verify with a quick A/B
  `uplox check`.
* **Reach for `ielr` only if LALR introduces a spurious conflict
  you can't easily restructure away.** Same conflict count as
  canonical, table size typically equal to LALR.

The `%shift` / `%reduce` escape hatches work identically in all
three modes; see [`conflict_resolution.md`](conflict_resolution.md).

## The three algorithms in one sentence each

* **canonical-LR(1):** every distinct lookahead context gets its
  own state. Tables can be 10–100× larger than LALR but every
  conflict is real.
* **LALR(1):** post-process the canonical table by collapsing
  states that share an LR(0) core (items with lookaheads
  stripped) and taking the union of their lookaheads. Smaller
  tables, but the merge can manufacture reduce/reduce conflicts
  that didn't exist in canonical LR(1).
* **IELR(1):** start with LALR; for each merged state that has a
  conflict the contributing canonical states don't have,
  pull those states back out of the merge group; repeat. You
  end up with LALR-sized tables for LALR-friendly grammars and
  carefully-split tables for the few cases where LALR merging
  manufactures conflicts.

## Concrete cost data from this repo

`%define lr.type canonical-lr` vs `lalr`, state counts after
running `uplox check` against each example grammar. The shrink
factor varies enormously with grammar shape:

    grammar           canonical    lalr   shrink
    ada_full              58611    1594    36.8x
    ada_subset             3574     417     8.6x
    c23                    3395     549     6.2x
    plm_full               1312     296     4.4x
    plm_subset              741     198     3.7x
    calc                     30      16     1.9x
    ambig_expr               18      10     1.8x   (8-conflict fixture)
    scoped                   13      10     1.3x
    plm_pre                  27      27     1.0x
    uplox_self               67      67     1.0x

For `ada_full` specifically, the same A/B looks like:

                          canonical-lr     lalr
    states                      58611      1594
    JSON bundle                  28 MB    1.4 MB
    build time                ~25 min    ~2 min

That's the operational cost of canonical LR(1) at the high end:
a 28 MB bundle and a 25-minute build (LALR's peak RSS during this
build was 2.9 GB; canonical needs noticeably more, which is why
the working setup runs under a memory watchdog). LALR is the
practical choice for any production-scale grammar.

For tiny grammars, the difference doesn't matter — `calc` goes
from 30 to 16 states, building both in well under a second.

## When canonical-LR(1) is the right call

* You're writing a brand-new grammar and the build still finishes
  in under a few seconds. Canonical's diagnostics are the gold
  standard; use them while you're iterating.
* You hit a conflict and want to be certain it's real before
  restructuring. A quick A/B (`canonical-lr` first, then `lalr`)
  tells you whether the conflict is structural or a merge artefact.
* You're working on a research grammar where exact LR(1) properties
  matter more than build time.

The fact that this is the default reflects all three above —
new grammars should start in the mode that gives the most honest
feedback.

## When LALR(1) is the right call

This is the right call for *almost every production grammar.*

* `uplox check` builds slowly enough to break your flow.
* The JSON bundle is awkwardly large for what you're embedding it
  in (CI fixtures, browser-shipped Wasm, embedded firmware).
* You ran an A/B and confirmed your grammar is LALR-friendly: same
  conflict count under both modes.

The verification is one extra `uplox check` run. Comment out the
`%define lr.type lalr` line, run check, note conflicts (should be
zero or whatever you've already accepted). Re-add the line, check
again. If the conflict count is unchanged, LALR is safe.

When LALR manufactures conflicts that canonical doesn't have, you
have three options:

1. **Restructure the grammar.** This is usually a small, local
   change. The canonical example in this repo is the generic
   renaming form in `examples/ada_full.uplox`, which originally
   manufactured reduce/reduce conflicts under LALR and was fixed
   by splitting an empty production into explicit empty + non-empty
   halves (commit `c38ca83`).
2. **Use `%shift` or `%reduce`.** See
   [`conflict_resolution.md`](conflict_resolution.md). The
   `KW_pragma` and `SEMI` directives in `ada_full.uplox` are the
   worked examples.
3. **Switch to IELR.** Below.

## When IELR(1) is the right call

IELR is a tightly-targeted tool. Pick it when:

* You've moved a grammar to LALR and a conflict appeared.
* The conflict isn't structural — i.e. it would *not* appear under
  canonical LR(1).
* The restructuring fix isn't obvious or would require duplicating
  a large rule group.

Under those conditions IELR splits exactly the states that need
splitting, leaves everything else merged, and produces a table
the same size as LALR for LALR-friendly grammars and the same
conflict count as canonical-LR(1) for the awkward ones.

For LALR-friendly grammars (everything in this repo), IELR
returns after the first iteration with a table identical to
LALR. Worst case it degenerates to canonical-LR — guaranteeing
the canonical conflict count.

**Caveat: this is a simplified IELR.** uplox implements
post-merge-split refinement, not the full Pager/Denny per-action
splitter. The full algorithm can produce strictly smaller tables
in edge cases. On every grammar in this repo so far the
simplified splitter matches the full algorithm's table size.

## A practical workflow

Most grammar work in this repo followed this pattern:

1. Write the grammar with the default `canonical-lr`.
2. Iterate until `uplox check` is conflict-free, leaning on
   canonical's honest diagnostics.
3. When the grammar reaches "real" size (hundreds of productions,
   build time you notice), A/B with `lalr` to verify LALR-friendly.
4. If LALR-friendly: add `%define lr.type lalr` and move on. You
   keep the same parse semantics, lose ~90% of the table size and
   build time.
5. If LALR introduces conflicts: try restructuring first
   ([`conflict_resolution.md`](conflict_resolution.md) has the
   patterns), then consider `ielr`.

The point is to spend your conflict-fighting time on real
ambiguities, not on merge artefacts you've inherited by switching
construction modes.

## What `%shift` and `%reduce` do across modes

The two escape-hatch directives behave identically in all three
construction modes. A `%shift ELSE` line suppresses a
shift/reduce conflict on ELSE whether the underlying table was
built canonical, LALR, or IELR. See
[`conflict_resolution.md`](conflict_resolution.md) for details
on when to reach for them.
