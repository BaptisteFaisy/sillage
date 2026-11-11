"""``DynamoCatalog`` against a fake client: the shape of every call, no network.

Nothing here talks to AWS: ``fake_dynamo.FakeDynamo`` answers like a low-level
DynamoDB client and records the payloads. What is checked is exactly what a real
service would reject or silently mis-answer: the primary key of a family, the
index used for a read, the low-level item shape (``S``/``N``/``BOOL``/``L``/
``M``/``SS``), the version sort key of a fiche, and the cascade delete.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

import pytest

from fake_dynamo import FakeDynamo
from sillage.catalog.dynamo import DynamoCatalog, tables
from sillage.catalog.dynamo_tables import TABLE_NAME, version_key
from sillage.catalog.local import LocalCatalog
from sillage.catalog.versions import bump, history
from sillage.kernel.errors import BackendUnavailable
from sillage.kernel.protocols import CatalogStore
from sillage.kernel.types import (
    AuthorKind,
    Claim,
    Client,
    Episode,
    Feedback,
    FeedbackResult,
    Fiche,
    Level,
    Scope,
    ScopeKind,
    Source,
    Trace,
    Usage,
)

NOW = datetime(2026, 11, 11, 12, 0, tzinfo=UTC)
COMMON = Scope(kind=ScopeKind.COMMON)


@pytest.fixture
def fake() -> FakeDynamo:
    return FakeDynamo()


@pytest.fixture
def catalog(fake: FakeDynamo, settings) -> DynamoCatalog:
    return DynamoCatalog(settings, client=fake)


def make_trace(scope: Scope, work_item_id: str = "guichet:2041") -> Trace:
    return Trace(
        work_item_id=work_item_id,
        scope=scope,
        source=Source.GUICHET,
        author_kind=AuthorKind.HUMAN,
        author="p-7f3a",
        technologies=("postgresql", "ecs"),
        closed_at=NOW,
        received_at=NOW,
        origin={"ticket": "2041"},
    )


def make_episode(
    scope: Scope, episode_id: str = "ep-1", work_item_id: str = "guichet:2041", **changes
) -> Episode:
    base = Episode(
        episode_id=episode_id,
        scope=scope,
        work_item_id=work_item_id,
        situation="500 sur le site",
        intent="retablir le service",
        created_at=NOW,
    )
    return replace(base, **changes)


def make_fiche(scope: Scope, fiche_id: str = "f-1", **changes) -> Fiche:
    base = Fiche(
        fiche_id=fiche_id,
        scope=scope,
        title="Connexions a la base epuisees",
        body="Le pool de connexions a ete porte de 10 a 50.",
        steps=("verifier le pool",),
        claims=(Claim("le pool est trop grand", ("ep-1",)),),
        sources=("guichet:2041",),
        level=Level.VALIDATED,
        validated_by="p-referent",
        validated_at=NOW,
    )
    return replace(base, **changes)


class TestConstruction:
    def test_a_client_can_be_injected_so_no_boto3_is_needed(self, fake: FakeDynamo, settings) -> None:
        assert DynamoCatalog(settings, client=fake) is not None

    def test_importing_the_module_does_not_import_boto3(self) -> None:
        import sys

        import sillage.catalog.dynamo  # noqa: F401

        assert "boto3" not in sys.modules

    def test_the_deployed_store_satisfies_the_same_protocol(self, catalog: DynamoCatalog) -> None:
        assert isinstance(catalog, CatalogStore)

    def test_the_table_definitions_are_exposed_for_the_infra(self) -> None:
        definitions = tables()
        assert definitions[0]["name"] == TABLE_NAME
        indexes = [index["name"] for index in definitions[0]["indexes"]]
        assert indexes == ["by_scope", "by_id"]
        assert definitions[0]["partition_key"] == {"name": "PK", "type": "S"}


class TestClients:
    def test_a_client_is_written_with_its_own_key(self, catalog: DynamoCatalog, fake, clients) -> None:
        catalog.put_client(clients[0])
        payload = fake.last("put_item")
        assert payload["TableName"] == TABLE_NAME
        assert payload["Item"]["PK"] == {"S": "CLIENT#a"}
        assert payload["Item"]["SK"] == {"S": "CLIENT"}
        assert payload["Item"]["name"] == {"S": "Client A - retail"}
        assert payload["Item"]["entityType"] == {"S": "client"}

    def test_a_client_is_read_back(self, catalog: DynamoCatalog, clients) -> None:
        catalog.put_client(clients[0])
        found = catalog.get_client("a")
        assert found.client_id == "a"
        assert found.name == clients[0].name
        assert set(found.technologies) == set(clients[0].technologies)
        assert catalog.get_client("inconnu") is None

    def test_an_unknown_client_costs_no_write(self, catalog: DynamoCatalog, fake) -> None:
        assert catalog.get_client("inconnu") is None
        assert fake.actions() == ["get_item"]

    def test_the_client_index_is_the_scope_index(self, catalog: DynamoCatalog, fake, clients) -> None:
        catalog.put_client(clients[0])
        assert fake.last("put_item")["Item"]["gsi1pk"] == {"S": "client"}


class TestTravaux:
    def test_a_trace_is_written_under_its_perimeter(self, catalog: DynamoCatalog, fake, scope_a) -> None:
        catalog.put_trace(make_trace(scope_a))
        item = fake.last("put_item")["Item"]
        assert item["PK"] == {"S": "SCOPE#client-a"}
        assert item["SK"] == {"S": "TRAVAIL#guichet:2041"}
        assert item["captureState"] == {"S": "stored"}
        assert item["doNotDistill"] == {"BOOL": False}
        assert item["author"] == {"S": "p-7f3a"}
        assert item["scope"]["M"]["clientId"] == {"S": "a"}

    def test_a_trace_is_read_by_its_identifier_alone(self, catalog: DynamoCatalog, fake, scope_a) -> None:
        catalog.put_trace(make_trace(scope_a))
        found = catalog.get_trace("guichet:2041")
        assert found.work_item_id == "guichet:2041"
        assert found.scope == scope_a
        query = fake.last("query")
        assert query["IndexName"] == "by_id"
        assert query["ExpressionAttributeValues"][":pk"] == {"S": "travail#guichet:2041"}

    def test_an_unknown_trace_is_absent(self, catalog: DynamoCatalog) -> None:
        assert catalog.get_trace("inconnu") is None

    def test_the_fingerprint_index_is_read_and_written(self, catalog: DynamoCatalog, fake) -> None:
        assert catalog.trace_seen("abc") is False
        catalog.mark_trace_seen("abc", "guichet:2041")
        assert fake.last("put_item")["Item"]["PK"] == {"S": "FINGERPRINT#abc"}
        assert catalog.trace_seen("abc") is True
        assert catalog.work_item_of("abc") == "guichet:2041"


class TestEpisodes:
    def test_an_episode_is_written_under_its_perimeter(self, catalog: DynamoCatalog, fake, scope_a) -> None:
        catalog.put_episode(make_episode(scope_a))
        item = fake.last("put_item")["Item"]
        assert item["PK"] == {"S": "SCOPE#client-a"}
        assert item["SK"] == {"S": "EPISODE#ep-1"}
        assert item["gsi1pk"] == {"S": "episode#client-a"}
        assert item["gsi2pk"] == {"S": "episode#ep-1"}

    def test_an_episode_is_read_through_the_id_index(self, catalog: DynamoCatalog, fake, scope_a) -> None:
        catalog.put_episode(make_episode(scope_a))
        assert catalog.get_episode("ep-1").episode_id == "ep-1"
        query = fake.last("query")
        assert query["IndexName"] == "by_id"
        assert query["ExpressionAttributeValues"][":pk"] == {"S": "episode#ep-1"}

    def test_episodes_are_listed_through_the_scope_index(self, catalog: DynamoCatalog, fake, scope_a) -> None:
        catalog.put_episode(make_episode(scope_a))
        rows = catalog.list_episodes(scope=scope_a)
        assert [row.episode_id for row in rows] == ["ep-1"]
        query = fake.last("query")
        assert query["IndexName"] == "by_scope"
        assert query["ExpressionAttributeValues"][":pk"] == {"S": "episode#client-a"}

    def test_the_limit_is_sent_to_the_service(self, catalog: DynamoCatalog, fake, scope_a) -> None:
        catalog.put_episode(make_episode(scope_a))
        catalog.list_episodes(scope=scope_a, limit=1)
        assert fake.last("query")["Limit"] == 1


class TestFiches:
    def test_a_fiche_is_keyed_by_its_version(self, catalog: DynamoCatalog, fake, scope_a) -> None:
        catalog.put_fiche(make_fiche(scope_a))
        item = fake.last("put_item")["Item"]
        assert item["PK"] == {"S": "FICHE#f-1"}
        assert item["SK"] == {"S": version_key(1)}
        assert item["version"] == {"N": "1"}
        assert item["level"] == {"S": "validated"}
        assert item["successCount"] == {"N": "0"}

    def test_the_versions_are_zero_padded_so_ten_comes_after_nine(self) -> None:
        assert version_key(9) < version_key(10)
        assert version_key(1) == "V#000001"

    def test_the_latest_version_is_read_through_the_id_index(
        self, catalog: DynamoCatalog, fake, scope_a
    ) -> None:
        fiche = make_fiche(scope_a)
        catalog.put_fiche(fiche)
        catalog.put_fiche(bump(fiche, body="correction"))
        assert catalog.get_fiche("f-1").version == 2
        assert fake.last("query")["IndexName"] == "by_id"

    def test_a_given_version_is_read_by_primary_key(self, catalog: DynamoCatalog, fake, scope_a) -> None:
        fiche = make_fiche(scope_a)
        catalog.put_fiche(fiche)
        catalog.put_fiche(bump(fiche, body="correction"))
        assert catalog.get_fiche("f-1", 1).body == fiche.body
        assert fake.last("get_item")["Key"]["SK"] == {"S": version_key(1)}

    def test_the_history_walks_every_kept_version(self, catalog: DynamoCatalog, scope_a) -> None:
        fiche = make_fiche(scope_a)
        for index in range(3):
            catalog.put_fiche(fiche)
            fiche = bump(fiche, title=f"Correction {index + 1}")
        assert [row.version for row in history(catalog, "f-1")] == [1, 2, 3]

    def test_list_fiches_keeps_the_latest_version_of_each(self, catalog: DynamoCatalog, scope_a) -> None:
        fiche = make_fiche(scope_a)
        catalog.put_fiche(fiche)
        catalog.put_fiche(bump(fiche, body="correction"))
        rows = catalog.list_fiches(scope=scope_a)
        assert len(rows) == 1 and rows[0].version == 2

    def test_the_level_is_filtered_in_code_never_indexed(self, catalog: DynamoCatalog, fake, scope_a) -> None:
        catalog.put_fiche(make_fiche(scope_a, level=Level.VALIDATED))
        assert catalog.list_fiches(scope=scope_a, level=Level.PROPOSED) == ()
        assert "FilterExpression" not in fake.last("query")


class TestSearchAndUsage:
    def test_the_search_uses_the_same_lexical_ranking(self, catalog: DynamoCatalog, scope_a) -> None:
        catalog.put_fiche(make_fiche(scope_a))
        assert [row.fiche_id for row in catalog.search_fiches(scope=scope_a, terms=["connexions"])] == ["f-1"]
        assert catalog.search_fiches(scope=scope_a, terms=["veille"]) == ()

    def test_a_usage_is_written_under_its_identifier(self, catalog: DynamoCatalog, fake, scope_a) -> None:
        catalog.put_usage(Usage("u-1", served_ids=("f-1",), scope=scope_a, at=NOW, problem="500"))
        item = fake.last("put_item")["Item"]
        assert item["PK"] == {"S": "USAGE#u-1"}
        assert item["SK"] == {"S": "USAGE"}
        assert item["servedIds"] == {"L": [{"S": "f-1"}]}
        assert catalog.get_usage("u-1").problem == "500"

    def test_a_feedback_shares_the_partition_of_its_usage(self, catalog: DynamoCatalog, fake) -> None:
        catalog.put_feedback(Feedback("u-1", FeedbackResult.HELPED, at=NOW, fiche_id="f-1"))
        item = fake.last("put_item")["Item"]
        assert item["PK"] == {"S": "USAGE#u-1"}
        assert item["SK"] == {"S": "RETOUR"}

    def test_list_feedback_scans_and_filters_on_the_fiche(self, catalog: DynamoCatalog) -> None:
        catalog.put_feedback(Feedback("u-1", FeedbackResult.HELPED, at=NOW, fiche_id="f-1"))
        catalog.put_feedback(Feedback("u-2", FeedbackResult.WRONG, at=NOW, fiche_id="f-2"))
        assert [row.usage_id for row in catalog.list_feedback("f-1")] == ["u-1"]


class TestCascade:
    def test_a_cascade_deletes_every_row_of_the_perimeter(
        self, catalog: DynamoCatalog, fake, scope_a
    ) -> None:
        catalog.put_trace(make_trace(scope_a))
        catalog.put_episode(make_episode(scope_a))
        catalog.put_fiche(make_fiche(scope_a))
        catalog.put_usage(Usage("u-1", served_ids=("f-1",), scope=scope_a, at=NOW))
        catalog.put_feedback(Feedback("u-1", FeedbackResult.HELPED, at=NOW, fiche_id="f-1"))
        report = catalog.delete_scope(scope_a)
        assert report["travaux"] == 1
        assert report["episodes"] == 1
        assert report["fiches"] == 1
        assert report["usages"] == 1
        assert report["retours"] == 1
        assert fake.items_of("SCOPE#client-a") == []
        assert catalog.get_fiche("f-1") is None

    def test_the_other_perimeter_is_untouched(self, catalog: DynamoCatalog, fake, scope_a, scope_b) -> None:
        catalog.put_episode(make_episode(scope_a, "ep-a"))
        catalog.put_episode(make_episode(scope_b, "ep-b", work_item_id="guichet:9"))
        catalog.delete_scope(scope_a)
        assert len(fake.items_of("SCOPE#client-b")) == 1

    def test_a_fiche_elsewhere_with_a_dead_source_is_reported(self, catalog: DynamoCatalog, scope_a) -> None:
        catalog.put_trace(make_trace(scope_a))
        catalog.put_fiche(make_fiche(COMMON, "f-commun", sources=("guichet:2041",)))
        report = catalog.delete_scope(scope_a)
        assert report["fiches_signalees"] == 1
        assert [row.fiche_id for row in catalog.flagged_fiches()] == ["f-commun"]
        assert catalog.get_fiche("f-commun") is not None


class TestDegradation:
    def test_a_client_error_becomes_backend_unavailable(self, catalog: DynamoCatalog, fake) -> None:
        fake.failure = RuntimeError("service indisponible")
        with pytest.raises(BackendUnavailable) as found:
            catalog.put_client(Client(client_id="a", name="A"))
        assert found.value.code == "backend_unavailable"
        assert found.value.details["action"] == "put_item"

    def test_a_read_failure_degrades_the_same_way(self, catalog: DynamoCatalog, fake) -> None:
        fake.failure = RuntimeError("limite atteinte")
        with pytest.raises(BackendUnavailable):
            catalog.get_client("a")


class TestPagination:
    def test_a_query_follows_the_pages(self, catalog: DynamoCatalog, fake, scope_a) -> None:
        fake.page_size = 1
        for index in range(3):
            catalog.put_episode(make_episode(scope_a, f"ep-{index}", created_at=NOW.replace(hour=index)))
        assert len(catalog.list_episodes(scope=scope_a)) == 3
        assert fake.actions().count("query") == 3


class TestParity:
    """The two stores answer the same questions the same way (same contract)."""

    def build(self, store, scope_a, scope_b) -> None:
        """Load the same small catalogue into any store."""
        store.put_client(Client(client_id="a", name="Client A"))
        store.put_trace(make_trace(scope_a))
        store.put_episode(make_episode(scope_a, "ep-a"))
        store.put_episode(make_episode(scope_b, "ep-b", work_item_id="guichet:9"))
        store.put_fiche(make_fiche(scope_a, "f-a"))
        store.put_fiche(make_fiche(scope_a, "f-veille", title="Veille technologique", sources=()))
        store.put_fiche(make_fiche(COMMON, "f-commun"))
        store.put_usage(Usage("u-1", served_ids=("f-a",), scope=scope_a, at=NOW))
        store.put_feedback(Feedback("u-1", FeedbackResult.HELPED, at=NOW, fiche_id="f-a"))

    def test_the_listings_are_identical(self, catalog: DynamoCatalog, clock, scope_a, scope_b) -> None:
        local = LocalCatalog(clock)
        for store in (local, catalog):
            self.build(store, scope_a, scope_b)
        assert [row.episode_id for row in local.list_episodes(scope=scope_a)] == [
            row.episode_id for row in catalog.list_episodes(scope=scope_a)
        ]
        assert [row.fiche_id for row in local.list_fiches(scope=scope_a)] == [
            row.fiche_id for row in catalog.list_fiches(scope=scope_a)
        ]
        assert [row.client_id for row in local.list_clients()] == [
            row.client_id for row in catalog.list_clients()
        ]

    def test_the_search_returns_the_same_fiches(self, catalog: DynamoCatalog, clock, scope_a) -> None:
        local = LocalCatalog(clock)
        for store in (local, catalog):
            self.build(store, scope_a, Scope(kind=ScopeKind.CLIENT, client_id="b"))
        terms = ["connexions", "pool"]
        assert [row.fiche_id for row in local.search_fiches(scope=scope_a, terms=terms)] == [
            row.fiche_id for row in catalog.search_fiches(scope=scope_a, terms=terms)
        ]

    def test_the_cascade_reports_the_same_counts(
        self, catalog: DynamoCatalog, clock, scope_a, scope_b
    ) -> None:
        local = LocalCatalog(clock)
        for store in (local, catalog):
            self.build(store, scope_a, scope_b)
        assert local.delete_scope(scope_a) == catalog.delete_scope(scope_a)
        assert [row.fiche_id for row in local.flagged_fiches()] == [
            row.fiche_id for row in catalog.flagged_fiches()
        ]
