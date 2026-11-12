"""Passage to the common knowledge: the six steps of chapter 13.6, in code.

A fiche validated inside one client perimeter may become common knowledge only
after it has been rewritten without any element proper to that client
(EF-GEN-02). Steps 1, 3, 4, 5 and 6 of chapter 13.6 are enforced here, by code;
step 2 (the rewrite) is the only one a model performs, which is exactly why the
other five are not left to it (chapter 10.1, layer 2).

The order of the vetoes is the order of their cost and of their strength:

1. the contract of the client (EF-GEN-06) - a client marked "no sharing" never
   feeds the common knowledge, whatever the quality of the rewrite;
2. the sources of the fiche (chapter 10.4) - a fiche whose statements cite no
   episode is not publishable at all;
3. the identifiers (EF-GEN-03) - one surviving identifier refuses the version;
4. the Bedrock Guardrails filter (EF-GEN-04) - a second opinion on the text;
5. the human approval (EF-GEN-05) - in the hackathon version it is always
   required, and the approver is a person, never an agent;
6. the publication - the approved text is written **verbatim** (EF-GEN-07).

Step 6 has one consequence the code enforces: the returned lesson carries no
backlink to the work it comes from. Its identifier is derived from its own text,
its ``episode_ids`` is empty and its scope is ``commun``: from another perimeter
there is nothing to follow back to the origin episode (EF-GEN-07, R15).
"""

from __future__ import annotations

from collections.abc import Iterable

from sillage.guards import identifiers as identifiers_module
from sillage.guards import sources as sources_module
from sillage.kernel.errors import CommonSharingRefused, LeakDetected, ValidationError
from sillage.kernel.ids import make_id
from sillage.kernel.protocols import GuardrailFilter
from sillage.kernel.types import Client, Fiche, Lesson, Level, Scope, ScopeKind

#: Confidence of a common lesson: a referent read it and approved it. The number
#: is set by code, never by a model (ENF-IA-01).
APPROVED_CONFIDENCE = 1.0


def contains_identifier(text: str, identifiers: Iterable[str]) -> bool:
    """True when one identifier of the list appears in ``text`` (13.6 step 3)."""
    return bool(identifiers_module.contains(text, identifiers))


def found_identifiers(text: str, identifiers: Iterable[str]) -> tuple[str, ...]:
    """The identifiers of the list that ``text`` still contains, sorted."""
    return tuple(sorted(identifiers_module.contains(text, identifiers)))


def check_common_version(text: str, identifiers: Iterable[str]) -> None:
    """Refuse a common version that still names the client (EF-GEN-03).

    Raises :class:`LeakDetected` carrying **the list** of the identifiers found,
    so that the refusal says what to rewrite and the journal proves it.
    """
    found = found_identifiers(text, identifiers)
    if found:
        raise LeakDetected(
            "la version commune contient encore un identifiant du client d'origine",
            identifiers=found,
        )


def approve_common(
    *,
    fiche: Fiche,
    rewritten: str,
    identifiers: Iterable[str],
    guardrails: GuardrailFilter | None = None,
    approver: str,
    client: Client | None = None,
) -> Lesson:
    """Run the vetoes of chapter 13.6 and return the lesson to publish (step 6).

    ``client`` is the contract of the perimeter the fiche comes from: EF-GEN-06
    cannot be checked without it, so a fiche of a client scope published without
    its contract is refused rather than trusted.
    """
    _check_contract(fiche, client)
    sources_module.check_claims(fiche)
    check_common_version(rewritten, identifiers)
    _check_guardrails(rewritten, guardrails)
    _check_approver(approver)
    return common_lesson(rewritten)


def common_lesson(rewritten: str) -> Lesson:
    """The lesson of step 6: the approved text, verbatim, with no backlink."""
    text = (rewritten or "").strip()
    if not text:
        raise ValidationError("la version commune approuvée est vide : rien à publier")
    return Lesson(
        lesson_id=make_id("common", text),
        scope=Scope(kind=ScopeKind.COMMON),
        use_case=_first_sentence(text),
        advice=(text,),
        pitfalls=(),
        confidence=APPROVED_CONFIDENCE,
        episode_ids=(),
        level=Level.VALIDATED,
    )


def _check_contract(fiche: Fiche, client: Client | None) -> None:
    """EF-GEN-06: sharing is a contract option, and the contract must be shown."""
    if fiche.scope.kind is not ScopeKind.CLIENT:
        return
    if client is None:
        raise CommonSharingRefused(
            "le contrat du client n'a pas été fourni : la mise en commun ne peut pas être vérifiée",
            client_id=fiche.scope.client_id or "",
        )
    if client.no_common:
        raise CommonSharingRefused(
            "ce client est marqué « pas de mise en commun » : rien ne sort de son périmètre",
            client_id=client.client_id,
        )


def _check_guardrails(rewritten: str, guardrails: GuardrailFilter | None) -> None:
    """EF-GEN-04: the sensitive data filter of Bedrock Guardrails, on top of the code.

    A masked answer is refused as well: what the referent approves at step 5 is
    what step 6 writes, and a text silently masked in between would be published
    without having been read.
    """
    if guardrails is None:
        return
    verdict = guardrails.scan(rewritten)
    if verdict.blocked:
        raise LeakDetected(
            "le filtre de données sensibles refuse la version commune",
            reasons=tuple(verdict.reasons),
        )
    if verdict.action == "masked" and verdict.masked_text and verdict.masked_text != rewritten:
        raise LeakDetected(
            "le filtre de données sensibles masque une partie de la version commune",
            reasons=tuple(verdict.reasons),
        )


def _check_approver(approver: str) -> None:
    """EF-GEN-05 and step 5: a human referent approves, an agent never does."""
    if not (approver or "").strip():
        raise ValidationError("la publication dans le commun exige un approbateur humain")


def _first_sentence(text: str) -> str:
    """The use case of the lesson: the first sentence of the approved text."""
    for separator in ("\n", ". "):
        head, separator_found, _ = text.partition(separator)
        if separator_found and head.strip():
            return head.strip()
    return text


__all__ = [
    "APPROVED_CONFIDENCE",
    "approve_common",
    "check_common_version",
    "common_lesson",
    "contains_identifier",
    "found_identifiers",
]
