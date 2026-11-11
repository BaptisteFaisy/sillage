# CONTRACT — module boundaries, signatures and invariants

This file is the agreement between contributors, human or agent. **Read your
section and the kernel before writing code.** If you need to change a signature
that another module uses, say so in the pull request description: the queue
merges in order, and a silent break costs everybody a red CI.

Rules that apply to every module without exception:

1. **Files < 500 lines, functions < 50 lines** (`make limits` enforces it).
   Split into modules and helpers instead of letting a file grow.
2. **No duplication.** If a helper exists in `kernel`, `capture` or `catalog`,
   import it. One implementation per logic.
3. **Offline by default.** Never import `boto3`, `fastapi`, `strands` or call
   the network at module import time. AWS code lives in a module imported
   lazily (`import boto3` *inside* the class constructor or a factory), so the
   test suite and the smoke run without any dependency installed.
4. **Tests with each module**, in `tests/test_<module>_*.py`, offline, fast.
5. **No number written by a model** (ENF-IA-01): rates, levels, metrics, token
   counts and freshness are computed by code, in `catalog/scoring.py` or
   `api/metrics.py`. A model may only produce text.
6. **No metric about a person** (ENF-RH-01): authors are pseudonyms
   (`kernel.ids.pseudonym`), and no function groups results by author.
7. **Degrade, do not fail** (ENF-DISP-02): catch `BackendUnavailable` at the
   edges (`tools`, `api`) and return an empty result with a note.
8. Ruff clean (`make lint`), type hints everywhere, docstrings in English,
   `from __future__ import annotations` at the top of every module.

Ownership map — **do not edit a file owned by another module**, and never edit
`pyproject.toml`, `Makefile`, `.github/**`, `.mergify.yml`, `scripts/**`,
`src/sillage/kernel/**`, `src/sillage/cli/__init__.py`, `tests/conftest.py`:

| Module | Paths it owns |
|---|---|
| kernel (merged) | `src/sillage/kernel/**`, `tests/test_kernel.py` |
| capture | `src/sillage/capture/**`, `tests/test_capture*.py` |
| memory | `src/sillage/memory/**`, `tests/test_memory*.py` |
| catalog | `src/sillage/catalog/**`, `tests/test_catalog*.py`, `tests/test_scoring*.py` |
| policy | `src/sillage/policy/**`, `tests/test_policy*.py`, `tests/test_isolation.py` |
| guards | `src/sillage/guards/**`, `tests/test_secrets.py`, `tests/test_poisoning.py`, `tests/test_generalisation.py`, `tests/test_privacy.py` |
| tools | `src/sillage/tools/**`, `tests/test_tools*.py` |
| agents | `src/sillage/agents/**`, `tests/test_agents*.py` |
| replay | `src/sillage/replay/**`, `tests/test_replay*.py` |
| api | `src/sillage/api/**`, `tests/test_api*.py` |
| demo | `src/sillage/demo/**`, `tests/test_demo*.py` |
| portal | `portal/**`, `tests/test_portal*.py` |
| infra | `infra/**`, `tests/test_infra*.py` |
| data | `data/**`, `tests/test_data*.py` |
| cli subcommands | `src/sillage/cli/<name>.py` (one file per subcommand) |
| docs | `docs/**` (not `docs/CONTRACT.md`, not `docs/fr/SPEC.fr.md`) |

---

## 1. kernel (already merged — read it, do not change it)

* `kernel/types.py` — `Scope`, `ScopeKind`, `Role`, `AuthorKind`, `AgentMode`,
  `PrincipalRole`, `Source`, `Outcome`, `Level`, `LEVEL_CODE`,
  `SERVED_TO_AUTONOMOUS_AGENT`, `SERVED_TO_HUMAN`, `FeedbackResult`,
  `ModelTier`, `ExtractionMode`, `Decision`, `TraceEvent`, `Turn`, `Trace`,
  `Episode`, `Lesson`, `Claim`, `Fiche`, `Usage`, `Feedback`, `Client`,
  `JournalEntry`, `ServedFiche`, `RecallResult`.
