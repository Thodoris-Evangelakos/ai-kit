from ai_kit.verification import FailureClass, classify_check


def test_failure_vocabulary_keeps_unclassified_and_infra_explicit() -> None:
    assert classify_check("acceptance") is FailureClass.ACCEPTANCE
    assert classify_check("runtime_errors") is FailureClass.OBSERVABILITY
    assert classify_check("verify") is FailureClass.UNKNOWN
    assert classify_check("verify", "Landlock unavailable") is FailureClass.INFRA
    assert {item.value for item in FailureClass} >= {
        "FLAKY",
        "HARNESS",
        "BASELINE_REGRESSION",
    }
