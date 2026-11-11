"""Ingestion of a finished job, in the order the specification imposes.

EF-CAP-03 is absolute: the secrets go **before any storage**. So the pipeline
never changes, and every step is visible in the report:

1. redact the trace (``capture.redact``);
2. screen it with Bedrock Guardrails when one is wired (chapter 10.3);
3. look for the fingerprint of an already captured job (EF-CAP-11, R3);
4. copy the normalised trace in the versioned object store (EF-CAP-13);
5. replay it as dated events, with ``ExtractionMode.SKIP`` when the author asked
   not to keep the work (EF-CAP-12, R4);
6. write the ``Travail`` row, and the ``Episode`` row of chapter 14;
7. return the report the caller - Guichet, the API, the smoke - reads.

Two rules shape the error handling. A trace blocked by a guardrail is refused
before a single byte is stored. A memory or object store that is down degrades:
the capture keeps what it can and says so, because Sillage must never be the
reason an incident stays open (ENF-DISP-02, R21).

No figure here comes from a model (ENF-IA-01), and no author appears anywhere
except as a pseudonym (ENF-RH-01).
"""

from __future__ import annotations

from collections.abc import Mapping
from contextlib import suppress
from dataclasses import dataclass, replace
from typing import Any

from sillage.capture.clock import SystemClock
from sillage.capture.dedupe import canonical_json, is_duplicate, raw_key, remember
from sillage.capture.episode import episode_from_trace
from sillage.capture.offline_memory import OfflineMemoryBackend
from sillage.capture.redact import redact_trace
from sillage.capture.sources import trace_from_payload
from sillage.catalog.local import LocalCatalog
from sillage.catalog.model import CAPTURE_EVENTS, CAPTURE_SKIPPED, CAPTURE_STORED
from sillage.kernel.config import DEFAULT, Settings
from sillage.kernel.errors import BackendUnavailable, NotFound, PoisonedContent
from sillage.kernel.ids import episode_id, pseudonym
from sillage.kernel.protocols import (
    BlobStore,
    CatalogStore,
    Clock,
    GuardrailFilter,
    MemoryBackend,
    ModelBackend,
)
from sillage.kernel.types import ExtractionMode, Level, Source, Trace, TraceEvent


@dataclass(frozen=True)
class IngestReport:
    """What one ingestion did, so a caller never has to guess."""

    trace: Trace
    events_created: bool
    redacted_secrets: int
    duplicate: bool
    do_not_distill: bool
    raw_key: str = ""
    episode_id: str = ""


