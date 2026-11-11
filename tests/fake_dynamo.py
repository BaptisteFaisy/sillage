"""A fake low-level DynamoDB client: no network, no boto3, real item shapes.

The fake keeps items in a dictionary keyed by ``(PK, SK)`` and answers the four
calls ``DynamoCatalog`` makes: ``put_item``, ``get_item``, ``delete_item``,
``query`` and ``scan``. It applies the small expression subset the catalogue
writes - an equality on the partition key of an index, an optional order through
``ScanIndexForward`` and ``Limit``, and an ``entityType = :e`` filter for the
scans - and records every payload so a test can assert on exactly what would be
sent to the service.

This is a **shape** fake, not an emulator: it exists to prove the keys, the index
names and the item attributes are right, which is what a real table would reject
or silently mis-answer.
"""

from __future__ import annotations

from typing import Any


class FakeDynamo:
    """Minimal low-level DynamoDB client, in memory, call-recording."""

    def __init__(self) -> None:
        self.items: dict[tuple[str, str], dict] = {}
        self.calls: list[tuple[str, dict]] = []
        #: Set to an exception instance to make every call fail.
        self.failure: Exception | None = None
        #: Rows per page, so pagination is exercised; 0 means "everything".
        self.page_size = 0

    # -- inspection -------------------------------------------------------- #
    def actions(self) -> list[str]:
        """The actions called so far, in order."""
        return [action for action, _ in self.calls]

    def last(self, action: str) -> dict:
        """The last payload sent for an action."""
        for name, payload in reversed(self.calls):
            if name == action:
                return payload
        raise AssertionError(f"aucun appel {action} enregistre")

    def items_of(self, prefix: str) -> list[dict]:
        """Stored items whose partition key starts with ``prefix``, ordered."""
        return [item for (pk, _), item in sorted(self.items.items()) if pk.startswith(prefix)]

    def _record(self, action: str, payload: dict) -> None:
        """Record a call, or raise the configured failure."""
        self.calls.append((action, payload))
        if self.failure is not None:
            raise self.failure

    @staticmethod
    def _value(attr: dict) -> str:
        """Value of a low-level string attribute."""
        return attr.get("S", "")

    # -- the calls the catalogue makes ------------------------------------- #
    def put_item(self, **kwargs: Any) -> dict:
        """Store an item under its primary key."""
        self._record("put_item", kwargs)
        item = kwargs["Item"]
        self.items[(self._value(item["PK"]), self._value(item["SK"]))] = item
        return {"ResponseMetadata": {"HTTPStatusCode": 200}}

    def get_item(self, **kwargs: Any) -> dict:
        """Read one item by its primary key."""
        self._record("get_item", kwargs)
        key = kwargs["Key"]
        found = self.items.get((self._value(key["PK"]), self._value(key["SK"])))
        return {"Item": found} if found else {}

    def delete_item(self, **kwargs: Any) -> dict:
        """Delete one item by its primary key."""
        self._record("delete_item", kwargs)
        key = kwargs["Key"]
        self.items.pop((self._value(key["PK"]), self._value(key["SK"])), None)
        return {"ResponseMetadata": {"HTTPStatusCode": 200}}

    def query(self, **kwargs: Any) -> dict:
        """Answer a query on one index: one partition, ordered by its sort key."""
        self._record("query", kwargs)
        names = kwargs["ExpressionAttributeNames"]
        wanted = self._value(kwargs["ExpressionAttributeValues"][":pk"])
        pk_name = names[_placeholder(kwargs["KeyConditionExpression"])]
        sk_name = "gsi1sk" if pk_name == "gsi1pk" else "gsi2sk"
        rows = [item for (_, _), item in sorted(self.items.items()) if self._value(item[pk_name]) == wanted]
        rows.sort(
            key=lambda item: self._value(item[sk_name]), reverse=not kwargs.get("ScanIndexForward", True)
        )
        return self._page(rows, kwargs)

    def scan(self, **kwargs: Any) -> dict:
        """Answer a scan filtered on the entity family."""
        self._record("scan", kwargs)
        wanted = self._value(kwargs["ExpressionAttributeValues"][":e"])
        rows = [
            item
            for (_, _), item in sorted(self.items.items())
            if self._value(item.get("entityType", {})) == wanted
        ]
        return self._page(rows, kwargs)

    # -- internals --------------------------------------------------------- #
    def _page(self, rows: list[dict], kwargs: dict) -> dict:
        """One page of results, with ``LastEvaluatedKey`` when more remain."""
        size = self.page_size or len(rows) or 1
        start = kwargs.get("ExclusiveStartKey")
        index = 0
        if start:
            keys = [(self._value(row["PK"]), self._value(row["SK"])) for row in rows]
            here = (self._value(start["PK"]), self._value(start["SK"]))
            index = keys.index(here) + 1 if here in keys else 0
        page = rows[index : index + size]
        answer: dict[str, Any] = {"Items": page, "Count": len(page)}
        if page and index + size < len(rows):
            answer["LastEvaluatedKey"] = {"PK": page[-1]["PK"], "SK": page[-1]["SK"]}
        return answer


def _placeholder(expression: str) -> str:
    """The placeholder used on the left of a key condition, as written: ``#gsi1pk``."""
    return expression.split(" = ")[0].strip()
