"""R13, R14, R15 and R6 - what may leave a client perimeter, and what may not.

Four recipes, one module:

* **R13** - a fiche of client A holding an account identifier is proposed for
  the common knowledge: the version is refused **as long as the identifier
  survives**, and accepted once the rewrite removed it (EF-GEN-02, EF-GEN-03);
* **R14** - a client marked "no sharing" never feeds the common knowledge,
  whatever the quality of the rewrite (EF-GEN-06);
* **R15** - a common lesson carries nothing of the client it comes from, and
  nothing that leads back to the origin episode (EF-GEN-07);
* **R6** - every statement of a fiche points to an episode; a statement added
  without a source is refused (EF-VAL-02).

The definitive set is ``data/fixtures/generalisation_set.json`` (30 fiches,
produced by the data module). While it is not there, the inline fallback below
plants 8 fiches, one per family of identifier of chapter 13.6 step 1: an account
id, an ARN, a host name, an IP address, a mail address, a resource name, the
name of the client itself, and a fiche holding all of them at once.
"""

from __future__ import annotations

from collections.abc import Iterable

import pytest

import guards_fixtures as gf
from sillage.guards import generalize, identifiers, sources
from sillage.kernel.errors import CommonSharingRefused, LeakDetected, UnsourcedClaim, ValidationError
from sillage.kernel.types import (
    SERVED_TO_AUTONOMOUS_AGENT,
    Claim,
    Client,
    Fiche,
    Lesson,
    Level,
    Scope,
    ScopeKind,
)

CLIENT_A = gf.make_client("a", "Client A - retail")
CLIENT_B = gf.make_client("b", "Client B - logistics")
CLIENT_CLOSED = gf.make_client("c", "Client C - industrie", no_common=True)

#: The inline fallback set: 8 fiches to generalise, one family of identifier each.
FALLBACK_CASES: tuple[dict[str, object], ...] = (
    {
        "id": "compte-aws",
        "text": "Le compte 123456789012 dépasse le quota de connexions de la base managée.",
        "identifiants": ["123456789012"],
        "commun": "Le compte du client dépasse le quota de connexions de la base managée.",
    },
    {
        "id": "nom-hote",
        "text": "Redémarrer db-a1.clienta.internal ne libère pas les connexions déjà ouvertes.",
        "identifiants": ["db-a1.clienta.internal"],
        "commun": "Redémarrer l'hôte de la base ne libère pas les connexions déjà ouvertes.",
    },
    {
        "id": "arn-role",
        "text": "Le rôle arn:aws:iam::123456789012:role/app-prod n'a pas la politique attendue.",
        "identifiants": ["arn:aws:iam::123456789012:role/app-prod", "123456789012"],
        "commun": "Le rôle applicatif n'a pas la politique attendue.",
    },
    {
        "id": "adresse-ip",
        "text": "Le pare-feu refuse 10.20.30.40 sur le port 5432 après le changement de groupe.",
        "identifiants": ["10.20.30.40"],
        "commun": "Le pare-feu refuse l'adresse de l'application sur le port de la base.",
    },
    {
        "id": "adresse-messagerie",
        "text": "Prévenir ops@clienta.example avant toute coupure du service de nuit.",
        "identifiants": ["ops@clienta.example"],
        "commun": "Prévenir l'équipe d'exploitation avant toute coupure du service de nuit.",
    },
    {
        "id": "nom-ressource",
        "text": "Le cluster prod-cluster01 sature dès que le pool dépasse 38 connexions.",
        "identifiants": ["prod-cluster01"],
        "commun": "Le cluster applicatif sature dès que le pool dépasse la valeur nominale.",
    },
    {
        "id": "nom-client",
        "text": "Chez Client A - retail, le déploiement de 13h50 a multiplié le pool par cinq.",
        "identifiants": ["Client A - retail"],
        "commun": "Un déploiement a multiplié par cinq la taille du pool de connexions par instance.",
    },
    {
        "id": "toutes-les-familles",
        "text": (
            "Chez Client A - retail, le compte 123456789012 héberge db-a1.clienta.internal "
            "(10.20.30.40) ; le rôle arn:aws:iam::123456789012:role/app-prod sature ; "
            "prévenir ops@clienta.example avant intervention."
        ),
        "identifiants": ["123456789012", "db-a1.clienta.internal", "10.20.30.40"],
        "commun": (
            "Le compte du client héberge l'hôte de la base ; le rôle applicatif sature ; "
            "prévenir l'équipe d'exploitation avant toute intervention."
        ),
    },
)

CASES = gf.load_fixture("generalisation_set.json", FALLBACK_CASES)


