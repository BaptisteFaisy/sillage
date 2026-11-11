"""Settings and namespace templates: every threshold of the specification.

Nothing here is decided by a model (ENF-IA-01). The numbers are the ones of
chapters 10.2, 13.5 and annexes A/C; they are read from the environment so a
demo can tighten them without a code change.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

#: Namespace templates of the episodic strategy (specification annex A).
EPISODE_NAMESPACE = "/strategy/{memoryStrategyId}/actor/{actorId}/session/{sessionId}/"
REFLECTION_NAMESPACE = "/strategy/{memoryStrategyId}/actor/{actorId}/"
FICHE_NAMESPACE = "/fiches/{scope}/"
COMMON_NAMESPACE = "/commun/"

_ENV_PREFIX = "SILLAGE_"


def _int(name: str, default: int) -> int:
    raw = os.environ.get(f"{_ENV_PREFIX}{name}")
    try:
        return int(raw) if raw else default
    except ValueError:
        return default


def _float(name: str, default: float) -> float:
    raw = os.environ.get(f"{_ENV_PREFIX}{name}")
    try:
        return float(raw) if raw else default
    except ValueError:
        return default


def _str(name: str, default: str) -> str:
    return os.environ.get(f"{_ENV_PREFIX}{name}", default)


def _bool(name: str, default: bool) -> bool:
    raw = os.environ.get(f"{_ENV_PREFIX}{name}")
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    """One place for the numbers the guards and the scoring depend on."""

    # -- AWS
    region: str = "eu-central-1"
    account_id: str = ""
    memory_name: str = "sillage_memoire"
    memory_description: str = "Memoire partagee de l'equipe : ingenieurs, developpeurs, agents"
    strategy_name: str = "episodes_travail"
    strategy_description: str = "Un episode par travail termine, des lecons par perimetre"
    strategy_id: str = ""  # filled by ``ensure_memory`` at deploy time
    event_expiry_days: int = 90
    record_expiry_days: int = 730
    gateway_name: str = "sillage-gateway"
    tool_target_name: str = "Savoir"  # actions are ``Savoir___<tool>``
    policy_mode: str = "ENFORCE"  # "LOG_ONLY" while tuning (specification 10.1)

    # -- models
    fast_model_id: str = "amazon.nova-lite-v1:0"
    capable_model_id: str = "anthropic.claude-sonnet-4-20250514-v1:0"
    guardrail_id: str = ""
    use_guardrails: bool = True

    # -- trust (chapter 10.2)
    proven_successes: int = 3
    proven_distinct_authors: int = 2
    set_aside_consecutive_failures: int = 2
    stale_days: int = 180
    guardian_period_days: int = 7

    # -- serving (EF-RES-02, ENF-PERF-03)
    max_fiches: int = 3
    max_episodes: int = 2
    max_lessons: int = 3
    max_recall_tokens: int = 1500
    recall_top_k: int = 5
    recall_latency_budget_ms: int = 2000
    tokens_per_char: float = 0.28  # rough estimate, used to stay under budget

    # -- capture
    max_events_per_call: int = 100
    max_event_metadata: int = 15
    max_trace_bytes: int = 512_000
    keep_raw_traces: bool = True

    # -- isolation (chapter 10.4)
    demo_clients: tuple[str, ...] = ("client-a", "client-b")
    internal_scope: str = "interne"
    common_scope: str = "commun"

    # -- behaviour
    offline: bool = True  # True: local backends; False: real AWS
    degrade_on_failure: bool = True  # ENF-DISP-02
    journal_decisions: bool = True
    retention_days: dict[str, int] = field(
        default_factory=lambda: {"trace": 730, "episode": 730, "fiche": 1825, "journal": 365}
    )

    @classmethod
    def from_env(cls) -> Settings:
        """Build settings from ``SILLAGE_*`` environment variables."""
        base = cls()
        return cls(
            region=_str("REGION", base.region),
            account_id=_str("ACCOUNT_ID", base.account_id),
            memory_name=_str("MEMORY_NAME", base.memory_name),
            strategy_id=_str("STRATEGY_ID", base.strategy_id),
            event_expiry_days=_int("EVENT_EXPIRY_DAYS", base.event_expiry_days),
            record_expiry_days=_int("RECORD_EXPIRY_DAYS", base.record_expiry_days),
            policy_mode=_str("POLICY_MODE", base.policy_mode),
            fast_model_id=_str("FAST_MODEL", base.fast_model_id),
            capable_model_id=_str("CAPABLE_MODEL", base.capable_model_id),
            guardrail_id=_str("GUARDRAIL_ID", base.guardrail_id),
            use_guardrails=_bool("USE_GUARDRAILS", base.use_guardrails),
            proven_successes=_int("PROVEN_SUCCESSES", base.proven_successes),
            proven_distinct_authors=_int("PROVEN_DISTINCT_AUTHORS", base.proven_distinct_authors),
            set_aside_consecutive_failures=_int("SET_ASIDE_FAILURES", base.set_aside_consecutive_failures),
            stale_days=_int("STALE_DAYS", base.stale_days),
            max_fiches=_int("MAX_FICHES", base.max_fiches),
            max_episodes=_int("MAX_EPISODES", base.max_episodes),
            max_recall_tokens=_int("MAX_RECALL_TOKENS", base.max_recall_tokens),
            recall_top_k=_int("RECALL_TOP_K", base.recall_top_k),
            offline=_bool("OFFLINE", base.offline),
            degrade_on_failure=_bool("DEGRADE", base.degrade_on_failure),
        )

    def episode_namespace(self, actor_id: str, session_id_: str) -> str:
        return EPISODE_NAMESPACE.format(
            memoryStrategyId=self.strategy_id or self.strategy_name,
            actorId=actor_id,
            sessionId=session_id_,
        )

    def reflection_namespace(self, actor_id: str) -> str:
        return REFLECTION_NAMESPACE.format(
            memoryStrategyId=self.strategy_id or self.strategy_name, actorId=actor_id
        )

    def fiche_namespace(self, scope_label: str) -> str:
        return FICHE_NAMESPACE.format(scope=scope_label)

    def common_namespace(self) -> str:
        return COMMON_NAMESPACE

    def action_name(self, tool: str) -> str:
        """Gateway action name used in the policies (annex D)."""
        return f"{self.tool_target_name}___{tool}"


DEFAULT = Settings()
