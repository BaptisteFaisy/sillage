"""The reference traces: what the smoke, the demo and the tests all replay.

Four finished jobs, one per way of capturing (chapter 4.3 and 13.2):

* :func:`ticket_2041` - the ticket of annex B, closed by an engineer after a
  wrong lead. Three secrets are planted in it on purpose: a capture that never
  meets a secret never proves it removes them (R2).
* :func:`investigation_77` - an AWS DevOps Agent investigation whose diagnosis
  is rejected by an engineer, who then gives the right one (journey 8.4).
* :func:`kiro_session` - a code agent session, accepted by the developer.
* :func:`merged_fix` - a correction merged in the repository of another client
  (EF-CAP-08).

Every payload is synthetic, every client fictional, every secret visibly fake
(``AKIAEXAMPLE...``, ``ghp_EXAMPLE...``): this file is committed, and a real
credential must never be able to reach it.
"""

from __future__ import annotations

from typing import Any

from sillage.capture.sources import (
    devops_agent_event,
    guichet_ticket,
    merged_pull_request,
)
from sillage.capture.sources import (
    kiro_session as kiro_session_trace,
)
from sillage.kernel.types import Trace

#: Secret planted in the ticket of annex B: an AWS access key, visibly fake.
PLANTED_ACCESS_KEY = "AKIAEXAMPLEFAKEKEY01"
#: Secret planted in the ticket: a GitHub token, visibly fake.
PLANTED_GITHUB_TOKEN = "ghp_EXAMPLEfake0000000000000000000000"
#: Secret planted in the ticket: a connection string with its password.
PLANTED_CONNECTION_STRING = "postgres://sillage:hunter2example@db-a1.clienta.internal:5432/app"


def ticket_2041_payload() -> dict[str, Any]:
    """The annex B ticket, as Guichet sends it when the engineer closes it."""
    return {
        "id": "ticket-2041",
        "ticket": "2041",
        "code": "INC-500",
        "client": "a",
        "title": "Erreurs 500 sur le site après le déploiement de 13h50",
        "trigger": "alarme",
        "created_at": "2026-11-11T14:02:00Z",
        "closed_at": "2026-11-11T15:02:00Z",
        "technologies": ["postgresql", "ecs"],
        "description": (
            "Le site renvoie des erreurs 500 depuis 14h02. Les journaux indiquent : "
            f"too many connections. (clé de service {PLANTED_ACCESS_KEY}, valeur de test)"
        ),
        "comments": [
            {
                "author": "alice",
                "author_kind": "human",
                "at": "2026-11-11T14:10:00Z",
                "text": (
                    "Hypothèse : la base est sous-dimensionnée. Je redémarre le service "
                    f"applicatif pour libérer les connexions. Jeton de debug {PLANTED_GITHUB_TOKEN}."
                ),
                "tool_output": (
                    "Redémarrage effectué. Connexions ouvertes : 12, puis 100 au bout de 4 minutes."
                ),
            },
            {
                "author": "alice",
                "author_kind": "human",
                "at": "2026-11-11T14:35:00Z",
                "text": (
                    "Le redémarrage ne règle rien. Le déploiement de 13h50 a changé la taille du "
                    "groupe de connexions de 10 à 50 par instance (journal : "
                    f"{PLANTED_CONNECTION_STRING}). Je reviens à 10."
                ),
                "tool_output": (
                    "Configuration rétablie. Connexions ouvertes stables à 38. Plus d'erreur 500."
                ),
            },
        ],
        "resolution": {"author": "alice", "confirmed": True, "at": "2026-11-11T15:02:00Z"},
    }


def ticket_2041() -> Trace:
    """The ticket of annex B, normalised, with its planted secrets still in it."""
    return guichet_ticket(ticket_2041_payload())


def investigation_77_payload(client: str = "a") -> dict[str, Any]:
    """The EventBridge event of a finished AWS DevOps Agent investigation."""
    return {
        "detail-type": "Investigation Completed",
        "detail": {
            "investigationId": "inv-77",
            "clientId": client,
            "trigger": "alarme",
            "startedAt": "2026-11-11T09:02:00Z",
            "endedAt": "2026-11-11T09:41:00Z",
            "title": "Enquête 77 - erreurs 500 sur l'API de commande",
            "alarm": "Erreurs 500 en hausse sur l'API de commande depuis 09h00.",
            "technologies": ["postgresql", "ecs"],
            "author": "agent-devops",
            "steps": [
                {
                    "actor": "agent-devops",
                    "actor_kind": "agent",
                    "at": "2026-11-11T09:06:00Z",
                    "text": "Diagnostic : la base est saturée, il faut augmenter sa taille.",
                    "tool_output": (
                        "SELECT count(*) FROM pg_stat_activity; 412 connexions pour 400 autorisées."
                    ),
                },
                {
                    "actor": "alice",
                    "actor_kind": "human",
                    "at": "2026-11-11T09:28:00Z",
                    "text": (
                        "Ce n'est pas la taille de la base : le déploiement de 08h50 a doublé le "
                        "pool par instance. Je ferme les connexions ouvertes et je reviens à 10."
                    ),
                    "tool_output": "Connexions actives après correctif : 38. Plus d'erreur 500.",
                },
            ],
            "verdict": {
                "kind": "rejected",
                "at": "2026-11-11T09:30:00Z",
                "reviewer": "alice",
                "reason": (
                    "La base n'a jamais refusé de connexion : le pool a été doublé par le "
                    "déploiement de 08h50."
                ),
                "rejected_diagnosis": "la base est saturée, il faut augmenter sa taille",
                "correct_diagnosis": (
                    "fuite de connexions depuis le déploiement de 08h50, pool passé de 10 à 20 par instance"
                ),
            },
        },
    }


