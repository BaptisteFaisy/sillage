"""Client identifiers: everything that names a client, found by code (13.6).

Step 1 of the passage to the common knowledge lists what designates the client
of an episode: the name, the account identifiers, the ARNs, the IP addresses,
the host names, the resource names and the mail addresses. Step 3 refuses the
rewritten version as soon as **one** element of that list survives in it
(EF-GEN-03). The list is produced here, by code, on the text of the origin: no
model decides what is an identifier, and no model can forget one (ENF-IA-01).

Boundary with :mod:`sillage.capture.redact` - there is **no duplication** of
patterns between the two modules, and they look at two different moments:

* ``capture/redact.py`` removes **secrets** on the way *in*: access keys,
  tokens, JWT, PEM blocks, connection strings, ``API_KEY=…``. A secret is
  destroyed, never stored, and its family is replaced by an explicit marker.
* ``guards/identifiers.py`` lists **client identifiers** on the way *out*, when
  a fiche leaves the client perimeter for the shared knowledge. An identifier is
  not destroyed here: it is *found*, so that the generalised version can be
  refused while it still contains one (the original stays inside its perimeter).

Consequence: a secret never reaches this module (it was removed at capture), and
this module never removes anything - it only reports. ``pii.mask`` covers the
third family, the personal data of the employees and of the contacts.

Matching is deliberately **over-detective**: a false positive refuses a
publication that a referent can rewrite, a false negative leaks a client into
the common knowledge of every other client.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from sillage.kernel.types import Client, Trace

#: Shorter strings are never identifiers: they would match ordinary prose.
MIN_IDENTIFIER_LENGTH = 3

#: Trailing punctuation a match may carry when it ends a sentence.
_TRAILING = ".,;:!?)]}>\"'`"

#: Separators a client name may use between its parts.
_DASHES = "-\u2010-\u2015"

#: Words of a client name that designate nothing: a legal form, a generic noun
#: or a place. They are dropped from the "significant words" variants, so that
#: ``Client A - retail`` yields ``client a`` and not the word ``client``.
GENERIC_NAME_WORDS: frozenset[str] = frozenset(
    {
        "client",
        "clients",
        "groupe",
        "group",
        "societe",
        "société",
        "entreprise",
        "company",
        "holding",
        "example",
        "exemple",
        "demo",
        "démo",
        "test",
        "tests",
        "sample",
        "partner",
        "partners",
        "service",
        "services",
        "solution",
        "solutions",
        "international",
        "global",
        "system",
        "systems",
        "technologies",
        "technology",
        "industry",
        "industries",
        "sarl",
        "sas",
        "sasu",
        "eurl",
        "spa",
        "ltd",
        "inc",
        "corp",
        "and",
        "the",
        "pour",
        "avec",
        "dans",
        "les",
        "des",
        "nord",
        "sud",
        "est",
        "ouest",
        "north",
        "south",
        "east",
        "west",
        "paris",
        "lyon",
        "france",
        "europe",
    }
)

#: Last labels that are file names, not domains (``README.md``, ``config.yaml``).
FILE_LABELS: frozenset[str] = frozenset(
    {
        "md",
        "txt",
        "py",
        "js",
        "ts",
        "tsx",
        "json",
        "yml",
        "yaml",
        "toml",
        "ini",
        "cfg",
        "conf",
        "log",
        "csv",
        "sh",
        "bash",
        "zsh",
        "rs",
        "go",
        "rb",
        "php",
        "cs",
        "kt",
        "java",
        "scala",
        "swift",
        "html",
        "css",
        "scss",
        "sql",
        "xml",
        "svg",
        "png",
        "jpg",
        "jpeg",
        "gif",
        "webp",
        "pdf",
        "zip",
        "tar",
        "gz",
        "tgz",
        "bak",
        "tmp",
        "lock",
        "env",
        "iso",
        "ipynb",
        "tf",
        "tfvars",
        "gradle",
        "properties",
        "woff",
        "ttf",
        "eot",
    }
)

#: Words a ``resource: <value>`` pair may capture in ordinary prose.
COMMON_WORDS: frozenset[str] = frozenset(
    {
        "the",
        "and",
        "for",
        "not",
        "with",
        "this",
        "that",
        "from",
        "une",
        "des",
        "les",
        "pour",
        "dans",
        "avec",
        "sur",
        "est",
        "sont",
        "cette",
        "que",
        "qui",
        "pas",
        "plus",
        "mais",
        "comme",
        "nous",
        "vous",
        "ils",
        "elle",
    }
)

#: Resource keys whose value names a resource of the client.
_RESOURCE_KEYS = (
    r"resource|ressource|bucket|cluster|instance|database|db|queue|topic|policy|stack"
    r"|repository|repo|table|function|vpc|subnet|volume|snapshot|secret"
    r"|security[_\- ]?group|load[_\- ]?balancer"
)

_OCTET = r"(?:25[0-5]|2[0-4][0-9]|1[0-9][0-9]|[1-9]?[0-9])"

#: One scan, one pass: the branches are ordered so that an ARN is taken whole
#: before its account id, and a mail address before the host name it contains.
COMBINED_PATTERN = re.compile(
    r"(?P<arn>arn:[^\s,;)\"'<>]+)"
    r"|(?P<email>[A-Za-z0-9._%+\-]+@[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)*\.[A-Za-z]{2,})"
    rf"|(?P<ip>(?:{_OCTET}\.){{3}}{_OCTET})"
    r"|(?P<host>(?:[A-Za-z0-9]{2,}(?:[A-Za-z0-9\-]{0,61}[A-Za-z0-9])?\.)+[A-Za-z]{3,})"
    r"|(?P<account>(?<![0-9A-Za-z])[0-9]{12}(?![0-9A-Za-z]))"
    rf"|(?P<resource>\b(?:{_RESOURCE_KEYS})s?(?:\s*[:=]\s*|\s+)[\"']?"
    r"(?P<resource_value>[A-Za-z0-9][A-Za-z0-9._\-]{1,63}))",
    re.IGNORECASE,
)

#: The families the scan knows, in the order of the pattern (documentation only).
FAMILIES: tuple[str, ...] = ("arn", "email", "ip", "host", "account", "resource")


def extract(text: str, *, client: Client | None = None) -> frozenset[str]:
    """Every identifier of the client that appears in ``text`` (13.6 step 1).

    ``client`` adds the variants of its name: the whole name, the name in lower
    case, the name without its dashes and punctuation, the head of the name
    (``client a``) and each significant word of it.
    """
    found = set(_scan(text))
    if client is not None:
        found |= name_variants(client.name)
    return frozenset(found)


def extract_from_trace(trace: Trace, client: Client | None = None) -> frozenset[str]:
    """The identifiers of a whole finished job: every event and every origin value."""
    found: set[str] = set()
    for event in trace.events:
        found |= extract(event.text, client=client)
    for value in trace.origin.values():
        found |= extract(str(value), client=client)
    if client is not None:
        found |= name_variants(client.name)
    return frozenset(found)


def name_variants(name: str) -> frozenset[str]:
    """The writings of a client name that designate it (13.6 step 1).

    Five writings: the name as it is written, in lower case; the same name with
    its dashes turned into spaces; the same name with every separator removed;
    the head of the name, before its first separator (``client a``); and each
    significant word of it. The five together are what a rewrite may leave
    behind, in any of the ways an engineer writes a client name.
    """
    lowered = (name or "").lower().strip()
    if not lowered:
        return frozenset()
    cleaned = _flatten(lowered)
    variants = {lowered, cleaned, re.sub(r"[^a-z0-9]+", "", cleaned)}
    head = _head_of(lowered)
    if head:
        variants.add(head)
    words = re.findall(r"[a-z0-9]+", cleaned)
    variants |= {word for word in words if _is_significant(word)}
    return frozenset(value for value in variants if len(value) >= MIN_IDENTIFIER_LENGTH)


def contains(text: str, identifiers: Iterable[str]) -> frozenset[str]:
    """The identifiers of ``identifiers`` that appear in ``text``.

    Comparison is case-insensitive and bounded: ``client a`` does not match
    inside ``client abc``, and an ARN does not need to be on its own line.
    """
    haystack = (text or "").lower()
    found: set[str] = set()
    for identifier in identifiers:
        needle = str(identifier).strip().lower()
        if len(needle) < MIN_IDENTIFIER_LENGTH:
            continue
        if _occurs(haystack, needle):
            found.add(str(identifier).strip())
    return frozenset(found)


def _occurs(haystack: str, needle: str) -> bool:
    """One bounded, case-insensitive occurrence search."""
    start = 0
    while True:
        index = haystack.find(needle, start)
        if index < 0:
            return False
        if _bounded(haystack, needle, index):
            return True
        start = index + 1


def _bounded(haystack: str, needle: str, index: int) -> bool:
    """True when the occurrence is not a fragment of a longer word."""
    before_ok = index == 0 or not (haystack[index - 1].isalnum() and needle[0].isalnum())
    end = index + len(needle)
    after_ok = end >= len(haystack) or not (haystack[end].isalnum() and needle[-1].isalnum())
    return before_ok and after_ok


def _flatten(name: str) -> str:
    """The name without its dashes, with single spaces."""
    return re.sub(r"\s+", " ", re.sub(f"[{_DASHES}]+", " ", name)).strip()


def _head_of(lowered: str) -> str:
    """The part of the name before its first separator: ``Client A - retail``."""
    first = re.split(f"[{_DASHES}]", lowered)[0]
    head = _flatten(first)
    if len(head) < MIN_IDENTIFIER_LENGTH:
        return ""
    return head if " " in head or _is_significant(head) else ""


def _is_significant(word: str) -> bool:
    """A word of the name that designates the client, and nothing else."""
    return len(word) >= 5 and word not in GENERIC_NAME_WORDS and not word.isdigit()


def _scan(text: str) -> list[str]:
    """One pass of :data:`COMBINED_PATTERN` over ``text``, with its filters."""
    if not text:
        return []
    values: list[str] = []
    for match in COMBINED_PATTERN.finditer(text):
        values.extend(_values_of(match))
    return values


def _values_of(match: re.Match[str]) -> list[str]:
    """The identifiers one match stands for: none, one, or a mail and its domain."""
    resource = match.group("resource_value")
    if resource:
        if resource.lower() in COMMON_WORDS or not _is_resource_name(resource):
            return []
        return [_clean(resource)]
    email = match.group("email")
    if email:
        # the domain of a mail address designates the client as much as the address
        return [_clean(email), _clean(email.split("@", 1)[1])]
    value = _clean(match.group(0))
    if match.group("host") and _is_file_name(value):
        return []
    return [value] if value else []


def _is_resource_name(value: str) -> bool:
    """A resource name looks like a name, not like a technology or a word."""
    if any(char.isdigit() for char in value):
        return True
    if any(char in "-_." for char in value):
        return True
    return any(char.isupper() for char in value[1:])


def _clean(value: str) -> str:
    """Strip the punctuation a match carries when it ends a sentence."""
    value = value.strip()
    while value and value[-1] in _TRAILING:
        value = value[:-1]
    if len(value) < MIN_IDENTIFIER_LENGTH:
        return ""
    return value


def _is_file_name(value: str) -> bool:
    """``README.md`` is a file, not a host name."""
    last = value.rsplit(".", 1)[-1].lower()
    return last in FILE_LABELS


__all__ = [
    "COMBINED_PATTERN",
    "COMMON_WORDS",
    "FAMILIES",
    "FILE_LABELS",
    "GENERIC_NAME_WORDS",
    "MIN_IDENTIFIER_LENGTH",
    "contains",
    "extract",
    "extract_from_trace",
    "name_variants",
]
