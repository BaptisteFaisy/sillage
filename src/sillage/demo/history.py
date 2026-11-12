"""The deterministic dataset of the demo: tenants, fault families, payloads.

Specification 19.1 asks the demonstration for three things this module holds:
**two fictional clients** and one **internal perimeter**, and a starting history
spread over **eight fault families**. The generated history itself belongs to the
``data`` lot (``data/generated/history.json``, 60 to 100 episodes); this module is
what :mod:`sillage.demo.seed` falls back on when that file is not there yet, so
the demo always has something to show and never depends on another lot.

Everything here is deterministic and synthetic:

* the payloads are Guichet tickets, the shape of annex B, normalised by
  :mod:`sillage.capture.sources` - the demo assembles, it does not reimplement;
* the authors are identities the capture pseudonymises (ENF-RH-01);
* the dates derive from one frozen instant, so a freshness figure never moves
  between two runs;
* every fault tells **a lead that failed and one that worked**, because that pair
  is the whole point of the project (EF-DIS-03, EF-DIS-04).

No text here contains a secret, an account identifier or a real client name.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sillage.capture.clock import DEMO_MOMENT
from sillage.kernel.types import Client

#: The client names the demonstration shows, read from the catalogue.
CLIENT_NAMES: dict[str, str] = {"a": "Client A - retail", "b": "Client B - logistics"}

#: The technological families each client runs, read by the reflection grouping.
CLIENT_TECHNOLOGIES: dict[str, tuple[str, ...]] = {
    "a": ("postgresql", "ecs"),
    "b": ("mysql", "eks"),
}

#: Perimeter of each occurrence of a family: the first client, the second one,
#: then the infrastructure of the MSP itself. ``None`` is the internal perimeter.
OCCURRENCE_SLOTS: tuple[int | None, ...] = (0, 0, 0, 1, 1, None, None)

#: First ticket number of the fallback history: the reference samples stay below it.
FIRST_TICKET = 3000


@dataclass(frozen=True)
class FaultFamily:
    """One of the eight fault families of specification 19.1.

    ``wrong_lead`` and ``wrong_result`` are the path that did not work: the
    capture reads the failure in the wording of the result, exactly as
    :mod:`sillage.memory.distill` does, so the episode keeps it apart from the
    teachings (EF-DIS-04).
    """

    number: int
    name: str
    code: str
    technologies: tuple[str, ...]
    alarm: str
    wrong_lead: str
    wrong_result: str
    fix: str
    fix_result: str

    @property
    def title(self) -> str:
        """The title a ticket of this family carries."""
        return self.name


#: The eight families, in the order of the table of specification 19.1.
FAMILIES: tuple[FaultFamily, ...] = (
    FaultFamily(
        number=1,
        name="Connexions à la base épuisées après un changement de configuration",
        code="INC-CONN",
        technologies=("postgresql", "ecs"),
        alarm=(
            "Le site renvoie des erreurs 500 et les journaux indiquent too many connections "
            "sur la base depuis le dernier déploiement."
        ),
        wrong_lead="Je redémarre le service applicatif pour libérer les connexions de la base.",
        wrong_result="Le redémarrage ne règle rien : les connexions remontent au maximum en quatre minutes.",
        fix=(
            "Je compare la taille du groupe de connexions avant et après le déploiement "
            "et je reviens à la valeur précédente."
        ),
        fix_result="Les connexions de la base restent stables, le site ne renvoie plus d'erreur 500.",
    ),
    FaultFamily(
        number=2,
        name="Certificat expiré sur un répartiteur de charge",
        code="INC-CERT",
        technologies=("nginx",),
        alarm=(
            "Le site affiche un avertissement de certificat depuis ce matin : "
            "le répartiteur de charge sert un certificat périmé."
        ),
        wrong_lead="Je recharge la configuration du répartiteur de charge.",
        wrong_result="Le rechargement est sans effet : l'avertissement de certificat reste affiché.",
        fix=(
            "Je remplace le certificat du répartiteur par celui renouvelé la veille "
            "et je contrôle sa date d'expiration."
        ),
        fix_result="Le certificat est valable, plus aucun avertissement dans le navigateur.",
    ),
    FaultFamily(
        number=3,
        name="File de messages qui retraite en boucle",
        code="INC-FILE",
        technologies=("sqs",),
        alarm="La file de messages grossit et le même message est traité en boucle depuis la nuit.",
        wrong_lead="J'ajoute des consommateurs à la file de messages.",
        wrong_result="L'ajout de consommateurs est inutile : les messages reviennent dans la file.",
        fix="Je corrige le consommateur qui échoue avant l'acquittement et je vide la file de rejeu.",
        fix_result="Chaque message est acquitté une fois, la file se vide.",
    ),
    FaultFamily(
        number=4,
        name="Fonction limitée par sa concurrence réservée",
        code="INC-FONC",
        technologies=("lambda",),
        alarm="La fonction refuse des invocations alors que le trafic n'a pas augmenté.",
        wrong_lead="J'augmente la mémoire allouée à la fonction.",
        wrong_result="L'augmentation de mémoire ne change rien : les invocations sont toujours refusées.",
        fix="Je relève la concurrence réservée de la fonction, qui plafonnait les invocations simultanées.",
        fix_result="Les invocations passent, plus aucun refus de saturation.",
    ),
    FaultFamily(
        number=5,
        name="Chaîne de déploiement cassée par une nouvelle version de l'image de construction",
        code="INC-CHAI",
        technologies=("docker",),
        alarm="La chaîne de déploiement échoue sur l'étape de construction depuis cette nuit.",
        wrong_lead="Je relance la chaîne de déploiement.",
        wrong_result="La relance est inefficace : l'étape de construction échoue encore.",
        fix="J'épingle la version de l'image de construction, la dernière publiée a cassé l'étape.",
        fix_result="La chaîne repasse au vert avec l'image épinglée.",
    ),
    FaultFamily(
        number=6,
        name="Disque plein à cause des journaux",
        code="INC-DISQ",
        technologies=("eks",),
        alarm="Le nœud passe en pression disque et les pods sont expulsés les uns après les autres.",
        wrong_lead="J'ajoute un nœud au groupe pour répartir la charge.",
        wrong_result=(
            "L'ajout d'un nœud est sans résultat : les nouveaux pods remplissent le disque à leur tour."
        ),
        fix="Je configure la rotation et la compression des journaux du nœud, qui remplissaient le disque.",
        fix_result="Le disque reste sous la moitié de sa capacité, plus aucune expulsion de pod.",
    ),
    FaultFamily(
        number=7,
        name="Rotation d'un coffre non prise en compte par l'application",
        code="INC-COFF",
        technologies=("mysql",),
        alarm="L'application refuse l'authentification à sa base depuis la rotation nocturne du coffre.",
        wrong_lead="Je réécris la valeur du coffre dans le fichier de configuration de l'application.",
        wrong_result="La réécriture est sans effet : le coffre remet sa valeur au déploiement suivant.",
        fix="J'abonne l'application à la rotation du coffre et je fais recharger la valeur en mémoire.",
        fix_result="L'application suit la rotation du coffre, plus aucune authentification refusée.",
    ),
    FaultFamily(
        number=8,
        name="Règle réseau supprimée par un déploiement d'infrastructure",
        code="INC-REZO",
        technologies=("haproxy",),
        alarm=(
            "Le mandataire ne joint plus la base, la connexion est refusée "
            "depuis le déploiement d'infrastructure."
        ),
        wrong_lead="Je redémarre le mandataire.",
        wrong_result="Le redémarrage du mandataire est inutile : la connexion reste refusée.",
        fix="Je rétablis la règle réseau que le déploiement d'infrastructure avait supprimée.",
        fix_result="Le mandataire joint la base, plus aucune connexion refusée.",
    ),
)


#: The code of the family the demonstration tells the story with: the exhausted
#: connections. The confirmations and the common knowledge land on its fiches.
STAR_CODE = FAMILIES[0].code

#: The families by their code: the title of a fiche comes from them.
FAMILY_BY_CODE: dict[str, FaultFamily] = {family.code: family for family in FAMILIES}


def client_name(client_id: str) -> str:
    """The fictional name of a tenant of the demo."""
    return CLIENT_NAMES.get(client_id, f"Client {client_id.upper()} - demo")


def client_technologies(client_id: str) -> tuple[str, ...]:
    """The technologies a tenant runs: the ones the reflection groups on."""
    return CLIENT_TECHNOLOGIES.get(client_id, ("postgresql", "ecs"))


def demo_clients(client_ids: Sequence[str]) -> tuple[Client, ...]:
    """The tenant rows of the demo, in the order they were asked for."""
    return tuple(
        Client(
            client_id=str(client_id),
            name=client_name(str(client_id)),
            technologies=client_technologies(str(client_id)),
        )
        for client_id in client_ids
    )


def perimeter_of(slot: int | None, client_ids: Sequence[str]) -> str:
    """The client id of one occurrence, or an empty string for the internal perimeter."""
    if slot is None or not client_ids:
        return ""
    return str(client_ids[slot % len(client_ids)])


def iso(moment: datetime) -> str:
    """An instant as the sources write it: ISO 8601 with a ``Z``."""
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def ticket_payload(
    family: FaultFamily,
    *,
    number: int,
    perimeter: str,
    moment: datetime,
    identity: str,
) -> dict[str, Any]:
    """One closed Guichet ticket of a fault family, in the shape of annex B."""
    opened = moment - timedelta(minutes=52)
    payload: dict[str, Any] = {
        "id": f"ticket-{number}",
        "ticket": str(number),
        "code": family.code,
        "title": family.title,
        "trigger": "alarme",
        "created_at": iso(opened),
        "closed_at": iso(moment),
        "technologies": list(family.technologies),
        "description": family.alarm,
        "comments": [
            {
                "author": identity,
                "author_kind": "human",
                "at": iso(opened + timedelta(minutes=12)),
                "text": family.wrong_lead,
                "tool_output": family.wrong_result,
            },
            {
                "author": identity,
                "author_kind": "human",
                "at": iso(moment - timedelta(minutes=14)),
                "text": family.fix,
                "tool_output": family.fix_result,
            },
        ],
        "resolution": {"author": identity, "confirmed": True, "at": iso(moment)},
        "synthetique": "1",
    }
    if perimeter:
        payload["client"] = perimeter
    return payload


def fallback_payloads(
    client_ids: Sequence[str] = ("a", "b"), moment: datetime = DEMO_MOMENT
) -> tuple[dict[str, Any], ...]:
    """The fallback history: eight families, spread over the perimeters.

    Deterministic by construction - the families, their order, the perimeters and
    the dates are all fixed - so two runs of the demo tell the same story. Each
    occurrence is one day older than the previous one, which is what makes the
    freshness of a fiche a meaningful figure on screen.
    """
    payloads: list[dict[str, Any]] = []
    number = FIRST_TICKET
    for family in FAMILIES:
        for slot in OCCURRENCE_SLOTS:
            perimeter = perimeter_of(slot, client_ids)
            age = len(payloads)
            payloads.append(
                ticket_payload(
                    family,
                    number=number,
                    perimeter=perimeter,
                    moment=moment - timedelta(days=age),
                    identity=f"ingenieur-{family.number}-{slot if slot is not None else 'msp'}",
                )
            )
            number += 1
    return tuple(payloads)


__all__ = [
    "CLIENT_NAMES",
    "CLIENT_TECHNOLOGIES",
    "FAMILIES",
    "FAMILY_BY_CODE",
    "FIRST_TICKET",
    "OCCURRENCE_SLOTS",
    "STAR_CODE",
    "FaultFamily",
    "client_name",
    "client_technologies",
    "demo_clients",
    "fallback_payloads",
    "iso",
    "perimeter_of",
    "ticket_payload",
]