* `kernel/protocols.py` — `Clock`, `MemoryRecord`, `MemoryBackend`,
  `ModelBackend`, `GuardrailFilter`, `GuardrailVerdict`, `CatalogStore`,
  `BlobStore`, `Principal`, `ToolRequest`, `Authorization`, `PolicyEngine`,
  `Journal`, `Registry`, `Notifier`.
* `kernel/errors.py` — `SillageError` and its subclasses, each with a stable
  `.code` and `.as_dict()`.
* `kernel/ids.py` — `make_id`, `work_item_id`, `trace_fingerprint`,
  `session_id`, `episode_id`, `lesson_id`, `fiche_id`, `usage_id`,
  `journal_id`, `slug`, `pseudonym`, `is_prefixed`.
* `kernel/config.py` — `Settings` (+ `DEFAULT`) with every threshold and the
  namespace helpers `episode_namespace`, `reflection_namespace`,
  `fiche_namespace`, `common_namespace`, `action_name`.

Invariants: `Scope.actor_id` is `client-<id>`, `interne` or `commun`; the actor
is the **perimeter**, never a person. `Trace.metadata` has ≤ 15 keys.

---

## 2. capture

```python
# capture/normalize.py
def normalize(raw: RawTrace) -> Trace            # RawTrace = source specific dict/dataclass
def to_events(ticket: dict) -> tuple[TraceEvent, ...]
def summarise_logs(text: str, model: ModelBackend, max_lines: int = 40) -> str

# capture/redact.py
SECRET_PATTERNS: tuple[re.Pattern[str], ...]     # access keys, tokens, passwords, connection strings
def redact(text: str) -> tuple[str, int]         # (cleaned text, number of secrets removed)
def redact_trace(trace: Trace) -> tuple[Trace, int]
def contains_secret(text: str) -> bool           # used by the tests, never to store

# capture/dedupe.py
def fingerprint(trace: Trace) -> str             # kernel.ids.trace_fingerprint over canonical JSON
def is_duplicate(store: CatalogStore, trace: Trace) -> bool

# capture/ingest.py
@dataclass(frozen=True)
class IngestReport:
    trace: Trace
    events_created: bool
    redacted_secrets: int
    duplicate: bool
    do_not_distill: bool
    raw_key: str = ""
    episode_id: str = ""

class IngestService:
    def __init__(self, *, settings, memory, catalog, blobs, model=None, guardrails=None, clock=None): ...
    def ingest_trace(self, trace: Trace) -> IngestReport
    def ingest_raw(self, source: Source, payload: dict) -> IngestReport   # normalise + ingest
    def mark_do_not_keep(self, work_item_id: str, by: str) -> None        # EF-CAP-12
    def build_default_service(**overrides) -> IngestService               # offline wiring

# capture/sample.py  (used by the smoke and the tests)
def ticket_2041() -> Trace                          # annex B, with a planted secret
def investigation_77(client: str = "a") -> Trace     # DevOps Agent, corrected by a human
def kiro_session(client: str = "a") -> Trace         # code agent session
def merged_fix(client: str = "b") -> Trace           # EF-CAP-08
```

Order of operations in `ingest_trace` (EF-CAP-03 before any storage):
redact → guardrail scan → dedupe → store raw in `BlobStore` → `create_events`
with `ExtractionMode.SKIP` when `do_not_distill` → write `Travail`/`Episode`
rows in the catalogue → return the report. Receiving the same trace twice
returns `duplicate=True` and changes nothing (R3).

Requirements: EF-CAP-01..06, 09..13. Tests: `test_capture_normalize.py`,
`test_capture_redact.py`, `test_capture_ingest.py` (R1, R2, R3, R4, R11).

---

## 3. memory

