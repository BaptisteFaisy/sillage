"""The two wirings of the stack: offline by default, AWS on demand.

One module holds both so the rule is written once: :func:`chosen` decides whether
a port comes from the caller or from the wiring, and a caller may inject ``None``
(the guardrails port is optional by contract). Everything else is a list of
constructors.

CONTRACT.md rule 3 shapes the AWS half: **boto3 is never imported at module
level**. :func:`aws_parts` reaches each real backend through
:func:`importlib.import_module` inside a function, and each of those classes
imports boto3 inside its own constructor. Importing this module - and therefore
:mod:`sillage.demo.stack`, the smoke and the whole test suite - works with zero
dependency installed.

One rule shapes the failures: a missing dependency, a missing credential or a
service that refuses the call all raise :class:`BackendUnavailable`, and nothing
else (ENF-DISP-02). The offline mode is never touched by it.

Ports no AWS implementation exists for yet keep their offline twin and are listed
in :data:`PROVISIONAL`, so ``sillage doctor`` can say what is real and what is not.
"""

from __future__ import annotations

from collections.abc import Callable
from importlib import import_module
from typing import Any

from sillage.capture.clock import FrozenClock, SystemClock
from sillage.catalog.local import ListJournal, ListNotifier, LocalBlobStore, LocalCatalog
from sillage.demo.ports import LocalRegistry, OfflineModels
from sillage.kernel.config import Settings
from sillage.kernel.errors import BackendUnavailable
from sillage.kernel.protocols import Clock, GuardrailFilter, MemoryBackend, ModelBackend, Registry
from sillage.memory.local import LocalMemoryBackend

#: Ports still served by their offline implementation in AWS mode, and why.
PROVISIONAL: dict[str, str] = {
    "blobs": "aucun BlobStore S3 mergé : la copie des traces reste en mémoire",
    "journal": "aucun Journal DynamoDB mergé : le journal des décisions reste en mémoire",
    "notifier": "aucun Notifier SNS mergé : les messages aux référents restent en mémoire",
    "registry": "aucun Registry AWS mergé : AWS Agent Registry reste en mémoire",
    "model": "lot agents non mergé : le modèle reste déterministe et hors ligne",
}

#: The offline ports whose AWS twin is imported on demand: port -> (module, class).
AWS_BACKENDS: dict[str, tuple[str, str]] = {
    "memory": ("sillage.memory.agentcore", "AgentCoreMemoryBackend"),
    "catalog": ("sillage.catalog.dynamo", "DynamoCatalog"),
    "guardrails": ("sillage.guards.pii", "BedrockGuardrails"),
    "model": ("sillage.agents.models", "BedrockModels"),
}


def chosen(overrides: dict[str, Any], name: str, factory: Callable[[], Any]) -> Any:
    """The port the caller injected, or the one the wiring builds.

    A key **present** in ``overrides`` wins even when its value is ``None``: the
    guardrails port is optional, and a test that wants no filter must be able to
    say so instead of getting the default one.
    """
    return overrides[name] if name in overrides else factory()


def composed(owner: Any, name: str, factory: Callable[[], Any]) -> Any:
    """A port another object already holds, or a fresh one when it does not.

    ``LocalCatalog`` composes its blob store, its journal and its notifier: the
    stack hands out those very objects, so a test that counts notifications reads
    the same notifier the catalogue writes to.
    """
    found = getattr(owner, name, None)
    return found if found is not None else factory()


# -- offline ---------------------------------------------------------------- #
def offline_parts(*, settings: Settings, overrides: dict[str, Any]) -> dict[str, Any]:
    """The offline ports: the standard library, no network, no model, no boto3."""
    clock: Clock = chosen(overrides, "clock", FrozenClock)
    catalog = chosen(overrides, "catalog", lambda: LocalCatalog(clock))
    return {
        "settings": settings,
        "clock": clock,
        "catalog": catalog,
        "blobs": chosen(overrides, "blobs", lambda: composed(catalog, "blobs", LocalBlobStore)),
        "journal": chosen(overrides, "journal", lambda: composed(catalog, "journal", ListJournal)),
        "notifier": chosen(overrides, "notifier", lambda: composed(catalog, "notifier", ListNotifier)),
        "memory": chosen(overrides, "memory", lambda: LocalMemoryBackend(settings, clock)),
        "model": chosen(overrides, "model", OfflineModels),
        "guardrails": chosen(overrides, "guardrails", lambda: None),
        "registry": chosen(overrides, "registry", lambda: LocalRegistry(clock)),
    }


