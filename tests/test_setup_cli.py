from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest


def run_cli(root: Path, config: Path, *args: str) -> subprocess.CompletedProcess[str]:
    source = str(Path(__file__).resolve().parents[1] / "src")
    return subprocess.run(
        [sys.executable, "-m", "ai_kit.cli", *args],
        cwd=root,
        env={**os.environ, "PYTHONPATH": source, "XDG_CONFIG_HOME": str(config)},
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=30,
    )


def make_repo(root: Path) -> None:
    subprocess.run(["git", "init", "-q", str(root)], check=True)


def test_harness_cli_persists_user_preference_without_project_changes(tmp_path: Path) -> None:
    root, config = tmp_path / "repo", tmp_path / "config"
    root.mkdir()
    initial = run_cli(root, config, "harness")
    assert initial.returncode == 0, initial.stderr
    assert "codex" in initial.stdout
    assert "Claude Code" in initial.stdout
    assert not config.exists()
    saved = run_cli(root, config, "harness", "opencode")
    assert saved.returncode == 0, saved.stderr
    assert 'setup_harness = "opencode"' in (config / "ai-kit/config.toml").read_text()
    assert list(root.iterdir()) == []
    shown = run_cli(root, config, "harness")
    assert "opencode" in shown.stdout
    invalid = run_cli(root, config, "harness", "unknown")
    assert invalid.returncode == 2
    assert 'setup_harness = "opencode"' in (config / "ai-kit/config.toml").read_text()


def test_setup_noninteractive_launch_refuses_before_repository_writes(tmp_path: Path) -> None:
    root, config = tmp_path / "repo", tmp_path / "config"
    make_repo(root)
    result = run_cli(root, config, "setup")
    assert result.returncode == 1
    assert "--no-launch" in result.stdout + result.stderr
    assert not (root / ".ai").exists()
    assert not (root / ".ai-local").exists()


def test_setup_manual_handoff_and_failed_finalize_remain_pending(tmp_path: Path) -> None:
    root, config = tmp_path / "repo", tmp_path / "config"
    make_repo(root)
    prepared = run_cli(root, config, "setup", "--no-launch")
    assert prepared.returncode == 0, prepared.stdout + prepared.stderr
    assert "pending" in prepared.stdout.lower()
    assert "protocol" in prepared.stdout.lower()
    lock = (root / "ai-kit.lock").read_bytes()
    resumed = run_cli(root, config, "setup", "--no-launch")
    assert resumed.returncode == 0, resumed.stdout + resumed.stderr
    assert (root / "ai-kit.lock").read_bytes() == lock
    failed = run_cli(root, config, "setup", "--finalize")
    assert failed.returncode == 1
    assert "report" in (failed.stdout + failed.stderr).lower()
    assert (root / "ai-kit.lock").read_bytes() == lock


def test_setup_rejects_unknown_harness_and_conflicting_modes_before_writes(tmp_path: Path) -> None:
    root, config = tmp_path / "repo", tmp_path / "config"
    make_repo(root)
    invalid = run_cli(root, config, "setup", "--harness", "unknown", "--no-launch")
    assert invalid.returncode == 2
    assert "unsupported harness" in invalid.stdout + invalid.stderr
    conflict = run_cli(root, config, "setup", "--finalize", "--no-launch")
    assert conflict.returncode == 2
    assert "cannot be combined" in conflict.stdout + conflict.stderr
    assert not (root / ".ai").exists()


def test_completed_setup_does_not_launch_again(tmp_path: Path) -> None:
    from ai_kit.setup import finalize_setup, prepare_setup
    from test_setup import write_dev, write_report

    root, config = tmp_path / "repo", tmp_path / "config"
    make_repo(root)
    write_dev(root)
    prepare_setup(root)
    write_report(root)
    finalize_setup(root)

    result = run_cli(root, config, "setup", "--no-launch")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "already completed" in result.stdout
    assert "pending" not in result.stdout.lower()


@pytest.mark.parametrize("name", ["codex", "codex_ds", "opencode", "claude"])
def test_interactive_setup_runs_harness_and_independent_finalization(
    tmp_path: Path, name: str
) -> None:
    import json

    from ai_kit.harness import ACTIVE_AGENT_MARKERS
    from ai_kit.setup import setup_status
    from test_menu import PtySession, child_env

    root, config, binaries = tmp_path / "repo", tmp_path / "config", tmp_path / "bin"
    make_repo(root)
    binaries.mkdir()
    executable = binaries / name
    executable.write_text(
        f"#!{sys.executable}\n"
        "import json, os, pathlib, sys\n"
        "if '--help' in sys.argv:\n"
        "    print('Usage: [PROMPT] --prompt PROMPT'); sys.exit(0)\n"
        "root = pathlib.Path.cwd()\n"
        "(root / 'dev').write_text('#!/bin/sh\\nset -eu\\nexit 0\\n')\n"
        "(root / 'dev').chmod(0o755)\n"
        "report = {'schema': 1, 'context': [], 'unresolved': [], "
        "'checks': {'verify': ['./dev', 'verify']}, 'baseline_checks': {}}\n"
        "(root / '.ai/setup/report.json').write_text(json.dumps(report))\n"
        "capture = {'args': sys.argv[1:], 'marker': os.environ.get('AI_KIT_SETUP_ACTIVE'), "
        "'model': os.environ.get('AI_KIT_MODEL_PREFERENCE')}\n"
        "(root / '.ai-local/stub-launch.json').write_text(json.dumps(capture))\n"
        "print('Setup harness stub completed.', flush=True)\n"
    )
    executable.chmod(0o755)
    env = child_env(
        XDG_CONFIG_HOME=str(config),
        PATH=str(binaries) + os.pathsep + os.environ["PATH"],
        AI_KIT_MODEL_PREFERENCE="my-existing-model",
    )
    for marker in (*ACTIVE_AGENT_MARKERS, "AI_KIT_SETUP_ACTIVE"):
        env.pop(marker, None)
    session = PtySession(root, "setup", "--harness", name, env=env)
    try:
        session.expect("Setup harness stub completed.")
        session.expect("AI Kit setup verified.")
        session.close()
    finally:
        session.cleanup()
    assert session.proc.returncode == 0
    assert setup_status(root) == "completed"
    capture = json.loads((root / ".ai-local/stub-launch.json").read_text())
    assert capture["marker"] == "1"
    assert capture["model"] == "my-existing-model"
    arguments = capture["args"]
    assert len(arguments) == (2 if name == "opencode" else 1)
    if name == "opencode":
        assert arguments[0] == "--prompt"
    assert ".ai/setup/protocol.md" in arguments[-1]
    assert not config.exists()
    assert session.after is not None
    assert session.after[:4] == session.before[:4]
