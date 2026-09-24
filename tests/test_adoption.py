"""Safe brownfield adoption against disposable, real Git repositories."""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import tomllib
from pathlib import Path

import pytest

from ai_kit.adoption import (
    CheckResult,
    adopt_safe,
    compare_baseline,
    mark_managed,
    recheck_baseline,
)
from ai_kit.system import UnsafeWriteError


def git(root: Path, *args: str) -> str:
    env = dict(os.environ)
    env.update(
        GIT_AUTHOR_NAME="AI Kit Test",
        GIT_AUTHOR_EMAIL="ai-kit-test@example.com",
        GIT_COMMITTER_NAME="AI Kit Test",
        GIT_COMMITTER_EMAIL="ai-kit-test@example.com",
        GIT_CONFIG_GLOBAL=os.devnull,
        GIT_CONFIG_SYSTEM=os.devnull,
    )
    return subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True,
        text=True,
        check=True,
        env=env,
    ).stdout.strip()


def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init", "-b", "main")
    return root


def commit(root: Path) -> str:
    git(root, "add", ".")
    git(root, "commit", "-m", "source")
    return git(root, "rev-parse", "HEAD")


def test_failed_python_check_is_recorded_without_changing_source(tmp_path: Path) -> None:
    root = repo(tmp_path)
    (root / "README.md").write_text("Run `pytest` to check this project.\n", encoding="utf-8")
    (root / "pyproject.toml").write_text('[project]\nname = "example"\n', encoding="utf-8")
    (root / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    tests = root / "tests"
    tests.mkdir()
    (tests / "test_app.py").write_text(
        "from app import VALUE\n\ndef test_value():\n    assert VALUE == 2\n"
    )
    head = commit(root)
    (root / "app.py").write_text("VALUE = 1  # local human edit\n", encoding="utf-8")
    before = git(root, "diff", "--binary", "HEAD", "--", "app.py", "tests/test_app.py")

    result = adopt_safe(root)

    assert result.source_commit == head
    assert result.status == "fail"
    assert result.checks["unit"].status == "fail"
    assert result.checks["unit"].exit_code == 1
    assert "assert 1 == 2" in result.checks["unit"].output
    assert git(root, "diff", "--binary", "HEAD", "--", "app.py", "tests/test_app.py") == before
    assert (root / "app.py").read_text() == "VALUE = 1  # local human edit\n"

    baseline = json.loads(result.baseline_path.read_text())
    custody = tomllib.loads(result.custody_path.read_text())
    inventory = result.inventory_path.read_text()
    assert baseline["commit"] == head
    assert baseline["source"]["dirty"] is True
    assert baseline["checks"]["unit"]["status"] == "fail"
    assert custody["custody"]["source_commit"] == head
    assert custody["managed"] is False
    assert custody["baseline"]["commit"] == head
    assert {"CLAIMED", "OBSERVED", "INFERRED", "UNKNOWN"} <= {
        fact.state for fact in result.evidence
    }
    assert all(state in inventory for state in ("CLAIMED", "OBSERVED", "INFERRED", "UNKNOWN"))


def test_dev_check_runs_in_copy_and_existing_artifacts_are_preserved(tmp_path: Path) -> None:
    root = repo(tmp_path)
    (root / "README.md").write_text("Run ./dev verify.\n")
    app = root / "app.txt"
    app.write_text("original\n")
    dev = root / "dev"
    dev.write_text('#!/bin/sh\nprintf "modified\\n" > app.txt\nexit 3\n')
    dev.chmod(0o755)
    commit(root)

    result = adopt_safe(root)

    assert result.status == "fail"
    assert result.checks["verify"].exit_code == 3
    assert any(
        fact.state == "CLAIMED" and "./dev verify" in fact.statement for fact in result.evidence
    )
    assert app.read_text() == "original\n"
    assert git(root, "diff", "--exit-code") == ""
    with pytest.raises(UnsafeWriteError):
        adopt_safe(root)
    assert json.loads(result.baseline_path.read_text())["checks"]["verify"]["exit_code"] == 3
    mark_managed(result)
    assert tomllib.loads(result.custody_path.read_text())["managed"] is True


def test_missing_check_stays_unknown(tmp_path: Path) -> None:
    root = repo(tmp_path)
    (root / "README.md").write_text("A project without a test command.\n")
    commit(root)

    result = adopt_safe(root)

    assert result.status == "unknown"
    assert result.checks["verify"].status == "unknown"
    assert result.checks["verify"].exit_code is None
    assert json.loads(result.baseline_path.read_text())["status"] == "unknown"


@pytest.mark.skipif(shutil.which("node") is None, reason="Node is unavailable")
def test_node_builtin_test_failure_is_an_honest_baseline(tmp_path: Path) -> None:
    root = repo(tmp_path)
    (root / "package.json").write_text('{"scripts":{"test":"node --test"}}\n')
    (root / "app.js").write_text("module.exports = 1;\n")
    (root / "app.test.js").write_text(
        "const test = require('node:test');\n"
        "const assert = require('node:assert/strict');\n"
        "test('approved value', () => assert.equal(require('./app'), 2));\n"
    )
    commit(root)

    result = adopt_safe(root)

    assert result.status == "fail"
    assert result.checks["unit"].command == ("node", "--test")
    assert result.checks["unit"].exit_code != 0
    assert (root / "app.js").read_text() == "module.exports = 1;\n"


def test_skipped_python_test_cannot_make_a_green_baseline(tmp_path: Path) -> None:
    root = repo(tmp_path)
    (root / "pyproject.toml").write_text('[project]\nname = "example"\n')
    tests = root / "tests"
    tests.mkdir()
    (tests / "test_app.py").write_text(
        'import pytest\n\n@pytest.mark.skip(reason="unimplemented")\n'
        "def test_app():\n    assert False\n"
    )
    commit(root)

    result = adopt_safe(root)

    assert result.status == "unknown"
    assert result.checks["unit"].status == "unknown"
    assert result.checks["unit"].exit_code == 0
    assert result.checks["unit"].counts["skipped"] == 1


def test_external_symlink_prevents_execution(tmp_path: Path) -> None:
    root = repo(tmp_path)
    source = root / "app.txt"
    source.write_text("original\n")
    (root / "linked.txt").symlink_to(source)
    dev = root / "dev"
    dev.write_text('#!/bin/sh\nprintf "modified\\n" > linked.txt\n')
    dev.chmod(0o755)
    commit(root)

    result = adopt_safe(root)

    assert result.status == "unknown"
    assert result.checks["verify"].exit_code is None
    assert "external symlink" in result.checks["verify"].reason
    assert source.read_text() == "original\n"


def test_dev_output_with_skipped_tests_is_not_pass(tmp_path: Path) -> None:
    root = repo(tmp_path)
    dev = root / "dev"
    dev.write_text('#!/bin/sh\nprintf "1 skipped in 0.01s\\n"\n')
    dev.chmod(0o755)
    commit(root)

    result = adopt_safe(root)

    assert result.checks["verify"].exit_code == 0
    assert result.checks["verify"].status == "unknown"
    assert result.status == "unknown"


def test_application_permission_failure_remains_a_failure(tmp_path: Path) -> None:
    root = repo(tmp_path)
    dev = root / "dev"
    dev.write_text(
        '#!/bin/sh\necho "Permission denied: application authorization failed"\nexit 7\n'
    )
    dev.chmod(0o755)
    commit(root)

    result = adopt_safe(root)

    assert result.status == "fail"
    assert result.checks["verify"].status == "fail"
    assert result.checks["verify"].exit_code == 7
    assert json.loads(result.baseline_path.read_text())["status"] == "fail"


def test_landlock_blocks_absolute_write_to_source(tmp_path: Path) -> None:
    root = repo(tmp_path)
    source = root / "app.txt"
    source.write_text("original\n")
    dev = root / "dev"
    dev.write_text(f'#!/bin/sh\nprintf "modified\\n" > {shlex.quote(str(source))}\n')
    dev.chmod(0o755)
    commit(root)

    result = adopt_safe(root)

    assert source.read_text() == "original\n"
    assert result.checks["verify"].exit_code is not None
    assert result.checks["verify"].exit_code != 0
    assert result.checks["verify"].status == "fail"
    assert result.checks["verify"].reason.startswith("Exited with code")


def test_landlock_failure_never_runs_check_unconfined(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def disabled(*_args: object) -> None:
        raise OSError("disabled")

    for failure in ("preflight", "enforcement"):
        base = tmp_path / failure
        base.mkdir()
        root = repo(base)
        source = root / "app.txt"
        source.write_text("original\n")
        dev = root / "dev"
        dev.write_text(f'#!/bin/sh\nprintf "modified\\n" > {shlex.quote(str(source))}\n')
        dev.chmod(0o755)
        commit(root)
        with monkeypatch.context() as patch:
            if failure == "preflight":
                patch.setattr("ai_kit.adoption._landlock_ruleset", disabled)
            else:
                patch.setattr("ai_kit.adoption._enforce_landlock", disabled)
            result = adopt_safe(root)
        assert result.status == "unknown"
        assert result.checks["verify"].exit_code is None
        assert source.read_text() == "original\n"


def test_recheck_baseline_uses_recorded_command_and_confinement(tmp_path: Path) -> None:
    root = repo(tmp_path)
    source = root / "app.txt"
    source.write_text("original\n")
    dev = root / "dev"
    dev.write_text("#!/bin/sh\nexit 0\n")
    dev.chmod(0o755)
    commit(root)
    assert adopt_safe(root).status == "pass"

    assert recheck_baseline(root).status == "pass"
    dev.write_text(f'#!/bin/sh\nprintf "modified\\n" > {shlex.quote(str(source))}\n')
    regression = recheck_baseline(root)
    assert regression.status == "fail"
    assert "verify: pass -> fail" in regression.regressions
    assert source.read_text() == "original\n"
    dev.unlink()
    missing = recheck_baseline(root)
    assert missing.status == "unknown"
    assert any("unavailable" in item for item in missing.unresolved)


def test_managed_repo_is_not_adopted_again(tmp_path: Path) -> None:
    root = repo(tmp_path)
    (root / "README.md").write_text("existing\n")
    commit(root)
    (root / "ai-kit.lock").write_text("human managed file\n")

    with pytest.raises(ValueError, match="already has AI Kit"):
        adopt_safe(root)
    assert not (root / ".ai").exists()


def test_baseline_comparison_reports_regression_and_unresolved_evidence() -> None:
    baseline = {
        "unit": CheckResult("fail", ("pytest",), 1, "", "failed", {"failed": 1, "passed": 2}),
        "lint": CheckResult("pass", ("ruff",), 0, "", "passed"),
    }
    current = {
        "unit": CheckResult("fail", ("pytest",), 1, "", "failed", {"failed": 2, "passed": 1}),
        "lint": CheckResult("unknown", ("ruff",), None, "", "timed out"),
    }

    comparison = compare_baseline(baseline, current)

    assert comparison.status == "fail"
    assert "unit: failed increased" in comparison.regressions
    assert "unit: passed decreased" in comparison.regressions
    assert "lint: current result unknown" in comparison.unresolved
    assert compare_baseline(baseline, {}).status == "unknown"
    assert compare_baseline({}, current).status == "fail"
    assert compare_baseline({}, {"lint": baseline["lint"]}).status == "unknown"
    extra_unknown = compare_baseline(
        {"lint": baseline["lint"]},
        {"lint": baseline["lint"], "acceptance": current["lint"]},
    )
    assert extra_unknown.status == "unknown"
    assert "acceptance: current result unknown" in extra_unknown.unresolved
    old_failure = CheckResult("fail", ("./dev", "verify"), 7, "old error", "failed")
    changed_failure = CheckResult("fail", ("./dev", "verify"), 7, "new error", "failed")
    assert (
        "verify: unquantified failure changed"
        in compare_baseline({"verify": old_failure}, {"verify": changed_failure}).unresolved
    )
    assert not compare_baseline({"verify": old_failure}, {"verify": old_failure}).unresolved
