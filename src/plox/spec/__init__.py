"""Grammar source reader and intermediate representation.

A ``.plox`` file is parsed by :mod:`plox.spec.reader` into a :class:`plox.spec.ir.GrammarIR`.
The IR is the only thing the rest of the toolchain consumes — readers for alternative
source formats can be added without touching downstream stages.
"""
