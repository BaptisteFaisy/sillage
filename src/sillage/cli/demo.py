"""``sillage demo`` - the narrated demonstration, line by line.

The command plays the six journeys of specification 19.2 on an offline stack and
prints one readable line per step, in French. It is the command the three minute
video is recorded from: every line is read from the stack the code built, so the
demonstration cannot claim something the memory does not hold.

The stack is built and seeded on the fly (60 episodes, eight fault families, two
fictional clients), which takes a fraction of a second and needs no AWS account,
no network and no dependency.
"""

from __future__ import annotations

import argparse

from sillage.demo.scenario import run

#: What the command prints when it is asked for help.
HELP = "Joue la démonstration hors ligne et l'affiche ligne par ligne."


def main(argv: list[str]) -> int:
    """Print the demonstration; always succeeds when the stack can be built."""
    parser = argparse.ArgumentParser(prog="sillage demo", description=__doc__.splitlines()[0])
    parser.add_argument("--quiet", action="store_true", help="n'affiche que les étapes")
    args = parser.parse_args(argv)
    for line in run():
        if args.quiet and not line[:1].isdigit():
            continue
        print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main([]))
