"""Keys and table definitions of the deployed catalogue (DynamoDB).

**Modelling choice: one table, composite keys.** The catalogue is small, written by
one service, and almost every read is "everything of one perimeter". A single
table ``sillage-catalogue`` keyed by ``PK``/``SK`` keeps one billing and one backup
story, lets a cascade delete walk a partition, and still gives the two reads the
code needs through two global secondary indexes:

* ``by_scope`` - ``gsi1pk = <entity>#<label>`` - listing a family in one
  perimeter, the shape of every tool read (``episode#client-a``);
* ``by_id`` - ``gsi2pk = <entity>#<identifier>`` - reading one row from its
  identifier alone, with ``gsi2sk`` ordered so a fiche's latest version is first.

Primary keys, one per family of chapter 14. ``<actor>`` is the perimeter
(``client-a``, ``interne``, ``commun``), never a person (specification 12.5):

=========================  ===========================  =============================
family                     PK                           SK
=========================  ===========================  =============================
Client                     ``CLIENT#<clientId>``        ``CLIENT``
Travail                    ``SCOPE#<actor>``            ``TRAVAIL#<workItemId>``
Episode                    ``SCOPE#<actor>``            ``EPISODE#<episodeId>``
Lecon                      ``SCOPE#<actor>``            ``LESSON#<lessonId>``
Fiche (une par version)    ``FICHE#<ficheId>``          ``V#<version>`` (6 chiffres)
Usage                      ``USAGE#<usageId>``          ``USAGE``
Retour                     ``USAGE#<usageId>``          ``RETOUR``
Empreinte (dedupe)         ``FINGERPRINT#<hash>``       ``FINGERPRINT``
=========================  ===========================  =============================

A fiche is keyed by its identifier **and** its version, so a correction writes a
new row and the previous one stays readable (EF-ENT-04). Versions are zero padded
because DynamoDB sorts strings: ``V#10`` would otherwise come before ``V#9``.

The journal is **not** in this table: ``Journal`` stays append-only in its own
store, where no code path can rewrite a past decision (ENF-SEC-05).
"""

from __future__ import annotations

from datetime import datetime

from sillage.kernel.types import Client, Episode, Feedback, Fiche, Lesson, Scope, Trace, Usage

#: Name of the single table of the deployed catalogue.
TABLE_NAME = "sillage-catalogue"

#: Names of the two global secondary indexes.
INDEX_BY_SCOPE = "by_scope"
INDEX_BY_ID = "by_id"

#: Width of the version segment of a fiche key: ``V#000042``.
VERSION_WIDTH = 6


def scope_pk(scope: Scope) -> str:
    """Partition key holding every scoped row of one perimeter."""
    return f"SCOPE#{scope.actor_id}"


def version_key(version: int) -> str:
    """Sort key of one version of a fiche, zero padded for string ordering."""
    return f"V#{max(0, int(version)):0{VERSION_WIDTH}d}"


def fiche_order(fiche: Fiche) -> str:
    """Order value of a fiche in the ``by_id`` index: its version, padded.

    Descending order on this value gives the latest version first, which is what
    ``get_fiche(id)`` reads and what ``versions.history`` then walks backwards.
    """
    return f"{max(0, int(fiche.version)):0{VERSION_WIDTH}d}"


def sort_stamp(at: datetime | None, ident: str) -> str:
    """Sort value of a row: its ISO date then its identifier, an absent date first."""
    return f"{at.isoformat() if at else '0000'}#{ident}"


def row_index(entity: str, *, ident: str, label: str, order: str, id_order: str = "") -> dict[str, str]:
    """The two index entries of a row.

    ``gsi1`` lists a family in one perimeter (``label``, the perimeter or the
    fiche a feedback is about); ``gsi2`` answers one identifier whatever the
    perimeter, with ``id_order`` as sort value - a fiche puts its padded version
    there so the latest version is the first row of the partition.
    """
    return {
        "gsi1pk": f"{entity}#{label}" if label else entity,
        "gsi1sk": order,
        "gsi2pk": f"{entity}#{ident}",
        "gsi2sk": id_order or order,
    }


def client_keys(client: Client) -> dict[str, str]:
    """Primary key of a tenant row."""
    return {"PK": f"CLIENT#{client.client_id}", "SK": "CLIENT"}


def travail_keys(trace: Trace) -> dict[str, str]:
    """Primary key of a ``Travail`` row: one per finished job of one perimeter."""
    return {"PK": scope_pk(trace.scope), "SK": f"TRAVAIL#{trace.work_item_id}"}


def episode_keys(episode: Episode) -> dict[str, str]:
    """Primary key of an episode row."""
    return {"PK": scope_pk(episode.scope), "SK": f"EPISODE#{episode.episode_id}"}


def lesson_keys(lesson: Lesson) -> dict[str, str]:
    """Primary key of a lesson row."""
    return {"PK": scope_pk(lesson.scope), "SK": f"LESSON#{lesson.lesson_id}"}


def fiche_keys(fiche: Fiche) -> dict[str, str]:
    """Primary key of one version of a fiche."""
    return {"PK": f"FICHE#{fiche.fiche_id}", "SK": version_key(fiche.version)}


def usage_keys(usage: Usage) -> dict[str, str]:
    """Primary key of a consultation."""
    return {"PK": f"USAGE#{usage.usage_id}", "SK": "USAGE"}


def feedback_keys(feedback: Feedback) -> dict[str, str]:
    """Primary key of the answer to a consultation: one answer per usage."""
    return {"PK": f"USAGE#{feedback.usage_id}", "SK": "RETOUR"}


def fingerprint_keys(fingerprint: str) -> dict[str, str]:
    """Primary key of the dedupe index (EF-CAP-11)."""
    return {"PK": f"FINGERPRINT#{fingerprint}", "SK": "FINGERPRINT"}


def table_definitions() -> list[dict[str, object]]:
    """CloudFormation-ready definition of the table and its indexes.

    ``infra/cdk`` reads this list instead of restating the schema: one definition,
    used by the code and by the deployment (ENF-REP-01).
    """
    return [
        {
            "name": TABLE_NAME,
            "partition_key": {"name": "PK", "type": "S"},
            "sort_key": {"name": "SK", "type": "S"},
            "billing_mode": "PAY_PER_REQUEST",
            "indexes": [
                {
                    "name": INDEX_BY_SCOPE,
                    "partition_key": {"name": "gsi1pk", "type": "S"},
                    "sort_key": {"name": "gsi1sk", "type": "S"},
                    "projection": "ALL",
                },
                {
                    "name": INDEX_BY_ID,
                    "partition_key": {"name": "gsi2pk", "type": "S"},
                    "sort_key": {"name": "gsi2sk", "type": "S"},
                    "projection": "ALL",
                },
            ],
        }
    ]
