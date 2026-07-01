from __future__ import annotations


class GeoAIError(RuntimeError):
    error_type = "permanent"


class TransientError(GeoAIError):
    error_type = "transient"


class PlanRecoverableError(GeoAIError):
    error_type = "plan_recoverable"


class PermanentError(GeoAIError):
    error_type = "permanent"


class BudgetExceededError(PermanentError):
    error_type = "budget_exceeded"


def classify_error(exc: BaseException | str) -> str:
    if isinstance(exc, GeoAIError):
        return exc.error_type
    text = str(exc).lower()
    if any(token in text for token in ("timeout", "timed out", "429", "rate limit", "503")):
        return "transient"
    if any(token in text for token in ("missing required", "schema", "unknown param")):
        return "plan_recoverable"
    return "permanent"
