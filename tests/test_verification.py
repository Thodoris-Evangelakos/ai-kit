import json
import sys
import time
from pathlib import Path

import pytest

from ai_kit.adoption import adopt_safe
from ai_kit.project import doctor_project, init_project, sync_project
from ai_kit.verification import (
    FailureClass,
    VerificationError,
    classify_check,
    is_evidence_fresh,
    load_policy,
    read_evidence,
    verify_project,
)
from test_cli_flow import cli, commit, git


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


def write_policy(root: Path, commands: dict[str, list[str] | None]) -> Path:
    path = root / ".ai/verification.toml"
    text = f"schema = 1\nrequired = {json.dumps(list(commands))}\n"
    for name, command in commands.items():
        if command is not None:
            text += f"\n[requirements.{json.dumps(name)}]\ncommand = {json.dumps(command)}\n"
    path.write_text(text)
    return path


def python(code: str) -> list[str]:
    return [sys.executable, "-B", "-c", code]


def behavior() -> list[str]:
    return python("from product import VALUE; assert VALUE == 42; print('behavior checked')")


@pytest.fixture
def project(tmp_path: Path) -> Path:
    git(tmp_path, "init", "-q", "-b", "main")
    init_project(tmp_path)
    (tmp_path / "product.py").write_text("VALUE = 42\n")
    write_policy(
        tmp_path,
        {"static": python("import ast; ast.parse(open('product.py').read())"), "tests": behavior()},
    )
    commit(tmp_path, "Project and verification policy", ".")
    return tmp_path


def test_commands_record_evidence_without_changing_product(project: Path) -> None:
    before = git(project, "rev-parse", "HEAD")
    evidence = verify_project(project)
    assert evidence["result"] == "pass"
    assert evidence["required"] == ["static", "tests"]
    assert evidence["repository"] == str(project)
    assert evidence["policy_sha256"] == load_policy(project).digest
    assert evidence["commit"] == before
    assert read_evidence(project) == evidence
    assert is_evidence_fresh(project, evidence)
    log = project / evidence["requirements"]["tests"]["artifacts"][0]
    assert "behavior checked" in log.read_text()
    assert git(project, "status", "--porcelain") == ""
    assert git(project, "rev-parse", "HEAD") == before
    assert not (project / ".ai/goals").exists()
    assert all(check.ok for check in doctor_project(project))


@pytest.mark.parametrize(
    "required,command,expected",
    [
        ("tests", python("raise SystemExit(9)"), "fail"),
        ("tests", ["ai-kit-missing-test-runner"], "unknown"),
        ("formal:bend", None, "unknown"),
        ("unrecognized", None, "unknown"),
        ("browser:playwright", behavior(), "pass"),
        ("custom:behavior", behavior(), "pass"),
    ],
)
def test_capabilities_fail_closed_or_use_explicit_commands(
    project: Path, required: str, command: list[str] | None, expected: str
) -> None:
    write_policy(project, {required: command})
    commit(project, "Configure requirement", ".ai/verification.toml")
    result = verify_project(project)
    assert result["result"] == expected
    assert result["requirements"][required]["status"] == expected
    cli(project, "verify", exit_code=0 if expected == "pass" else 1)


@pytest.mark.parametrize(
    "policy",
    [
        'schema = 2\nrequired = ["tests"]',
        'schema = true\nrequired = ["tests"]',
        "schema = 1\nrequired = []",
        'schema = 1\nrequired = ["tests", "tests"]',
        'schema = 1\nrequired = ["../escape"]',
        "schema = 1\nrequired = [7]",
        'schema = 1\nrequired = ["tests"]\nextra = true',
        'schema = 1\nrequired = ["tests"]\n[requirements.typo]\ncommand = ["true"]',
        'schema = 1\nrequired = ["tests"]\n[requirements.tests]\ncommand = "true"',
        'schema = 1\nrequired = ["tests"]\n[requirements.tests]\ncommand = []',
        'schema = 1\nrequired = ["tests"]\n[requirements.tests]\ntimeout_seconds = 0',
        'schema = 1\nrequired = ["baseline"]\n[requirements.baseline]\ncommand = ["true"]',
    ],
)
def test_invalid_policy_cannot_be_synced_or_verified(project: Path, policy: str) -> None:
    (project / ".ai/verification.toml").write_text(policy)
    with pytest.raises(VerificationError):
        load_policy(project)
    assert sync_project(project, check=True).problems
    assert not all(check.ok for check in doctor_project(project))


