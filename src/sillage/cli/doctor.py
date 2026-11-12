"""``sillage doctor`` - diagnose the installation, offline and AWS.

The command answers the question the acceptance scenario R22 asks: "can this be
installed in a clean account by following the README?" It reports, in this order:

1. what works **offline** - the Python version, the wiring of the stack, the
   demonstration history and the narrated scenario. Nothing here needs an AWS
   account, a network or a dependency;
2. what is **missing for the AWS mode** - boto3, the region, the account, the
   credentials, the AgentCore memory and its strategy, the gateway, the capture
   queue and the guardrails. A missing element is named, never guessed;
3. the **settings in force** - what the environment variables changed, and which
   ports are still served by their offline twin in AWS mode.

No check reads a secret and no check calls the network: the doctor must be
runnable on a machine that has nothing but the repository. The exit code is
non-zero only when the offline stack itself cannot be built.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from sillage.demo.wiring import PROVISIONAL, boto3_available
from sillage.kernel.config import Settings

#: What the command prints when it is asked for help.
HELP = "Diagnostique l'installation : ce qui marche hors ligne, ce qui manque pour AWS."

OK, MISSING, INFO = "OK", "MANQUE", "INFO"
MARKS = {OK: "+", MISSING: "!", INFO: "-"}

#: Environment variables that may carry AWS credentials. Never printed.
CREDENTIAL_VARIABLES: tuple[str, ...] = (
    "AWS_ACCESS_KEY_ID",
    "AWS_PROFILE",
    "AWS_ROLE_ARN",
    "AWS_SESSION_TOKEN",
)

#: The capture queue of the deployed stack, named by the environment.
QUEUE_VARIABLE = "SILLAGE_QUEUE_URL"


@dataclass(frozen=True)
class Check:
    """One line of the diagnosis."""

    name: str
    status: str
    detail: str = ""


# -- offline ----------------------------------------------------------------- #
def offline_checks() -> list[Check]:
    """What works with zero dependency installed."""
    found: list[Check] = [
        Check(
            "python",
            OK if sys.version_info >= (3, 12) else MISSING,
            f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro} "
            "(3.12 ou plus attendu)",
        )
    ]
    found.append(_stack_check())
    found.append(_history_check())
    found.append(_scenario_check())
    return found


def _stack_check() -> Check:
    """The offline stack builds, and inside the two seconds the contract allows."""
    try:
        started = time.perf_counter()
        from sillage.demo.stack import build

        stack = build()
        elapsed = time.perf_counter() - started
    except Exception as error:  # the stack is the whole point: report it plainly
        return Check("câblage hors ligne", MISSING, f"{type(error).__name__}: {error}")
    episodes = len(tuple(stack.catalog.list_episodes(limit=0)))
    fiches = len(tuple(stack.catalog.list_fiches()))
    status = OK if elapsed < 2.0 else MISSING
    return Check(
        "câblage hors ligne",
        status,
        f"{elapsed:.2f} s, {episodes} épisodes, {len(tuple(stack.catalog.list_lessons()))} leçons, "
        f"{fiches} fiches",
    )


def _history_check() -> Check:
    """Where the demonstration history comes from."""
    from sillage.demo.history_file import FALLBACK_SOURCE, HISTORY_PATH

    if HISTORY_PATH.exists():
        return Check("historique", OK, f"{HISTORY_PATH} (généré par le lot data)")
    return Check("historique", INFO, f"{FALLBACK_SOURCE} — {HISTORY_PATH} absent")


def _scenario_check() -> Check:
    """The narrated demonstration runs from start to finish."""
    try:
        from sillage.demo.scenario import run

        lines = run()
    except Exception as error:
        return Check("scénario de démonstration", MISSING, f"{type(error).__name__}: {error}")
    return Check("scénario de démonstration", OK, f"{len(lines)} ligne(s) affichable(s)")


# -- AWS --------------------------------------------------------------------- #
def aws_checks(settings: Settings) -> list[Check]:
    """What the AWS mode needs, and what is missing."""
    if not boto3_available():
        return [
            Check(
                "boto3",
                MISSING,
                "non installé : le mode AWS est indisponible (pip install 'sillage[aws]')",
            )
        ]
    import boto3

    return [
        Check("boto3", OK, getattr(boto3, "__version__", "version inconnue")),
        Check(
            "région",
            OK if settings.region else MISSING,
            settings.region or "SILLAGE_REGION non renseignée",
        ),
        Check(
            "compte AWS",
            OK if settings.account_id else INFO,
            settings.account_id or "SILLAGE_ACCOUNT_ID non renseignée (les ARN en ont besoin)",
        ),
        Check("identifiants", *_credentials()),
        *aws_service_checks(settings),
    ]


def aws_service_checks(settings: Settings) -> list[Check]:
    """The AgentCore and Bedrock resources the deployed stack needs."""
    queue = os.environ.get(QUEUE_VARIABLE)
    return [
        Check(
            "mémoire AgentCore",
            OK if settings.memory_name else MISSING,
            f"nom « {settings.memory_name} », stratégie « {settings.strategy_name} »",
        ),
        Check(
            "stratégie d'épisodes",
            OK if settings.strategy_id else INFO,
            settings.strategy_id or "identifiant vide : créé au déploiement par ensure_memory",
        ),
        Check(
            "passerelle MCP",
            OK if settings.gateway_name else MISSING,
            f"« {settings.gateway_name} », cible « {settings.tool_target_name} »",
        ),
        Check(
            "file de capture",
            OK if queue else INFO,
            queue or f"{QUEUE_VARIABLE} non renseignée (créée par infra/cdk)",
        ),
        Check(
            "garde-fous Bedrock",
            OK if settings.guardrail_id else INFO,
            settings.guardrail_id
            if settings.guardrail_id
            else "SILLAGE_GUARDRAIL_ID vide : le filtre de données sensibles est désactivé",
        ),
    ]


def _credentials() -> tuple[str, str]:
    """Where the credentials come from, named but never printed."""
    for name in CREDENTIAL_VARIABLES:
        if os.environ.get(name):
            return OK, f"variable d'environnement {name}"
    path = Path.home() / ".aws" / "credentials"
    if path.exists():
        return OK, f"fichier {path}"
    return MISSING, "ni variable d'environnement ni ~/.aws/credentials : un rôle de charge de travail ?"


# -- settings ---------------------------------------------------------------- #
def settings_checks(settings: Settings) -> list[Check]:
    """The settings in force, and the ports still served offline in AWS mode."""
    found = [
        Check(
            "mode",
            INFO,
            "hors ligne" if settings.offline else "AWS (SILLAGE_OFFLINE=false)",
        ),
        Check("politiques", INFO, f"mode {settings.policy_mode}"),
        Check(
            "modèles",
            INFO,
            f"rapide « {settings.fast_model_id} », capable « {settings.capable_model_id} »",
        ),
        Check(
            "seuils",
            INFO,
            f"éprouvée après {settings.proven_successes} succès par "
            f"{settings.proven_distinct_authors} auteurs, à l'écart après "
            f"{settings.set_aside_consecutive_failures} échecs, périmée à {settings.stale_days} jours",
        ),
        Check(
            "restitution",
            INFO,
            f"{settings.max_fiches} fiches, {settings.max_episodes} épisodes, "
            f"{settings.max_recall_tokens} jetons au plus",
        ),
    ]
    for port, reason in sorted(PROVISIONAL.items()):
        found.append(Check(f"port {port}", INFO, reason))
    return found


# -- the command ------------------------------------------------------------- #
def run(verbose: bool = False) -> int:
    """Print the diagnosis; non-zero only when the offline stack is broken."""
    settings = Settings.from_env()
    sections: tuple[tuple[str, list[Check]], ...] = (
        ("Hors ligne", offline_checks()),
        ("Mode AWS", aws_checks(settings)),
        ("Réglages", settings_checks(settings)),
    )
    width = max(len(check.name) for _, checks in sections for check in checks)
    broken = 0
    for title, checks in sections:
        print(f"\n{title}")
        for check in checks:
            print(f"  [{MARKS[check.status]}] {check.name.ljust(width)}  {check.detail}")
            if check.status == MISSING:
                broken += 1
    offline_broken = any(check.status == MISSING for check in sections[0][1])
    print(
        f"\nDOCTOR : {broken} élément(s) manquant(s)"
        + (" — le mode AWS n'est pas prêt" if broken else " — tout est en place")
    )
    if verbose:
        print("(les éléments « INFO » ne bloquent pas : ce sont des réglages ou des ports provisoires)")
    return 1 if offline_broken else 0


def main(argv: list[str]) -> int:
    """The entry point the ``sillage`` dispatcher calls."""
    parser = argparse.ArgumentParser(prog="sillage doctor", description=__doc__.splitlines()[0])
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)
    return run(verbose=args.verbose)


if __name__ == "__main__":
    raise SystemExit(main([]))
