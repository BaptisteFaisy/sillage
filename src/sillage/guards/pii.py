"""Personal data: masked offline, filtered by Bedrock Guardrails in production.

Two families of data must never reach the shared knowledge:

* the **personal data** of the employees and of the contacts of a client -
  chapter 17.1: Sillage is not a system that evaluates or monitors people, and a
  first name, an address, a phone number, a plate or a card number has nothing
  to do with a lesson about an infrastructure;
* the **client identifiers** - handled by :mod:`sillage.guards.identifiers`, not
  here, and the **secrets** - removed at the capture by
  :mod:`sillage.capture.redact`, not here either. No pattern is duplicated
  between the three modules.

:func:`mask` is the offline heuristic: deterministic, no dependency, no model.
It replaces what it recognises by an explicit marker, so a reader of the episode
sees a hole and knows a hole was made on purpose.

:class:`BedrockGuardrails` is the production filter of chapter 10.3 and of
EF-GEN-04: the sensitive data filter of Bedrock Guardrails, applied on top of
the code controls. boto3 is imported **inside** the client property, never at
module import time (CONTRACT.md rule 3), and a client object can be injected so
the tests check the response shapes without a single network call.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

from sillage.kernel.config import DEFAULT, Settings
from sillage.kernel.errors import BackendUnavailable
from sillage.kernel.protocols import GuardrailVerdict

#: Prefix of every replacement: a reader spots a mask at a glance.
MARKER = "[MASKED:{name}]"

#: First names the offline heuristic recognises. A heuristic, not a directory:
#: it exists to catch the ordinary ``Prénom Nom`` of a ticket, and the
#: production filter of Bedrock Guardrails covers the rest.
FIRST_NAMES: frozenset[str] = frozenset(
    {
        "alice",
        "amine",
        "anne",
        "antoine",
        "beatrice",
        "camille",
        "cecile",
        "chloe",
        "claire",
        "david",
        "elodie",
        "emilie",
        "emma",
        "fabien",
        "florence",
        "franck",
        "frederic",
        "gaelle",
        "guillaume",
        "helene",
        "hugo",
        "ines",
        "jean",
        "julie",
        "julien",
        "karim",
        "laetitia",
        "laurent",
        "lea",
        "manon",
        "marc",
        "marie",
        "mathieu",
        "michael",
        "michel",
        "nicolas",
        "olivier",
        "paul",
        "pauline",
        "pierre",
        "raphael",
        "sandrine",
        "sebastien",
        "sophie",
        "stephane",
        "sylvie",
        "thomas",
        "valerie",
        "vincent",
        "yann",
        "james",
        "john",
        "mary",
        "peter",
        "sarah",
        "steve",
    }
)

_TITLES = r"M\.|Mme|Mlle|Monsieur|Madame|Dr|Professor|Mr|Mrs|Ms"
_STREET = (
    r"rue|avenue|av|bd|boulevard|place|impasse|allée|allee|chemin|route|quai|cours"
    r"|square|passage|voie|zone|za|zi|zac|street|road|drive|lane"
)


def _family(name: str, expression: str, *, ignore_case: bool = True) -> tuple[str, re.Pattern[str]]:
    """Compile one personal datum family."""
    return name, re.compile(expression, re.IGNORECASE if ignore_case else 0)


#: The families :func:`mask` knows, applied in this order: the long numeric
#: shapes first, so that a card number is not cut in two by a phone number. The
#: person family is case-sensitive on purpose - a name is written with capitals,
#: and an all-lower-case sentence must not lose its words.
PII_FAMILIES: tuple[tuple[str, re.Pattern[str]], ...] = (
    _family(
        "nir",
        r"(?<![0-9])[12][\s.]?[0-9]{2}[\s.]?(?:0[1-9]|1[0-2])[\s.]?[0-9]{2}"
        r"[\s.]?[0-9]{2,3}[\s.]?[0-9]{3}[\s.]?[0-9]{2}(?![0-9])",
    ),
    _family(
        "address",
        rf"(?<![0-9])[0-9]{{1,4}}(?:\s*(?:bis|ter|quater))?\s*,?\s+(?:{_STREET})\b[^,;\n]{{0,60}}"
        r"(?:,\s*[0-9]{5}\s+[A-Za-zà-ÿ'\u2019\- ]{0,40})?",
    ),
    _family("phone", r"(?:(?:\+|00)33|0)\s*[1-9](?:[\s.\-]?[0-9]{2}){4}(?![0-9])"),
    _family(
        "plate",
        r"(?<![A-Z0-9])[A-HJ-NP-TV-Z]{2}-[0-9]{3}-[A-HJ-NP-TV-Z]{2}(?![A-Z0-9])",
        ignore_case=False,
    ),
    _family("email", r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)*\.[A-Za-z]{2,}"),
    _family(
        "person",
        rf"(?:\b(?:{_TITLES})\s+[A-ZÀ-Ý][\w'\u2019\-]+(?:\s+[A-ZÀ-Ý][\w'\u2019\-]+)?)"
        rf"|(?:\b(?i:{'|'.join(sorted(FIRST_NAMES))})\s+[A-ZÀ-Ý][\w'\u2019\-]{{1,}}\b)",
        ignore_case=False,
    ),
    _family("card", r"(?<![0-9])(?:[0-9][ \-]?){12,18}[0-9](?![0-9])"),
)

#: The Bedrock Guardrails action that means "refuse", and the ones that mask.
BLOCKED_ACTIONS = ("BLOCKED", "GUARDRAIL_INTERVENED")
MASKED_ACTIONS = ("ANONYMIZED", "MASKED")


def mask(text: str) -> str:
    """Replace every personal datum recognised in ``text`` by an explicit marker.

    The card family is checked with the Luhn algorithm, so an identifier made of
    digits (an account id, a timestamp) is not mistaken for a payment card.
    """
    if not text:
        return text
    for name, pattern in PII_FAMILIES:
        if name == "card":
            text = _sub_if(text, pattern, name, _is_luhn_valid)
        else:
            text = pattern.sub(lambda _match, label=name: MARKER.format(name=label), text)
    return text


def marker(name: str) -> str:
    """The explicit, stable replacement of one personal datum family."""
    return MARKER.format(name=name)


def contains_pii(text: str) -> bool:
    """True when ``text`` still holds a personal datum family. Used by the tests."""
    return any(pattern.search(text or "") for _, pattern in PII_FAMILIES)


class BedrockGuardrails:
    """The sensitive data filter of Bedrock Guardrails (EF-GEN-04, chapter 10.3).

    Implements :class:`sillage.kernel.protocols.GuardrailFilter`. boto3 is
    imported lazily, inside :attr:`client`, so importing this module offline -
    the test suite, the smoke, the demo - never needs the dependency.
    """

    def __init__(
        self,
        settings: Settings = DEFAULT,
        *,
        guardrail_id: str = "",
        version: str = "DRAFT",
        client: Any = None,
    ) -> None:
        self.settings = settings
        self.guardrail_id = guardrail_id or settings.guardrail_id
        self.version = version
        self._client = client

    @property
    def client(self) -> Any:
        """The ``bedrock-runtime`` client: the only place boto3 is imported."""
        if self._client is None:
            self._client = _boto3_client(self.settings)
        return self._client

    def scan(self, text: str) -> GuardrailVerdict:
        """One text under the filter: blocked, masked, or left alone."""
        if not text:
            return GuardrailVerdict()
        if not self.guardrail_id:
            raise BackendUnavailable("aucun garde-fou Bedrock configuré (SILLAGE_GUARDRAIL_ID)")
        payload = {
            "guardrailIdentifier": self.guardrail_id,
            "guardrailVersion": self.version,
            "source": "INPUT",
            "text": text,
        }
        return _verdict(self._call(payload))

    def _call(self, payload: dict[str, Any]) -> dict[str, Any]:
        """``ApplyGuardrail``; a client error degrades, it never fails the caller."""
        try:
            answer = self.client.apply_guardrail(**payload)
        except Exception as error:  # the port degrades, it never fails (ENF-DISP-02)
            raise BackendUnavailable(
                "le garde-fou Bedrock n'a pas répondu", aws_code=_error_code(error)
            ) from error
        return answer if isinstance(answer, dict) else {}


def _boto3_client(settings: Settings) -> Any:
    """Build the boto3 client - the only place boto3 is imported."""
    try:
        import boto3
    except ImportError as error:  # pragma: no cover - boto3 is an extra
        raise BackendUnavailable("boto3 n'est pas installé") from error
    return boto3.client("bedrock-runtime", region_name=settings.region)


def _verdict(response: dict[str, Any]) -> GuardrailVerdict:
    """Turn one ``ApplyGuardrail`` answer into the port's verdict."""
    reasons = _reasons(response.get("assessments") or ())
    masked = _masked_text(response)
    action = str(response.get("action") or "NONE")
    if action in BLOCKED_ACTIONS and not masked:
        return GuardrailVerdict(blocked=True, action="blocked", reasons=reasons)
    if action in BLOCKED_ACTIONS or masked:
        return GuardrailVerdict(action="masked", masked_text=masked, reasons=reasons)
    return GuardrailVerdict(reasons=reasons)


