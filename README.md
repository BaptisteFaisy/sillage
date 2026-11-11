# Sillage

**One shared memory for the whole team: engineers, developers and AI agents.**
Every finished job — a closed ticket, an agent investigation, a code session, a
merged fix — leaves a trace the others find again exactly when they need it.

AWS MSP AI Hackathon 2026, Track 1 (*Run Your MSP Better*), targeting the
**Best Amazon Bedrock AgentCore Implementation** special prize.

> Internal work is in French (`docs/fr/SPEC.fr.md` is the source specification).
> Every deliverable read by the judges is in English, starting with this file.

## The problem

In an MSP, knowledge is lost twice. When an engineer solves a hard problem, the
solution stays in their head or in a ticket nobody will read again. When an AI
agent solves the same problem it is worse: it forgets everything at the end of
its session, and the next agent repeats the same attempts and the same mistakes.

Each product has its own closed memory: the ticketing tool has its knowledge
base, the operations agent learns in its own space, the code agent has its
per-project instruction files. None of them serves all three populations.

## What Sillage does

| Object | What it is | Produced by |
|---|---|---|
| **Episode** | One finished job: situation, intent, turn by turn what was tried, verdict, teachings | AgentCore Memory (episodic strategy) |
| **Lesson** | What several close episodes teach: use case, advice, pitfalls, confidence 0.1–1.0 | AgentCore Memory (reflection) |
| **Fiche** | A lesson reviewed, corrected and validated by a human referent | Writer agent, then a referent |

What makes it different:

- **One memory for three populations.** An agent benefits from what an engineer
  learned last week, and the other way round.
- **Failures count as much as successes.** "Do not restart the service, it fixes
  nothing" saves as much time as the solution does.
- **Client isolation is enforced by rules, not by the good will of a model.**
  AgentCore Policy decides before the tool runs; the namespace is computed from
  the token, never from free text.
- **Knowledge is graded by usage.** Each fiche carries its sources, its success
  rate and its last confirmation date. A fiche that fails twice is set aside.
- **Corrections made to agents are captured.** When an engineer rejects an
  agent's diagnosis and gives the right one, the episode keeps both, and the
  next lesson contains the trap to avoid.

## Architecture

```
Guichet (demo ticketing)  AWS DevOps Agent  Kiro  Git merge
            \                   |          /      /
             +--- Capture: API Gateway / EventBridge / SQS / Lambda
                        |  normalise, redact secrets, dedupe
                        v
                  S3 (versioned traces)
                        v
        Amazon Bedrock AgentCore
        +---------------------------------------------+
        | Memory   episodes + lessons (reflection)     |
        | Gateway  MCP tools, single entry point       |
        | Policy   who calls what, for which client    |
        | Runtime  writer, generaliser, guardian,      |
        |          answerer (Strands Agents)           |
        | Identity  inbound tokens, outbound creds     |
        | Observability + Evaluations                  |
        +---------------------------------------------+
              |                       |
        DynamoDB catalogue      AWS Agent Registry
        (exact state, rates)    (MCP server + skills)
              |
        Portal + Guichet (CloudFront, S3, API GW, Lambda, Cognito)
```

Seven AgentCore building blocks, each for its own job — see
[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) and
[`docs/HLD.md`](docs/HLD.md).

## Repository layout

| Path | Contents |
|---|---|
| `src/sillage/kernel/` | Vocabulary (types), ports (protocols), errors, ids, settings |
| `src/sillage/capture/` | Normalisation, secret redaction, dedupe, ingestion, source adapters |
| `src/sillage/memory/` | Namespaces, AgentCore Memory backend, offline backend, distillation, search |
| `src/sillage/catalog/` | DynamoDB + offline catalogue, trust levels, scoring, versioning |
| `src/sillage/policy/` | Principals, policy engine, rules, Cedar output, decision journal |
| `src/sillage/guards/` | Identifier extraction, generalisation control, sourcing control, poisoning |
| `src/sillage/tools/` | The 11 MCP tools and the MCP server |
| `src/sillage/agents/` | The four Strands agents (writer, generaliser, guardian, answerer) |
| `src/sillage/replay/` | Replay bench: same problems with and without memory |
| `src/sillage/api/` | Portal API, capture endpoints, Guichet demo ticketing |
| `src/sillage/demo/` | Offline stack wiring: seeded data, local backends, one call to build |
| `portal/` | Static web UI, including the public "try it" page for the judges |
| `infra/cdk/` | Infrastructure as code, one stack per concern |
| `data/` | Synthetic history generator and fixtures (probes, secrets, poisoned tickets) |
| `tests/` | Offline unit tests and acceptance scenarios R1–R22 |
| `docs/` | English deliverables; `docs/fr/SPEC.fr.md` is the source specification |

## Quick start

```bash
make install        # dev dependencies (python >= 3.12)
make all            # lint + size guard + offline tests + smoke
make smoke          # the whole loop, offline, one command
make demo           # seeded demo: capture -> episode -> lesson -> fiche -> recall
make bench          # replay bench, with and without memory
make serve          # portal + Guichet on http://127.0.0.1:8080
make mcp            # MCP server on stdio
```

Everything above runs **offline and without an AWS account**: each port has a
local implementation. The real AWS path (`SILLAGE_OFFLINE=false`) uses the same
ports against AgentCore Memory, Gateway, Policy, Runtime, Bedrock and DynamoDB.

## Deploying on AWS

```bash
cd infra/cdk && cdk deploy --all      # eu-central-1, budget alarm included
```

See [`docs/RUNBOOK.md`](docs/RUNBOOK.md) for the full installation, the demo
data load and the teardown. A new client is a configuration entry, not code.

## Guarantees, and how they are proved

| Guarantee | Layer | Proof in this repository |
|---|---|---|
| A client never reads another client's knowledge | Policy + code | 40 probes, `tests/test_isolation.py` (R9, R15) |
| No secret is ever stored | Code, at capture | 30 planted secrets, `tests/test_secrets.py` (R2) |
| No poisoned instruction reaches an autonomous agent | Policy + guardrails | 20 booby-trapped tickets, `tests/test_poisoning.py` (R17) |
| No number is written by a model | Code | Scoring and levels have pure-code tests (R16) |
| Nothing client-specific leaks into the common knowledge | Code + human approval | 30 fiches to generalise, `tests/test_generalisation.py` (R13) |
| No metric about a person | Design | `tests/test_privacy.py` (ENF-RH-01) |

## Licence

MIT — see [LICENSE](LICENSE).
