"""R2 - 30 planted secrets: none of them survives the way out.

The recipe is short: *a ticket containing an access key - no trace of the key in
memory nor in the object store*. The test proves it in three steps, on every
secret of the set:

1. the capture detects it and destroys it (:mod:`sillage.capture.redact`), and
   leaves an explicit marker so a reader sees the hole;
2. the **output pipeline** of the guards - quarantine of the poisoned events,
   list of the client identifiers, control of the common version - carries
   nothing of it: the lesson published in the common knowledge is clean;
3. the ingested trace, its copy in the object store and the records replayed in
   memory are clean too: this is the "nor in S3" of R2.

The definitive set is ``data/fixtures/planted_secrets.json`` (30 secrets,
produced by the data module). While it is not there, the inline fallback below
plants 14 secrets covering the eight families the recipe names. Every one of
them is **visibly fictional** (``AKIAEXAMPLE…``, ``ghp_EXAMPLE…``): this file is
committed, and a real credential must never be able to reach it.

The secrets are not removed by the guards: ``capture/redact.py`` owns that
family, on the way in, and ``guards/`` never duplicates its patterns. What the
guards add is the second filter of chapter 10.3 - Bedrock Guardrails - which
refuses a publication the filter blocks.
"""

from __future__ import annotations

import pytest

import guards_fixtures as gf
from sillage.capture.ingest import build_default_service
from sillage.capture.redact import contains_secret, redact, redact_trace
from sillage.guards import generalize, identifiers, poison
from sillage.kernel.errors import LeakDetected
from sillage.kernel.types import Trace

#: The eight families the recipe R2 names, and the ones the fallback adds.
FAMILIES = frozenset(
    {
        "cle_acces_aws",
        "jeton_github",
        "jeton_gitlab",
        "jwt",
        "mot_de_passe",
        "chaine_connexion",
        "cle_pem",
        "api_key",
        "jeton_slack",
        "jeton_porteur",
        "cle_secrete_aws",
        "secret_generique",
    }
)

