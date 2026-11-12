"""The stack of the demo: it builds offline, fast, and says what it cannot do.

These tests prove the three promises of CONTRACT.md §11:

* ``build()`` works with **zero dependency installed** - neither boto3, nor
  fastapi, nor strands is imported - and in **under two seconds**;
* every port is the implementation it claims to be, and any of them can be
  replaced by a keyword;
* the AWS mode refuses **cleanly** when boto3 is missing, and the offline mode is
  never affected by it.

The last two are marked ``recette``: they are the acceptance scenarios R21 (a
Sillage that is down must not block the caller) and R22 (the installation in a
clean account must be diagnosable).
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from sillage.capture.clock import FrozenClock
from sillage.capture.ingest import IngestService
from sillage.catalog.local import ListJournal, ListNotifier, LocalBlobStore, LocalCatalog
from sillage.demo.ports import LocalRegistry, OfflineModels
from sillage.demo.stack import Stack, build
from sillage.kernel.config import Settings
from sillage.kernel.errors import BackendUnavailable, PolicyDenied
from sillage.memory.local import LocalMemoryBackend
from sillage.policy.engine import PolicyEngine

SRC = Path(__file__).resolve().parent.parent / "src"


class BrokenMemory:
    """A memory that is down: every call answers ``BackendUnavailable``."""

    def ensure_memory(self, *args: object, **kwargs: object) -> str:
        raise BackendUnavailable("mémoire indisponible")

    def create_events(self, *args: object, **kwargs: object) -> str:
        raise BackendUnavailable("mémoire indisponible")

    def retrieve(self, *args: object, **kwargs: object) -> tuple[object, ...]:
        raise BackendUnavailable("mémoire indisponible")

    def list_records(self, *args: object, **kwargs: object) -> tuple[object, ...]:
        raise BackendUnavailable("mémoire indisponible")

    def put_records(self, *args: object, **kwargs: object) -> None:
        raise BackendUnavailable("mémoire indisponible")

    def delete_records(self, *args: object, **kwargs: object) -> int:
        raise BackendUnavailable("mémoire indisponible")

    def reflections(self, *args: object, **kwargs: object) -> tuple[object, ...]:
        raise BackendUnavailable("mémoire indisponible")


def test_the_stack_holds_exactly_the_contract_fields() -> None:
    """The twelve fields of CONTRACT.md §11, in the same order."""
    from dataclasses import fields

    assert [field.name for field in fields(Stack)] == [
        "settings",
        "clock",
        "memory",
        "catalog",
        "blobs",
        "journal",
        "policy",
        "model",
        "guardrails",
        "registry",
        "notifier",
        "ingest",
    ]


def test_build_accepts_the_contract_keywords(settings: Settings) -> None:
    """``seed``, ``settings`` and ``clients`` are the keywords of the contract."""
    stack = build(seed=False, settings=settings, clients=("a",))
    assert [client.client_id for client in stack.clients()] == ["a"]
    assert not tuple(stack.catalog.list_episodes(limit=0))


def test_the_stack_builds_offline_and_fast(settings: Settings) -> None:
    """The stack is built in less than two seconds, with no dependency."""
    started = time.perf_counter()
    stack = build(seed=True, settings=settings)
    elapsed = time.perf_counter() - started
    assert isinstance(stack, Stack)
    assert elapsed < 2.0, f"la construction a pris {elapsed:.2f} s"


def test_the_demo_imports_no_aws_dependency() -> None:
    """Neither boto3, nor fastapi, nor strands is imported by the demo."""
    code = (
        "import sys\n"
        "import sillage.demo.stack as stack\n"
        "stack.build()\n"
        "for name in ('boto3', 'fastapi', 'strands'):\n"
        "    assert name not in sys.modules, name\n"
    )
    environment = {**os.environ, "PYTHONPATH": str(SRC)}
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=environment)
    assert result.returncode == 0, result.stderr


def test_every_port_is_the_expected_implementation(settings: Settings) -> None:
    """The offline stack holds the offline implementation of each port."""
    stack = build(seed=False, settings=settings)
    assert isinstance(stack.memory, LocalMemoryBackend)
    assert isinstance(stack.catalog, LocalCatalog)
    assert isinstance(stack.blobs, LocalBlobStore)
    assert isinstance(stack.journal, ListJournal)
    assert isinstance(stack.notifier, ListNotifier)
    assert isinstance(stack.policy, PolicyEngine)
    assert isinstance(stack.model, OfflineModels)
    assert isinstance(stack.registry, LocalRegistry)
    assert isinstance(stack.ingest, IngestService)
    assert isinstance(stack.clock, FrozenClock)
    assert stack.guardrails is None


def test_the_policies_are_enforced(settings: Settings) -> None:
    """Layer 1 refuses, it does not only log."""
    stack = build(seed=False, settings=settings)
    assert stack.policy.mode == "ENFORCE"
    agent = stack.principal("agent", "b", "autonomous")
    with pytest.raises(PolicyDenied):
        stack.enforce("recall", agent, clientId="a")


def test_two_clients_and_the_internal_perimeter(settings: Settings) -> None:
    """The demo registers two fictional clients and its own perimeter."""
    stack = build(seed=False, settings=settings)
    assert [client.client_id for client in stack.clients()] == ["a", "b"]
    assert [scope.actor_id for scope in stack.perimeters()] == ["client-a", "client-b", "interne"]


def test_any_port_can_be_replaced(settings: Settings) -> None:
    """A keyword replaces a port: that is how a test injects its own."""
    memory = LocalMemoryBackend(settings)
    catalog = LocalCatalog()
    stack = build(seed=False, settings=settings, memory=memory, catalog=catalog)
    assert stack.memory is memory
    assert stack.catalog is catalog
    assert stack.blobs is catalog.blobs
    assert stack.journal is catalog.journal
    assert stack.notifier is catalog.notifier


def test_the_injected_policy_is_the_one_that_decides(settings: Settings) -> None:
    """A caller may bring its own policy engine, and it is the one that answers."""
    policy = PolicyEngine(settings=settings, mode="LOG_ONLY")
    stack = build(seed=False, settings=settings, policy=policy)
    assert stack.policy is policy
    assert stack.policy.mode == "LOG_ONLY"


def test_the_aws_mode_refuses_cleanly_without_boto3(monkeypatch: pytest.MonkeyPatch) -> None:
    """A missing dependency raises ``BackendUnavailable``, never an import error."""
    monkeypatch.setitem(sys.modules, "boto3", None)
    with pytest.raises(BackendUnavailable):
        build(offline=False, seed=False)


def test_the_offline_mode_survives_the_aws_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """The offline mode is never affected by the AWS one (CONTRACT.md rule 7)."""
    monkeypatch.setitem(sys.modules, "boto3", None)
    stack = build(seed=True)
    assert isinstance(stack.memory, LocalMemoryBackend)
    assert tuple(stack.catalog.list_episodes(limit=0))


@pytest.mark.recette
def test_r21_a_capture_continues_when_the_memory_is_down(settings: Settings, trace_ticket: object) -> None:
    """R21 - a Sillage that is down must not block the caller."""
    stack = build(seed=False, settings=settings, memory=BrokenMemory())
    report = stack.ingest.ingest_trace(trace_ticket)
    assert report.episode_id, "l'épisode doit être écrit même si la mémoire est down"
    assert stack.catalog.get_episode(report.episode_id) is not None
    assert stack.catalog.get_trace(trace_ticket.work_item_id) is not None


@pytest.mark.recette
def test_r22_the_installation_can_be_diagnosed(capsys: pytest.CaptureFixture) -> None:
    """R22 - the installation in a clean account must be diagnosable."""
    from sillage.cli import doctor

    assert doctor.main([]) == 0
    printed = capsys.readouterr().out
    assert "câblage hors ligne" in printed
    assert "Mode AWS" in printed
    assert "boto3" in printed
