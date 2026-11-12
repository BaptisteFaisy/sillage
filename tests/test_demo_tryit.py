"""The public try page: what the memory returns, and what it refuses.

The page of specification 19.1 is the one a judge uses without any account. The
tests below check the two halves of its promise:

* an engineer of client A reads the fiches of client A, with their level, their
  source and their success rate;
* an agent of client B that asks for client A is **refused**, and the refusal
  names the rule that refused - otherwise the isolation would only be a promise.

Two invariants come with them: an autonomous agent never receives a content that
is not validated (EF-RES-05), and the common knowledge names no client (R15).
"""

from __future__ import annotations

import json

import pytest

from sillage.demo import tryit

PROBLEM = "erreurs 500, too many connections sur la base après un déploiement"


@pytest.fixture(scope="module", autouse=True)
def _fresh_page():
    """One page for the module, with its own stack."""
    tryit.reset()
    yield
    tryit.reset()


def test_an_engineer_of_a_sees_the_fiches_of_a() -> None:
    """The engineer of client A reads the knowledge of client A."""
    answer = tryit.ask(PROBLEM, "ingénieur", "a")
    assert answer["fiches"], "la page doit servir des fiches"
    for fiche in answer["fiches"]:
        assert fiche["portefeuille"] == "client-a"
        assert fiche["niveau"] in {"raw", "proposed", "validated", "proven"}
    validated = [fiche for fiche in answer["fiches"] if fiche["niveau"] in {"validated", "proven"}]
    assert validated, "la page doit servir au moins une fiche validée"
    for fiche in validated:
        assert fiche["taux"] is not None
        assert fiche["sources"]
    assert answer["usage_id"]


def test_an_autonomous_agent_of_b_is_refused_when_it_asks_for_a() -> None:
    """The refusal between clients is visible, with the rule that refused."""
    answer = tryit.ask(PROBLEM, "agent autonome", "b")
    refusals = answer["refusals"]
    assert refusals, "la page doit montrer ce qu'elle refuse"
    crossing = [refusal for refusal in refusals if refusal["regle"] == "S.1"]
    assert crossing, refusals
    assert crossing[0]["outil"] == "recall"
    assert crossing[0]["decision"] == "forbid"
    assert crossing[0]["raison"]


def test_an_autonomous_agent_never_receives_unvalidated_content() -> None:
    """Only the validated and the proven reach an autonomous agent (EF-RES-05)."""
    answer = tryit.ask(PROBLEM, "agent autonome", "b")
    for fiche in [*answer["fiches"], *answer["commun"]]:
        assert fiche["niveau"] in {"validated", "proven"}
        assert fiche["code"] in {"2", "3"}


def test_an_autonomous_agent_is_refused_the_unvalidated_content() -> None:
    """``recall_all`` is refused to an autonomous agent (R8)."""
    answer = tryit.ask(PROBLEM, "agent autonome", "b")
    refused = {refusal["regle"] for refusal in answer["refusals"]}
    assert "D.2" in refused


def test_a_referent_may_validate_and_publish() -> None:
    """The two tools a referent alone may use are not refused to a referent."""
    answer = tryit.ask(PROBLEM, "référent", "a")
    refused = {refusal["regle"] for refusal in answer["refusals"]}
    assert "M.2" not in refused
    assert "D.4" not in refused


def test_the_common_knowledge_holds_nothing_of_a() -> None:
    """The common lesson reaches client B without a word about client A (R15)."""
    answer = tryit.ask(PROBLEM, "agent autonome", "b")
    assert answer["commun"], "la page doit servir le savoir commun"
    for fiche in answer["commun"]:
        assert fiche["portefeuille"] == "commun"
        text = " ".join(fiche.get("etapes") or []) + " " + fiche.get("titre", "")
        assert "Client A" not in text
        assert "retail" not in text
        assert not fiche.get("sources"), "le commun ne renvoie à aucun épisode"


def test_the_engineer_of_b_does_not_see_the_fiches_of_a() -> None:
    """The cloisonnement holds in the answer itself, not only in the refusals."""
    answer = tryit.ask(PROBLEM, "ingénieur", "b")
    for fiche in answer["fiches"]:
        assert fiche["portefeuille"] == "client-b"


def test_the_answer_is_serialisable() -> None:
    """The portal serves the answer as JSON: it must hold nothing else."""
    answer = tryit.ask(PROBLEM, "ingénieur", "a")
    printed = json.dumps(answer, ensure_ascii=False)
    assert json.loads(printed)["client"] == "a"
    assert answer["reponse"]
    assert answer["note"]


def test_an_unknown_role_reads_like_an_engineer() -> None:
    """A role the page does not know must not crash it."""
    answer = tryit.ask(PROBLEM, "un rôle inventé", "a")
    assert answer["fiches"]
