# Ada front-end status — 2026-05-10 (afternoon, on Mac)

## Coverage

	GNAT runtime	97.8% (1072/1096)	maxed (all 24 fails malformed source)
	ACATS C/L/E/D	99.8% (2844/2849)	5 fails left, all unfixable or LR-blocked

All session work is committed (head `7662d7d`). Latest build bundle:
`/tmp/ada_full_v39.json` (25 MB). Grammar: 669 productions, 53565
states, 0 conflicts. uplox check ~30 s on Mac M-series; build
~15–25 min depending on state count; build peak RSS ~2.5 GB.

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

## What still fails (5 ACATS)

	3	malformed source — non-ASCII bytes in `a22006c`, `a2a031a`,
	    `c2a021b` that no Ada compiler accepts.
	2	range attribute as range constraint
	    (`X : Integer range A1'Range(2)`, `Y : Integer range F'Range`).
	    Adding `<name> 'range' <name>` (or <simple_expr>) to
	    <subtype_indication> reproducibly produces 4 SEMI shift/reduce
	    conflicts at `subtype X is T range Y` boundaries that no
	    available uplox directive can resolve. Restricting to
	    `<name> TICK 'range' ...` would also reduce/reduce-conflict
	    with the existing <name> TICK <attribute_designator> rule
	    (since `'range'` is a valid attribute_designator).

Net session: **+83 ACATS files (96.9% → 99.8%)**. GNAT runtime
unchanged at 97.8% (every fail is malformed source).

## Things uplox would need for further coverage

* **`%reduce` directive** (or LR(2) lookahead) — would close the
  range-attribute-as-range-constraint pair (~2 files). Generic
  renaming, bare case-as-call-arg, and aspect-spec after discriminants
  used to need LR(2); all three landed via grammar restructure
  instead.
* **Context-sensitive lex hooks** — same as before; the
  `disambiguate_apostrophe` workaround in `ada_corpus.py` could
  go away.
* **macOS RLIMIT_AS support** — would let us drop the watchdog.

## Session commits

```
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
