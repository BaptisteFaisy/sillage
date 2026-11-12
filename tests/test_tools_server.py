"""The MCP server: the door every client goes through (EF-RES-07).

The tests here speak the protocol: the handshake, the list of the tools, and
a call. What matters is that a call goes through the tool layer - and
therefore through the policy engine - and that a refusal comes back as a
refusal, with the rule that refused in it.
"""

from __future__ import annotations

import io
import json
import sys

from sillage.tools.registry import NAMES
from sillage.tools.server import PROTOCOL_VERSION, TOOLS, handle
from tools_fakes import CLIENT_A, CLIENT_B, add_fiche, build_stack

#: The problem the demo asks about.
PROBLEM = "erreurs 500, too many connections après un déploiement"


def test_initialize_announces_the_protocol_and_the_server():
    stack = build_stack()

    answer = handle(stack, {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})

    result = answer["result"]
    assert answer["id"] == 1
    assert result["protocolVersion"] == PROTOCOL_VERSION
    assert result["capabilities"]["tools"] == {"listChanged": False}
    assert result["serverInfo"]["name"] == "sillage"


def test_tools_list_exposes_the_eleven_tools_of_the_table():
    stack = build_stack()

    answer = handle(stack, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"})

    tools = answer["result"]["tools"]
    assert len(tools) == 11
    assert tuple(tool["name"] for tool in tools) == NAMES
    for tool in tools:
        assert tool["description"]
        assert tool["inputSchema"]["type"] == "object"


def test_tools_call_answers_with_the_figures_of_the_catalogue():
    stack = build_stack()
    fiche = add_fiche(stack)

    answer = handle(
        stack,
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "recall", "arguments": {"clientId": CLIENT_A, "problem": PROBLEM}},
        },
    )

    result = answer["result"]
    assert result["isError"] is False
    payload = json.loads(result["content"][0]["text"])
    assert payload["usageId"].startswith("u-")
    assert payload["fiches"][0]["id"] == fiche.fiche_id
    assert payload["fiches"][0]["niveau"] == "2"
    assert payload["jetonsEstimes"] > 0


def test_tools_call_returns_the_refusal_of_the_policy():
    stack = build_stack()
    add_fiche(stack)

    answer = handle(
        stack,
        {
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {
                "name": "recall",
                "arguments": {
                    "clientId": CLIENT_A,
                    "problem": PROBLEM,
                    "token": {
                        "sub": "agent-b",
                        "role": "agent",
                        "mode": "autonomous",
                        "clientId": CLIENT_B,
                    },
                },
            },
        },
    )

    result = answer["result"]
    assert result["isError"] is True
    payload = json.loads(result["content"][0]["text"])
    assert payload["error"] == "policy_denied"
    assert "S.1" in payload["message"]


def test_tools_call_of_an_unknown_tool_is_a_json_rpc_error():
    stack = build_stack()

    answer = handle(
        stack,
        {"jsonrpc": "2.0", "id": 5, "method": "tools/call", "params": {"name": "inexistant"}},
    )

    assert answer["error"]["code"] == -32602
    assert "inexistant" in answer["error"]["message"]


def test_an_unknown_method_is_a_json_rpc_error():
    stack = build_stack()

    answer = handle(stack, {"jsonrpc": "2.0", "id": 6, "method": "tools/invoquer"})

    assert answer["error"]["code"] == -32602


def test_a_notification_has_no_answer():
    stack = build_stack()

    answer = handle(stack, {"jsonrpc": "2.0", "method": "notifications/initialized"})

    assert answer == {}


def test_serve_stdio_answers_one_request_per_line(monkeypatch, capsys):
    stack = build_stack()
    add_fiche(stack)
    requests = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "recall", "arguments": {"clientId": CLIENT_A, "problem": PROBLEM}},
        },
    ]
    payload = "\n".join(json.dumps(one) for one in requests) + "\n"
    monkeypatch.setattr(sys, "stdin", io.StringIO(payload))

    from sillage.tools.server import serve_stdio

    assert serve_stdio(stack) == 0
    lines = [line for line in capsys.readouterr().out.splitlines() if line.strip()]
    assert len(lines) == 2
    assert json.loads(lines[0])["result"]["serverInfo"]["name"] == "sillage"
    assert json.loads(lines[1])["result"]["content"]


def test_the_schemas_of_the_tools_are_the_ones_of_the_table():
    assert {tool["name"] for tool in TOOLS} == {
        "recall",
        "recall_all",
        "get_item",
        "record_episode",
        "report_outcome",
        "propose_fiche",
        "validate_fiche",
        "publish_common",
        "set_aside",
        "export_skill",
        "forget",
    }
    recall_schema = next(tool for tool in TOOLS if tool["name"] == "recall")
    assert recall_schema["inputSchema"]["required"] == ["clientId", "problem"]


def test_the_command_lists_the_tools_it_exposes(capsys):
    """``sillage mcp --list`` tells a client what it will find."""
    from sillage.cli.mcp import main

    assert main(["--list"]) == 0
    printed = capsys.readouterr().out
    assert "recall" in printed
    assert "11 outil(s)" in printed
