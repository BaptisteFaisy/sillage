"""Normalisation: one role per element, dated events, an explicit end.

Covers recipe R1 (a closed ticket becomes a normalised trace) and the capture
requirements about the shape of a trace: EF-CAP-02 (dated events, one role per
element), EF-CAP-04 (perimeter, source, author kind, pseudonym, technologies),
EF-CAP-05/06/07/08 (the four ways a job reaches Sillage), EF-CAP-09 (one mixed
episode with the share of each) and EF-CAP-10 (the verdict of a human).
"""

from __future__ import annotations

import pytest

from sillage.capture import sample
from sillage.capture.authorship import (
    LABEL_AGENT_CODE,
    LABEL_AGENT_EXPLOITATION,
    LABEL_ALARM,
    LABEL_COMMAND,
    LABEL_HUMAN,
    LABEL_SYSTEM,
    HumanVerdict,
    VerdictKind,
    author_kind_of,
    author_shares,
    strip_label,
)
from sillage.capture.episode import episode_from_trace
from sillage.capture.logs import summarise_logs
from sillage.capture.normalize import (
    build_trace,
    ensure_closing,
    normalize,
    parse_moment,
    scope_of,
    to_events,
)
from sillage.capture.redact import redact_trace
from sillage.capture.sources import declared_episode, trace_from_payload
from sillage.kernel.errors import ValidationError
from sillage.kernel.ids import pseudonym
from sillage.kernel.types import (
    AuthorKind,
    ModelTier,
    Role,
    Scope,
    ScopeKind,
    Source,
    Trace,
    TraceEvent,
)

#: The six events of annex B, once the planted secrets are removed.
ANNEXE_B = (
    "[alarme] Le site renvoie des erreurs 500 depuis 14h02. Les journaux indiquent : "
    "too many connections. (clé de service [REDACTED:aws_access_key], valeur de test)",
    "[ingénieur] Hypothèse : la base est sous-dimensionnée. Je redémarre le service applicatif "
    "pour libérer les connexions. Jeton de debug [REDACTED:github_token].",
    "[commande] Redémarrage effectué. Connexions ouvertes : 12, puis 100 au bout de 4 minutes.",
    "[ingénieur] Le redémarrage ne règle rien. Le déploiement de 13h50 a changé la taille du "
    "groupe de connexions de 10 à 50 par instance (journal : [REDACTED:connection_string]). "
    "Je reviens à 10.",
    "[commande] Configuration rétablie. Connexions ouvertes stables à 38. Plus d'erreur 500.",
    "[système] Ticket clos. Résolution confirmée par le demandeur.",
)


class FakeModel:
    """A deterministic model: it never touches the network, it answers or fails."""

    def __init__(self, answer: str = "", fails: bool = False) -> None:
        self.answer = answer
        self.fails = fails
        self.calls: list[str] = []

    def complete(
        self,
        *,
        prompt: str,
        system: str = "",
        tier: ModelTier = ModelTier.FAST,
        json_schema: dict | None = None,
    ) -> str:
        self.calls.append(prompt)
        if self.fails:
            raise RuntimeError("modele indisponible")
        return self.answer


