"""Secrets removal: nothing survives, anywhere (EF-CAP-03, recipe R2).

R2 asks for 30 planted secrets and not one of them left, neither in the
normalised trace nor in the raw copy of the object store. ``data/fixtures/
planted_secrets.json`` is the set the data module will provide; until it exists,
the inline set below stands in for it. Every value here is visibly fake - an
``AKIAEXAMPLE...`` key, a ``ghp_EXAMPLE...`` token - because this file is
committed and a real credential must never be able to reach it.
"""

from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest

from sillage.capture import sample
from sillage.capture.ingest import build_default_service
from sillage.capture.normalize import build_trace
from sillage.capture.redact import (
    SECRET_PATTERNS,
    contains_secret,
    redact,
    redact_mapping,
    redact_trace,
)
from sillage.kernel.types import AuthorKind, Role, Scope, ScopeKind, Source, Trace, TraceEvent

#: Where the data module will put the shared set of 30 planted secrets.
FIXTURE = Path(__file__).resolve().parent.parent / "data" / "fixtures" / "planted_secrets.json"

#: The fallback set: 30 fake secrets, at least one per family Sillage knows.
#: The family is kept next to the value so a test can say which one leaked.
PLANTED: tuple[tuple[str, str], ...] = (
    ("aws_access_key", "AKIAEXAMPLEFAKEKEY01"),
    ("aws_access_key", "ASIAEXAMPLEFAKEKEY02"),
    ("aws_access_key", "AKIAEXAMPLEFAKEKEY03"),
    ("aws_access_key", "AROAEXAMPLEFAKEKEY04"),
    ("github_token", "ghp_EXAMPLEfake0000000000000000000001"),
    ("github_token", "gho_EXAMPLEfake0000000000000000000002"),
    ("github_token", "ghs_EXAMPLEfake0000000000000000000003"),
    ("github_token", "ghu_EXAMPLEfake0000000000000000000004"),
    ("gitlab_token", "glpat-EXAMPLEfake0000000001"),
    ("gitlab_token", "glpat-EXAMPLEfake0000000002"),
    ("slack_token", "xoxb-EXAMPLE-1234567890"),
    ("slack_token", "xoxp-EXAMPLE-0987654321"),
    ("jwt", "eyJhbGciOiJIUzI1NiJ9.eyJleGFtcGxlIjpmYWtlfQ.c2lnbmF0dXJlLWV4ZW1wbGU"),
    ("jwt", "eyJleGFtcGxlIjoiZmFrZSJ9.eyJzZWNvbmQiOiJleGVtcGxlIn0.ZXhhbXBsZS1mYWtlLXNpZ25lZA"),
    (
        "private_key",
        "-----BEGIN PRIVATE KEY-----\nMIIEEXAMPLEfakekeymaterial\n-----END PRIVATE KEY-----",
    ),
    (
        "private_key",
        "-----BEGIN RSA PRIVATE KEY-----\nMIIBEXAMPLEfakekeymaterial\n-----END RSA PRIVATE KEY-----",
    ),
    ("connection_string", "postgres://sillage:hunter2example@db-a1.clienta.internal:5432/app"),
    ("connection_string", "mysql://root:exemplemotdepasse@db-b.clientb.internal:3306/app"),
    ("connection_string", "mongodb+srv://sillage:exemplemotdepasse@cluster0.example.mongodb.net/app"),
    ("connection_string", "redis://default:exampleredispass@cache.internal:6379/0"),
    ("password", "password=hunter2example"),
    ("password", "mot de passe : exemplemotdepasse"),
    ("password", "passwd: exemplesecret123"),
    ("password", "pwd=exemplepwd1234"),
    ("aws_secret_key", "aws_secret_access_key=wJalrXUtnFEMIEXAMPLEKEYfake0000000000"),
    ("aws_secret_key", "AWS_SECRET_ACCESS_KEY = exemplesecretkeyfake000000000000"),
    ("generic_secret", "API_KEY=exempleapiclfake0000000000"),
    ("generic_secret", "api-key: exemple-api-key-fake-0000"),
    ("generic_secret", "client_secret=exempleclientsecret00"),
    ("generic_secret", "token=exempletokenvalue0000000"),
)