```python
# memory/namespaces.py
def episode_namespace(settings, scope: Scope, work_item_id: str) -> str
def reflection_namespace(settings, scope: Scope) -> str
def fiche_namespace(settings, scope: Scope) -> str
def common_namespace(settings) -> str
def namespace_of_token(settings, principal: Principal) -> tuple[str, ...]  # never from free text

# memory/local.py
class LocalMemoryBackend:            # implements MemoryBackend, pure stdlib
    def __init__(self, settings: Settings, clock: Clock | None = None): ...
    def namespace(self, actor_id: str, session_id: str = "") -> str
    # semantic-ish search: tf-idf cosine over record content, deterministic

# memory/agentcore.py
class AgentCoreMemoryBackend:        # boto3 imported inside __init__
    def __init__(self, settings, client=None, control_client=None): ...
    # CreateMemory / CreateEvent / RetrieveMemoryRecords / BatchCreateMemoryRecords
    # / DeleteMemoryRecords / ListMemoryRecords, shapes of annexes A, B, C

# memory/distill.py
def distil_episode(trace: Trace, records: Sequence[MemoryRecord], model: ModelBackend) -> Episode
def reflect(scope: Scope, episodes: Sequence[Episode], model: ModelBackend) -> tuple[Lesson, ...]
def lesson_from_records(scope: Scope, records: Sequence[MemoryRecord]) -> tuple[Lesson, ...]

# memory/search.py
def rewrite_intent(problem: str, model: ModelBackend | None) -> str        # 13.4 step 1
@dataclass(frozen=True)
class SearchHit: record_id, namespace, content, score, level, scope
def search(settings, memory, catalog, *, scope, query, top_k) -> tuple[SearchHit, ...]
def rank(hits, *, scope_first: Scope, levels: set[Level]) -> tuple[SearchHit, ...]  # EF-RES-04
```

Invariants: reflection never crosses two clients (EF-DIS-02) — `reflect` is
called per scope and asserts it. A search always targets **one** namespace;
the caller merges client + common results, client first.

Requirements: EF-DIS-01..07, EF-RES-09. Tests: `test_memory_local.py`,
`test_memory_namespaces.py`, `test_memory_search.py`, `test_memory_distill.py`.
`memory/agentcore.py` is tested with a fake boto3 client object (no network).

---

## 4. catalog

```python
# catalog/model.py       — row shapes / (de)serialisation for DynamoDB
def fiche_to_item(fiche: Fiche) -> dict ; def fiche_from_item(item: dict) -> Fiche   # + same for each entity

# catalog/local.py
class LocalCatalog:      # implements CatalogStore + BlobStore + Journal + Notifier, in memory
class LocalBlobStore:    # implements BlobStore on a dict (or a directory)
class ListJournal:       # implements Journal on a list, append only

# catalog/dynamo.py      # boto3 inside __init__, same CatalogStore contract
class DynamoCatalog: ...

# catalog/levels.py
TRANSITIONS: dict[Level, tuple[Level, ...]]
def can_transition(current: Level, target: Level) -> bool
def next_level_after_successes(*, level, successes: int, authors: int, settings=DEFAULT) -> Level
def should_set_aside(*, consecutive_failures: int, settings=DEFAULT) -> bool
def apply_feedback(fiche: Fiche, result: FeedbackResult, *, settings=DEFAULT) -> Fiche  # pure

# catalog/scoring.py
def success_rate(*, successes: int, failures: int) -> float          # (s+1)/(s+f+2)
def freshness_days(fiche: Fiche, now: datetime) -> int | None
def is_stale(fiche: Fiche, now: datetime, *, settings=DEFAULT) -> bool
def served(fiche: Fiche, now, settings=DEFAULT) -> ServedFiche
def estimate_tokens(text: str, settings=DEFAULT) -> int
def catalogue_metrics(catalog, now) -> dict                          # EF-PIL-01..04, no per-person key

# catalog/versions.py
def bump(fiche: Fiche, **changes) -> Fiche          # version+1, keeps history row
def history(catalog, fiche_id: str) -> tuple[Fiche, ...]
```

