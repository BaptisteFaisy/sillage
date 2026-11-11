"""``sillage smoke`` - the whole loop, offline, in one command.

CI runs it on every pull request. Each step probes the module it needs and
reports SKIP when that module is not merged yet, so the smoke stays green from
the bootstrap commit to the full demo, and shows exactly how much of the loop
is wired. Exit code is non-zero only on a real failure.
"""

from __future__ import annotations

import argparse
import importlib
from collections.abc import Callable
from dataclasses import dataclass

PASS, SKIP, FAIL = "PASS", "SKIP", "FAIL"


class Skip(Exception):
    """A module of the loop is not available yet."""


@dataclass(frozen=True)
class Result:
    name: str
    status: str
    detail: str = ""


def _need(module: str) -> object:
    try:
        return importlib.import_module(module)
    except ModuleNotFoundError as exc:
        raise Skip(f"{module} absent ({exc.name})") from exc


def check_kernel() -> str:
    from sillage.kernel.ids import episode_id, slug, trace_fingerprint
    from sillage.kernel.types import LEVEL_CODE, Level, Scope, ScopeKind

    scope = Scope(kind=ScopeKind.CLIENT, client_id="a")
    assert scope.actor_id == "client-a", scope.actor_id
    assert Scope(kind=ScopeKind.COMMON).actor_id == "commun"
    assert episode_id("client-a", "guichet:2041") == episode_id("client-a", "guichet:2041")
    assert trace_fingerprint("w", "abc") == trace_fingerprint("w", "abc")
    assert slug("Connexions à la base épuisées") == "connexions-a-la-base-epuisees"
    assert LEVEL_CODE[Level.PROVEN] == "3" and LEVEL_CODE[Level.SET_ASIDE] == "E"
    return "scopes, ids deterministes, niveaux 0/1/2/3/E/R"


def check_capture() -> str:
    capture = _need("sillage.capture.ingest")
    sample = _need("sillage.capture.sample")
    service = capture.build_default_service()
    trace = sample.ticket_2041()
    ingested = service.ingest_trace(trace)
    assert ingested.events, "aucun evenement normalise"
    assert ingested.redacted_secrets >= 0
    again = service.ingest_trace(trace)
    assert again.duplicate, "EF-CAP-11 : la meme trace doit etre ignoree"
    events, secrets = len(ingested.trace.events), ingested.redacted_secrets
    return f"{events} evenements, {secrets} secret(s) retire(s), double reception ignoree"


def check_memory() -> str:
    local = _need("sillage.memory.local")
    from sillage.kernel.config import Settings

    backend = local.LocalMemoryBackend(Settings())
    backend.ensure_memory(Settings().memory_name, "smoke")
    sample = _need("sillage.capture.sample")
    backend.create_events(actor_id="client-a", session_id="ticket-2041", trace=sample.ticket_2041())
    hits = backend.retrieve(
        namespace=backend.namespace("client-a", "ticket-2041"), query="connexions base", top_k=3
    )
    assert hits, "recherche semantique sans resultat"
    return f"{len(hits)} enregistrement(s) retrouves hors ligne"


def check_catalog() -> str:
    scoring = _need("sillage.catalog.scoring")
    levels = _need("sillage.catalog.levels")
    rate = scoring.success_rate(successes=5, failures=0)
    assert abs(rate - 1.0) < 1e-9, rate
    assert abs(scoring.success_rate(0, 0) - 0.5) < 1e-9
    assert levels.next_level_after_successes(level="validated", successes=3, authors=2) == "proven"
    assert levels.should_set_aside(consecutive_failures=2) is True
    return "taux (reussites+1)/(total+2), passage eprouvee et mise a l'ecart"


