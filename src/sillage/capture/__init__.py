"""Capture: a finished job becomes a normalised, secret free, unique trace.

Public surface of the module. Everything here is offline and deterministic: no
AWS import, no network, no model call at import time, and no figure produced by
a model (ENF-IA-01).

The pipeline, in the order :mod:`sillage.capture.ingest` applies it:

``normalize`` -> ``redact`` -> guardrails -> ``dedupe`` -> object store ->
Memory events -> catalogue rows -> report.

Sub-modules:

* :mod:`sillage.capture.normalize` - a raw payload becomes a ``Trace`` of dated
  events, one role per element, the author between brackets, an explicit closing
  event (13.1);
* :mod:`sillage.capture.authorship` - who did the work, the share of each, and
  the verdict of a human on the work of an agent (EF-CAP-09, EF-CAP-10);
* :mod:`sillage.capture.logs` - long journals reduced to their useful lines;
* :mod:`sillage.capture.redact` - secrets removed before any storage (EF-CAP-03);
* :mod:`sillage.capture.dedupe` - the deterministic fingerprint (EF-CAP-11);
* :mod:`sillage.capture.sources` - one thin adapter per capture entry point;
* :mod:`sillage.capture.episode` - the catalogue episode of a captured job;
* :mod:`sillage.capture.ingest` - the service and its report;
* :mod:`sillage.capture.sample` - the four reference traces;
* :mod:`sillage.capture.offline_memory` - the ``MemoryBackend`` the service falls
  back on while ``sillage.memory`` is not merged.
"""

from __future__ import annotations

from sillage.capture.authorship import HumanVerdict, VerdictKind
from sillage.capture.dedupe import canonical_json, fingerprint, is_duplicate
from sillage.capture.ingest import IngestReport, IngestService, build_default_service
from sillage.capture.logs import summarise_logs
from sillage.capture.normalize import RawTrace, build_trace, normalize, to_events
from sillage.capture.offline_memory import OfflineMemoryBackend
from sillage.capture.redact import SECRET_PATTERNS, contains_secret, redact, redact_trace
from sillage.capture.sources import trace_from_payload

__all__ = [
    "SECRET_PATTERNS",
    "HumanVerdict",
    "IngestReport",
    "IngestService",
    "OfflineMemoryBackend",
    "RawTrace",
    "VerdictKind",
    "build_default_service",
    "build_trace",
    "canonical_json",
    "contains_secret",
    "fingerprint",
    "is_duplicate",
    "normalize",
    "redact",
    "redact_trace",
    "summarise_logs",
    "to_events",
    "trace_from_payload",
]