def planted_secrets() -> tuple[tuple[str, str], ...]:
    """The shared fixture when the data module provides it, the inline set else."""
    if not FIXTURE.exists():
        return PLANTED
    loaded = json.loads(FIXTURE.read_text(encoding="utf-8"))
    rows = loaded.get("secrets", loaded) if isinstance(loaded, dict) else loaded
    parsed = [
        (str(row.get("family", "")), str(row.get("secret") or row.get("value") or ""))
        if isinstance(row, dict)
        else ("", str(row))
        for row in rows
    ]
    return tuple(row for row in parsed if row[1]) or PLANTED


def secrets_only() -> tuple[str, ...]:
    """The planted values, without their family."""
    return tuple(secret for _, secret in planted_secrets())


def is_visibly_fake(secret: str) -> bool:
    """True when a value cannot be mistaken for a real credential.

    A JWT hides its payload in base64, so the words are looked for in the
    decoded segments too: a committed fixture must never hold a real secret.
    """
    candidates = [secret.lower()]
    for part in secret.split("."):
        padded = part + "=" * (-len(part) % 4)
        try:
            candidates.append(base64.urlsafe_b64decode(padded).decode("utf-8", "ignore").lower())
        except ValueError:
            continue
    return any("example" in text or "fake" in text or "exemple" in text for text in candidates)


def trace_of_secrets() -> Trace:
    """One trace holding every planted secret: one action event per secret."""
    events = tuple(
        TraceEvent(
            role=Role.ASSISTANT,
            text=f"[ingénieur] Vérification {index} : {secret}",
            author="p-0000",
            author_kind=AuthorKind.HUMAN,
            kind="action",
        )
        for index, (_, secret) in enumerate(planted_secrets(), start=1)
    )
    return build_trace(
        source=Source.GUICHET,
        scope=Scope(kind=ScopeKind.CLIENT, client_id="a"),
        events=events,
        external_id="secrets",
        origin={"note": f"jeton de secours {PLANTED[4][1]}"},
    )


class TestPlantedSet:
    """The set itself: 30 secrets, all fake, all recognisable."""

    def test_there_are_thirty_of_them(self) -> None:
        assert len(planted_secrets()) == 30

    def test_every_secret_is_visibly_fake(self) -> None:
        for secret in secrets_only():
            assert is_visibly_fake(secret), f"secret pas visiblement fictif : {secret}"

    def test_every_secret_is_detected(self) -> None:
        for family, secret in planted_secrets():
            assert contains_secret(secret), f"{family}: {secret} non détecté"


class TestRedact:
    """The contract of ``redact``: explicit, stable, not reversible, counted."""

    def test_the_replacement_names_the_family(self) -> None:
        cleaned, removed = redact("clé AKIAEXAMPLEFAKEKEY01 trouvée")
        assert cleaned == "clé [REDACTED:aws_access_key] trouvée"
        assert removed == 1

    def test_the_secret_never_survives(self) -> None:
        for family, secret in planted_secrets():
            cleaned, removed = redact(f"avant {secret} après")
            assert removed >= 1, f"{family}: rien retiré"
            assert secret not in cleaned, f"{family}: {secret} subsiste"
            assert not contains_secret(cleaned), f"{family}: encore un secret"

    def test_a_removal_is_not_reversible(self) -> None:
        cleaned, _ = redact("password=hunter2example")
        assert "hunter2example" not in cleaned
        assert cleaned == "[REDACTED:password]"

    def test_redaction_is_idempotent(self) -> None:
        once, first = redact("API_KEY=exempleapiclfake0000000000 puis pwd=exemplepwd1234")
        twice, second = redact(once)
        assert first == 2 and second == 0
        assert once == twice

    def test_a_text_without_a_secret_is_untouched(self) -> None:
        text = "Le pool de connexions est passé de 10 à 50 par instance."
        assert redact(text) == (text, 0)
        assert contains_secret(text) is False

    def test_every_family_has_its_pattern(self) -> None:
        families = {family for family, _ in planted_secrets()}
        assert len(SECRET_PATTERNS) >= len(families)

    def test_a_mapping_is_cleaned_too(self) -> None:
        cleaned, removed = redact_mapping({"code": "INC-500", "note": "clé AKIAEXAMPLEFAKEKEY01"})
        assert removed == 1
        assert cleaned["code"] == "INC-500"
        assert "AKIA" not in cleaned["note"]


