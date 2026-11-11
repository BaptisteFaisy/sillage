"""``sillage`` command line: one dispatcher, one module per subcommand.

Each subcommand lives in its own module so that several contributors (human or
agent) can add one without touching this file - the registry below is the only
shared surface. Keep it that way: merge conflicts here block the whole queue.
"""

from __future__ import annotations

import argparse
import importlib
import sys

#: subcommand -> module holding ``run(argv) -> int`` and ``HELP``
COMMANDS: dict[str, str] = {
    "smoke": "sillage.cli.smoke",
    "demo": "sillage.cli.demo",
    "serve": "sillage.cli.serve",
    "mcp": "sillage.cli.mcp",
    "seed": "sillage.cli.seed",
    "bench": "sillage.cli.bench",
    "doctor": "sillage.cli.doctor",
    "synth": "sillage.cli.synth",
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="sillage", description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=sorted(COMMANDS), help="what to run")
    parser.add_argument("args", nargs=argparse.REMAINDER, help="command arguments")
    return parser


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in {"-h", "--help"}:
        build_parser().print_help()
        return 0
    name, rest = argv[0], argv[1:]
    module_name = COMMANDS.get(name)
    if module_name is None:
        print(f"sillage: unknown command {name!r}", file=sys.stderr)
        return 2
    try:
        module = importlib.import_module(module_name)
    except ModuleNotFoundError as exc:  # subcommand not merged yet
        print(f"sillage {name}: not available yet ({exc.name})", file=sys.stderr)
        return 0
    return int(module.main(rest) or 0)


if __name__ == "__main__":
    raise SystemExit(main())
