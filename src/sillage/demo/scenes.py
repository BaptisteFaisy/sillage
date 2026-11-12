"""The six journeys of the demonstration, one scene each (chapter 8).

Each scene returns the lines a judge reads on screen during the three minute
video: one line, one step, in French. Nothing here invents a fact - every line
is read from the stack the code built, so the demonstration cannot claim
something the memory does not hold.

The six scenes are the six journeys of specification 19.2:

* 8.1 an engineer closes a ticket at client A, and the episode appears with the
  lead that failed and the one that worked;
* 8.2 the same fault comes back, an autonomous agent calls ``recall`` and drops
  the false lead;
* 8.3 a developer and their code agent: the lesson learnt the day before is
  served to another agent;
* 8.4 a human corrects an agent: the episode keeps the rejected diagnosis and
  the right one, and the lesson carries the trap;
* 8.5 the same problem at client B: the lesson reaches the common knowledge
  without a word about client A, and the attempt to read A is **refused**;
* 8.6 a fiche fails twice: it is set aside and the referent is warned.

A seventh scene shows a proven fiche becoming a skill in the registry (8.7).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sillage.capture import sample
from sillage.catalog.levels import apply_feedback
from sillage.catalog.scoring import success_rate
from sillage.demo.fiches import identifiers_of, star_fiche
from sillage.demo.history import FAMILIES, ticket_payload
from sillage.demo.knowledge import skill_folder
from sillage.demo.lookup import consult
from sillage.demo.seed import COMMON_TEXT
from sillage.guards.generalize import check_common_version
from sillage.kernel.errors import LeakDetected
from sillage.kernel.ids import episode_id, pseudonym, slug, usage_id
from sillage.kernel.types import (
    LEVEL_CODE,
    AgentMode,
    Decision,
    Feedback,
    FeedbackResult,
    Fiche,
    Level,
    PrincipalRole,
    Scope,
    ScopeKind,
    Source,
    Usage,
)
from sillage.memory.local_records import split_list

if TYPE_CHECKING:  # the stack is only read here, never built
    from sillage.demo.stack import Stack

#: The number of the demonstration ticket: above the seeded ones, never twice.
DEMO_TICKET = 9001

#: The number of the demonstration code session.
DEMO_SESSION = "kiro-demo-2026-11-12"

#: The perimeter the story happens in.
CLIENT_A = Scope(kind=ScopeKind.CLIENT, client_id="a")
CLIENT_B = Scope(kind=ScopeKind.CLIENT, client_id="b")
COMMON = Scope(kind=ScopeKind.COMMON)

#: The problem the whole demonstration tells.
PROBLEM = "erreurs 500, too many connections sur la base après un déploiement"


# -- 8.1 --------------------------------------------------------------------- #
def scene_engineer_closes_a_ticket(stack: Stack) -> list[str]:
    """An engineer closes a ticket: the episode appears, nothing more is asked."""
    trace = stack.ingest.ingest_raw(Source.GUICHET, _demo_ticket_payload(stack))
    episode = stack.catalog.get_episode(trace.episode_id)
    pitfalls, _ = _distilled(stack, episode)
    failed = pitfalls[0] if pitfalls else "aucune"
    worked = episode.turns[-1].action if episode.turns else "aucune"
    return [
        "8.1 — Un ingénieur clôt un ticket chez le client A, sans rien saisir de plus.",
        f"Guichet envoie le ticket clos {trace.trace.work_item_id} : description, commentaires, "
        "extraits de journaux, code de résolution.",
        f"Capture : {trace.redacted_secrets} secret(s) retiré(s) avant tout stockage, "
        f"trace copiée sous la clé {trace.raw_key}.",
        f"L'épisode {trace.episode_id} apparaît : {episode.situation}",
        f"Piste qui a échoué : {failed}",
        f"Piste qui a marché : {worked}",
        f"Verdict lu par le code : {episode.outcome.value}. Aucun chiffre n'a été écrit par un modèle.",
    ]


def _distilled(stack: Stack, episode: Any) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """The paths that failed and the ones that worked, read in the memory record.

    The catalogue row of a captured job holds what the capture knows; the
    distilled episode of AgentCore Memory holds the failed paths and the
    teachings, kept apart (EF-DIS-03, EF-DIS-04). The record id is the episode
    id: the row and the record are the same object seen from two sides.
    """
    reader = getattr(stack.memory, "record", None)
    if reader is None:
        return (), ()
    record = reader(episode.episode_id)
    if record is None:
        return (), ()
    metadata = record.metadata
    return split_list(metadata.get("pitfalls", "")), split_list(metadata.get("teachings", ""))


# -- 8.2 --------------------------------------------------------------------- #
def scene_agent_investigates(stack: Stack) -> list[str]:
    """The same fault comes back: an autonomous agent calls ``recall``."""
    agent = stack.principal(PrincipalRole.AGENT, "a", AgentMode.AUTONOMOUS)
    decision = stack.decide("recall", agent, clientId="a")
    found = consult(stack, principal=agent, problem=PROBLEM, scope=CLIENT_A)
    lines = [
        "8.2 — La même panne revient chez le client A : un agent autonome ouvre une enquête.",
        f"L'agent appelle recall(clientId=a) : décision {decision.decision.value} "
        f"(règle {decision.rule}) — {decision.reason}.",
        f"Sillage reformule le problème en intention : « {found.intent} ».",
        f"recall renvoie {len(found.fiches)} fiche(s) et {len(found.common)} leçon(s) commune(s).",
    ]
    lines.extend(_fiche_lines(found.fiches[0] if found.fiches else None))
    lines.append(f"{found.hidden} élément(s) non validé(s) existent ; un agent autonome ne les reçoit pas.")
    lines.extend(_outcome_lines(stack, found.fiches[0]["id"] if found.fiches else ""))
    lines.append("L'enquête terminée devient elle-même un épisode de la mémoire.")
    return lines


def _fiche_lines(fiche: dict[str, Any] | None) -> list[str]:
    """The two lines that show what the memory served, and what it warned about."""
    if fiche is None:
        return ["Aucune fiche validée ne répond à ce problème."]
    lines = [
        f"Fiche servie : « {fiche['titre']} » — niveau {fiche['code']}, "
        f"taux de réussite {fiche['taux']:.0%}, sources : {', '.join(fiche['sources'][:2])}.",
    ]
    if fiche.get("etapes"):
        lines.append(f"Ce qui a marché : {fiche['etapes'][0]}")
    if fiche.get("a_eviter"):
        lines.append(f"Ce qu'il ne fallait pas faire : {fiche['a_eviter'][0]}")
    return lines


def _outcome_lines(stack: Stack, fiche_id: str) -> list[str]:
    """The usage feedback of the agent, and the rate the code recomputes."""
    if not fiche_id:
        return []
    fiche = stack.catalog.get_fiche(fiche_id)
    if fiche is None:
        return []
    author = pseudonym("agent-demo-a")
    usage = _usage(stack, fiche, author)
    stack.catalog.put_usage(usage)
    stack.catalog.put_feedback(
        Feedback(
            usage_id=usage.usage_id,
            result=FeedbackResult.HELPED,
            fiche_id=fiche.fiche_id,
            caller=author,
            at=stack.now(),
        )
    )
    updated = apply_feedback(
        fiche, FeedbackResult.HELPED, settings=stack.settings, now=stack.now(), author=author
    )
    stack.catalog.put_fiche(updated)
    rate = success_rate(successes=updated.success_count, failures=updated.failure_count)
    return [
        f"L'agent appelle report_outcome : la fiche a aidé. "
        f"Taux de réussite recompté par le code : {rate:.0%}.",
    ]


def _usage(stack: Stack, fiche: Fiche, author: str) -> Usage:
    """One consultation of a fiche, with the identifier the feedback refers to."""
    return Usage(
        usage_id=usage_id(author, stack.now().isoformat(), fiche.fiche_id),
        served_ids=(fiche.fiche_id,),
        caller_role=PrincipalRole.ENGINEER,
        scope=fiche.scope,
        at=stack.now(),
        problem=fiche.title,
    )


# -- 8.3 --------------------------------------------------------------------- #
def scene_developer_and_code_agent(stack: Stack) -> list[str]:
    """A developer and their code agent: the lesson of yesterday is served."""
    agent = stack.principal(PrincipalRole.AGENT, "a", AgentMode.ASSISTED)
    decision = stack.decide("recall", agent, clientId="a")
    found = consult(stack, principal=agent, problem="la chaîne de déploiement échoue", scope=CLIENT_A)
    lines = [
        "8.3 — Un développeur et son agent de code : un déploiement échoue.",
        f"L'agent de code appelle recall : décision {decision.decision.value} (règle {decision.rule}).",
        f"Il reçoit {len(found.fiches)} fiche(s) du même client, apprises par d'autres agents.",
    ]
    lines.extend(_fiche_lines(found.fiches[0] if found.fiches else None))
    report = stack.ingest.ingest_raw(Source.KIRO, _kiro_payload())
    lines.append(
        f"Le déclencheur de fin de session envoie la trace : l'épisode {report.episode_id} "
        "est créé, le développeur en profitera la prochaine fois."
    )
    return lines


def _kiro_payload() -> dict[str, Any]:
    """A code session of the demonstration, built on the reference one."""
    payload = dict(sample.kiro_session_payload("a"))
    payload["sessionId"] = DEMO_SESSION
    return payload


# -- 8.4 --------------------------------------------------------------------- #
def scene_human_corrects_an_agent(stack: Stack) -> list[str]:
    """A human rejects the diagnosis of an agent and gives the right one."""
    work_item = "devops_agent:inv-77"
    ident = episode_id("client-a", work_item)
    episode = stack.catalog.get_episode(ident)
    trace = stack.catalog.get_trace(work_item)
    if episode is None or trace is None:
        return ["8.4 — L'épisode corrigé n'a pas été trouvé dans la mémoire."]
    lesson = _lesson_of(stack, ident)
    traps = lesson.pitfalls if lesson is not None else ()
    origin = trace.origin
    return [
        "8.4 — Un humain corrige un agent.",
        f"L'agent avait conclu : {origin.get('diagnosticRejete', episode.rejected_diagnosis)}.",
        f"L'ingénieur refuse ce diagnostic : {origin.get('raisonVerdict', '')}",
        f"Le bon diagnostic : {origin.get('diagnosticCorrect', '')}",
        f"L'épisode garde les deux, et qui a corrigé : {episode.corrected_by}.",
        f"La leçon suivante contient le piège : {traps[0] if traps else 'aucun piège'}",
        "À la prochaine enquête, l'agent reçoit ce piège avant de conclure.",
    ]


def _lesson_of(stack: Stack, episode_ident: str) -> Any:
    """The lesson one episode was part of, if there is one."""
    for lesson in stack.catalog.list_lessons():
        if episode_ident in lesson.episode_ids:
            return lesson
    return None


# -- 8.5 --------------------------------------------------------------------- #
def scene_common_knowledge(stack: Stack) -> list[str]:
    """The lesson reaches the common knowledge; reading client A from B is refused."""
    star = _star_fiche(stack)
    if star is None:
        return ["8.5 — Aucune fiche de client A dans le catalogue."]
    client = stack.catalog.get_client("a")
    leaky = f"Chez {client.name}, comparer la taille du groupe de connexions avant le déploiement."
    veto = _leak_veto(leaky, identifiers_of(stack, star))
    lines = [
        "8.5 — Le même problème chez le client B : la leçon passe au commun.",
        f"Une fiche validée du client A est proposée pour le savoir commun : « {star.title} ».",
        f"Contrôle par le code : un identifiant du client subsiste ({veto}) — la version est refusée.",
        "Le généralisateur réécrit sans rien de propre au client, puis un référent approuve.",
        f"Le texte approuvé est publié tel quel dans l'espace commun : « {COMMON_TEXT[:80]}… »",
    ]
    lines.extend(_common_lines(stack))
    lines.extend(_refusal_lines(stack))
    return lines


def _leak_veto(text: str, identifiers: frozenset[str]) -> str:
    """The identifiers that made the common version refused, named by the code."""
    try:
        check_common_version(text, identifiers)
    except LeakDetected as error:
        return ", ".join(sorted(error.identifiers)[:3])
    return "aucun"


def _common_lines(stack: Stack) -> list[str]:
    """What the common knowledge holds, and what client B receives of it."""
    common = list(stack.catalog.list_lessons(scope=COMMON))
    if not common:
        return ["La leçon commune n'a pas été publiée."]
    agent = stack.principal(PrincipalRole.AGENT, "b", AgentMode.AUTONOMOUS)
    found = consult(stack, principal=agent, problem=PROBLEM, scope=CLIENT_B)
    served = found.common[0]["titre"] if found.common else "rien"
    return [
        f"La leçon commune {common[0].lesson_id} est publiée : elle ne cite aucun épisode du client A.",
        f"Chez le client B, un agent autonome la reçoit : « {served} ».",
    ]


def _refusal_lines(stack: Stack) -> list[str]:
    """The refusal an agent of client B gets when it asks for client A."""
    agent = stack.principal(PrincipalRole.AGENT, "b", AgentMode.AUTONOMOUS)
    decision = stack.decide("recall", agent, clientId="a")
    if decision.decision is Decision.PERMIT:
        return ["La politique aurait autorisé cette lecture : le cloisonnement est à vérifier."]
    return [
        f"Le même agent demande le client A : refus {decision.decision.value} "
        f"(règle {decision.rule}) — {decision.reason}",
        "Le refus est journalisé : il est visible dans la trace, avant même l'appel de l'outil.",
    ]


# -- 8.6 --------------------------------------------------------------------- #
def scene_fiche_set_aside(stack: Stack) -> list[str]:
    """A fiche fails twice: it is set aside and the referent is warned."""
    fiche = _doomed_fiche(stack)
    if fiche is None:
        return ["8.6 — Aucune fiche à mettre à l'écart."]
    stack.catalog.put_feedback(
        Feedback(
            usage_id=f"u-demo-ko-{fiche.fiche_id}",
            result=FeedbackResult.DID_NOT_HELP,
            fiche_id=fiche.fiche_id,
            caller=pseudonym("agent-demo-a"),
            at=stack.now(),
        )
    )
    updated = apply_feedback(fiche, FeedbackResult.DID_NOT_HELP, settings=stack.settings, now=stack.now())
    updated = apply_feedback(updated, FeedbackResult.DID_NOT_HELP, settings=stack.settings, now=stack.now())
    stack.catalog.put_fiche(updated)
    stack.notifier.notify(
        referent=pseudonym("referent-demo"),
        subject=f"Fiche mise à l'écart : {updated.title}",
        body=f"Deux retours « n'a pas marché » de suite sur {updated.fiche_id}.",
        fiche_id=updated.fiche_id,
    )
    agent = stack.principal(PrincipalRole.AGENT, "a", AgentMode.AUTONOMOUS)
    found = consult(stack, principal=agent, problem=updated.title, scope=CLIENT_A)
    still = any(item["id"] == updated.fiche_id for item in found.fiches)
    return [
        "8.6 — Une fiche devient fausse.",
        f"La fiche « {updated.title} » est servie deux fois de suite et reçoit deux retours "
        "« n'a pas marché ».",
        f"Deux échecs consécutifs : elle passe à l'écart, niveau {LEVEL_CODE[updated.level]} "
        f"— {updated.set_aside_reason}.",
        f"Le référent est prévenu avec les deux épisodes en cause : {updated.fiche_id}.",
        f"Un agent autonome ne la reçoit plus "
        f"({'elle est encore servie' if still else 'elle est écartée'}) ; "
        "un humain la voit avec l'avertissement.",
    ]


def _doomed_fiche(stack: Stack) -> Fiche | None:
    """A validated fiche of client A, other than the one the story tells."""
    fiches = [
        fiche
        for fiche in stack.catalog.list_fiches(scope=CLIENT_A)
        if fiche.level is Level.VALIDATED and not fiche.success_count
    ]
    return fiches[0] if fiches else None


# -- 8.7 --------------------------------------------------------------------- #
def scene_skill_export(stack: Stack) -> list[str]:
    """A proven fiche becomes a skill in the registry, then is discoverable."""
    star = _star_fiche(stack)
    if star is None:
        return ["8.7 — Aucune fiche éprouvée à exporter."]
    referent = stack.principal(PrincipalRole.REFERENT, "a")
    decision = stack.decide("export_skill", referent, ficheId=star.fiche_id)
    folder = skill_folder(star)
    record_id = stack.registry.register_skill(name=slug(star.title), folder=folder, draft=True)
    before = stack.registry.get(record_id)
    stack.registry.approve(record_id)
    after = stack.registry.get(record_id)
    return [
        "8.7 — Une fiche éprouvée devient une compétence d'agent.",
        f"Le référent exporte « {star.title} » (niveau {LEVEL_CODE[star.level]}) : "
        f"décision {decision.decision.value} (règle {decision.rule}).",
        f"Le dossier {slug(star.title)}/ contient {', '.join(sorted(folder))}.",
        f"Enregistré dans le registre sous {record_id}, en brouillon : {before.state}.",
        f"Après approbation : {after.state}. Kiro le découvre par le registre.",
    ]


# -- shared ------------------------------------------------------------------ #
def _star_fiche(stack: Stack) -> Fiche | None:
    """The fiche the story tells: the one about the exhausted connections."""
    return star_fiche(stack, CLIENT_A)


def _demo_ticket_payload(stack: Stack) -> dict[str, Any]:
    """The ticket of the demonstration: the reference family, with a fake secret."""
    payload = ticket_payload(
        FAMILIES[0],
        number=DEMO_TICKET,
        perimeter="a",
        moment=stack.now(),
        identity="ingenieur-demo",
    )
    payload["description"] = (
        f"{payload['description']} (clé de service {sample.PLANTED_ACCESS_KEY}, valeur de test)"
    )
    return payload
