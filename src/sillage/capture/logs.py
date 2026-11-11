"""Long journals reduced to the useful lines (13.1).

An incident journal is thousands of lines of noise around a handful of errors.
AgentCore Memory replays what it is given, so a raw journal both costs tokens
and drowns the episode. The specification asks for a fast model to keep the
useful lines, and for a **deterministic fallback without a model** so the
capture never depends on a model being reachable (ENF-DISP-02) and never
produces a different trace on two machines.

No number here comes from a model: the kept lines and the omitted count are
computed by the code (ENF-IA-01).
"""

from __future__ import annotations

from sillage.kernel.protocols import ModelBackend

#: Words that make a journal line worth keeping.
ERROR_WORDS = (
    "error",
    "erreur",
    "fatal",
    "exception",
    "traceback",
    "timeout",
    "refused",
    "denied",
    "unable",
    "échec",
    "panne",
    "critical",
    "too many connections",
    "out of memory",
)

#: Lines of context kept at the head and at the tail of a journal.
CONTEXT_LINES = 2

#: System prompt of the fast model, in French like every business text.
LOG_SUMMARY_SYSTEM = (
    "Tu réduis un journal d'incident aux lignes utiles : erreurs, causes et "
    "conséquences. Tu ne réponds qu'avec les lignes gardées, en français, sans "
    "préambule et sans commentaire."
)


def split_lines(text: str) -> list[str]:
    """The non empty lines of a journal, stripped of their surroundings."""
    return [line.strip() for line in (text or "").splitlines() if line.strip()]


def is_error_line(line: str) -> bool:
    """True when a line looks like something worth keeping."""
    lowered = line.lower()
    return any(word in lowered for word in ERROR_WORDS)


def summarise_logs(text: str, model: ModelBackend | None = None, max_lines: int = 40) -> str:
    """Reduce a journal to at most ``max_lines`` useful lines.

    The model is tried first, then the deterministic fallback: a model that is
    absent, slow or failing must never cost the trace (ENF-DISP-02).
    """
    lines = split_lines(text)
    if not lines:
        return ""
    if len(lines) <= max_lines:
        return "\n".join(lines)
    return model_summary(lines, model, max_lines) or deterministic_summary(lines, max_lines)


def model_summary(lines: list[str], model: ModelBackend | None, max_lines: int) -> str:
    """Ask a fast model for the useful lines; empty string when unavailable."""
    if model is None:
        return ""
    prompt = "Journal d'incident :\n" + "\n".join(lines)
    prompt += f"\n\nGarde au plus {max_lines} lignes utiles, dans l'ordre."
    try:
        answer = model.complete(prompt=prompt, system=LOG_SUMMARY_SYSTEM)
    except Exception:  # a failing model degrades to the deterministic summary
        return ""
    return "\n".join(split_lines(answer)[:max_lines])


def deterministic_summary(lines: list[str], max_lines: int) -> str:
    """The fallback without a model: context, errors, context, and the count."""
    budget = max(max_lines - 1, 1)
    kept = useful_lines(lines, budget)
    omitted = len(lines) - len(kept)
    if omitted <= 0:
        return "\n".join(kept)
    return "\n".join([*kept, f"[... {omitted} ligne(s) de journal omise(s) ...]"])


def useful_lines(lines: list[str], budget: int) -> list[str]:
    """Head, error lines and tail, cut down to ``budget`` lines."""
    head = lines[:CONTEXT_LINES]
    tail = lines[-CONTEXT_LINES:] if len(lines) > CONTEXT_LINES * 2 else []
    middle = lines[CONTEXT_LINES : len(lines) - len(tail)] if tail else lines[CONTEXT_LINES:]
    errors = [line for line in middle if is_error_line(line)]
    room = max(budget - len(head) - len(tail), 0)
    return [*head, *errors[:room], *tail][:budget]


__all__ = [
    "CONTEXT_LINES",
    "ERROR_WORDS",
    "LOG_SUMMARY_SYSTEM",
    "deterministic_summary",
    "is_error_line",
    "model_summary",
    "split_lines",
    "summarise_logs",
    "useful_lines",
]