@pytest.mark.parametrize("relative", ["product.py", ".ai/verification.toml", ".ai/profile.toml"])
def test_changes_invalidate_saved_evidence(project: Path, relative: str) -> None:
    evidence = verify_project(project)
    path = project / relative
    path.write_text(path.read_text() + "\n# changed\n")
    assert not is_evidence_fresh(project, evidence)
    with pytest.raises(VerificationError, match="uncommitted"):
        verify_project(project)
    commit(project, "Change covered state", relative)
    assert not is_evidence_fresh(project, evidence)
    assert is_evidence_fresh(project, verify_project(project))
    assert not is_evidence_fresh(
        project, {**evidence, "commit": git(project, "rev-parse", "HEAD"), "policy_sha256": "wrong"}
    )


def test_each_run_executes_checks_and_overwrites_latest_result(project: Path) -> None:
    write_policy(
        project,
        {"tests": python("from pathlib import Path; assert not Path('.ai-local/fail').exists()")},
    )
    commit(project, "Check external runtime condition", ".ai/verification.toml")
    first = verify_project(project)
    (project / ".ai-local/fail").touch()
    assert verify_project(project)["result"] == "fail"
    assert not is_evidence_fresh(project, read_evidence(project))
    assert len(list((project / ".ai-local/evidence").glob("run-*/evidence.json"))) == 2
    assert first["result"] == "pass"


def test_check_mutating_product_cannot_produce_passing_evidence(project: Path) -> None:
    write_policy(
        project,
        {
            "tests": python(
                "from pathlib import Path; Path('product.py').write_text('VALUE = 0\\n')"
            )
        },
    )
    commit(project, "Configure mutating check", ".ai/verification.toml")
    evidence = verify_project(project)
    assert evidence["result"] == "unknown"
    assert "changed during verification" in evidence["reason"]
    assert not is_evidence_fresh(project, evidence)


def test_timeout_is_unknown_and_kills_descendants(project: Path) -> None:
    command = python(
        "import subprocess, sys, time; "
        "subprocess.Popen([sys.executable, '-c', "
        '\'import time; time.sleep(2); open("late.txt", "w").write("bad")\']); '
        "time.sleep(20)"
    )
    policy = write_policy(project, {"tests": command})
    policy.write_text(policy.read_text() + "timeout_seconds = 1\n")
    commit(project, "Bound verification time", ".ai/verification.toml")
    result = verify_project(project)
    assert result["result"] == "unknown"
    assert "timed out" in result["requirements"]["tests"]["reason"]
    time.sleep(1.5)
    assert not (project / "late.txt").exists()