#: The inline fallback set: 14 planted secrets, every one of them fictional.
FALLBACK_SECRETS: tuple[dict[str, str], ...] = (
    {
        "id": "cle-acces-aws",
        "famille": "cle_acces_aws",
        "secret": "AKIAEXAMPLEFAKEKEY01",
        "text": "[alarme] Erreurs 500. Clé de service relevée dans les journaux : AKIAEXAMPLEFAKEKEY01.",
    },
    {
        "id": "jeton-github",
        "famille": "jeton_github",
        "secret": "ghp_EXAMPLEfake0000000000000000000000",
        "text": "[ingénieur] Jeton de debug pour le dépôt : ghp_EXAMPLEfake0000000000000000000000.",
    },
    {
        "id": "jeton-gitlab",
        "famille": "jeton_gitlab",
        "secret": "glpat-EXAMPLEfake000000000000",
        "text": "[ci] Jeton du runner : glpat-EXAMPLEfake000000000000, à révoquer après l'incident.",
    },
    {
        "id": "jwt-session",
        "famille": "jwt",
        "secret": "eyJhbGciEXAMPLE.eyJzdWJEXAMPLE.sigEXAMPLEfake",
        "text": "[outil] Session ouverte avec eyJhbGciEXAMPLE.eyJzdWJEXAMPLE.sigEXAMPLEfake.",
    },
    {
        "id": "mot-de-passe-clair",
        "famille": "mot_de_passe",
        "secret": "ExampleFakePassword01",
        "text": "[ingénieur] Connexion au bastion : password: ExampleFakePassword01 (compte de test).",
    },
    {
        "id": "mot-de-passe-francais",
        "famille": "mot_de_passe",
        "secret": "ExampleFake01",
        "text": "[guichet] L'agent a laissé son mot de passe : ExampleFake01 dans le commentaire.",
    },
    {
        "id": "chaine-connexion",
        "famille": "chaine_connexion",
        "secret": "postgres://sillage:examplefakepwd@db-a1.clienta.internal:5432/app",
        "text": "[journal] La connexion postgres://sillage:examplefakepwd@db-a1.clienta.internal:5432/app"
        " a été refusée.",
    },
    {
        "id": "cle-privee-pem",
        "famille": "cle_pem",
        "secret": "MIIEEXAMPLEFAKEKEYDATA0000",
        "text": "[ingénieur] Clé collée par erreur :\n-----BEGIN PRIVATE KEY-----\n"
        "MIIEEXAMPLEFAKEKEYDATA0000\n-----END PRIVATE KEY-----",
    },
    {
        "id": "api-key",
        "famille": "api_key",
        "secret": "EXAMPLEFAKEKEY0123456789",
        "text": "[configuration] API_KEY=EXAMPLEFAKEKEY0123456789 dans le fichier d'environnement.",
    },
    {
        "id": "jeton-slack",
        "famille": "jeton_slack",
        "secret": "xoxb-EXAMPLE-0000-0000-faketoken",
        "text": "[notification] Bot du canal : xoxb-EXAMPLE-0000-0000-faketoken.",
    },
    {
        "id": "jeton-porteur",
        "famille": "jeton_porteur",
        "secret": "EXAMPLEfakeTOKEN0000000",
        "text": "[http] Entête de la requête : Bearer EXAMPLEfakeTOKEN0000000.",
    },
    {
        "id": "cle-secrete-aws",
        "famille": "cle_secrete_aws",
        "secret": "EXAMPLEFAKEsecretKEY0000000",
        "text": "[terraform] aws_secret_access_key = EXAMPLEFAKEsecretKEY0000000 dans l'état distant.",
    },
    {
        "id": "secret-key",
        "famille": "secret_generique",
        "secret": "EXAMPLEFAKE0000000000",
        "text": "[déploiement] secret_key=EXAMPLEFAKE0000000000 injecté au démarrage du conteneur.",
    },
    {
        "id": "client-secret",
        "famille": "secret_generique",
        "secret": "EXAMPLEfakeCLIENT0000",
        "text": "[oauth] client_secret: EXAMPLEfakeCLIENT0000 pour l'application du portail.",
    },
)

SECRETS = gf.load_fixture("planted_secrets.json", FALLBACK_SECRETS)


def _cases() -> list[tuple[str, str, str]]:
    """(identifier, texte du ticket, secret planté) for every entry of the set."""
    return [
        (
            gf.entry_id(entry, index, ("secret_id", "famille")),
            gf.entry_text(entry, ("secret_text",)),
            str(gf.entry_field(entry, ("secret", "value", "needle", "planted", "cle"), "")),
        )
        for index, entry in enumerate(SECRETS)
    ]


CASES = _cases()
IDS = [case[0] for case in CASES]
SECRETS_PARAM = pytest.mark.parametrize(("secret_id", "text", "secret"), CASES, ids=IDS)


def test_le_jeu_comporte_au_moins_dix_secrets() -> None:
    """R2 asks for 30; the fallback already plants 14, and every one is checked."""
    assert len(CASES) >= 10, CASES
    assert all(text for _, text, _ in CASES)


def test_le_repli_couvre_les_familles_exigees() -> None:
    """The eight families the recipe names are all planted in the fallback set."""
    planted = {entry["famille"] for entry in FALLBACK_SECRETS}
    assert planted >= FAMILIES, sorted(FAMILIES - planted)


def test_les_secrets_plantes_sont_tous_visiblement_fictifs() -> None:
    """A committed file must never carry something that could be a real secret."""
    for entry in FALLBACK_SECRETS:
        assert "EXAMPLE" in entry["secret"].upper() or "fake" in entry["secret"].lower()


@SECRETS_PARAM
def test_chaque_secret_plante_est_detecte(secret_id: str, text: str, secret: str) -> None:
    """The capture sees it: a secret it cannot find is a secret it stores."""
    if not secret:
        pytest.skip("le jeu chargé ne donne pas le secret en clair")
    assert secret in text, secret_id
    assert contains_secret(text), secret_id


