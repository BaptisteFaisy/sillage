"""The MCP schemas of the eleven tools (table 15.1).

One place holds what a client discovers: the name of the tool, what it does,
and the shape of its arguments. The gateway serves them as they are, so a
client MCP - Kiro in particular - sees exactly the table of the specification.

The schemas are the same for everybody: the **who may call** is not written
here, it is decided by layer 1 (table 10.5) and the policies of annexe D. The
optional ``token`` argument carries the claims of the caller when the client
runs the server outside the gateway.
"""

from __future__ import annotations

from typing import Any

#: The claim names an MCP client may send to identify itself.
TOKEN_PROPERTY = "token"


def _schema(properties: dict[str, Any], required: tuple[str, ...]) -> dict[str, Any]:
    """One JSON schema of a tool."""
    return {
        "type": "object",
        "properties": {**properties, TOKEN_PROPERTY: _token_property()},
        "required": list(required),
        "additionalProperties": False,
    }


def _token_property() -> dict[str, Any]:
    """The optional claims of the caller: role, mode and client."""
    return {
        "type": "object",
        "description": (
            "Revendications du jeton de l'appelant : role (engineer, referent, agent, "
            "team_lead, admin), mode (autonomous, assisted) et clientId. "
            "Hors passerelle, c'est ce qui décide de ce que l'appelant peut lire."
        ),
        "properties": {
            "sub": {"type": "string"},
            "role": {"type": "string"},
            "mode": {"type": "string"},
            "clientId": {"type": "string"},
        },
        "additionalProperties": True,
    }


#: The eleven tools of the gateway, in the order of the table 15.1.
SCHEMAS: tuple[dict[str, Any], ...] = (
    {
        "name": "recall",
        "description": (
            "Les fiches validées d'un périmètre pour un problème décrit, les pistes "
            "à éviter, et l'identifiant d'usage du retour à donner ensuite."
        ),
        "inputSchema": _schema(
            {
                "clientId": {"type": "string", "description": "Le client du périmètre demandé."},
                "problem": {"type": "string", "description": "Le problème, en langage courant."},
                "techno": {"type": "string", "description": "La technologie concernée, si elle est connue."},
            },
            ("clientId", "problem"),
        ),
    },
    {
        "name": "recall_all",
        "description": (
            "Comme recall, plus les leçons et les épisodes non validés, signalés comme "
            "tels. Réservé aux humains et aux agents assistés."
        ),
        "inputSchema": _schema(
            {
                "clientId": {"type": "string", "description": "Le client du périmètre demandé."},
                "problem": {"type": "string", "description": "Le problème, en langage courant."},
                "techno": {"type": "string", "description": "La technologie concernée, si elle est connue."},
            },
            ("clientId", "problem"),
        ),
    },
    {
        "name": "get_item",
        "description": "Une fiche, un épisode ou une leçon, en entier, si le périmètre le permet.",
        "inputSchema": _schema(
            {"id": {"type": "string", "description": "L'identifiant de l'objet."}},
            ("id",),
        ),
    },
    {
        "name": "record_episode",
        "description": (
            "Déclarer un épisode : la structure est imposée, et l'épisode rejoint la "
            "mémoire du client. Une trace déjà reçue ne crée pas un second épisode."
        ),
        "inputSchema": _schema(
            {
                "clientId": {"type": "string", "description": "Le client du périmètre."},
                "workItemId": {"type": "string", "description": "L'identifiant du travail."},
                "situation": {"type": "string", "description": "Ce qui se passait."},
                "intent": {"type": "string", "description": "Ce que l'on cherchait à obtenir."},
                "attempts": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Les pistes essayées, dans l'ordre.",
                },
                "outcome": {
                    "type": "string",
                    "enum": ["success", "failure", "mixed", "unknown"],
                    "description": "L'issue du travail.",
                },
                "evidence": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Les éléments qui prouvent le résultat.",
                },
            },
            ("clientId", "workItemId", "situation", "intent"),
        ),
    },
    {
        "name": "report_outcome",
        "description": (
            "Dire si la connaissance servie a aidé : le code compte, fait bouger le niveau "
            "et prévient le référent quand une fiche échoue deux fois de suite."
        ),
        "inputSchema": _schema(
            {
                "usageId": {"type": "string", "description": "L'identifiant d'usage reçu."},
                "result": {
                    "type": "string",
                    "enum": ["helped", "did_not_help", "wrong"],
                    "description": "Ce que la consultation a apporté.",
                },
                "note": {"type": "string", "description": "Une précision, en une phrase."},
            },
            ("usageId", "result"),
        ),
    },
    {
        "name": "propose_fiche",
        "description": (
            "Proposer une fiche en brouillon à partir d'une leçon ou d'épisodes. "
            "Elle attend la relecture d'un référent."
        ),
        "inputSchema": _schema(
            {
                "lessonId": {"type": "string", "description": "La leçon à mettre en fiche."},
                "episodeIds": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Les épisodes sources de la fiche.",
                },
                "draft": {
                    "type": "object",
                    "description": "Le contenu proposé : title, body, steps, avoid, validity, claims.",
                },
            },
            (),
        ),
    },
    {
        "name": "validate_fiche",
        "description": (
            "Valider, corriger ou refuser une fiche. Réservé au référent. Une affirmation "
            "sans épisode source est refusée."
        ),
        "inputSchema": _schema(
            {
                "ficheId": {"type": "string", "description": "La fiche à décider."},
                "decision": {
                    "type": "string",
                    "enum": ["validate", "correct", "refuse"],
                    "description": "La décision du référent.",
                },
                "edits": {
                    "type": "object",
                    "description": "Les corrections : title, body, steps, avoid, validity, claims.",
                },
            },
            ("ficheId", "decision"),
        ),
    },
    {
        "name": "publish_common",
        "description": (
            "Publier une fiche dans le savoir commun, après le contrôle des identifiants, "
            "le filtre de données sensibles et l'approbation du référent."
        ),
        "inputSchema": _schema(
            {
                "ficheId": {"type": "string", "description": "La fiche d'origine."},
                "commonText": {
                    "type": "string",
                    "description": "La version commune, sans aucun élément du client.",
                },
            },
            ("ficheId", "commonText"),
        ),
    },
    {
        "name": "set_aside",
        "description": (
            "Mettre une fiche à l'écart : elle n'est plus servie aux agents. "
            "Réservé au référent et à l'administrateur."
        ),
        "inputSchema": _schema(
            {
                "ficheId": {"type": "string", "description": "La fiche à écarter."},
                "reason": {"type": "string", "description": "La raison de la mise à l'écart."},
            },
            ("ficheId", "reason"),
        ),
    },
    {
        "name": "export_skill",
        "description": (
            "Exporter une fiche éprouvée en compétence d'agent : un dossier SKILL.md, "
            "enregistré dans le registre comme brouillon."
        ),
        "inputSchema": _schema(
            {"ficheId": {"type": "string", "description": "La fiche à exporter."}},
            ("ficheId",),
        ),
    },
    {
        "name": "forget",
        "description": (
            "Oublier un travail : son épisode disparaît, et les fiches qui s'appuyaient "
            "dessus sont signalées. Réservé à l'administrateur et à l'auteur du travail."
        ),
        "inputSchema": _schema(
            {"workItemId": {"type": "string", "description": "Le travail à oublier."}},
            ("workItemId",),
        ),
    },
)

#: The names of the eleven tools, in the order of the table 15.1.
NAMES: tuple[str, ...] = tuple(schema["name"] for schema in SCHEMAS)

__all__ = ["NAMES", "SCHEMAS", "TOKEN_PROPERTY"]
