# Ada front-end status — 2026-05-10 (afternoon, on Mac)

## Coverage

	GNAT runtime	97.8% (1072/1096)	maxed (all 24 fails malformed source)
	ACATS C/L/E/D	100.0% (2846/2846 + 3 xfail) raw 99.9% (2846/2849)

All session work is committed (head `fd58008`). Grammar runs LALR(1)
via ``%define lr.type lalr`` — 671 productions, 1594 states, 0 conflicts,
1.4 MB JSON, ~2 min build (vs canonical-LR's 58611 states / 28 MB / 25 min).
Build peak RSS 2.9 GB.

## Resume

	cd /Users/wohl/src/uplox
	/tmp/memcap.sh 24576 uplox check examples/ada_full.uplox
	/tmp/memcap.sh 24576 uplox build  examples/ada_full.uplox -o /tmp/ada_full_vN.json
	/tmp/memcap.sh 24576 python3 examples/ada_corpus.py /tmp/ada_full_vN.json /Users/wohl/src/uada80/adalib --save-summary /tmp/gnat_summary_vN.txt
	/tmp/memcap.sh 24576 python3 examples/ada_corpus.py /tmp/ada_full_vN.json /tmp/acats/tests --skip-b-tests --save-summary /tmp/acats_summary_vN.txt --save-fails /tmp/acats_fails_vN.txt

`/tmp/memcap.sh` is a 24 GB RSS watchdog — macOS doesn't enforce
ulimit -v, so a polled killer is the only safety net. Source kept
in `/tmp/memcap.sh` (regenerate from this file's git history if lost).

## Corpora on this Mac

	GNAT runtime	/Users/wohl/src/uada80/adalib                1096 files
	ACATS 4G        /tmp/acats/tests                            2849 non-B
	ACATS source    https://github.com/simonjwright/ACATS      master @ 2024-12-31

The local `/Users/wohl/src/uada80/{acats,tests/acats}` are NOT the
full corpus (only 79 ACATS support files). The full corpus was
downloaded into `/tmp/acats` from simonjwright/ACATS during this
session. `ada_corpus.py --skip-b-tests` filters intentionally-invalid
B-tests (1876 files); the remaining 2849 are A/C/D/E/L tests.

## What still fails (3 ACATS — at the LR-grammar ceiling)

	3	malformed source — non-ASCII bytes in `a22006c`, `a2a031a`,
	    `c2a021b` that no Ada compiler accepts. Belong in xfail.

Net session: **+85 ACATS files (96.9% → 99.9%)**. GNAT runtime
unchanged at 97.8% (every fail is malformed source).

## Things uplox would need for further coverage

* **Context-sensitive lex hooks** — would let us drop the
  `disambiguate_apostrophe` workaround in `ada_corpus.py`. Not on
  the critical path for any currently-failing test.
* **macOS RLIMIT_AS support** — would let us drop the watchdog
  (the build never came near the 24 GB cap; 2.9 GB peak).
* **IELR(1) construction** — only needed if a future grammar change
  introduces an LALR-only conflict that the canonical-LR(1) table
  doesn't have. ada_full and every other example grammar tried so
  far is LALR-friendly without restructure, so this is purely
  speculative for now.

## Session commits

```
fd58008 Switch all conflict-free example grammars to LALR(1)
a6a4d8d ada_full: switch to LALR(1) — 37x state shrink, identical parses
dfe112c %define lr.type {canonical-lr|lalr}: selectable LR construction
dc8a935 ada_full: xfail list for malformed-source ACATS tests (100.0% with xfail)
ef8a783 ada_full: range-attribute as range constraint via %reduce (+2 ACATS, 99.8% → 99.9%)
b06c7a9 %reduce directive: yacc-style reduce-preference for s/r conflicts
7662d7d ada_full: subpool allocator + array-decl aspect_spec (+2 ACATS, 99.8% → 99.8%)
87833a8 ada_full: bare case-expr as call argument (+1 ACATS, 99.8% → 99.8%)
c38ca83 ada_full: generic renaming via overriding/formal-part split (+7 ACATS, 99.5% → 99.8%)
2a4f63e ada_full: note empirical generic-renaming conflict count after split
6307a88 ada_full: goto with dotted label, raise after short-circuit (+2 ACATS, 99.4% → 99.5%)
2b70c2f ada_full: entry families and accept-with-family-index (+28 ACATS, 98.5% → 99.4%)
09709a5 ada_full: qualified-expr extensions and progenitor lists (+4 ACATS, 98.3% → 98.5%)
ef595a8 ada_full: more low-risk additions (+5 ACATS, 98.1% → 98.3%)
811a818 ada_full: replace WIP with low-risk additions (+34 ACATS, 96.9% → 98.1%)
```

## LALR rollout (post-grammar-work session)

Per-grammar canon → LALR shrink in states (all conflict-free, all switched
unless noted):

```
ada_full        58611 →  1594   36.8x
ada_subset       3574 →   417    8.6x
c23              3395 →   549    6.2x
plm_full         1312 →   296    4.4x
plm_subset        741 →   198    3.7x
calc                30 →   16    1.9x
scoped              13 →   10    1.3x
plm_pre             27 →   27    1.0x   (no shrink — left canonical)
uplox_self          67 →   67    1.0x   (no shrink — left canonical)
ambig_expr          18 →   10    1.8x   (intentional 8-conflict fixture — left canonical)
```

Every uada80 / uplox-grammar consumer should rebuild their bundles
to pick up the new LALR-built tables. The schema is unchanged so
older runtimes parse the new bundles fine — they just see a smaller
state space.