class TestRedactTrace:
    """``redact_trace``: every event and every attribute of the trace."""

    def test_the_events_are_cleaned(self) -> None:
        trace, removed = redact_trace(trace_of_secrets())
        assert removed >= len(planted_secrets())
        for event in trace.events:
            assert not contains_secret(event.text), event.text

    def test_the_origin_attributes_are_cleaned(self) -> None:
        trace, _ = redact_trace(trace_of_secrets())
        assert "ghp_" not in trace.origin["note"]
        assert "[REDACTED:github_token]" in trace.origin["note"]

    def test_the_trace_keeps_its_shape(self) -> None:
        original = trace_of_secrets()
        cleaned, _ = redact_trace(original)
        assert len(cleaned.events) == len(original.events)
        assert cleaned.work_item_id == original.work_item_id
        assert cleaned.author == original.author
        assert cleaned.scope == original.scope

    def test_the_input_trace_is_not_modified(self) -> None:
        original = trace_of_secrets()
        before = original.events[0].text
        redact_trace(original)
        assert original.events[0].text == before
        assert contains_secret(before)


@pytest.mark.recette
class TestR2NothingSurvives:
    """R2: 30 planted secrets, none left in the trace nor in the raw copy."""

    def test_no_secret_in_the_ingested_trace(self) -> None:
        service = build_default_service()
        report = service.ingest_trace(trace_of_secrets())
        assert report.redacted_secrets >= len(planted_secrets())
        for event in report.trace.events:
            assert not contains_secret(event.text), event.text
            for secret in secrets_only():
                assert secret not in event.text

    def test_no_secret_in_the_raw_copy_of_the_object_store(self) -> None:
        service = build_default_service()
        report = service.ingest_trace(trace_of_secrets())
        stored = service.blobs.get(report.raw_key)
        assert stored, "EF-CAP-13 : aucune copie brute"
        assert not contains_secret(stored)
        for family, secret in planted_secrets():
            for line in secret.splitlines():
                assert line.strip() not in stored, f"{family}: {line!r} subsiste dans la copie"

    def test_the_reference_ticket_is_cleaned_too(self) -> None:
        service = build_default_service()
        dirty = sample.ticket_2041()
        assert contains_secret(dirty.events[0].text), "le secret planté a disparu de l'échantillon"
        report = service.ingest_trace(dirty)
        assert report.redacted_secrets == 3
        assert sample.PLANTED_ACCESS_KEY not in report.trace.events[0].text
        assert sample.PLANTED_GITHUB_TOKEN not in report.trace.events[1].text
        assert sample.PLANTED_CONNECTION_STRING not in report.trace.events[3].text
        stored = service.blobs.get(report.raw_key) or ""
        assert sample.PLANTED_ACCESS_KEY not in stored
        assert sample.PLANTED_GITHUB_TOKEN not in stored
        assert sample.PLANTED_CONNECTION_STRING not in stored

    def test_the_episode_of_the_catalogue_is_clean_too(self) -> None:
        service = build_default_service()
        report = service.ingest_trace(trace_of_secrets())
        episode = service.catalog.get_episode(report.episode_id)
        assert episode is not None
        assert not contains_secret(episode.situation)
        assert not contains_secret(episode.intent)
        for turn in episode.turns:
            assert not contains_secret(turn.action)
            assert not contains_secret(turn.result)


class TestSampleSecrets:
    """The samples carry real looking secrets, so the redaction is exercised."""

    def test_the_ticket_of_annex_b_holds_three_of_them(self) -> None:
        found = sum(1 for event in sample.ticket_2041().events if contains_secret(event.text))
        assert found == 3

    def test_the_other_samples_stay_clean(self) -> None:
        for trace in (sample.investigation_77(), sample.kiro_session(), sample.merged_fix()):
            for event in trace.events:
                assert not contains_secret(event.text), trace.work_item_id
