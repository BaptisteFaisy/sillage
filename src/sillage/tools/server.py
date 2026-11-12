"""The MCP server: the gateway speaks to any MCP client (EF-RES-07).

Chapter 13.7 lists the clients - AWS DevOps Agent, Kiro, the agents of the
house, any other MCP client - and they all reach the same eleven tools, under
the same policies. This module is that single door: JSON-RPC on stdin and
stdout, the standard transport of the protocol.

Three methods matter:

* ``initialize`` - the handshake, with the protocol version and the tools the
  server exposes;
* ``tools/list`` - the schemas of table 15.1;
* ``tools/call`` - the call itself, which goes through the tool layer, and
  therefore through the policy engine before anything runs.

A refusal is a normal answer here too: it comes back as a content whose
``isError`` is true, with the rule that refused in it.
"""

from __future__ import annotations

import base64
import binascii
import json
import os
import sys
from collections.abc import Callable, Mapping
from typing import Any

from sillage.kernel.protocols import Principal
from sillage.kernel.types import Fiche, Lesson, RecallResult
from sillage.policy.principal import from_token
from sillage.tools.common import publish_common
from sillage.tools.errors import is_refusal
from sillage.tools.fiche import propose_fiche, set_aside, validate_fiche
from sillage.tools.forget import forget
from sillage.tools.outcome import report_outcome
from sillage.tools.recall import get_item, recall, recall_all
from sillage.tools.record import record_episode
from sillage.tools.registry import SCHEMAS, TOKEN_PROPERTY
from sillage.tools.skill import export_skill
from sillage.tools.views import fiche_view, lesson_view, recall_view

#: The MCP tools the gateway exposes: the eleven of the table 15.1.
TOOLS: tuple[dict[str, Any], ...] = SCHEMAS

#: The version of the protocol the server speaks.
PROTOCOL_VERSION = "2024-11-05"

#: The identity the server announces.
SERVER_NAME = "sillage"
SERVER_VERSION = "0.1.0"

#: The environment variable that carries the claims of the caller.
TOKEN_ENV = "SILLAGE_TOKEN"

#: The function behind each tool.
FUNCTIONS: dict[str, Callable[..., Any]] = {
    "recall": recall,
    "recall_all": recall_all,
    "get_item": get_item,
    "record_episode": record_episode,
    "report_outcome": report_outcome,
    "propose_fiche": propose_fiche,
    "validate_fiche": validate_fiche,
    "publish_common": publish_common,
    "set_aside": set_aside,
    "export_skill": export_skill,
    "forget": forget,
}

#: The name of the argument of the MCP call, and the parameter of the tool.
ARGUMENTS: dict[str, dict[str, str]] = {
    "recall": {"clientId": "client_id", "problem": "problem", "techno": "techno"},
    "recall_all": {"clientId": "client_id", "problem": "problem", "techno": "techno"},
    "get_item": {"id": "id"},
    "record_episode": {
        "clientId": "client_id",
        "workItemId": "work_item_id",
        "situation": "situation",
        "intent": "intent",
        "attempts": "attempts",
        "outcome": "outcome",
        "evidence": "evidence",
    },
    "report_outcome": {"usageId": "usage_id", "result": "result", "note": "note"},
    "propose_fiche": {"lessonId": "lesson_id", "episodeIds": "episode_ids", "draft": "draft"},
    "validate_fiche": {"ficheId": "fiche_id", "decision": "decision", "edits": "edits"},
    "publish_common": {"ficheId": "fiche_id", "commonText": "common_text"},
    "set_aside": {"ficheId": "fiche_id", "reason": "reason"},
    "export_skill": {"ficheId": "fiche_id"},
    "forget": {"workItemId": "work_item_id"},
}


class _UnknownTool(Exception):
    """The client asked for something the gateway does not expose."""


def handle(stack: Any, payload: dict[str, Any]) -> dict[str, Any]:
    """The answer of one JSON-RPC request.

    A notification has no answer: an empty dictionary says so to the caller.
    """
    method = str(payload.get("method") or "")
    request_id = payload.get("id")
    if method.startswith("notifications/"):
        return {}
    try:
        result = _dispatch(stack, method, dict(payload.get("params") or {}))
    except _UnknownTool as exc:
        return _error(request_id, -32602, str(exc))
    except Exception as exc:  # a tool that breaks must not break the server
        return _error(request_id, -32603, f"Erreur interne du serveur : {exc}", type(exc).__name__)
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _dispatch(stack: Any, method: str, params: dict[str, Any]) -> dict[str, Any]:
    """What each method of the protocol answers."""
    if method == "initialize":
        return _initialize(params)
    if method == "ping":
        return {}
    if method == "tools/list":
        return {"tools": [dict(schema) for schema in TOOLS]}
    if method == "tools/call":
        return _call(stack, params)
    raise _UnknownTool(f"méthode inconnue : {method}")