# -- AWS -------------------------------------------------------------------- #
def boto3_available() -> bool:
    """True when boto3 can be imported: the only question the AWS mode asks."""
    try:
        import_module("boto3")
    except ImportError:
        return False
    return True


def require_boto3() -> None:
    """Refuse the AWS mode cleanly when the dependency is not installed."""
    if not boto3_available():
        raise BackendUnavailable(
            "boto3 n'est pas installé : le mode AWS est indisponible (pip install 'sillage[aws]')",
            dependency="boto3",
        )


def unavailable(what: str, error: BaseException) -> BackendUnavailable:
    """One shape for every AWS failure: what is down, and what it said."""
    return BackendUnavailable(f"{what} indisponible : {error}", cause=type(error).__name__)


def backend_class(port: str) -> Any:
    """The class of one AWS port, imported on demand."""
    module, name = AWS_BACKENDS[port]
    try:
        return getattr(import_module(module), name)
    except (ImportError, AttributeError) as error:
        raise unavailable(f"{module}.{name}", error) from error


def built(label: str, factory: Any, *args: Any, **kwargs: Any) -> Any:
    """Call one constructor; any failure reads as a backend that is down."""
    try:
        return factory(*args, **kwargs)
    except BackendUnavailable:
        raise
    except Exception as error:  # boto3 errors, absent credentials, refused call
        raise unavailable(label, error) from error


def aws_memory(settings: Settings) -> MemoryBackend:
    """AgentCore Memory over boto3 (annexes A, B and C)."""
    require_boto3()
    return built("AgentCore Memory", backend_class("memory"), settings)


def aws_catalog(settings: Settings, clock: Clock) -> Any:
    """The DynamoDB catalogue."""
    require_boto3()
    return built("le catalogue DynamoDB", backend_class("catalog"), settings, clock=clock)


def aws_guardrails(settings: Settings) -> GuardrailFilter | None:
    """Bedrock Guardrails, ``None`` when the settings ask for no filter."""
    if not settings.use_guardrails:
        return None
    return built("Bedrock Guardrails", backend_class("guardrails"), settings)


def aws_model(settings: Settings) -> ModelBackend:
    """The Bedrock models of the ``agents`` lot, or the offline model until then."""
    try:
        factory = backend_class("model")
    except BackendUnavailable:  # the agents lot is not merged yet
        return OfflineModels()
    return built("Amazon Bedrock", factory, settings)


def aws_registry(clock: Clock) -> Registry:
    """AWS Agent Registry has no implementation yet: the offline one stands in."""
    return LocalRegistry(clock)


def aws_parts(*, settings: Settings, overrides: dict[str, Any]) -> dict[str, Any]:
    """The AWS ports, ``overrides`` winning over each of them.

    The clock is the machine clock: the freshness of a deployed fiche is a real
    figure, not a frozen one. boto3 is imported here, and only here.
    """
    clock: Clock = chosen(overrides, "clock", SystemClock)
    catalog = chosen(overrides, "catalog", lambda: aws_catalog(settings, clock))
    return {
        "settings": settings,
        "clock": clock,
        "catalog": catalog,
        "memory": chosen(overrides, "memory", lambda: aws_memory(settings)),
        "blobs": chosen(overrides, "blobs", LocalBlobStore),
        "journal": chosen(overrides, "journal", ListJournal),
        "notifier": chosen(overrides, "notifier", ListNotifier),
        "model": chosen(overrides, "model", lambda: aws_model(settings)),
        "guardrails": chosen(overrides, "guardrails", lambda: aws_guardrails(settings)),
        "registry": chosen(overrides, "registry", lambda: aws_registry(clock)),
    }


__all__ = [
    "AWS_BACKENDS",
    "PROVISIONAL",
    "aws_catalog",
    "aws_guardrails",
    "aws_memory",
    "aws_model",
    "aws_parts",
    "aws_registry",
    "backend_class",
    "boto3_available",
    "built",
    "chosen",
    "composed",
    "offline_parts",
    "require_boto3",
    "unavailable",
]