Invariant: `apply_feedback` and `next_level_after_successes` are **pure
functions of the counts** — the state machine of chapter 10.2 and nothing else.
Two consecutive negative feedbacks set the fiche aside and return it (the
caller notifies the referent). `no_feedback_count` never enters the rate
(EF-RET-05).

Requirements: EF-VAL-04..07, EF-RET-04, EF-ENT-04, EF-PIL-*. Tests:
`test_catalog_levels.py`, `test_catalog_scoring.py`, `test_catalog_local.py`.

---

## 5. policy

```python
# policy/rules.py
@dataclass(frozen=True)
class Rule: id, effect: Decision, action: str, when: Callable[[ToolRequest], bool], why: str
RULES: tuple[Rule, ...]                 # D.1..D.5 + admin/publish rules, evaluated in order
# policy/engine.py
class PolicyEngine:                     # default deny, forbid wins, LOG_ONLY | ENFORCE
    def __init__(self, settings=DEFAULT, rules=RULES, journal: Journal | None = None, mode: str = "ENFORCE")
    def authorize(self, request: ToolRequest) -> Authorization
    def enforce(self, request: ToolRequest) -> None        # raises PolicyDenied
def build_default_engine(**overrides) -> PolicyEngine
# policy/principal.py
def from_token(claims: dict) -> Principal                  # claims only, never the payload
def demo_token(role: PrincipalRole, client_id: str | None = None, mode: AgentMode | None = None) -> dict
# policy/cedar.py
def render_policies(settings=DEFAULT) -> str               # annex D text, for the real deployment
def policy_document() -> dict                              # what infra/cdk feeds to AgentCore Policy
# policy/journal.py
def entry(*, request, authorization, usage_id="", work_item_id="") -> JournalEntry
```

Invariant: the engine decides **before** any tool logic, on `request.principal`
and `request.arguments["clientId"]` only. In `LOG_ONLY` mode a forbid is
journalised and returned but does not raise.

Requirements: chapter 10.1/10.4/10.5, EF-ADM-02. Tests: `test_policy_engine.py`,
`test_isolation.py` (40 probes from client B towards client A, R9/R15).

---

## 6. guards

```python
# guards/identifiers.py
def extract(text: str, *, client: Client | None = None) -> frozenset[str]
    # account ids, ARNs, IPs, hostnames, resource names, emails, client names
def extract_from_trace(trace: Trace, client: Client | None = None) -> frozenset[str]
# guards/generalize.py
def contains_identifier(text: str, identifiers: Iterable[str]) -> bool
def check_common_version(text: str, identifiers: Iterable[str]) -> None   # raises LeakDetected
def approve_common(*, fiche, rewritten, identifiers, guardrails=None, approver: str) -> Lesson
# guards/sources.py
def check_claims(fiche: Fiche) -> None            # raises UnsourcedClaim (EF-VAL-02)
def unsourced_claims(fiche: Fiche) -> tuple[Claim, ...]
def coverage(fiche: Fiche) -> float               # share of claims that cite an episode
# guards/poison.py
INSTRUCTION_PATTERNS: tuple[re.Pattern[str], ...]
def detect(text: str) -> tuple[str, ...]          # reasons
def is_poisoned(text: str) -> bool
def as_quotation(text: str, *, source: str) -> str   # memory is quoted, never instructed
def screen_trace(trace: Trace) -> Trace            # flags/quarantines poisoned events
# guards/pii.py
def mask(text: str) -> str                        # offline heuristic
class BedrockGuardrails:                          # lazy boto3, implements GuardrailFilter
    def scan(self, text: str) -> GuardrailVerdict
```

