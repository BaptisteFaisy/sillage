"""The passage to the common knowledge: the six steps of chapter 13.6.

The tool does not decide anything here: it prepares the six steps and lets
:func:`sillage.guards.generalize.approve_common` run them, in the order the
specification gives, because that module already holds the rules:

1. the contract of the client (EF-GEN-06) - a client marked "no sharing" never
   feeds the common knowledge;
2. the sources of the fiche (EF-VAL-02) - a fiche whose statements cite no
   episode is not publishable;
3. the identifiers (EF-GEN-03) - one surviving identifier refuses the version;
4. the sensitive data filter of Bedrock Guardrails (EF-GEN-04);
5. the human approval (EF-GEN-05) - only a referent gets this far (rule D.4);
6. the publication (EF-GEN-07) - the approved text is written **verbatim**, and
   the lesson it becomes carries no backlink to the episode it came from.

The identifiers of step 3 are computed from the **origin traces** of the fiche
and from the contract of its client: the code decides what designates the
client, and a referent cannot approve a version that still names it (R13).
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from sillage.guards.generalize import approve_common
from sillage.guards.identifiers import extract_from_trace, name_variants
from sillage.kernel.errors import NotFound, PolicyDenied, SillageError
from sillage.kernel.protocols import Principal
from sillage.kernel.types import Client, Fiche, Lesson, ScopeKind
from sillage.memory.namespaces import common_namespace
from sillage.tools.errors import quietly, tool_error
from sillage.tools.gateway import caller_of, enforce, now_of, writer_of
from sillage.tools.texts import lesson_record


def publish_common(
    stack: Any,
    *,
    fiche_id: str,
    common_text: str,
    principal: Principal | None = None,
) -> Lesson | dict[str, Any]:
    """Publish a fiche in the common knowledge, after the six checks.

    Only a referent may call it (rule D.4, table 10.5). The version is written
    verbatim, and the lesson it becomes cannot lead back to the episode of the
    client it came from (EF-GEN-07, R15).
    """
    caller = caller_of(principal)
    try:
        enforce(stack, "publish_common", caller, ficheId=fiche_id, commonText=common_text)
    except PolicyDenied as exc:
        return tool_error(exc)
    try:
        return _publish(stack, caller, fiche_id=fiche_id, common_text=common_text)
    except SillageError as exc:
        return tool_error(exc)


def _publish(stack: Any, caller: Principal, *, fiche_id: str, common_text: str) -> Lesson:
    """Run the six steps, then write the approved text in the common space."""
    fiche = stack.catalog.get_fiche(fiche_id)
    if fiche is None:
        raise NotFound(f"fiche inconnue : {fiche_id}", ficheId=fiche_id)
    client = _client(stack, fiche)
    lesson = approve_common(
        fiche=fiche,
        rewritten=common_text,
        identifiers=_identifiers(stack, fiche, client),
        guardrails=stack.guardrails,
        approver=writer_of(caller),
        client=client,
    )
    lesson = replace(lesson, memory_record_id=lesson.lesson_id)
    stack.catalog.put_lesson(lesson)
    _write(stack, lesson)
    return lesson


def _client(stack: Any, fiche: Fiche) -> Client | None:
    """The contract of the perimeter the fiche comes from (EF-GEN-06)."""
    if fiche.scope.kind is not ScopeKind.CLIENT:
        return None
    return stack.catalog.get_client(fiche.scope.client_id or "")


def _identifiers(stack: Any, fiche: Fiche, client: Client | None) -> frozenset[str]:
    """Everything that designates the client in the origin of the fiche.

    The list comes from the traces the fiche cites and from the name of the
    client: the code decides what an identifier is, a model never does.
    """
    found: set[str] = set()
    for source in fiche.sources:
        trace = stack.catalog.get_trace(source)
        if trace is not None:
            found |= extract_from_trace(trace, client)
    if client is not None:
        found |= name_variants(client.name)
    return frozenset(found)


def _write(stack: Any, lesson: Lesson) -> None:
    """Write the approved text in the common space, verbatim (EF-GEN-07)."""
    record = lesson_record(lesson, namespace=common_namespace(stack.settings), created_at=now_of(stack))
    quietly(stack.memory.put_records, [record])


__all__ = ["publish_common"]