def investigation_77(client: str = "a") -> Trace:
    """An agent investigation a human rejected, then corrected (journey 8.4)."""
    return devops_agent_event(investigation_77_payload(client))


def kiro_session_payload(client: str = "a") -> dict[str, Any]:
    """What the ``Stop`` trigger of Kiro sends at the end of a session."""
    return {
        "sessionId": "kiro-2026-11-11-01",
        "clientId": client,
        "requester": "bob",
        "startedAt": "2026-11-11T10:05:00Z",
        "endedAt": "2026-11-11T10:52:00Z",
        "title": "Session Kiro - migration de schémas qui échoue",
        "request": "Le déploiement de la chaîne de paiement échoue sur l'étape de migration.",
        "technologies": ["postgresql", "ecs"],
        "author": "agent-kiro",
        "steps": [
            {
                "actor": "agent-kiro",
                "actor_kind": "agent",
                "at": "2026-11-11T10:12:00Z",
                "text": (
                    "La migration ouvre le pool avant de créer les index. Je déplace l'ouverture "
                    "du pool après la migration et j'ajoute un test d'intégration."
                ),
                "tool_output": "pytest -q tests/test_migration.py : 12 réussis.",
            },
            {
                "actor": "bob",
                "actor_kind": "human",
                "at": "2026-11-11T10:44:00Z",
                "text": "Je relance la chaîne complète sur l'environnement de préproduction.",
                "tool_output": "Chaîne de déploiement verte en 6 minutes 12.",
            },
        ],
        "verdict": {
            "kind": "accepted",
            "at": "2026-11-11T10:48:00Z",
            "reviewer": "bob",
            "reason": "La correction passe en préproduction, la chaîne est verte.",
        },
    }


def kiro_session(client: str = "a") -> Trace:
    """A code agent session, finished and accepted by the developer."""
    return kiro_session_trace(kiro_session_payload(client))


def merged_fix_payload(client: str = "b") -> dict[str, Any]:
    """The payload a code repository sends when a correction is merged."""
    return {
        "pullRequest": "412",
        "clientId": client,
        "createdAt": "2026-11-11T16:20:00Z",
        "title": "Corriger la migration qui épuise le pool de connexions",
        "description": (
            "La migration 2026_11_10 échoue chez le client B : le pool est ouvert avant la "
            "création des index. Lié au ticket INC-512."
        ),
        "ticket": "INC-512",
        "author": "carol",
        "technologies": ["mysql", "eks"],
        "mergedAt": "2026-11-11T17:20:00Z",
        "steps": [
            {
                "actor": "agent-kiro",
                "actor_kind": "agent",
                "at": "2026-11-11T16:40:00Z",
                "text": "Je déplace l'ouverture du pool après la création des index.",
                "tool_output": ("Chaîne : étape migrate verte, étape deploy verte, 0 avertissement."),
            },
            {
                "actor": "carol",
                "actor_kind": "human",
                "at": "2026-11-11T17:05:00Z",
                "text": "Je relance la chaîne sur la branche de fusion avant de merger.",
            },
        ],
        "reviews": [
            {
                "author": "dave",
                "state": "approuvé",
                "text": "La migration passe sur l'environnement de préproduction.",
                "at": "2026-11-11T17:12:00Z",
            }
        ],
    }


def merged_fix(client: str = "b") -> Trace:
    """A correction merged in the repository, with its review (EF-CAP-08)."""
    return merged_pull_request(merged_fix_payload(client))


def all_samples() -> tuple[Trace, ...]:
    """The four reference traces, in the order the demo tells them."""
    return (ticket_2041(), investigation_77(), kiro_session(), merged_fix())


__all__ = [
    "PLANTED_ACCESS_KEY",
    "PLANTED_CONNECTION_STRING",
    "PLANTED_GITHUB_TOKEN",
    "all_samples",
    "investigation_77",
    "investigation_77_payload",
    "kiro_session",
    "kiro_session_payload",
    "merged_fix",
    "merged_fix_payload",
    "ticket_2041",
    "ticket_2041_payload",
]
