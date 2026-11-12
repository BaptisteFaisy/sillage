"""Guards of Sillage: what leaves a client perimeter, and what never leaves it.

Public surface of the module. Everything here is offline, deterministic and
computed by code: no model call, no network, no AWS import at this level -
``guards.pii.BedrockGuardrails`` is the one class that imports boto3, inside its
client property.

Three guarantees of the project rest on this module:

1. **nothing proper to a client reaches the common knowledge** - what was learnt
   at a client's leaves only as a general lesson, cleaned and approved
   (:mod:`sillage.guards.identifiers`, :mod:`sillage.guards.generalize`);
2. **no fiche without a source is publishable** - every statement points back to
   an episode (:mod:`sillage.guards.sources`);
3. **every captured text is untrusted data** - a text slipped into a ticket
   never becomes an instruction followed by an agent weeks later
   (:mod:`sillage.guards.poison`).

Sub-modules:

* :mod:`sillage.guards.identifiers` - step 1 of chapter 13.6: everything that
  names the client, found by code;
* :mod:`sillage.guards.generalize` - steps 3 to 6: the vetoes and the approved
  common lesson;
* :mod:`sillage.guards.sources` - EF-VAL-02: a statement cites an episode, or
  the fiche is not publishable;
* :mod:`sillage.guards.poison` - chapter 10.3: detection, quotation and
  quarantine of the poisoned events;
* :mod:`sillage.guards.pii` - personal data masked offline, and the Bedrock
  Guardrails filter behind the ``GuardrailFilter`` port.

**Boundary with the other modules - no pattern is duplicated.** Three modules
look at text, at three different moments, for three different families:

* ``capture/redact.py``, on the way **in**: the **secrets** - access keys,
  tokens, JWT, PEM blocks, connection strings, ``API_KEY=…``. It destroys them,
  counts them and leaves an explicit marker.
* ``guards/`` (this module), on the way **out**: the **client identifiers**, the
  **sources of a fiche**, the **poisoned instructions** and the **personal
  data**. It finds them, refuses the publication, quotes them, masks them.
* ``policy/rules.py``, before any tool: **who may call what, for which client**.
  It forbids the call and journalises the refusal.

Consequences of that split: a secret never reaches ``guards/`` - it was removed
at the capture - and ``guards/`` never removes a secret, it never looks for one.
The perimeter of a call is never recomputed here: ``policy`` refused it already,
long before. And ``guards/`` never writes: it vetoes, it quotes, it masks, and
it hands a decision back to the caller.
"""

from __future__ import annotations

from sillage.guards.generalize import (
    APPROVED_CONFIDENCE,
    approve_common,
    check_common_version,
    common_lesson,
    contains_identifier,
    found_identifiers,
)
from sillage.guards.identifiers import (
    COMBINED_PATTERN,
    FAMILIES,
    contains,
    extract,
    extract_from_trace,
    name_variants,
)
from sillage.guards.pii import (
    PII_FAMILIES,
    BedrockGuardrails,
    contains_pii,
    marker,
    mask,
)
from sillage.guards.poison import (
    INSTRUCTION_PATTERNS,
    QUARANTINE_KIND,
    QUOTATION_SUFFIX,
    REASONS,
    as_quotation,
    detect,
    is_poisoned,
    screen_trace,
)
from sillage.guards.sources import (
    FULL_COVERAGE,
    check_claims,
    coverage,
    is_publishable,
    unsourced_claims,
)

__all__ = [
    "APPROVED_CONFIDENCE",
    "COMBINED_PATTERN",
    "FAMILIES",
    "FULL_COVERAGE",
    "INSTRUCTION_PATTERNS",
    "PII_FAMILIES",
    "QUARANTINE_KIND",
    "QUOTATION_SUFFIX",
    "REASONS",
    "BedrockGuardrails",
    "approve_common",
    "as_quotation",
    "check_claims",
    "check_common_version",
    "common_lesson",
    "contains",
    "contains_identifier",
    "contains_pii",
    "coverage",
    "detect",
    "extract",
    "extract_from_trace",
    "found_identifiers",
    "is_poisoned",
    "is_publishable",
    "marker",
    "mask",
    "name_variants",
    "screen_trace",
    "unsourced_claims",
]
