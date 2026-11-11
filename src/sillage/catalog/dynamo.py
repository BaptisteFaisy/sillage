"""The deployed catalogue: the same ``CatalogStore`` on Amazon DynamoDB.

**Modelling choice: one table, composite keys** - the layout, the two global
secondary indexes (``by_scope``, ``by_id``) and the reasoning are in
:mod:`sillage.catalog.dynamo_tables`. In short: the catalogue is small, written by
one service, and almost every read is "everything of one perimeter"; one table
keeps a single billing and backup story, lets a cascade delete walk a partition,
and stays close enough to chapter 14 to be audited by a referent.

The items are the ones :mod:`sillage.catalog.model` serialises, in the low-level
``{"S": ...}`` form, so what the offline tests check is what reaches the service.

boto3 is imported **inside the constructor**, never at module import (CONTRACT.md
rule 3). Every call goes through :meth:`_call`, which turns any client error into
``BackendUnavailable`` so the callers can degrade instead of failing
(ENF-DISP-02).
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from sillage.catalog.dynamo_rows import DynamoRows
from sillage.catalog.dynamo_tables import (
    TABLE_NAME,
    client_keys,
    fingerprint_keys,
    row_index,
    sort_stamp,
    table_definitions,
    travail_keys,
)
from sillage.catalog.model import (
    client_from_item,
    client_to_item,
    fingerprint_to_item,
    travail_from_item,
    travail_to_item,
    work_item_of_fingerprint,
)
from sillage.kernel.config import DEFAULT, Settings
from sillage.kernel.errors import BackendUnavailable
from sillage.kernel.protocols import Clock
from sillage.kernel.types import Client, Fiche, Trace

#: Low-level attribute values of a plain string mapping.
StrAttrs = dict[str, dict[str, str]]


def as_keys(values: dict[str, str]) -> StrAttrs:
    """Turn plain key strings into the low-level key shape of DynamoDB."""
    return {name: {"S": value} for name, value in values.items()}


class DynamoCatalog(DynamoRows):
    """``CatalogStore`` on DynamoDB: one table, two indexes, lazy boto3."""

    def __init__(
        self,
        settings: Settings = DEFAULT,
        *,
        clock: Clock | None = None,
        table_name: str = TABLE_NAME,
        client: Any = None,
        endpoint_url: str = "",
    ) -> None:
        self.settings = settings
        self.clock = clock
        self.table = table_name
        #: Fiches kept at the last cascade whose sources no longer exist (EF-ENT-03).
        self.flagged: tuple[Fiche, ...] = ()
        if client is not None:
            self._ddb = client
            return
        import boto3

        local = {"endpoint_url": endpoint_url} if endpoint_url else {}
        self._ddb = boto3.client("dynamodb", region_name=settings.region, **local)

    # -- plumbing ---------------------------------------------------------- #
    def _call(self, action: str, **kwargs: Any) -> dict:
        """One DynamoDB call; any client error becomes ``BackendUnavailable``."""
        try:
            return getattr(self._ddb, action)(**kwargs)
        except Exception as exc:
            raise BackendUnavailable(
                f"catalogue DynamoDB : {action} a echoue", action=action, cause=str(exc)
            ) from exc

    def _put(self, keys: dict[str, str], item: dict, index: dict[str, str]) -> None:
        """Write one row, primary key plus the two index entries of its family."""
        full = {**item, **as_keys(index), **as_keys(keys)}
        self._call("put_item", TableName=self.table, Item=full)

    def _get(self, keys: dict[str, str]) -> dict | None:
        """Read one row by its primary key, ``None`` when absent."""
        found = self._call("get_item", TableName=self.table, Key=as_keys(keys), ConsistentRead=True)
        return found.get("Item")

    def _delete(self, keys: dict[str, str]) -> None:
        """Delete one row by its primary key."""
        self._call("delete_item", TableName=self.table, Key=as_keys(keys))

    def _query(
        self,
        *,
        pk_name: str,
        pk: str,
        sk_name: str,
        index: str,
        descending: bool = True,
        limit: int = 0,
    ) -> tuple[dict, ...]:
        """Every row of one index partition, following the pages."""
        params: dict[str, Any] = {
            "TableName": self.table,
            "IndexName": index,
            "KeyConditionExpression": f"#{pk_name} = :pk",
            "ExpressionAttributeNames": {f"#{pk_name}": pk_name},
            "ExpressionAttributeValues": {":pk": {"S": pk}},
            "ScanIndexForward": not descending,
        }
        if limit:
            params["Limit"] = limit
        return self._pages("query", params, limit)

    def _scan(self, entity: str) -> tuple[dict, ...]:
        """Every row of one family, across perimeters.

        A scan is the administrative path - the dashboard, the guardian pass, a
        cascade - never a tool read: a tool always knows its perimeter and reads
        the ``by_scope`` index instead.
        """
        params: dict[str, Any] = {
            "TableName": self.table,
            "FilterExpression": "#e = :e",
            "ExpressionAttributeNames": {"#e": "entityType"},
            "ExpressionAttributeValues": {":e": {"S": entity}},
        }
        return self._pages("scan", params, 0)

    def _pages(self, action: str, params: dict[str, Any], limit: int) -> tuple[dict, ...]:
        """Read every page of a query or a scan, stopping at ``limit`` rows."""
        rows: list[dict] = []
        while True:
            page = self._call(action, **params)
            rows.extend(page.get("Items", []))
            last = page.get("LastEvaluatedKey")
            if not last or (limit and len(rows) >= limit):
                return tuple(rows)
            params = {**params, "ExclusiveStartKey": last}

    @staticmethod
    def row_key(item: dict) -> dict[str, str]:
        """Primary key of an item read from the table, ready for a delete."""
        return {"PK": item["PK"]["S"], "SK": item["SK"]["S"]}

    # -- clients ----------------------------------------------------------- #
    def put_client(self, client: Client) -> None:
        """Write a tenant."""
        index = row_index("client", ident=client.client_id, label="", order=client.client_id)
        self._put(client_keys(client), client_to_item(client), index)

    def get_client(self, client_id: str) -> Client | None:
        """One tenant, ``None`` when unknown."""
        found = self._get({"PK": f"CLIENT#{client_id}", "SK": "CLIENT"})
        return client_from_item(found) if found else None

    def list_clients(self) -> Sequence[Client]:
        """Every tenant, sorted by identifier."""
        rows = [client_from_item(item) for item in self._scan("client")]
        return tuple(sorted(rows, key=lambda client: client.client_id))

    # -- travaux ----------------------------------------------------------- #
    def put_trace(self, trace: Trace) -> None:
        """Write the ``Travail`` row of a finished job.

        The events stay in AgentCore Memory and the raw copy in the object store
        (chapter 12.4); this row is the exact state of the capture: perimeter,
        source, author kind, pseudonym, dates, capture state and the "do not
        keep" flag (EF-CAP-12).
        """
        index = row_index(
            "travail",
            ident=trace.work_item_id,
            label=trace.scope.actor_id,
            order=sort_stamp(trace.closed_at, trace.work_item_id),
        )
        self._put(travail_keys(trace), travail_to_item(trace), index)

    def get_trace(self, work_item_id: str) -> Trace | None:
        """One finished job, read from its identifier alone."""
        items = self._by_id("travail", work_item_id, limit=1)
        return travail_from_item(items[0]) if items else None

    def trace_seen(self, fingerprint: str) -> bool:
        """True when this exact capture was already received (R3)."""
        return self._get(fingerprint_keys(fingerprint)) is not None

    def mark_trace_seen(self, fingerprint: str, work_item_id: str) -> None:
        """Remember a fingerprint and the work item it belongs to."""
        keys = fingerprint_keys(fingerprint)
        index = row_index("fingerprint", ident=fingerprint, label="", order=fingerprint)
        self._put(keys, fingerprint_to_item(fingerprint, work_item_id), index)

    def work_item_of(self, fingerprint: str) -> str:
        """The work item a remembered fingerprint points at, empty when unknown."""
        found = self._get(fingerprint_keys(fingerprint))
        return work_item_of_fingerprint(found) if found else ""


def tables() -> list[dict[str, object]]:
    """The table definitions of the catalogue, for ``infra/cdk``."""
    return table_definitions()


__all__ = ["DynamoCatalog", "as_keys", "tables"]
