# Ada front-end status — 2026-05-10

## Coverage

	GNAT runtime	97.8% (1072/1096)
	ACATS C/L/E/D	89.2% (2541/2849)

All session work is committed and pushed (current head `2eb4c07`).
Latest build bundle: `/tmp/ada_full_v22.json`. Grammar is now 29053
states; uplox builds take ~41 min on this machine. Cost-per-file
landed crossed ~14 min in the v22 round — practical mining wall.

## Resume

```bash
cd /home/wohl/src/uplox
uplox check examples/ada_full.uplox     # ~10-12 min on the big grammar
time uplox build examples/ada_full.uplox -o /tmp/ada_full_vN.json   # ~30-36 min
python examples/ada_corpus.py /tmp/ada_full_vN.json /home/wohl/src/uada80/adalib
python /tmp/run_acats.py    /tmp/ada_full_vN.json /home/wohl/src/uada80/tests/acats/tests
```

## What still fails

### GNAT runtime (24 files, all corpus issues)

	12	`digits` used as identifier
	 4	empty based literal `##`
	 3	`rem` used as identifier
	 2	tick / attribute (1 stray apostrophe, 1 attribute clause in stmt context)
	 1	`entry` / `delta` / `false` used as identifier (1 each)

All 24 are GNAT corpus files that misuse Ada reserved words as
identifiers or have malformed numeric literals — no compiler accepts
them. Effectively maxed out.

### ACATS (308 fails — the realistic ceiling for this grammar)

LR-blocked clusters (would need uplox engine changes or major
restructuring):

	~192	pragma between with/use and library_unit (RM 10.1.2) — the
	    	v17 grammar restructure made `<one_unit> -> <pragma>` a
	    	standalone unit, which is conflict-free but doesn't accept
	    	context-clause pragmas. Adding pragma BACK to `<context_item>`
	    	produces a 1-2-conflict shift/reduce on KW_pragma at unit
	    	boundaries that LALR-1 can't disambiguate without lookahead-2
	    	or a precedence/prefer directive (uplox v0 has neither).
	~31 	aspect_spec on `type_decl` — explicitly noted in the existing
	    	rule comment as conflicting with the derived-type
	    	``with record`` extension form.
	~10 	entry family with discrete-range parameter (`entry E (1..2);`)
	    	— the existing comment notes this conflicts with the regular
	    	`entry E (params)` formal-part shape.
	~6  	generic renaming (`generic package X renames Y;`) — produces
	    	15 conflicts when added; the `generic` prefix can't
	    	distinguish renaming from generic-decl on KW_package
	    	lookahead alone.
	~5  	`reverse` quantifier in quantified expression (caused 7674
	    	conflicts when attempted).

Tractable but non-trivial residual gaps (each needs careful LALR
factoring):

	~19 	deep / mixed extension aggregates with allocators
	~13 	index constraint with subtype indication on access type
	    	(`type P is access I_5_ARRAY (I_5 range 0 .. 6)`)
	~5  	bare conditional inside qualified expression
	    	(`Month'(if X then A elsif ... else ...)`)

## Things uplox would need for further coverage

* **Context-sensitive lex hooks** — already worked around for the
  `Character_Range'(...)` shape via `disambiguate_apostrophe()` in
  the corpus driver. uplox v0 regex has no lookbehind and the
  token-filter hook can re-tag but not un-consume input.
* **Token splitting** — useful for shapes the regex over-consumes.
  Currently tokens are atomic from the parser's view.
* **Precedence / prefer-shift directives** — would let the
  context-pragma case land without LALR factoring gymnastics.

## Session commits this run

```
2eb4c07 ada_full: extension aggregate with positional body (+3 ACATS, 89.1% → 89.2%)
77a353f ada_full: range-attr fix, multi-label, DOT char, pipe-discrim (+11 ACATS, 88.7% → 89.1%)
ee1d91c ada_full: enum keyword literals, range attribute (+12 ACATS, 88.3% → 88.7%)
8392111 ada_full: subprogram-body aspect, subtype delta/digits, DOT keyword names, quantified expr (+40 ACATS, 86.9% → 88.3%)
ed51d28 ada_full: extended return, choice param, use type, op-string assoc (+120 ACATS, 82.7% → 86.9%)
059e246 ada_full: more ACATS gaps (+48 files, 81.0% → 82.7%)
0ff8c6d ada_full: operator-symbol names + based reals (+86 ACATS, +1 GNAT, 81.0% / 97.8%)
2caa42e ada_full: colon-based ints + guarded select alts (+26 ACATS, 77.0% → 78.0%)
cfb3675 ada_full: mixed pos+named aggregates, subtype aspect_spec (+30 ACATS, 76.0% → 77.0%)
24e10f9 ada_full: block/loop labels, terminate, null record (+377 ACATS, 62.8% → 76.0%)
0e047ce ada_full: subunit declarations (+52 ACATS, 60.9% → 62.8%)
b618303 ada_corpus: pre-pass disambiguates TICK from CHAR_LIT (+1 GNAT, 97.6 → 97.7%)
36a96b8 ada_full: trailing pragma + qualified-aggregate allocator (+18 GNAT, 96.0 → 97.6%)
... earlier session: 92.3% → 96.0% on GNAT runtime
```

Net session: **GNAT 92.3% → 97.8% (+60 files); ACATS 60.9% → 89.2% (+805 files)**.