def _masked_text(response: dict[str, Any]) -> str:
    """The rewritten text of the filter, when it masked something."""
    outputs = response.get("outputs") or ()
    for output in outputs:
        if isinstance(output, dict) and output.get("text"):
            return str(output["text"])
    return ""


def _reasons(assessments: Any) -> tuple[str, ...]:
    """The stable labels of what the filter found, for the journal."""
    reasons: list[str] = []
    for assessment in assessments:
        if not isinstance(assessment, dict):
            continue
        reasons.extend(_found(assessment.get("contentPolicy"), "filters"))
        reasons.extend(_found(assessment.get("sensitiveInformationPolicy"), "privacyFilters"))
        reasons.extend(_found(assessment.get("sensitiveInformationPolicy"), "piiEntities"))
        reasons.extend(_found(assessment.get("wordPolicy"), "customWords"))
        reasons.extend(_found(assessment.get("topicPolicy"), "topicsConfig"))
    return tuple(dict.fromkeys(reasons))


def _found(policy: Any, key: str) -> list[str]:
    """The entries of one policy whose action is not ``NONE``."""
    if not isinstance(policy, dict):
        return []
    found: list[str] = []
    for entry in policy.get(key) or ():
        if not isinstance(entry, dict):
            continue
        action = str(entry.get("action") or "").upper()
        if action and action != "NONE":
            label = entry.get("type") or entry.get("name") or key
            found.append(f"{label}:{action}")
    return found


def _sub_if(text: str, pattern: re.Pattern[str], name: str, keep_if: Callable[[str], bool]) -> str:
    """Replace the matches of ``pattern`` that ``keep_if`` accepts."""
    return pattern.sub(
        lambda match: MARKER.format(name=name) if keep_if(match.group(0)) else match.group(0),
        text,
    )


def _is_luhn_valid(candidate: str) -> bool:
    """The Luhn control: a card number is not any long run of digits."""
    digits = [int(char) for char in candidate if char.isdigit()]
    if len(digits) < 13:
        return False
    total = 0
    for index, digit in enumerate(reversed(digits)):
        if index % 2:
            digit *= 2
            if digit > 9:
                digit -= 9
        total += digit
    return total % 10 == 0


def _error_code(error: BaseException) -> str:
    """The error code of a client error, empty when the error carries none."""
    response = getattr(error, "response", None)
    if isinstance(response, dict):
        return str(response.get("Error", {}).get("Code") or "")
    return type(error).__name__


__all__ = [
    "BLOCKED_ACTIONS",
    "FIRST_NAMES",
    "MARKER",
    "MASKED_ACTIONS",
    "PII_FAMILIES",
    "BedrockGuardrails",
    "contains_pii",
    "marker",
    "mask",
]