class IngestService:
    """The capture pipeline, wired on the four ports it needs."""

    def __init__(
        self,
        *,
        settings: Settings,
        memory: MemoryBackend,
        catalog: CatalogStore,
        blobs: BlobStore,
        model: ModelBackend | None = None,
        guardrails: GuardrailFilter | None = None,
        clock: Clock | None = None,
    ) -> None:
        self.settings = settings
        self.memory = memory
        self.catalog = catalog
        self.blobs = blobs
        self.model = model
        self.guardrails = guardrails
        self.clock = clock or SystemClock()

    # -- the pipeline ------------------------------------------------------ #
    def ingest_trace(self, trace: Trace) -> IngestReport:
        """Ingest one normalised trace; the same trace twice gives one episode."""
        clean, removed = redact_trace(trace)
        clean = self.screen(clean)
        clean = self.honour_do_not_keep(clean)
        key = raw_key(clean)
        ident = episode_id(clean.scope.actor_id, clean.work_item_id)
        if is_duplicate(self.catalog, clean):
            return IngestReport(
                trace=clean,
                events_created=False,
                redacted_secrets=removed,
                duplicate=True,
                do_not_distill=clean.do_not_distill,
                raw_key=key,
                episode_id=ident,
            )
        return self._store(clean, removed, key, ident)

    def ingest_raw(self, source: Source, payload: Mapping[str, Any]) -> IngestReport:
        """Normalise the payload of one source, then ingest it."""
        return self.ingest_trace(trace_from_payload(source, payload, self.model))

    def mark_do_not_keep(self, work_item_id: str, by: str) -> None:
        """Keep a job out of the distillation, on its author's word (EF-CAP-12).

        The trace stays stored - it is the audit trail - but it is flagged, and
        an episode already written is withdrawn so it is served to nobody any
        more. The person who asked is a pseudonym, like every author (ENF-RH-01).
        """
        trace = self.catalog.get_trace(work_item_id)
        if trace is None:
            raise NotFound(f"travail inconnu : {work_item_id}", work_item_id=work_item_id)
        flagged = replace(
            trace,
            do_not_distill=True,
            origin={**trace.origin, "doNotKeepBy": pseudonym(by), "captureState": CAPTURE_SKIPPED},
        )
        self.catalog.put_trace(flagged)
        # the flagged capture is the one a new reception must recognise (R3 + R4)
        remember(self.catalog, flagged)
        self._withdraw_episode(trace)

    # -- steps ------------------------------------------------------------- #
    def honour_do_not_keep(self, trace: Trace) -> Trace:
        """A job its author asked not to keep stays out of the distillation.

        Guichet can send the same ticket again after the author clicked "a ne pas
        retenir": the decision belongs to the author (17.1), not to the payload of
        the last reception.
        """
        if trace.do_not_distill:
            return trace
        stored = self.catalog.get_trace(trace.work_item_id)
        if stored is None or not stored.do_not_distill:
            return trace
        return replace(trace, do_not_distill=True)

    def screen(self, trace: Trace) -> Trace:
        """Apply Bedrock Guardrails to every element, before anything is stored."""
        if self.guardrails is None:
            return trace
        screened = [self._screened_event(event) for event in trace.events]
        return replace(trace, events=tuple(screened))

    def _screened_event(self, event: TraceEvent) -> TraceEvent:
        """One element under the guardrail: blocked, masked, or left alone."""
        assert self.guardrails is not None  # guarded by ``screen``
        verdict = self.guardrails.scan(event.text)
        if verdict.blocked:
            raise PoisonedContent(
                "contenu refusé par le garde-fou à la capture", reasons=tuple(verdict.reasons)
            )
        if verdict.action == "masked" and verdict.masked_text:
            return replace(event, text=verdict.masked_text)
        return event

    def _store(self, clean: Trace, removed: int, key: str, ident: str) -> IngestReport:
        """Everything that happens once the capture is known to be new."""
        stamped = replace(clean, received_at=self.clock.now())
        self._keep_raw(key, stamped)
        record = self._replay(stamped)
        state = self._capture_state(stamped, record)
        final = replace(stamped, origin={**stamped.origin, "rawKey": key, "captureState": state})
        self.catalog.put_trace(final)
        if not final.do_not_distill:
            self.catalog.put_episode(
                episode_from_trace(final, memory_record_id=record, created_at=self.clock.now())
            )
        remember(self.catalog, clean)
        return IngestReport(
            trace=final,
            events_created=bool(record),
            redacted_secrets=removed,
            duplicate=False,
            do_not_distill=final.do_not_distill,
            raw_key=key,
            episode_id=ident,
        )

    def _keep_raw(self, key: str, trace: Trace) -> None:
        """Copy the redacted trace in the versioned store (EF-CAP-13)."""
        if not self.settings.keep_raw_traces:
            return
        with suppress(BackendUnavailable):
            # the object store is down: the capture continues without its copy
            self.blobs.put(key, canonical_json(trace))

    def _replay(self, trace: Trace) -> str:
        """Replay the trace as dated events, or nothing when Memory is down."""
        mode = ExtractionMode.SKIP if trace.do_not_distill else ExtractionMode.DEFAULT
        try:
            return self.memory.create_events(
                actor_id=trace.scope.actor_id,
                session_id=trace.session_id,
                trace=trace,
                extraction_mode=mode,
            )
        except BackendUnavailable:
            return ""

    def _capture_state(self, trace: Trace, record: str) -> str:
        """The capture state of the ``Travail`` row (chapter 14)."""
        if trace.do_not_distill:
            return CAPTURE_SKIPPED
        return CAPTURE_EVENTS if record else CAPTURE_STORED

    def _withdraw_episode(self, trace: Trace) -> None:
        """Withdraw the episode of a job its author asked not to keep."""
        ident = episode_id(trace.scope.actor_id, trace.work_item_id)
        episode = self.catalog.get_episode(ident)
        if episode is not None and episode.level is not Level.WITHDRAWN:
            self.catalog.put_episode(replace(episode, level=Level.WITHDRAWN))


# -- offline wiring --------------------------------------------------------- #
def default_memory(settings: Settings, clock: Clock | None = None) -> MemoryBackend:
    """The memory backend of a default service: ``sillage.memory`` if it is
    merged, the capture's own offline recorder otherwise.

    The capture must run with zero dependency and before every other module
    exists, so the fallback is part of the contract, not a convenience.
    """
    try:
        from sillage.memory.local import LocalMemoryBackend
    except Exception:  # not merged yet, or not importable: fall back
        return OfflineMemoryBackend(settings, clock)
    for attempt in (lambda: LocalMemoryBackend(settings, clock), lambda: LocalMemoryBackend(settings)):
        try:
            return attempt()
        except Exception:
            # a backend that cannot be built - wrong signature, missing
            # configuration, absent dependency - must not stop the capture:
            # Sillage degrades, it does not fail (ENF-DISP-02).
            continue
    return OfflineMemoryBackend(settings, clock)


def build_default_service(**overrides: Any) -> IngestService:
    """An ingestion service that is 100 % offline: no AWS, no network, no model.

    Every part can be replaced by a keyword, which is how the demo stack, the
    tests and the smoke share one wiring while injecting their own fakes.
    """
    settings = overrides.pop("settings", None) or DEFAULT
    clock = overrides.pop("clock", None) or SystemClock()
    catalog = overrides.pop("catalog", None) or LocalCatalog(clock)
    blobs = overrides.pop("blobs", None) or getattr(catalog, "blobs", catalog)
    memory = overrides.pop("memory", None) or default_memory(settings, clock)
    return IngestService(
        settings=settings,
        memory=memory,
        catalog=catalog,
        blobs=blobs,
        model=overrides.pop("model", None),
        guardrails=overrides.pop("guardrails", None),
        clock=clock,
    )


__all__ = ["IngestReport", "IngestService", "build_default_service", "default_memory"]