def _initialize(params: dict[str, Any]) -> dict[str, Any]:
    """The handshake: the protocol version, the capabilities and the identity."""
    version = str(params.get("protocolVersion") or PROTOCOL_VERSION)
    return {
        "protocolVersion": version,
        "capabilities": {"tools": {"listChanged": False}},
        "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
    }


def _call(stack: Any, params: dict[str, Any]) -> dict[str, Any]:
    """One tool call, through the tool layer and therefore through the policy."""
    name = str(params.get("name") or "")
    function = FUNCTIONS.get(name)
    if function is None:
        raise _UnknownTool(f"outil inconnu : {name}")
    arguments = dict(params.get("arguments") or {})
    kwargs = {parameter: arguments[key] for key, parameter in ARGUMENTS[name].items() if key in arguments}
    result = function(stack, **kwargs, principal=_principal(arguments))
    return _content(stack, result)


def _principal(arguments: dict[str, Any]) -> Principal | None:
    """The caller of a call: from the claims the client sent, or the default.

    Outside the gateway, the client identifies itself with the claims of its
    token. The engine then decides exactly as it does in production.
    """
    claims = _claims(arguments)
    if claims is None:
        return None
    return from_token(claims)


def _claims(arguments: dict[str, Any]) -> dict[str, Any] | None:
    """The claims of the caller: the argument, or the environment."""
    raw = arguments.get(TOKEN_PROPERTY)
    if raw is None:
        raw = os.environ.get(TOKEN_ENV)
    if raw is None:
        return None
    if isinstance(raw, Mapping):
        return dict(raw)
    return _decode(str(raw))


def _decode(raw: str) -> dict[str, Any] | None:
    """Read the claims: JSON, or the same JSON encoded in base64."""
    try:
        decoded = json.loads(raw)
    except json.JSONDecodeError:
        decoded = None
    if decoded is None:
        try:
            decoded = json.loads(base64.b64decode(raw, validate=True).decode("utf-8"))
        except (binascii.Error, UnicodeDecodeError, json.JSONDecodeError, ValueError):
            return None
    return decoded if isinstance(decoded, dict) else None


def _content(stack: Any, result: Any) -> dict[str, Any]:
    """The answer of a tool call, as the protocol expects it."""
    payload = _payload(stack, result)
    return {
        "content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False, default=str)}],
        "isError": is_refusal(result),
    }


def _payload(stack: Any, result: Any) -> dict[str, Any]:
    """The body of the answer: the object of the tool, rendered."""
    if isinstance(result, RecallResult):
        return recall_view(result, stack)
    if isinstance(result, Fiche):
        return fiche_view(result, stack)
    if isinstance(result, Lesson):
        return lesson_view(result)
    if isinstance(result, Mapping):
        return dict(result)
    return {"result": str(result)}


def _error(request_id: Any, code: int, message: str, kind: str = "") -> dict[str, Any]:
    """A JSON-RPC error, in French."""
    error: dict[str, Any] = {"code": code, "message": message}
    if kind:
        error["data"] = {"type": kind}
    return {"jsonrpc": "2.0", "id": request_id, "error": error}


def serve_stdio(stack: Any) -> int:
    """Serve the protocol on stdin and stdout, one JSON request per line."""
    for line in sys.stdin:
        stripped = line.strip()
        if not stripped:
            continue
        try:
            payload = json.loads(stripped)
        except json.JSONDecodeError:
            _write(_error(None, -32700, "requête JSON illisible"))
            continue
        answer = handle(stack, payload)
        if answer:
            _write(answer)
    return 0


def _write(payload: dict[str, Any]) -> None:
    """Write one answer on the standard output, and flush it."""
    sys.stdout.write(json.dumps(payload, ensure_ascii=False, default=str) + "\n")
    sys.stdout.flush()


__all__ = [
    "ARGUMENTS",
    "FUNCTIONS",
    "PROTOCOL_VERSION",
    "SERVER_NAME",
    "SERVER_VERSION",
    "TOKEN_ENV",
    "TOOLS",
    "handle",
    "serve_stdio",
]
