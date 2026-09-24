from __future__ import annotations

import json
import os
import shlex
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


def test_greenfield_goal_gate_and_stale_commit(tmp_path: Path) -> None:
    root = tmp_path / "greenfield"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    assert "0.1.0" in cli(root, "--version")
    (root / "app.py").write_text("def value():\n    return 0\n")
    cli(root, "init")
    cli(root, "init")
    cli(root, "sync", "--check")
    cli(root, "doctor")
    commit(root, "Initial project", ".")

    command = f"{shlex.quote(sys.executable)} -B -c 'from app import value; assert value() == 42'"
    cli(
        root,
        "goal",
        "new",
        "value-42",
        "--title",
        "Return the expected value",
        "--intent",
        "A user gets the approved value",
        "--accept",
        "The value is 42 after a fresh process starts",
        "--check",
        f"acceptance={command}",
        "--check",
        f"verify={command}",
    )
    commit(root, "Approve acceptance", ".ai/goals")
    cli(root, "goal", "state", "value-42", "ACTIVE")
    cli(root, "goal", "state", "value-42", "IMPLEMENTED")
    cli(root, "goal", "complete", "value-42", exit_code=1)
    cli(root, "goal", "verify", "value-42", exit_code=1)
    cli(root, "goal", "complete", "value-42", exit_code=1)

    (root / "app.py").write_text("def value():\n    return 42\n")
    commit(root, "Implement approved value", "app.py")
    cli(root, "goal", "state", "value-42", "IMPLEMENTED")
    cli(root, "goal", "verify", "value-42")
    (root / "app.py").write_text("def value():\n    return 13\n")
    commit(root, "Change product after verification", "app.py")
    assert "stale" in cli(root, "goal", "complete", "value-42", exit_code=1)
    (root / "app.py").write_text("def value():\n    return 42\n")
    commit(root, "Restore approved value", "app.py")
    cli(root, "goal", "verify", "value-42")
    cli(root, "goal", "complete", "value-42")
    assert (root / ".ai/goals/accepted/value-42.toml").is_file()
    assert not (root / ".ai/goals/active/value-42.toml").exists()
    commit(root, "Accept verified goal", ".ai/goals")
    cli(root, "doctor")
    (root / "app.py").write_text("def value():\n    return 13\n")
    commit(root, "Regress accepted behavior", "app.py")
    assert "DONE (stale)" in cli(root, "goal", "status")
    cli(root, "doctor", exit_code=1)
    cli(root, "goal", "reopen", "value-42")
    assert (root / ".ai/goals/active/value-42.toml").is_file()


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
    cli(root, "doctor")
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
