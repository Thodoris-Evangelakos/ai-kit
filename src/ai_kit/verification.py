"""Small, explicit vocabulary for verification failures."""

from enum import StrEnum


class FailureClass(StrEnum):
    STATIC = "STATIC"
    UNIT = "UNIT"
    CONTRACT = "CONTRACT"
    INTEGRATION = "INTEGRATION"
    ACCEPTANCE = "ACCEPTANCE"
    OBSERVABILITY = "OBSERVABILITY"
    INFRA = "INFRA"
    FLAKY = "FLAKY"
    HARNESS = "HARNESS"
    BASELINE_REGRESSION = "BASELINE_REGRESSION"
    UNKNOWN = "UNKNOWN"


def classify_check(name: str, reason: str = "") -> FailureClass:
    if "landlock" in reason.lower() or "sandbox" in reason.lower():
        return FailureClass.INFRA
    return {
        "lint": FailureClass.STATIC,
        "static": FailureClass.STATIC,
        "unit": FailureClass.UNIT,
        "contract": FailureClass.CONTRACT,
        "integration": FailureClass.INTEGRATION,
        "acceptance": FailureClass.ACCEPTANCE,
        "runtime_errors": FailureClass.OBSERVABILITY,
    }.get(name, FailureClass.UNKNOWN)