def journal(lines: int) -> str:
    """A journal of ``lines`` lines, with two errors in the middle of the noise."""
    rows = [f"2026-11-11T10:00:{index:02d} INFO requete servie en 12 ms" for index in range(lines)]
    rows[lines // 2] = "2026-11-11T10:00:30 ERROR too many connections for role app"
    rows[lines // 2 + 1] = "2026-11-11T10:00:31 FATAL connection pool exhausted"
    return "\n".join(rows)


@pytest.fixture
def ticket() -> Trace:
    """The reference ticket of annex B, still holding its planted secrets."""
    return sample.ticket_2041()


@pytest.mark.recette
class TestR1ClosedTicket:
    """R1: a closed ticket gives dated events, one role per element."""

    def test_roles_follow_annex_b(self, ticket) -> None:
        assert [event.role for event in ticket.events] == [
            Role.USER,
            Role.ASSISTANT,
            Role.TOOL,
            Role.ASSISTANT,
            Role.TOOL,
            Role.OTHER,
        ]

    def test_kinds_follow_the_elements(self, ticket) -> None:
        assert [event.kind for event in ticket.events] == [
            "request",
            "action",
            "tool_output",
            "action",
            "tool_output",
            "system",
        ]

    def test_every_event_is_dated(self, ticket) -> None:
        assert all(event.at is not None for event in ticket.events)
        assert ticket.closed_at is not None

    def test_texts_are_the_ones_of_annex_b(self, ticket) -> None:
        clean, _ = redact_trace(ticket)
        assert tuple(event.text for event in clean.events) == ANNEXE_B

    def test_every_element_carries_its_author_between_brackets(self, ticket) -> None:
        for event in ticket.events:
            assert event.text.startswith("["), event.text
            assert event.text.split("]", maxsplit=1)[0] + "]" in {
                LABEL_ALARM,
                LABEL_HUMAN,
                LABEL_COMMAND,
                LABEL_SYSTEM,
            }

    def test_tool_outputs_are_kept(self, ticket) -> None:
        outputs = [event for event in ticket.events if event.role is Role.TOOL]
        assert len(outputs) == 2
        assert all(event.kind == "tool_output" for event in outputs)

    def test_trace_ends_with_an_explicit_closing(self, ticket) -> None:
        last = ticket.events[-1]
        assert last.role is Role.OTHER and last.kind == "system"
        assert last.text.startswith(LABEL_SYSTEM)

    def test_the_one_who_resolves_is_assistant(self, ticket) -> None:
        resolver = pseudonym("alice")
        assistants = [event for event in ticket.events if event.role is Role.ASSISTANT]
        assert assistants and all(event.author == resolver for event in assistants)
        assert ticket.author == resolver


class TestEF02TraceMetadata:
    """EF-CAP-02 and 12.6: the event metadata, at most 15 keys."""

    def test_metadata_stays_under_the_limit(self, ticket, settings) -> None:
        assert len(ticket.metadata) <= settings.max_event_metadata

    def test_metadata_carries_what_chapter_12_6_asks(self, ticket) -> None:
        assert ticket.metadata["source"] == Source.GUICHET.value
        assert ticket.metadata["auteurType"] == AuthorKind.HUMAN.value
        assert ticket.metadata["auteur"].startswith("p-")
        assert ticket.metadata["scope"] == "client-a"
        assert "postgresql" in ticket.metadata["techno"]


class TestEF04TraceAttributes:
    """EF-CAP-04: perimeter, source, author kind, pseudonym, technologies."""

    def test_the_ticket_carries_its_five_attributes(self, ticket) -> None:
        assert ticket.scope == Scope(kind=ScopeKind.CLIENT, client_id="a")
        assert ticket.source is Source.GUICHET
        assert ticket.author_kind is AuthorKind.HUMAN
        assert ticket.author == pseudonym("alice")
        assert ticket.technologies == ("postgresql", "ecs")

    def test_no_author_is_ever_a_name(self) -> None:
        for trace in sample.all_samples():
            assert trace.author.startswith("p-"), trace.author
            for event in trace.events:
                assert not event.author or event.author.startswith("p-"), event.author

    def test_a_payload_without_client_falls_in_the_internal_perimeter(self) -> None:
        assert scope_of({"source": "guichet"}).actor_id == "interne"
        assert scope_of({"clientId": "b"}).actor_id == "client-b"


class TestToEvents:
    """``to_events``: the Guichet ticket, element by element."""

    def test_the_resolver_acts_and_the_others_ask(self) -> None:
        events = to_events(
            {
                "description": "Le service ne répond plus.",
                "comments": [
                    {"author": "alice", "text": "Je regarde les journaux."},
                    {"author": "bob", "text": "Chez moi le site est lent aussi."},
                ],
                "resolution": {"author": "alice", "confirmed": True},
                "closed_at": "2026-11-11T12:00:00Z",
            }
        )
        assert [event.role for event in events] == [
            Role.USER,
            Role.ASSISTANT,
            Role.USER,
            Role.OTHER,
        ]
        assert events[1].text == "[ingénieur] Je regarde les journaux."
        assert events[1].kind == "action" and events[2].kind == "request"

    def test_a_ticket_closed_without_confirmation_says_so(self) -> None:
        events = to_events({"description": "panne", "resolution": {"confirmed": False}})
        assert events[-1].text == "[système] Ticket clos sans résolution confirmée."

    def test_the_journal_of_a_ticket_is_reduced(self) -> None:
        events = to_events({"description": "panne", "logs": journal(120)})
        tool = [event for event in events if event.kind == "tool_output"]
        assert len(tool) == 1
        assert len(tool[0].text.splitlines()) <= 40
        assert "too many connections" in tool[0].text


class TestSummariseLogs:
    """13.1: long journals reduced to their useful lines, with a fallback."""

    def test_a_short_journal_is_left_alone(self) -> None:
        assert summarise_logs("une ligne\ndeux lignes", None) == "une ligne\ndeux lignes"

    def test_without_a_model_the_useful_lines_survive(self) -> None:
        kept = summarise_logs(journal(120), None, max_lines=40)
        lines = kept.splitlines()
        assert len(lines) <= 40
        assert any("too many connections" in line for line in lines)
        assert any("connection pool exhausted" in line for line in lines)
        assert lines[-1].startswith("[... ") and "omise" in lines[-1]

    def test_the_fallback_is_deterministic(self) -> None:
        text = journal(200)
        assert summarise_logs(text, None) == summarise_logs(text, None)

    def test_a_model_answer_is_used_when_it_answers(self) -> None:
        model = FakeModel(answer="erreur de pool\ncause : déploiement")
        assert summarise_logs(journal(120), model, max_lines=40) == "erreur de pool\ncause : déploiement"
        assert model.calls and "Journal d'incident" in model.calls[0]

    def test_a_model_that_fails_costs_nothing(self) -> None:
        kept = summarise_logs(journal(120), FakeModel(fails=True), max_lines=40)
        assert "too many connections" in kept

    def test_a_model_answer_is_cut_to_the_budget(self) -> None:
        model = FakeModel(answer="\n".join(f"ligne {index}" for index in range(80)))
        assert len(summarise_logs(journal(120), model, max_lines=10).splitlines()) == 10


class TestEF09MixedWork:
    """EF-CAP-09: an agent starts, a human finishes -> one episode, mixed."""

    def test_one_trace_for_the_whole_work(self) -> None:
        trace = sample.investigation_77()
        assert trace.work_item_id == "devops_agent:inv-77"
        assert trace.session_id == trace.work_item_id
        assert trace.author_kind is AuthorKind.MIXED

    def test_the_share_of_each_is_counted_by_the_code(self) -> None:
        trace = sample.investigation_77()
        assert trace.origin["partAgent"] == "1"
        assert trace.origin["partHumain"] == "1"
        assert author_shares(trace.events)["agent"] == 1

    def test_both_actors_appear_in_the_same_episode(self) -> None:
        trace = sample.investigation_77()
        actors = {event.author for event in trace.events if event.kind == "action"}
        assert len(actors) == 2
        assert pseudonym("agent-devops") in actors and pseudonym("alice") in actors

    def test_the_author_of_the_trace_is_the_one_who_finished(self) -> None:
        assert sample.investigation_77().author == pseudonym("alice")
        assert sample.kiro_session().author == pseudonym("bob")

    def test_the_kind_is_computed_not_declared(self) -> None:
        human = TraceEvent(role=Role.ASSISTANT, text="x", author_kind=AuthorKind.HUMAN, kind="action")
        agent = TraceEvent(role=Role.ASSISTANT, text="y", author_kind=AuthorKind.AGENT, kind="action")
        assert author_kind_of((human, agent)) is AuthorKind.MIXED
        assert author_kind_of((human,)) is AuthorKind.HUMAN
        assert author_kind_of((agent,)) is AuthorKind.AGENT


class TestEF10HumanVerdict:
    """EF-CAP-10 and journey 8.4: the verdict of a human on an agent's work."""

    def test_a_rejected_diagnosis_is_an_event_of_the_trace(self) -> None:
        trace = sample.investigation_77()
        verdicts = [event for event in trace.events if "rejeté par un ingénieur" in event.text]
        assert len(verdicts) == 1
        assert verdicts[0].role is Role.OTHER and verdicts[0].kind == "system"

    def test_the_verdict_carries_the_three_parts(self) -> None:
        text = next(event for event in sample.investigation_77().events if "rejeté par" in event.text).text
        assert "la base est saturée, il faut augmenter sa taille" in text
        assert "Raison :" in text
        assert "Bon diagnostic : fuite de connexions" in text

    def test_the_verdict_comes_before_the_closing(self) -> None:
        events = sample.investigation_77().events
        assert events[-2].text.startswith("[système] Diagnostic de l'agent rejeté")
        assert events[-1].text.startswith("[système] Enquête close")

    def test_the_verdict_is_attached_to_the_trace(self) -> None:
        origin = sample.investigation_77().origin
        assert origin["verdict"] == VerdictKind.REJECTED.value
        assert origin["verdictPar"] == pseudonym("alice")
        assert origin["diagnosticRejete"].startswith("la base est saturée")
        assert origin["diagnosticCorrect"].startswith("fuite de connexions")

    def test_an_accepted_session_is_captured_too(self) -> None:
        trace = sample.kiro_session()
        assert trace.origin["verdict"] == VerdictKind.ACCEPTED.value
        assert any("accepté par un ingénieur" in event.text for event in trace.events)

    def test_a_sentence_never_ends_with_two_full_stops(self) -> None:
        verdict = HumanVerdict(
            kind=VerdictKind.REJECTED,
            reviewer="alice",
            reason="Le pool a été doublé.",
            rejected_diagnosis="la base est saturée",
            correct_diagnosis="fuite de connexions",
        )
        assert ".." not in verdict.body()
        assert verdict.body().endswith("fuite de connexions.")


class TestLabels:
    """13.1: the label tells who speaks, whatever the source."""

    def test_one_label_per_author(self) -> None:
        assert LABEL_HUMAN == "[ingénieur]"
        assert LABEL_AGENT_EXPLOITATION == "[agent d'exploitation]"
        assert LABEL_AGENT_CODE == "[agent de code]"
        assert LABEL_SYSTEM == "[système]"

    def test_the_agent_of_an_investigation_is_an_exploitation_agent(self) -> None:
        assert any(LABEL_AGENT_EXPLOITATION in event.text for event in sample.investigation_77().events)

    def test_the_agent_of_a_session_is_a_code_agent(self) -> None:
        assert any(LABEL_AGENT_CODE in event.text for event in sample.kiro_session().events)
        assert any(LABEL_AGENT_CODE in event.text for event in sample.merged_fix().events)

    def test_a_striped_label_reads_back(self) -> None:
        assert strip_label("[ingénieur] Je redémarre.") == "Je redémarre."


class TestEnsureClosing:
    """13.1: the service detects the end of the episode on an explicit event."""

    def test_a_trace_without_an_end_gets_one(self) -> None:
        events = (TraceEvent(role=Role.ASSISTANT, text="[ingénieur] Je redémarre.", kind="action"),)
        closed = ensure_closing(events)
        assert len(closed) == 2
        assert closed[-1].role is Role.OTHER and closed[-1].kind == "system"

    def test_a_trace_that_already_ends_is_left_alone(self) -> None:
        trace = sample.ticket_2041()
        assert len(ensure_closing(trace.events)) == len(trace.events)


class TestEF06DeclaredEpisode:
    """EF-CAP-06: an agent declares its own episode, with an imposed structure."""

    def payload(self, **changes) -> dict:
        base = {
            "clientId": "a",
            "workItemId": "agent-a:session-9",
            "situation": "La chaîne de déploiement échoue sur l'étape de build.",
            "intent": "Reconstruire l'image sans le cache corrompu.",
            "attempts": [
                {
                    "action": "Je vide le cache de build.",
                    "reasoning": "une couche est corrompue",
                    "result": "Build vert en 3 minutes.",
                    "at": "2026-11-11T11:02:00Z",
                }
            ],
            "evidence": "journal de la chaîne : build vert",
            "outcome": "success",
            "author": "agent-maison",
            "endedAt": "2026-11-11T11:20:00Z",
        }
        base.update(changes)
        return base

    def test_a_declared_episode_becomes_a_trace(self) -> None:
        trace = declared_episode(self.payload())
        assert trace.source is Source.SELF_DECLARED
        assert trace.work_item_id == "agent-a:session-9"
        assert trace.author_kind is AuthorKind.AGENT
        assert trace.author == pseudonym("agent-maison")

    def test_the_trace_says_the_agent_summarised_itself(self) -> None:
        trace = declared_episode(self.payload())
        assert trace.origin["declarePar"] == pseudonym("agent-maison")
        assert trace.events[-1].text.startswith("[système] Épisode déclaré par l'agent.")

    def test_the_declared_outcome_is_read_by_the_code(self) -> None:
        assert episode_from_trace(declared_episode(self.payload())).outcome.value == "success"
        failed = declared_episode(self.payload(outcome="failure"))
        assert episode_from_trace(failed).outcome.value == "failure"

    def test_the_imposed_structure_is_checked(self) -> None:
        with pytest.raises(ValidationError) as error:
            declared_episode(self.payload(intent=""))
        assert "intent" in str(error.value)


class TestSources:
    """EF-CAP-05/07/08: every entry point gives the same normalised shape."""

    def test_the_eventbridge_event_of_an_investigation(self) -> None:
        trace = trace_from_payload(Source.DEVOPS_AGENT, sample.investigation_77_payload())
        assert trace == sample.investigation_77()
        assert trace.source is Source.DEVOPS_AGENT

    def test_the_stop_trigger_of_a_code_session(self) -> None:
        trace = trace_from_payload(Source.KIRO, sample.kiro_session_payload())
        assert trace == sample.kiro_session()
        assert trace.events[-1].text.startswith("[système] Session close")

    def test_a_merged_correction_keeps_its_review(self) -> None:
        trace = trace_from_payload(Source.MERGE, sample.merged_fix_payload())
        assert trace.source is Source.MERGE and trace.scope.actor_id == "client-b"
        assert trace.origin["ticket"] == "INC-512"
        assert any("Relecture (approuvé)" in event.text for event in trace.events)
        assert trace.events[-1].text.startswith("[système] Correction fusionnée")

    def test_a_raw_payload_of_the_common_shape(self) -> None:
        trace = normalize(
            {
                "source": "guichet",
                "external_id": "3000",
                "client": "b",
                "author": "carol",
                "technologies": ["mysql"],
                "closed_at": "2026-11-11T18:00:00Z",
                "events": [
                    {"kind": "request", "text": "Le site est lent.", "identity": "dave"},
                    {"kind": "action", "text": "Je purge le cache.", "identity": "carol"},
                ],
            }
        )
        assert trace.work_item_id == "guichet:3000"
        assert trace.events[0].role is Role.USER
        assert trace.events[1].text == "[ingénieur] Je purge le cache."
        assert trace.events[-1].kind == "system"
        assert trace.closed_at is not None

    def test_build_trace_computes_what_the_payload_does_not_say(self) -> None:
        trace = build_trace(
            source=Source.GUICHET,
            scope=Scope(kind=ScopeKind.INTERNAL),
            events=(),
            external_id="1",
        )
        assert trace.author_kind is not None
        assert trace.events[-1].kind == "system"
        assert trace.origin["partAgent"] == "0"


class TestParseMoment:
    """Dates: one rule for every source, and never an invented instant."""

    def test_iso_with_a_z_is_read_as_utc(self) -> None:
        moment = parse_moment("2026-11-11T14:02:00Z")
        assert moment is not None and moment.utcoffset().total_seconds() == 0

    def test_an_unreadable_date_is_unknown_not_now(self) -> None:
        assert parse_moment("hier") is None
        assert parse_moment("") is None
        assert parse_moment(None) is None
