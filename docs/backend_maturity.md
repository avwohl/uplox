# Backend maturity

uplox emits drivers for four target languages. All four pass their
unit-test suites for the same lexer/parser features (typedef-name hack,
balanced brackets, post-reduce hook, parse-error format), but the
production track record is uneven:

- **Python** (the in-process runtime and the emitted `uplox_<g>.py`
  shim) — what every downstream compiler in the **Related projects**
  section of the [README](../README.md#related-projects) uses. Hammered against full real-world corpora: 100%
  on `gcc-c-torture` (1514 tests) + `c-testsuite` (220 tests) via
  uc386; 100% on ACATS A/C/D/E/L (2846 tests) and 97.8% on the GNAT
  runtime (1072/1096) via uada80; full BDOS rebuild via uplm80;
  end-to-end Cowgol via ucow. When you want confidence that an
  end-to-end parse will hold up, this is the path.
- **C**, **C++**, **Lua** — the emitted parsers compile, link, and
  pass the unit-test surface (including the typedef-name hack, the
  `%balanced=` lexer feature, post-reduce hooks, and ECMA-style parse
  error messages), but no production downstream compiler consumes
  them yet. Treat them as parity-tracked: every Python feature has a
  mirror test in each of the other three backends, but real-world
  hardening has happened on Python.