@SECRETS_PARAM
def test_la_redaction_detruit_le_secret_et_laisse_un_marqueur(secret_id: str, text: str, secret: str) -> None:
    """EF-CAP-03: removed, counted, and replaced by an explicit marker."""
    cleaned, removed = redact(text)
    assert removed >= 1, secret_id
    assert not contains_secret(cleaned), cleaned
    assert cleaned.startswith("[REDACTED:") or "[REDACTED:" in cleaned, cleaned
    if secret:
        assert secret not in cleaned, secret_id


@SECRETS_PARAM
def test_le_pipeline_de_sortie_ne_laisse_passer_aucun_secret(secret_id: str, text: str, secret: str) -> None:
    """Redaction, then the guards: quarantine, identifiers, common version."""
    trace = gf.make_trace([text, "[ingénieur] Je reviens à la configuration précédente."])
    clean, removed = redact_trace(trace)
    assert removed >= 1, secret_id

    screened = poison.screen_trace(clean)
    client = gf.make_client()
    found = identifiers.extract_from_trace(screened, client=client)
    rewritten = gf.generalize_text(_body_of(screened), found)
    fiche = gf.make_fiche(claims=(gf.make_claim("Revenir au pool de connexions précédent."),))

    lesson = generalize.approve_common(
        fiche=fiche, rewritten=rewritten, identifiers=found, approver="p-refer", client=client
    )
    published = gf.all_text(*lesson.advice, lesson.use_case, *lesson.pitfalls)
    assert not contains_secret(published), published
    if secret:
        assert secret not in published, secret_id
    for event in screened.events:
        assert not contains_secret(event.text), event.text


@SECRETS_PARAM
def test_ni_dans_la_copie_objet_ni_en_memoire(secret_id: str, text: str, secret: str) -> None:
    """R2, letter by letter: no trace of the key in memory nor in the object store."""
    service = build_default_service()
    report = service.ingest_trace(gf.make_trace([text, "[ingénieur] Configuration rétablie."]))
    assert report.redacted_secrets >= 1, secret_id

    blob = service.blobs.get(report.raw_key) or ""
    assert blob, "la copie objet de la trace manque"
    assert not contains_secret(blob), blob
    for record in service.memory.list_records(namespace_path=""):
        assert not contains_secret(record.content), record.content
        assert not contains_secret(str(record.metadata)), record.metadata

    stored = service.catalog.get_trace(report.trace.work_item_id)
    assert stored is not None
    for event in stored.events:
        assert not contains_secret(event.text), event.text
    if secret:
        assert secret not in gf.all_text(blob, *[event.text for event in stored.events])


def test_le_filtre_guardrails_refuse_la_publication() -> None:
    """EF-GEN-04: the sensitive data filter is a second veto, on top of the code."""
    guardrails = gf.ScriptedGuardrails(blocked=True, reasons=("SENSITIVE_DATA:AWS_ACCESS_KEY",))
    with pytest.raises(LeakDetected) as refusal:
        generalize.approve_common(
            fiche=gf.make_fiche(),
            rewritten="Revenir au pool de connexions précédent après un déploiement.",
            identifiers=(),
            guardrails=guardrails,
            approver="p-refer",
            client=gf.make_client(),
        )
    assert guardrails.scanned, "le filtre n'a pas vu le texte"
    assert refusal.value.details.get("reasons") == ("SENSITIVE_DATA:AWS_ACCESS_KEY",)


def test_le_filtre_ne_masque_pas_en_silence_ce_qui_est_approuve() -> None:
    """Step 6 writes what step 5 approved: a masked text is refused, not published."""
    guardrails = gf.ScriptedGuardrails(masked_text="Revenir au pool précédent.")
    with pytest.raises(LeakDetected):
        generalize.approve_common(
            fiche=gf.make_fiche(),
            rewritten="Revenir au pool de connexions précédent après un déploiement.",
            identifiers=(),
            guardrails=guardrails,
            approver="p-refer",
            client=gf.make_client(),
        )


def _body_of(trace: Trace) -> str:
    """The text of every event of a trace, as a generaliser would rewrite it."""
    return "\n".join(event.text for event in trace.events)
