"""Preprocessor front-ends bundled with plox.

A preprocessor here is a small plox-built parser that consumes raw
target-language source and emits transformed source ready for the main
parser. Each one is a turnkey helper: ``import`` it and call its single
exported function. The grammar lives under ``examples/`` alongside the
others; this package contains only the host-side glue.

Currently supported:

* :mod:`plox.preprocess.plm` — PL/M-80 LITERALLY expansion and
  ``$``-directive recognition. See ``examples/plm_pre.plox``.
"""
