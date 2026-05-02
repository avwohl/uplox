"""GLR (Generalised LR) extension on top of the canonical LR(1) collection.

The canonical LR(1) builder in :mod:`plox.parse.lr1` already records every
action for each (state, terminal) pair. In LR mode, having more than one
action is an error. In GLR mode, the same data feeds a Tomita-style driver
that explores all conflicting actions in parallel via a graph-structured
stack (GSS) and produces a parse *forest* rather than a single tree.

This package contains:

* :mod:`plox.parse.glr.table` — a :class:`GLRTable` view that derives a
  multi-action ACTION map from an :class:`~plox.parse.lr1.LRTable`
  (single-action map + conflicts list).
* :mod:`plox.parse.glr.runtime` — the Tomita driver. Operates on tokens and
  an action map, returns a forest node for the start symbol.

The plan calls Phase 6 a "GLR extension" — *extension*, not a separate
parser. We intentionally reuse the LR item-set construction so the same
tables that produce a deterministic parse for unambiguous grammars produce
a parse forest for ambiguous ones.
"""

from .runtime import GLRParseError, glr_parse
from .table import GLRTable, glr_from_lr

__all__ = ["GLRTable", "glr_from_lr", "glr_parse", "GLRParseError"]
