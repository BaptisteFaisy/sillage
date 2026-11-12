"""``sillage seed`` - load the history and print what was loaded.

The command loads the synthetic history of the demonstration into an offline
stack and prints the counters: how many episodes, lessons and fiches the
catalogue holds, and where the history came from.

The generated history of the ``data`` lot (``data/generated/history.json``) is
used when it exists; otherwise the deterministic fallback of
:mod:`sillage.demo.history` answers, so the command never fails because ``data/``
is empty. Loading twice is harmless: the capture recognises the traces it
already received.

Everything runs offline, in memory, in a fraction of a second.
"""

from __future__ import annotations

import argparse

from sillage.demo.seed import seed_stack
from sillage.demo.stack import build

#: What the command prints when it is asked for help.
HELP = "Charge l'historique de démonstration et affiche les compteurs."


def main(argv: list[str]) -> int:
    """Load the history, print the counters; zero when the history was loaded."""
    parser = argparse.ArgumentParser(prog="sillage seed", description=__doc__.splitlines()[0])
    parser.add_argument("--path", default="", help="fichier d'historique à charger")
    args = parser.parse_args(argv)
    stack = build(seed=False)
    counters = seed_stack(stack, path=args.path or None)
    for name, value in counters.items():
        print(f"{name:>16} : {value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main([]))