Requirements: EF-GEN-02..04, EF-VAL-02, chapter 10.3. Tests: `test_secrets.py`
(30 planted secrets, R2), `test_poisoning.py` (20 booby-trapped tickets, R17),
`test_generalisation.py` (30 fiches, R13/R14/R15), `test_privacy.py`.

---

## 7. tools

One function per tool, all taking an explicit `stack` (see §11) as first
argument, all enforcing policy **then** running the logic, all journalising.

```python
# tools/recall.py
def recall(stack, *, client_id: str, problem: str, techno: str = "", principal=None) -> RecallResult
def recall_all(stack, *, client_id: str, problem: str, techno: str = "", principal=None) -> RecallResult
def get_item(stack, *, id: str, principal=None) -> dict
# tools/record.py
def record_episode(stack, *, client_id, work_item_id, situation, intent, attempts, outcome, evidence, principal=None) -> dict
# tools/outcome.py
def report_outcome(stack, *, usage_id: str, result: str, note: str = "", principal=None) -> dict
# tools/fiche.py
def propose_fiche(stack, *, lesson_id="", episode_ids=(), draft: dict | None = None, principal=None) -> Fiche
def validate_fiche(stack, *, fiche_id, decision: str, edits: dict | None = None, principal=None) -> Fiche
def set_aside(stack, *, fiche_id, reason: str, principal=None) -> Fiche
# tools/common.py
def publish_common(stack, *, fiche_id, common_text: str, principal=None) -> Lesson
# tools/skill.py
def export_skill(stack, *, fiche_id, principal=None) -> dict          # SKILL.md folder + registry record
def skill_folder(fiche: Fiche) -> dict[str, str]                       # {"SKILL.md": ...}
# tools/forget.py
def forget(stack, *, work_item_id: str, principal=None) -> dict        # cascade + dependent fiches flagged
# tools/server.py
TOOLS: tuple[dict, ...]           # MCP tool schemas (name, description, inputSchema)
def handle(stack, payload: dict) -> dict      # JSON-RPC: initialize / tools/list / tools/call
def serve_stdio(stack) -> int
# tools/errors.py
def tool_error(exc: SillageError) -> dict     # a refusal is returned, and journalised (ENF-IA-03)
```

Invariant: `recall` returns at most `settings.max_fiches` fiches,
`settings.max_episodes` episodes and the list of paths to avoid, under
`settings.max_recall_tokens` estimated tokens (EF-RES-02, ENF-PERF-03). An
autonomous agent never receives a level outside
`SERVED_TO_AUTONOMOUS_AGENT` (EF-RES-05), and gets a `not_served_note` saying
how many non-validated episodes exist. Every answer carries a `usage_id`
(EF-RET-01) and every element its level, source, author kind and last
confirmation (EF-RES-03).

Requirements: 15.1, EF-RES-*, EF-RET-*, EF-EXE-*, EF-ENT-03. Tests:
`test_tools_recall.py`, `test_tools_lifecycle.py`, `test_tools_server.py`.

---

## 8. agents

