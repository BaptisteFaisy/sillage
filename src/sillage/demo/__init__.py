"""Demo: the wiring every test, the smoke and the portal share.

This package is the missing piece of the specification (CONTRACT.md §11): six
modules are merged, but nothing was wired together. Without this wiring every
following module - the tools, the agents, the replay bench, the portal API -
would reinvent its own assembly, and the suite would drift apart.

Four things live here:

* :mod:`sillage.demo.stack` - :class:`~sillage.demo.stack.Stack` and
  :func:`~sillage.demo.stack.build`, the one call that assembles the twelve
  ports, offline by default and with no dependency installed;
* :mod:`sillage.demo.seed` - the starting history, the lessons, the fiches and
  the common knowledge, so the demo has something to show;
* :mod:`sillage.demo.scenario` - the narrated demonstration of the journeys
  8.1 to 8.6, one readable line per step, for the three minute video;
* :mod:`sillage.demo.tryit` - the engine of the public try page: what the memory
  returns, and what it refuses.

Everything is offline, deterministic and free of any figure produced by a model
(ENF-IA-01). The texts shown on screen are French; the code is English.
"""

from __future__ import annotations

from sillage.demo.history import FAMILIES, FaultFamily, demo_clients
from sillage.demo.knowledge import fiche_text, lesson_text, publish_fiche, publish_lesson, skill_folder
from sillage.demo.lookup import Consultation, consult
from sillage.demo.ports import LocalRegistry, OfflineModels, RegistryRecord
from sillage.demo.scenario import run
from sillage.demo.seed import load_history, seed_stack
from sillage.demo.stack import DEFAULT_CLIENTS, Stack, build, register_clients
from sillage.demo.tryit import ask
from sillage.demo.wiring import PROVISIONAL, boto3_available

__all__ = [
    "DEFAULT_CLIENTS",
    "FAMILIES",
    "PROVISIONAL",
    "Consultation",
    "FaultFamily",
    "LocalRegistry",
    "OfflineModels",
    "RegistryRecord",
    "Stack",
    "ask",
    "boto3_available",
    "build",
    "consult",
    "demo_clients",
    "fiche_text",
    "lesson_text",
    "load_history",
    "publish_fiche",
    "publish_lesson",
    "register_clients",
    "run",
    "seed_stack",
    "skill_folder",
]
