"""DynamoDB attribute values: the low-level vocabulary the catalogue writes.

An item is a plain ``{attribute: AttributeValue}`` dictionary - the shape the
**low-level** DynamoDB client takes and returns (``{"S": "client-a"}``,
``{"N": "3"}``, ``{"SS": ["ecs", "postgresql"]}``). Nothing here imports boto3, so
the offline stores, the tests and the smoke run exercise exactly the
serialisation the deployed stack uses, and a test can assert on the real
``put_item`` payload.

Two DynamoDB rules shape the writers: a set is **omitted** when empty (the
service refuses an empty ``SS``) and an absent optional value is omitted rather
than written as ``NULL``. On the way back every reader is tolerant: a missing or
unreadable attribute yields the caller's default instead of an exception, so a
row written by an older version of Sillage is read, not rejected.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from typing import Any

from sillage.kernel.types import Scope, ScopeKind

#: One DynamoDB attribute in its low-level shape, for example ``{"S": "client-a"}``.
ATTR = dict[str, Any]

#: Attribute name of the family, present on every item of the catalogue.
ENTITY = "entityType"

#: Epoch used when a required date is missing from a row: never ``None``, never now.
EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


def s(value: str) -> ATTR:
    """A string attribute."""
    return {"S": value}


def n(value: int | float) -> ATTR:
    """A number attribute; DynamoDB wants it as a decimal string."""
    return {"N": str(value)}


def boolean(value: bool) -> ATTR:
    """A boolean attribute."""
    return {"BOOL": bool(value)}


def list_of(values: Iterable[ATTR]) -> ATTR:
    """A list attribute; an empty list is legal in DynamoDB."""
    return {"L": list(values)}


def string_set(values: Iterable[str]) -> ATTR | None:
    """An ``SS`` attribute, sorted for determinism, ``None`` when empty.

    DynamoDB rejects an empty set, so the caller omits the attribute instead.
    """
    cleaned = sorted({value for value in values if value})
    return {"SS": cleaned} if cleaned else None


def map_of(values: Mapping[str, str]) -> ATTR | None:
    """A map attribute of strings, ``None`` when empty."""
    return {"M": {key: s(value) for key, value in values.items()}} if values else None


def instant(value: datetime | None) -> ATTR | None:
    """A datetime as an ISO 8601 string attribute, ``None`` when absent."""
    return s(value.isoformat()) if value is not None else None


def put_attr(item: dict, key: str, attr: ATTR | None) -> dict:
    """Add an optional attribute, skipping ``None``."""
    if attr is not None:
        item[key] = attr
    return item


def as_text(attr: object) -> str:
    """Best effort text of one attribute value, empty string when unreadable."""
    if isinstance(attr, dict):
        if isinstance(attr.get("S"), str):
            return attr["S"]
        if "N" in attr:
            return str(attr["N"])
    return ""


def text(item: Mapping[str, Any], key: str, default: str = "") -> str:
    """Read a string attribute, ``default`` when missing or unreadable."""
    return as_text(item.get(key)) or default


def integer(item: Mapping[str, Any], key: str, default: int = 0) -> int:
    """Read a number attribute as an int, ``default`` when missing."""
    value = item.get(key)
    if not isinstance(value, dict) or "N" not in value:
        return default
    try:
        return int(float(value["N"]))
    except (TypeError, ValueError):
        return default


def real(item: Mapping[str, Any], key: str, default: float = 0.0) -> float:
    """Read a number attribute as a float, ``default`` when missing."""
    value = item.get(key)
    if not isinstance(value, dict) or "N" not in value:
        return default
    try:
        return float(value["N"])
    except (TypeError, ValueError):
        return default


def flag(item: Mapping[str, Any], key: str, default: bool = False) -> bool:
    """Read a boolean attribute, ``default`` when missing."""
    value = item.get(key)
    if isinstance(value, dict) and isinstance(value.get("BOOL"), bool):
        return value["BOOL"]
    return default


def strings(item: Mapping[str, Any], key: str) -> tuple[str, ...]:
    """Read a list or a set attribute, empty tuple when missing.

    Both ``L`` and ``SS`` are accepted so a row keeps reading correctly when a
    field moves from an ordered list to a set, or the reverse.
    """
    value = item.get(key)
    if not isinstance(value, dict):
        return ()
    if isinstance(value.get("L"), list):
        return tuple(found for found in (as_text(entry) for entry in value["L"]) if found)
    if isinstance(value.get("SS"), list):
        return tuple(found for found in value["SS"] if isinstance(found, str) and found)
    return ()


def mapping_of_strings(item: Mapping[str, Any], key: str) -> dict[str, str]:
    """Read a map attribute of strings, empty dict when missing."""
    value = item.get(key)
    if not isinstance(value, dict) or not isinstance(value.get("M"), dict):
        return {}
    return {name: as_text(attr) for name, attr in value["M"].items()}


def moment(item: Mapping[str, Any], key: str) -> datetime | None:
    """Read an ISO 8601 attribute as a datetime, ``None`` when missing."""
    raw = text(item, key)
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        return None


def moment_or_epoch(item: Mapping[str, Any], key: str) -> datetime:
    """Read a required date, the epoch when the row is incomplete."""
    return moment(item, key) or EPOCH


def member(kind: type, item: Mapping[str, Any], key: str, default: object) -> Any:
    """Read an enum member by value, ``default`` when missing or unknown."""
    raw = text(item, key)
    if not raw:
        return default
    try:
        return kind(raw)
    except ValueError:
        return default


# --------------------------------------------------------------------------- #
# perimeter
# --------------------------------------------------------------------------- #
def scope_to_item(scope: Scope) -> ATTR:
    """The perimeter a row belongs to: its kind and, for a client, its id.

    The actor of AgentCore Memory is the perimeter, never the person
    (specification 12.5): that is why no author is ever part of a key here.
    """
    body: dict[str, ATTR] = {"kind": s(scope.kind.value)}
    if scope.client_id:
        body["clientId"] = s(scope.client_id)
    return {"M": body}


def scope_from_item(item: Mapping[str, Any], key: str = "scope") -> Scope:
    """Read the perimeter of a row; an unreadable row falls back to the internal one."""
    value = item.get(key)
    body = value.get("M", {}) if isinstance(value, dict) else {}
    kind = member(ScopeKind, body, "kind", ScopeKind.CLIENT)
    client_id = text(body, "clientId") or None
    if kind is not ScopeKind.CLIENT:
        return Scope(kind=kind)
    if not client_id:  # a client perimeter without an id is not a perimeter
        return Scope(kind=ScopeKind.INTERNAL)
    return Scope(kind=kind, client_id=client_id)