```python
# agents/prompts.py
WRITER_SYSTEM, GENERALISER_SYSTEM, GUARDIAN_SYSTEM, ANSWERER_SYSTEM: str
def writer_prompt(lesson: Lesson, episodes: Sequence[Episode]) -> str
def generaliser_prompt(fiche: Fiche, identifiers: Iterable[str]) -> str
def guardian_prompt(catalogue_state: dict) -> str
def answerer_prompt(question: str, hits: Sequence[dict]) -> str
# agents/writer.py       -> draft fiche, every claim cites an episode (EF-VAL-01)
def draft_fiche(stack, *, lesson_id: str = "", episode_ids: Sequence[str] = ()) -> Fiche
# agents/generaliser.py  -> client free rewrite (EF-GEN-02); never publishes
def rewrite(fiche: Fiche, *, identifiers: Iterable[str], model: ModelBackend) -> str
# agents/guardian.py     -> weekly pass: duplicates, contradictions, stale (EF-ENT-01/02)
@dataclass(frozen=True) class Proposal: kind, fiche_ids, reason
def weekly_pass(stack, *, now=None) -> tuple[Proposal, ...]
def find_duplicates(catalog) -> tuple[Proposal, ...]
def find_contradictions(catalog) -> tuple[Proposal, ...]
def find_stale(catalog, now, settings=DEFAULT) -> tuple[Proposal, ...]
# agents/answerer.py     -> a sourced answer; says so when there is no source
def answer(stack, *, question: str, client_id: str) -> dict   # {"answer": str, "sources": [...], "no_source": bool}
# agents/models.py
class BedrockModels:      # implements ModelBackend, boto3 lazy, two tiers + guardrail
class OfflineModels:      # implements ModelBackend, deterministic, no network:
                          # extracts intention/claims from the input text with rules
# agents/runtime.py
class AgentCoreRuntime:   # invoke an agent hosted on AgentCore Runtime (lazy boto3)
def local_invoke(agent_name: str, payload: dict, stack) -> dict   # offline path
```

Invariant: the offline model must be **deterministic** so tests and the replay
bench are reproducible. An agent never validates, never publishes and never
leaves its client: it goes through the same tools, therefore the same policies.

Requirements: 13.3, EF-VAL-01, EF-GEN-02, EF-ENT-01/02, EF-RES-08. Tests:
`test_agents_writer.py`, `test_agents_guardian.py`, `test_agents_answerer.py`.

---

## 9. replay

```python
# replay/problems.py
@dataclass(frozen=True) class Problem: id, family, statement, expected_steps, traps, solution_key
def load(path: Path | None = None) -> tuple[Problem, ...]      # data/fixtures/replay_problems.json
# replay/bench.py
@dataclass(frozen=True) class ArmResult: attempts, solved_at_least_once, solved_at_least_twice,
                                          solved_at_least_three_times, mean_steps, mean_tokens
@dataclass(frozen=True) class BenchReport: problem_count, attempts, without_memory, with_memory,
                                           net_gain, repeated_traps, rows
def run(stack, *, problems: int = 24, attempts: int = 4, seed: int = 7) -> BenchReport
def attempt(stack, problem: Problem, *, memory: bool, index: int) -> dict
# replay/report.py
def as_markdown(report: BenchReport) -> str
def as_json(report: BenchReport) -> dict
```

Invariant: the bench is deterministic (fixed seed) and offline; a trap marked
"does not work" in memory must not be retried — `repeated_traps` is 0 with
memory (chapter 5). No per-person metric anywhere.

Requirements: chapter 5 and 16, EF-PIL-03. Tests: `test_replay_bench.py` (R20).

---

## 10. api

```python
# api/app.py
def create_app(stack=None) -> "FastAPI"     # fastapi imported lazily inside
# api/auth.py
def principal_of(request) -> Principal      # Cognito JWT in AWS, demo tokens offline
def require(request, role: PrincipalRole) -> Principal
# api/routes_portal.py     GET /episodes /fiches /fiches/{id} /validation /search /ask
#                          /metrics /replay /journal /admin/*  (15.3)
# api/routes_capture.py    POST /capture/ticket /capture/session /capture/merge (signed)
# api/routes_tools.py      POST /tools/{name}  -> goes through the gateway path, same policies
# api/guichet.py           the demo ticketing tool: open, comment, close -> auto capture,
#                          suggested fiches on open (EF-RES-06), one click feedback (EF-RET-03)
# api/metrics.py           EF-PIL-01..04: everything computed by code
# api/signature.py         HMAC verification of the capture endpoints
```

Invariant: every write from the portal goes through the tool layer with the
caller's principal, so the same policies apply (15.3). A missing Sillage must
never break Guichet (R21): endpoints return `503` with a note, the ticket tool
keeps working.

Tests: `test_api_portal.py`, `test_api_capture.py`, `test_api_guichet.py`,
using `fastapi.testclient` when available and skipping cleanly otherwise.

