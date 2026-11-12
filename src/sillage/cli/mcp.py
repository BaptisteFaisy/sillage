"""``sillage mcp`` - the MCP server on stdin/stdout.

This is the entry point of chapter 13.7: any MCP client - Kiro in particular -
reaches the eleven tools of table 15.1 by running this command, and it reaches
them under the same policies as the deployed gateway. Nothing here talks to
the network: the stack is the offline one of :mod:`sillage.demo.stack`.

Three uses:

    sillage mcp                 # serve the protocol, one JSON request per line
    sillage mcp --list          # what the server exposes, and nothing else
    sillage mcp --call recall --args '{"clientId":"a","problem":"..."}'
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

HELP = __doc__.splitlines()[0]


def build_stack(seed: bool = True) -> Any:
    """The stack the server runs on: the offline one of the demo module."""
    from sillage.demo.stack import build

    return build(seed=seed)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="sillage mcp", description=HELP)
    parser.add_argument("--list", action="store_true", help="liste les outils exposés, puis quitte")
    parser.add_argument("--call", metavar="OUTIL", help="appelle un outil, avec --args, puis quitte")
    parser.add_argument("--args", metavar="JSON", default="{}", help="les arguments de --call, en JSON")
    parser.add_argument("--no-seed", action="store_true", help="démarre sur une mémoire vide")
    args = parser.parse_args(argv)
    if args.list:
        return _list()
    try:
        stack = build_stack(seed=not args.no_seed)
    except ModuleNotFoundError as exc:
        print(f"sillage mcp : le câblage n'est pas disponible ({exc.name})", file=sys.stderr)
        return 1
    if args.call:
        return _call(stack, args.call, args.args)
    from sillage.tools.server import serve_stdio

    return serve_stdio(stack)


def _list() -> int:
    """What the server exposes: the eleven tools, one line each."""
    from sillage.tools.registry import SCHEMAS

    for schema in SCHEMAS:
        names = ", ".join(schema["inputSchema"].get("required") or ())
        print(f"{schema['name']:15s} {schema['description']}")
        if names:
            print(f"{'':15s} obligatoire : {names}")
    print(f"{len(SCHEMAS)} outil(s) exposé(s) en MCP.")
    return 0


def _call(stack: Any, name: str, raw: str) -> int:
    """One call, as the gateway would make it, printed on the standard output."""
    from sillage.tools.server import handle

    try:
        arguments = json.loads(raw)
    except json.JSONDecodeError as exc:
        print(f"sillage mcp : --args n'est pas du JSON ({exc})", file=sys.stderr)
        return 2
    answer = handle(stack, {"method": "tools/call", "params": {"name": name, "arguments": arguments}})
    print(json.dumps(answer, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main([]))