def _cases() -> list[tuple[str, str, tuple[str, ...], str]]:
    """(id, texte client, identifiants connus, version commune) for every fiche."""
    built: list[tuple[str, str, tuple[str, ...], str]] = []
    for index, entry in enumerate(CASES):
        case_id = gf.entry_id(entry, index, ("case_id", "fiche"))
        text = gf.entry_text(entry, ("client_text", "version_client", "origine"))
        declared = gf.entry_field(entry, ("identifiants", "identifiers", "known"), [])
        general = str(gf.entry_field(entry, ("commun", "common", "general", "rewritten"), ""))
        found = tuple(declared) + tuple(identifiers.extract(text, client=CLIENT_A))
        built.append((case_id, text, found, general or gf.generalize_text(text, found)))
    return built


SITUATIONS = _cases()
IDS = [case[0] for case in SITUATIONS]
CASES_PARAM = pytest.mark.parametrize(("case_id", "text", "found", "general"), SITUATIONS, ids=IDS)


# --------------------------------------------------------------------------- #
# step 1 of chapter 13.6: the list of identifiers, built by code
# --------------------------------------------------------------------------- #
def test_les_six_familles_d_identifiants_sont_extraites() -> None:
    """Account id, ARN, IP, host name, mail address, resource name: all found."""
    text = (
        "compte 123456789012, arn:aws:iam::123456789012:role/app-prod, 10.20.30.40, "
        "db-a1.clienta.internal, ops@clienta.example, cluster prod-cluster01"
    )
    found = identifiers.extract(text)
    assert "123456789012" in found
    assert "arn:aws:iam::123456789012:role/app-prod" in found
    assert "10.20.30.40" in found
    assert "db-a1.clienta.internal" in found
    assert "ops@clienta.example" in found
    assert "prod-cluster01" in found


def test_le_nom_du_client_et_ses_variantes_sont_des_identifiants() -> None:
    """The name, in lower case, without its dashes, and its significant words."""
    variants = identifiers.name_variants(CLIENT_A.name)
    assert {"client a - retail", "client a retail", "clientaretail", "client a", "retail"} <= variants
    assert identifiers.contains("vu chez Client A la semaine dernière", variants)
    assert not identifiers.contains("vu chez un autre client la semaine dernière", variants)


def test_les_faux_amis_ne_sont_pas_des_identifiants() -> None:
    """A file name, a version, a technology or ordinary prose are not identifiers."""
    text = "Voir README.md et config.yaml, version 1.2.3, db: postgresql, cf. ceci, e.g. là."
    assert not identifiers.extract(text)


def test_la_recherche_est_bornee() -> None:
    """``client a`` does not match inside ``client abc``: no fragment of a word."""
    assert identifiers.contains("chez client a", ["client a"])
    assert not identifiers.contains("chez client abc", ["client a"])
    assert identifiers.contains("arn:aws:iam::123456789012:role/x", ["123456789012"])


def test_la_trace_entiere_est_parcourue() -> None:
    """Every event and every origin value of the finished job is scanned."""
    trace = gf.make_trace(
        ["[alarme] 10.20.30.40 ne répond plus.", "[ingénieur] Je bascule sur db-a1.clienta.internal."]
    )
    found = identifiers.extract_from_trace(trace, client=CLIENT_A)
    assert {"10.20.30.40", "db-a1.clienta.internal", "client a"} <= found


# --------------------------------------------------------------------------- #
# R13 - the common version is refused while one identifier survives
# --------------------------------------------------------------------------- #
def test_r13_le_jeu_comporte_au_moins_huit_fiches_a_generaliser() -> None:
    """R13 asks for 30 fiches; the fallback already plants 8, with identifiers."""
    assert len(SITUATIONS) >= 8, len(SITUATIONS)
    assert all(found for _, _, found, _ in SITUATIONS), "une fiche ne contient aucun identifiant"


@CASES_PARAM
def test_r13_la_version_commune_est_refusee_tant_que_l_identifiant_subsiste(
    case_id: str, text: str, found: tuple[str, ...], general: str
) -> None:
    """EF-GEN-03: one surviving identifier is enough to refuse the version."""
    assert generalize.contains_identifier(text, found) is True, case_id
    with pytest.raises(LeakDetected) as refusal:
        generalize.check_common_version(text, found)
    leaked = refusal.value.identifiers
    assert leaked, f"{case_id}: le refus doit dire quels identifiants subsistent"
    assert set(leaked) <= set(found), leaked
    assert refusal.value.code == "leak_detected"


@CASES_PARAM
def test_r13_la_version_reecrite_passe_le_controle(
    case_id: str, text: str, found: tuple[str, ...], general: str
) -> None:
    """Once the rewrite removed every identifier, the same control says yes."""
    assert generalize.contains_identifier(general, found) is False, f"{case_id}: {general}"
    generalize.check_common_version(general, found)


