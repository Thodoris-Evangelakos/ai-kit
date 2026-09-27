from __future__ import annotations

import json
import os
import subprocess
import sys
import tomllib
from pathlib import Path


def git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, text=True, check=True
    )
    return result.stdout.strip()


def commit(root: Path, message: str, *paths: str) -> None:
    git(root, "add", *paths)
    subprocess.run(
        [
            "git",
            "-C",
            str(root),
            "-c",
            "user.name=AI Kit Test",
            "-c",
            "user.email=test@example.invalid",
            "commit",
            "-qm",
            message,
        ],
        check=True,
    )


def cli(root: Path, *args: str, exit_code: int = 0) -> str:
    source = str(Path(__file__).resolve().parents[1] / "src")
    result = subprocess.run(
        [sys.executable, "-m", "ai_kit.cli", *args],
        cwd=root,
        env={
            **os.environ,
            "COLUMNS": "120",
            "PYTHONPATH": source + os.pathsep + os.environ.get("PYTHONPATH", ""),
        },
        capture_output=True,
        text=True,
    )
    assert result.returncode == exit_code, (args, result.stdout, result.stderr)
    return result.stdout + result.stderr


def test_greenfield_policy_verification_and_freshness(tmp_path: Path) -> None:
    root = tmp_path / "greenfield"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    assert "0.1.0" in cli(root, "--version")
    assert "No such command" in cli(root, "goal", "status", exit_code=2)
    (root / "app.py").write_text("def value():\n    return 0\n")
    cli(root, "init")
    cli(root, "init")
    cli(root, "sync", "--check")
    assert "no implementation configured" in cli(root, "doctor", exit_code=1)
    policy = root / ".ai/verification.toml"
    command = [sys.executable, "-B", "-c", "from app import value; assert value() == 42"]
    policy.write_text(
        'schema = 1\nrequired = ["tests"]\n[requirements.tests]\ncommand = '
        + json.dumps(command)
        + "\n"
    )
    commit(root, "Initial project and policy", ".")
    cli(root, "doctor")  # Configuration only: the behavioral check still fails.
    assert "FAIL" in cli(root, "verify", exit_code=1)
    cli(root, "verify", "--check", exit_code=1)
    (root / "app.py").write_text("def value():\n    return 42\n")
    assert "uncommitted" in cli(root, "verify", exit_code=1)
    commit(root, "Implement expected behavior", "app.py")
    cli(root, "verify")
    cli(root, "verify", "--check")
    assert git(root, "status", "--porcelain") == ""
    (root / "app.py").write_text("def value():\n    return 13\n")
    commit(root, "Regress behavior", "app.py")
    assert "stale" in cli(root, "verify", "--check", exit_code=1)
    cli(root, "doctor")  # Doctor never consumes or executes verification evidence.
    cli(root, "verify", exit_code=1)
    policy.write_text('schema = 1\nrequired = ["formal:bend"]\n')
    commit(root, "Require unavailable verifier", ".ai/verification.toml")
    assert "UNKNOWN" in cli(root, "verify", exit_code=1)
    cli(root, "doctor", exit_code=1)


def test_safe_adoption_preserves_failing_evidence(tmp_path: Path) -> None:
    root = tmp_path / "brownfield"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    (root / "README.md").write_text("# Legacy project\nRun ./dev verify.\n")
    (root / "app.txt").write_text("existing user data\n")
    dev = root / "dev"
    dev.write_text("#!/bin/sh\nexit 7\n")
    dev.chmod(0o755)
    commit(root, "Legacy source", ".")
    source_head = git(root, "rev-parse", "HEAD")

    cli(root, "adopt", "--safe", exit_code=1)
    assert git(root, "rev-parse", "HEAD") == source_head
    assert (root / "app.txt").read_text() == "existing user data\n"
    assert dev.read_text() == "#!/bin/sh\nexit 7\n"
    baseline = json.loads((root / ".ai/baseline.json").read_text())
    assert baseline["commit"] == source_head
    assert baseline["status"] == "fail"
    assert baseline["checks"]["verify"]["exit_code"] == 7
    assert "CLAIMED" in (root / ".ai/adoption/inventory.md").read_text()
    assert "OBSERVED" in (root / ".ai/adoption/inventory.md").read_text()
    assert tomllib.loads((root / ".ai/CUSTODY.toml").read_text())["managed"] is True
    assert (root / "ai-kit.lock").is_file()
    cli(root, "sync", "--check")
    cli(root, "doctor", exit_code=1)  # New policy still needs project commands.
    assert "No measured baseline regression" in cli(root, "baseline", "compare")
    dev.write_text("#!/bin/sh\nexit 8\n")
    assert "unquantified failure changed" in cli(root, "baseline", "compare", exit_code=1)


def test_safe_adoption_preflights_human_router(tmp_path: Path) -> None:
    root = tmp_path / "existing-router"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    (root / "AGENTS.md").write_text("human instructions\n")
    commit(root, "Existing instructions", "AGENTS.md")

    assert "overwrite user work" in cli(root, "adopt", "--safe", exit_code=1)
    assert (root / "AGENTS.md").read_text() == "human instructions\n"
    assert not (root / ".ai").exists()
