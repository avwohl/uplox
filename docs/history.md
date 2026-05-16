# Project history — the phase plan

uplox shipped against a nine-phase plan; this page records what each
phase delivered and which release closed it. [CHANGELOG.md](../CHANGELOG.md)
is the version-by-version log; this page is the phase-by-phase view.

All nine phases of the original plan landed in v1.0.0. The v1.1.0
polish round closed the items the v1.0.0 release notes called out as
deferred at the cross-language level (token-filter ABI, post-reduce
hook, GLR symmetry, self-host). v1.2.0 closed the post-1.0
deferred-grammar list for `c_subset` and lifted the action-body
carve-out from the self-host grammar. v1.3.0 / v1.4.0 closed
cross-backend parity gaps. v2.0.0 reworked the DSL surface.

```
Phase  Deliverable                                                             Shipped
-----  ----------------------------------------------------------------------  -------
1      Repo skeleton, pyproject, CI, license, grammar source format frozen     1.0.0
2      Lexer pipeline: regex → NFA → DFA → JSON, Python driver, tests          1.0.0
3      LR(1) parser builder, conflict reporting, JSON, Python driver           1.0.0
4      AST schema + default builder + hooks framework                          1.0.0
5      Port the smallest uc* front-end as the first real grammar (plm_subset)  1.0.0
6      GLR extension                                                           1.0.0
7      C backend (re-entrant) — the highest-value target                       1.0.0
8      C++ and Lua backends                                                    1.0.0
9      Port remaining uc* front-ends; declare v1                               1.0.0
—      C / C++ / Lua: token-filter ABI + post-reduce hook                      1.1.0
—      Self-host bootstrap (uplox_self.uplox)                                  1.1.0
—      LR(1) build perf (~30% on c_subset)                                     1.1.0
—      c_subset: variadic, multi-line #define, bit-fields                      1.2.0
—      c_subset: designated initializers, compound literals                    1.2.0
—      c_subset: full abstract declarators, _Generic, sizeof(type)             1.2.0
—      Lexer %balanced= for non-regular tokens; action bodies in uplox_self    1.2.0
—      %balanced= parity in JSON bundle + C / C++ / Lua / emitted-Python       1.3.0
—      Parse-error diagnostics (expected-token list, EOI rendering) parity     1.4.0
—      DSL rework: <name> non-terminals, '…' literals, %keywords               2.0.0
```

For the full feature-by-feature release log, see
[`CHANGELOG.md`](../CHANGELOG.md).