@CASES_PARAM
def test_r13_la_publication_suit_le_meme_controle(
    case_id: str, text: str, found: tuple[str, ...], general: str
) -> None:
    """The veto of step 3 is the one ``approve_common`` applies, in the same order."""
    fiche = gf.make_fiche(body=text)
    with pytest.raises(LeakDetected):
        _approve(fiche, text, found, client=CLIENT_A)
    lesson = _approve(fiche, general, found, client=CLIENT_A)
    assert lesson.scope.kind is ScopeKind.COMMON
    assert lesson.advice == (general.strip(),), "étape 6 : le texte approuvé est écrit tel quel"


# --------------------------------------------------------------------------- #
# R14 - a client marked "no sharing" never feeds the common knowledge
# --------------------------------------------------------------------------- #
def test_r14_un_client_sans_mise_en_commun_ne_publie_jamais() -> None:
    """EF-GEN-06: the contract wins, even over a perfectly rewritten version."""
    fiche = gf.make_fiche(scope=Scope(kind=ScopeKind.CLIENT, client_id="c"))
    with pytest.raises(CommonSharingRefused) as refusal:
        _approve(fiche, "Revenir au pool de connexions précédent après un déploiement.", (), CLIENT_CLOSED)
    assert refusal.value.details["client_id"] == "c"


def test_r14_le_refus_du_contrat_precede_le_controle_des_identifiants() -> None:
    """The veto is checked first: a leaking version is still refused for the contract."""
    fiche = gf.make_fiche(scope=Scope(kind=ScopeKind.CLIENT, client_id="c"))
    with pytest.raises(CommonSharingRefused):
        _approve(fiche, "Redémarrer db-a1.clienta.internal.", ("db-a1.clienta.internal",), CLIENT_CLOSED)


def test_r14_sans_contrat_la_publication_est_refusee() -> None:
    """No contract shown, no publication: the control cannot be skipped by omission."""
    fiche = gf.make_fiche(scope=Scope(kind=ScopeKind.CLIENT, client_id="a"))
    with pytest.raises(CommonSharingRefused):
        generalize.approve_common(
            fiche=fiche,
            rewritten="Revenir au pool de connexions précédent.",
            identifiers=(),
            approver="p-refer",
        )


def test_r14_un_client_qui_partage_publie() -> None:
    """The other side of the contract option: ``no_common=False`` publishes."""
    fiche = gf.make_fiche(scope=Scope(kind=ScopeKind.CLIENT, client_id="b"))
    lesson = _approve(fiche, "Revenir au pool de connexions précédent.", (), CLIENT_B)
    assert lesson.scope.kind is ScopeKind.COMMON


def test_l_approbation_humaine_est_toujours_exigee() -> None:
    """EF-GEN-05 and step 5: in the hackathon version, no referent, no publication."""
    fiche = gf.make_fiche()
    for approver in ("", "   "):
        with pytest.raises(ValidationError):
            generalize.approve_common(
                fiche=fiche,
                rewritten="Revenir au pool de connexions précédent.",
                identifiers=(),
                approver=approver,
                client=CLIENT_A,
            )


def test_une_version_commune_vide_n_est_pas_publiee() -> None:
    """Step 6 writes the approved text: an empty text publishes nothing."""
    for empty in ("", "   ", "\n"):
        with pytest.raises(ValidationError):
            _approve(gf.make_fiche(), empty, (), CLIENT_A)


# --------------------------------------------------------------------------- #
# R15 - a common lesson carries nothing of its client, and no way back
# --------------------------------------------------------------------------- #
def test_r15_la_lecon_commune_ne_contient_rien_du_client_d_origine() -> None:
    """Every identifier of the origin is absent from the published lesson."""
    trace = gf.make_trace(
        [
            "[alarme] Erreurs 500 sur db-a1.clienta.internal (10.20.30.40), compte 123456789012.",
            "[ingénieur] Le déploiement a multiplié le pool par cinq. Je reviens à 10.",
        ]
    )
    found = identifiers.extract_from_trace(trace, client=CLIENT_A)
    fiche = gf.make_fiche(body=trace.events[0].text)
    lesson = _approve(
        fiche,
        "Un déploiement a multiplié le pool de connexions par cinq ; revenir à la valeur nominale.",
        found,
        CLIENT_A,
    )
    published = gf.all_text(*lesson.advice, lesson.use_case, *lesson.pitfalls, lesson.lesson_id).lower()
    for identifier in found:
        assert identifier.lower() not in published, identifier
    assert lesson.scope.kind is ScopeKind.COMMON
    assert lesson.level in SERVED_TO_AUTONOMOUS_AGENT


