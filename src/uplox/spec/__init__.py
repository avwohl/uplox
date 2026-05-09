"""Grammar source reader and intermediate representation.

A ``.uplox`` file is parsed by :mod:`uplox.spec.reader` into a :class:`uplox.spec.ir.GrammarIR`.
The IR is the only thing the rest of the toolchain consumes — readers for alternative
source formats can be added without touching downstream stages.
"""