def test_recursive_dev_verification_fails_closed(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = Path(__file__).resolve().parents[1] / "src"
    (project / "dev").write_text(f"#!/bin/sh\nexec {sys.executable} -m ai_kit.cli verify\n")
    write_policy(project, {"tests": ["./dev", "verify"]})
    commit(project, "Accidentally recursive policy", ".")
    # The real child CLI inherits the recursion guard, including through shell wrappers.
    monkeypatch.setenv("PYTHONPATH", str(source))
    result = verify_project(project)
    assert result["result"] != "pass"
    log = project / result["requirements"]["tests"]["artifacts"][0]
    assert "recursive verification" in log.read_text()


def test_product_test_cochange_is_advisory(project: Path) -> None:
    (project / "test_product.py").write_text("from product import VALUE\nassert VALUE == 42\n")
    (project / "product.py").write_text("VALUE = 42  # public behavior\n")
    commit(project, "Change behavior and its tests", ".")
    evidence = verify_project(project)
    assert evidence["result"] == "pass"
    assert any("changed together" in warning for warning in evidence["warnings"])


def test_baseline_regression_and_missing_baseline_block_verification(tmp_path: Path) -> None:
    git(tmp_path, "init", "-q", "-b", "main")
    dev = tmp_path / "dev"
    dev.write_text("#!/bin/sh\nexit 0\n")
    dev.chmod(0o755)
    commit(tmp_path, "Inherited check", ".")
    assert adopt_safe(tmp_path).status == "pass"
    init_project(tmp_path, adopted=True)
    write_policy(tmp_path, {"tests": python("assert 2 + 2 == 4"), "baseline": None})
    commit(tmp_path, "Adopt repository", ".")
    assert verify_project(tmp_path)["result"] == "pass"
    dev.write_text("#!/bin/sh\nexit 7\n")
    commit(tmp_path, "Regress inherited check", "dev")
    evidence = verify_project(tmp_path)
    assert evidence["result"] == "fail"
    comparison = evidence["requirements"]["baseline"]["comparison"]
    assert "verify: pass -> fail" in comparison["regressions"]
    assert comparison["checks"]["verify"]["exit_code"] == 7
    dev.write_text(f"#!/bin/sh\nexec {sys.executable} -m ai_kit.cli verify\n")
    commit(tmp_path, "Redirect inherited check into policy by mistake", "dev")
    recursive = verify_project(tmp_path)["requirements"]["baseline"]
    assert recursive["status"] == "fail"
    assert "recursive verification" in recursive["comparison"]["checks"]["verify"]["output"]
    (tmp_path / ".ai/baseline.json").unlink()
    commit(tmp_path, "Remove baseline", ".ai/baseline.json")
    assert verify_project(tmp_path)["result"] == "unknown"
    write_policy(tmp_path, {"tests": python("pass")})
    with pytest.raises(VerificationError, match="must require baseline"):
        load_policy(tmp_path)


def test_unchanged_inherited_failure_is_reported_without_inventing_success(tmp_path: Path) -> None:
    git(tmp_path, "init", "-q", "-b", "main")
    dev = tmp_path / "dev"
    dev.write_text("#!/bin/sh\nexit 7\n")
    dev.chmod(0o755)
    commit(tmp_path, "Known inherited failure", ".")
    assert adopt_safe(tmp_path).status == "fail"
    init_project(tmp_path, adopted=True)
    write_policy(
        tmp_path, {"tests": python("assert 'new behavior'.startswith('new')"), "baseline": None}
    )
    commit(tmp_path, "Require no regression", ".")
    result = verify_project(tmp_path)
    assert result["result"] == "pass"
    assert result["requirements"]["baseline"]["comparison"]["status"] == "fail"
    assert "inherited status: fail" in result["requirements"]["baseline"]["reason"]
    dev.write_text("#!/bin/sh\nexit 8\n")
    commit(tmp_path, "Unquantified changed failure", "dev")
    assert verify_project(tmp_path)["result"] == "unknown"


def test_failure_does_not_skip_other_required_mechanisms(project: Path) -> None:
    write_policy(
        project,
        {
            "tests": python("raise SystemExit(3)"),
            "formal:bend": None,
            "custom:behavior": behavior(),
        },
    )
    commit(project, "Require multiple mechanisms", ".ai/verification.toml")
    result = verify_project(project)
    assert result["result"] == "fail"
    assert {name: item["status"] for name, item in result["requirements"].items()} == {
        "tests": "fail",
        "formal:bend": "unknown",
        "custom:behavior": "pass",
    }


def test_ignored_uncommitted_policy_cannot_describe_a_commit(project: Path) -> None:
    git(project, "rm", "--cached", ".ai/verification.toml")
    with (project / ".gitignore").open("a") as ignored:
        ignored.write(".ai/verification.toml\n")
    commit(project, "Accidentally ignore policy", ".")
    assert git(project, "status", "--porcelain") == ""
    with pytest.raises(VerificationError, match="commit the verification policy"):
        verify_project(project)


def test_evidence_refuses_symlinked_output_directory(project: Path, tmp_path: Path) -> None:
    outside = tmp_path / ".ai-local/outside"
    outside.mkdir()
    (project / ".ai-local/evidence").symlink_to(outside, target_is_directory=True)
    with pytest.raises(VerificationError, match="symlinked evidence"):
        verify_project(project)
    assert not list(outside.iterdir())