def test_r15_aucun_renvoi_vers_le_travail_d_origine() -> None:
    """EF-GEN-07: no episode, no fiche, no work item to follow back."""
    trace = gf.make_trace(["[alarme] Erreurs 500 sur db-a1.clienta.internal."])
    fiche = gf.make_fiche(sources=(trace.work_item_id,))
    lesson = _approve(fiche, "Revenir au pool de connexions précédent.", (), CLIENT_A)
    assert lesson.episode_ids == ()
    published = gf.all_text(*lesson.advice, lesson.use_case, lesson.lesson_id)
    assert trace.work_item_id not in published
    assert fiche.fiche_id not in published
    assert not any(source in published for source in fiche.sources)


def test_r15_l_identifiant_de_la_lecon_ne_depend_que_du_texte() -> None:
    """Two fiches of two clients, one approved text: one common lesson, no origin."""
    text = "Revenir au pool de connexions précédent après un déploiement."
    fiche_a = gf.make_fiche(fiche_id="f-aaaaaa", scope=Scope(kind=ScopeKind.CLIENT, client_id="a"))
    fiche_b = gf.make_fiche(fiche_id="f-bbbbbb", scope=Scope(kind=ScopeKind.CLIENT, client_id="b"))
    lesson_a = _approve(fiche_a, text, (), CLIENT_A)
    lesson_b = _approve(fiche_b, text, (), CLIENT_B)
    assert lesson_a.lesson_id == lesson_b.lesson_id
    assert lesson_a.lesson_id.startswith("co-")
    assert fiche_a.fiche_id not in lesson_a.lesson_id
    assert fiche_b.fiche_id not in lesson_b.lesson_id


def test_r15_la_lecon_commune_est_servable_a_un_agent_autonome() -> None:
    """10.2: a lesson approved by a referent is validated, so an agent may read it."""
    lesson = _approve(gf.make_fiche(), "Revenir au pool de connexions précédent.", (), CLIENT_A)
    assert lesson.level is Level.VALIDATED
    assert lesson.confidence == generalize.APPROVED_CONFIDENCE


# --------------------------------------------------------------------------- #
# R6 - every statement points to an episode
# --------------------------------------------------------------------------- #
def test_r6_une_affirmation_ajoutee_sans_source_est_refusee() -> None:
    """EF-VAL-02: the writer agent added a statement no episode backs."""
    fiche = gf.make_fiche(
        claims=(
            gf.make_claim("Revenir au pool de 10 connexions par instance.", ("ep-1a2b3c",)),
            Claim(text="Il faut aussi augmenter la mémoire du conteneur."),
        )
    )
    with pytest.raises(UnsourcedClaim) as refusal:
        sources.check_claims(fiche)
    assert refusal.value.details["count"] == 1
    assert refusal.value.details["claims"] == ("Il faut aussi augmenter la mémoire du conteneur.",)
    assert sources.is_publishable(fiche) is False


def test_r6_une_fiche_dont_chaque_affirmation_source_est_acceptee() -> None:
    """The other side of R6: every statement cites an episode, the fiche passes."""
    fiche = gf.make_fiche(
        claims=(
            gf.make_claim("Revenir au pool de 10 connexions.", ("ep-1a2b3c",)),
            gf.make_claim("Le redémarrage seul ne règle rien.", ("ep-1a2b3c", "ep-4d5e6f")),
        )
    )
    sources.check_claims(fiche)
    assert sources.coverage(fiche) == 1.0
    assert sources.is_publishable(fiche) is True


def test_r6_la_couverture_est_calculee_par_le_code() -> None:
    """Chapter 16 asks for 100 %: the share is computed, never estimated (ENF-IA-01)."""
    fiche = gf.make_fiche(
        claims=(
            gf.make_claim("Une.", ("ep-1",)),
            gf.make_claim("Deux.", ("ep-2",)),
            Claim(text="Trois."),
        )
    )
    assert sources.coverage(fiche) == pytest.approx(2 / 3)
    assert len(sources.unsourced_claims(fiche)) == 1
    assert sources.coverage(gf.make_fiche(claims=())) == sources.FULL_COVERAGE


def test_r6_la_publication_dans_le_commun_exige_des_sources() -> None:
    """Chapter 10.4: a fiche without a source is not publishable - not even in common."""
    fiche = gf.make_fiche(claims=(Claim(text="Affirmation sans épisode."),))
    with pytest.raises(UnsourcedClaim):
        _approve(fiche, "Revenir au pool de connexions précédent.", (), CLIENT_A)


def _approve(
    fiche: Fiche,
    rewritten: str,
    found: tuple[str, ...] | Iterable[str],
    client: Client | None,
) -> Lesson:
    """One publication, with the referent the recipe always requires."""
    return generalize.approve_common(
        fiche=fiche,
        rewritten=rewritten,
        identifiers=found,
        approver="p-refer",
        client=client,
    )