def check_policy() -> str:
    engine_mod = _need("sillage.policy.engine")
    from sillage.kernel.protocols import Principal, ToolRequest
    from sillage.kernel.types import AgentMode, PrincipalRole

    engine = engine_mod.build_default_engine()
    cross = ToolRequest(
        tool="recall",
        principal=Principal(
            subject="agent-b", role=PrincipalRole.AGENT, client_id="b", mode=AgentMode.AUTONOMOUS
        ),
        arguments={"clientId": "a"},
    )
    assert engine.authorize(cross).decision.value == "forbid", "R9 : fuite entre clients"
    wide = ToolRequest(tool="recall_all", principal=cross.principal, arguments={"clientId": "b"})
    assert engine.authorize(wide).decision.value == "forbid", "R8 : non valide a un agent autonome"
    engineer = ToolRequest(
        tool="validate_fiche",
        principal=Principal(subject="p-1", role=PrincipalRole.ENGINEER),
        arguments={"ficheId": "f-1"},
    )
    assert engine.authorize(engineer).decision.value == "forbid", "R7 : validation reservee au referent"
    return "R7/R8/R9 refuses, decisions journalisees"


def check_guards() -> str:
    identifiers = _need("sillage.guards.identifiers")
    generalize = _need("sillage.guards.generalize")
    found = identifiers.extract(
        "compte 123456789012, hôte db-a1.clienta.internal, arn:aws:iam::123456789012:role/x"
    )
    assert found, "aucun identifiant client detecte"
    assert generalize.contains_identifier("sur db-a1.clienta.internal", found) is True
    assert generalize.contains_identifier("sur l'hôte de la base du client", found) is False
    return f"{len(found)} identifiant(s) extraits, controle EF-GEN-03"


def check_tools() -> str:
    tools = _need("sillage.tools.recall")
    demo = _need("sillage.demo.stack")
    stack = demo.build()
    answer = tools.recall(
        stack, client_id="a", problem="erreurs 500, too many connections après un déploiement"
    )
    assert answer.usage_id, "pas d'identifiant d'usage (EF-RET-01)"
    assert len(answer.fiches) <= 3, "EF-RES-02 : au plus 3 fiches"
    return f"usage={answer.usage_id} fiches={len(answer.fiches)} a-eviter={len(answer.avoid)}"


def check_bench() -> str:
    bench = _need("sillage.replay.bench")
    demo = _need("sillage.demo.stack")
    report = bench.run(demo.build(), problems=2, attempts=2)
    assert report.without_memory.attempts and report.with_memory.attempts
    return f"rejeu {report.problem_count} problemes x {report.attempts} essais, gain {report.net_gain:+.2f}"


CHECKS: tuple[tuple[str, Callable[[], str]], ...] = (
    ("noyau", check_kernel),
    ("capture", check_capture),
    ("memoire", check_memory),
    ("catalogue", check_catalog),
    ("politiques", check_policy),
    ("gardes", check_guards),
    ("outils", check_tools),
    ("rejeu", check_bench),
)


def run(verbose: bool = False) -> int:
    results: list[Result] = []
    for name, check in CHECKS:
        try:
            results.append(Result(name, PASS, check()))
        except Skip as skip:
            results.append(Result(name, SKIP, str(skip)))
        except Exception as exc:
            results.append(Result(name, FAIL, f"{type(exc).__name__}: {exc}"))
    width = max(len(r.name) for r in results)
    for result in results:
        mark = {"PASS": "+", "SKIP": "-", "FAIL": "!"}[result.status]
        print(f"  [{mark}] {result.name.ljust(width)}  {result.detail}")
        if result.status == FAIL and verbose:
            print(f"      {result.detail}")
    passed = sum(1 for r in results if r.status == PASS)
    failed = sum(1 for r in results if r.status == FAIL)
    print(f"SMOKE: {passed} reussi(s), {len(results) - passed - failed} non disponible(s), {failed} echec(s)")
    return 1 if failed else 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="sillage smoke", description=__doc__.splitlines()[0])
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)
    return run(verbose=args.verbose)


if __name__ == "__main__":
    raise SystemExit(main([]))