---

## 11. demo (the wiring every test and the smoke share)

```python
# demo/stack.py
@dataclass(frozen=True)
class Stack:
    settings: Settings
    clock: Clock
    memory: MemoryBackend
    catalog: CatalogStore
    blobs: BlobStore
    journal: Journal
    policy: PolicyEngine
    model: ModelBackend
    guardrails: GuardrailFilter | None
    registry: Registry
    notifier: Notifier
    ingest: IngestService
def build(*, seed: bool = True, settings: Settings | None = None, clients=("a","b")) -> Stack
# demo/seed.py
def seed_stack(stack) -> dict          # counts of what was loaded
def load_history(stack, path=None) -> int   # data/generated/history.json (60-100 episodes, 8 families)
# demo/scenario.py
def run() -> list[str]                 # the narrated demo, printed line by line (annexes B and E)
# demo/tryit.py
def ask(problem: str, role: str, client_id: str) -> dict   # the public "try it" page backend,
                                                           # shows what is refused and why
```

Invariant: `build()` must work with **zero dependency installed** and in under
two seconds; it is called by the smoke, the tests, the CLI and the portal.

---

## 12. portal

Static files only (`portal/index.html`, `portal/app.js`, `portal/styles.css`,
`portal/try.html`): no build step, no framework, no CDN. It talks to the API of
§10. Screens: episode feed, validation queue, fiche detail with sources and
success rate, search, natural language question, metrics dashboard, journal,
and the public try page where a judge picks a role and a client and sees both
what the memory returns **and what it refuses**. French UI, English comments.
Tests check the served files and the API contract they rely on.

---

## 13. infra

`infra/cdk/app.py` plus one stack per concern: `memory_stack.py` (annex A),
`capture_stack.py`, `gateway_stack.py` (MCP target `Savoir`, the 11 tools),
`policy_stack.py` (annex D, `LOG_ONLY` then `ENFORCE`), `runtime_stack.py`
(four Strands agents), `identity_stack.py`, `catalog_stack.py` (DynamoDB),
`portal_stack.py` (CloudFront/S3/API GW/Cognito), `registry_stack.py`,
`observability_stack.py`, `budget_stack.py` (AWS Budgets alarm, day one).
`cdk` imported lazily; `tests/test_infra_shapes.py` asserts the stack classes
exist and that the annex A/B/C/D JSON shapes are the ones the code sends, with
stub modules when `aws_cdk` is not installed. `infra/README.md` documents the
install in a clean account (R22).

---

## 14. data

`data/generator/` builds the synthetic history: 8 fault families (specification
19.1), two fictional clients plus the internal perimeter, 60–100 episodes,
each with a failed lead and a working one. `data/fixtures/` holds the test
sets: `leak_probes.json` (40), `planted_secrets.json` (30),
`poisoned_tickets.json` (20), `generalisation_set.json` (30),
`replay_problems.json` (24 × 4 attempts), `retrieval_labels.json` (50),
`recette.json` (R1–R22 as data). Generated files go to `data/generated/`
(git-ignored) except the fixtures, which are committed. The generator is
deterministic (fixed seed) and prints a manifest. Every file says clearly that
the data is synthetic.

---

## 15. Pull request procedure

1. Work in your own worktree on a branch named `<type>/<module>-<subject>`
   (`feat/capture-redact-secrets`). Never push to `main`: it is protected, the
   Mergify queue merges for you.
2. `make lint && make limits && make test && make smoke` must pass locally.
3. Open the PR with a body containing what changed, the requirements covered
   (`EF-…`, `R…`) and anything you had to decide that the contract did not say.
4. Checks `ci` and `ci-budget` green → the queue merges (rebase). A conflict is
   resolved by the InferHub conflict agent; do not force-push over it.
5. Keep the PR to one module. A PR touching two modules blocks everybody.
