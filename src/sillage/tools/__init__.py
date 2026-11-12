"""The tools of Sillage: the eleven of the table 15.1, behind one gateway.

Every function here takes the stack as its first argument and does the same
three things, in this order:

1. it asks **layer 1** whether the call is allowed, and it returns the refusal
   instead of raising it (ENF-IA-03);
2. it works in the perimeter the **token** carries, never in one a text of the
   call names (specification 10.4);
3. it writes what it did in the catalogue, so that the next consultation and
   the next feedback have something to read.

The names, the arguments and the answers are the table 15.1 of the
specification; the rules are the ones of :mod:`sillage.policy` and of
:mod:`sillage.guards`, which this package calls and never re-implements.
"""

from __future__ import annotations

from sillage.tools.common import publish_common
from sillage.tools.errors import degraded, is_refusal, tool_error
from sillage.tools.fiche import propose_fiche, set_aside, validate_fiche
from sillage.tools.forget import forget
from sillage.tools.outcome import report_outcome
from sillage.tools.recall import get_item, recall, recall_all
from sillage.tools.record import record_episode
from sillage.tools.registry import SCHEMAS
from sillage.tools.server import TOOLS, handle, serve_stdio
from sillage.tools.skill import export_skill, skill_folder

__all__ = [
    "SCHEMAS",
    "TOOLS",
    "degraded",
    "export_skill",
    "forget",
    "get_item",
    "handle",
    "is_refusal",
    "propose_fiche",
    "publish_common",
    "recall",
    "recall_all",
    "record_episode",
    "report_outcome",
    "serve_stdio",
    "set_aside",
    "skill_folder",
    "tool_error",
    "validate_fiche",
]
