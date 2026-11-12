"""The export of a fiche as a skill (annexe F, EF-EXE-01, EF-EXE-02).

Chapter 8.7 describes the journey: a referent exports a proven fiche, the
folder goes to the AWS Agent Registry as a **draft**, and it becomes
discoverable by every MCP client - Kiro in particular - once it is approved.

The folder is the shape of annexe F: a ``SKILL.md`` file with a front matter
(``name``, ``description``), the moment to use it, the steps, what not to do,
and what to do after the usage. :func:`skill_folder` is a pure function of the
fiche: the demo, the tests and the tool all read the very file Kiro would load.
"""

from __future__ import annotations

from typing import Any

from sillage.kernel.errors import NotFound, PolicyDenied, SillageError, ValidationError
from sillage.kernel.protocols import Principal
from sillage.kernel.types import LEVEL_CODE, Fiche, Level
from sillage.tools.errors import tool_error
from sillage.tools.gateway import caller_of, enforce
from sillage.tools.texts import name_of

#: The levels a fiche may carry when it is exported as a skill.
EXPORTABLE_LEVELS: frozenset[Level] = frozenset({Level.VALIDATED, Level.PROVEN})

#: The level of a fiche, in the words of the specification.
LEVEL_WORDS: dict[Level, str] = {
    Level.RAW: "brute",
    Level.PROPOSED: "proposée",
    Level.VALIDATED: "validée",
    Level.PROVEN: "éprouvée",
    Level.SET_ASIDE: "mise à l'écart",
    Level.WITHDRAWN: "retirée",
}


def export_skill(
    stack: Any,
    *,
    fiche_id: str,
    principal: Principal | None = None,
) -> dict[str, Any]:
    """Export a fiche as a skill, and register it as a draft.

    Only a referent may call it (rule M.4, table 10.5). The record stays a
    draft: the approval is the next step of the journey, and it belongs to the
    registry (8.7, R18).
    """
    caller = caller_of(principal)
    try:
        enforce(stack, "export_skill", caller, ficheId=fiche_id)
    except PolicyDenied as exc:
        return tool_error(exc)
    try:
        return _export(stack, fiche_id=fiche_id)
    except SillageError as exc:
        return tool_error(exc)


def _export(stack: Any, *, fiche_id: str) -> dict[str, Any]:
    """Build the folder, then register it as a draft."""
    fiche = stack.catalog.get_fiche(fiche_id)
    if fiche is None:
        raise NotFound(f"fiche inconnue : {fiche_id}", ficheId=fiche_id)
    if fiche.level not in EXPORTABLE_LEVELS:
        raise ValidationError(
            f"seule une fiche validée ou éprouvée s'exporte en compétence "
            f"(celle-ci est de niveau {LEVEL_CODE[fiche.level]})",
            ficheId=fiche_id,
            niveau=LEVEL_CODE[fiche.level],
        )
    folder = skill_folder(fiche)
    name = name_of(fiche)
    record_id = stack.registry.register_skill(name=name, folder=folder, draft=True)
    return {
        "recordId": record_id,
        "name": name,
        "files": sorted(folder),
        "folder": folder,
        "state": "draft",
        "message": (
            f"Compétence « {name} » enregistrée en brouillon dans le registre : "
            "elle devient découvrable après approbation."
        ),
    }


def skill_folder(fiche: Fiche) -> dict[str, str]:
    """The folder of a skill exported from a fiche, in the shape of annexe F.

    The function is pure: the same fiche always gives the same folder, and the
    text is written as it is - no figure, no rewriting (ENF-IA-01).
    """
    name = name_of(fiche)
    lines = [
        "---",
        f"name: {name}",
        f"description: {skill_description(fiche)}",
        "---",
        "",
        f"# {fiche.title}",
        "",
        f"Source : fiche {fiche.fiche_id} de Sillage, {_level_word(fiche)}, "
        f"dernière confirmation le {_date_of(fiche)}.",
        "",
        "## Quand l'utiliser",
        (f"- {fiche.validity}" if fiche.validity else "- Le problème décrit par la fiche."),
        "",
        "## Étapes",
        *(f"{number}. {step}" for number, step in enumerate(fiche.steps, start=1)),
        "",
        "## À ne pas faire",
        *(f"- {trap}" for trap in fiche.avoid),
        "",
        "## Après usage",
        "Appeler l'outil `report_outcome` de Sillage pour dire si ces étapes ont réglé le problème.",
    ]
    return {"SKILL.md": "\n".join(line for line in lines if line is not None)}


def skill_description(fiche: Fiche) -> str:
    """The one line that tells an MCP client when to load the skill."""
    subject = fiche.title[0].lower() + fiche.title[1:] if fiche.title else "un incident"
    return f"À utiliser quand {subject}. Indique quoi vérifier d'abord et ce qu'il ne faut pas faire."


def _level_word(fiche: Fiche) -> str:
    """The level of a fiche, in the words of the specification."""
    return LEVEL_WORDS.get(fiche.level, fiche.level.value)


def _date_of(fiche: Fiche) -> str:
    """The date of the last confirmation, in French, or a dash when there is none."""
    if fiche.last_confirmation is None:
        return "—"
    return fiche.last_confirmation.strftime("%d %B %Y")


__all__ = ["EXPORTABLE_LEVELS", "export_skill", "skill_description", "skill_folder"]
