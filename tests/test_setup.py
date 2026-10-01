"""Harness-assisted safe setup against real disposable Git repositories."""

from __future__ import annotations

import json
import os
import stat
import subprocess
import tomllib
from pathlib import Path

import pytest

from ai_kit import setup
from ai_kit.project import human_bytes, load_lock, sync_project
from ai_kit.setup import (
    SetupError,
    finalize_setup,
    prepare_setup,
    setup_status,
)

WORKING_DEV = """#!/bin/sh
set -eu
case "${1:-}" in
  setup) exit 0 ;;
  check) true ;;
  verify) "$0" check ;;
  *) exit 2 ;;
esac
"""

FAILING_DEV = """#!/bin/sh
set -eu
echo "inherited failure"
exit 1
"""


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
        ["git", "-C", str(root), *args], capture_output=True, text=True, check=True, env=env
    ).stdout.strip()


def repo(tmp_path: Path, name: str = "repo") -> Path:
    root = tmp_path / name
    root.mkdir()
    git(root, "init", "-b", "main")
    return root


def commit(root: Path) -> str:
    git(root, "add", "-A")
    git(root, "commit", "-qm", "source")
    return git(root, "rev-parse", "HEAD")


def write_dev(root: Path, script: str = WORKING_DEV, mode: int = 0o755) -> None:
    path = root / "dev"
    path.write_text(script)
    os.chmod(path, mode)


def write_report(
    root: Path,
    *,
    checks: dict[str, list[str]] | None = None,
    context: list[str] | None = None,
    baseline: dict[str, list[str]] | None = None,
    unresolved: list[str] | None = None,
) -> None:
    payload = {
        "schema": 1,
        "context": context if context is not None else [],
        "checks": checks if checks is not None else {"verify": ["./dev", "verify"]},
        "baseline_checks": baseline or {},
        "unresolved": unresolved or [],
    }
    path = root / ".ai/setup/report.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n")


def test_prepare_new_repo_is_pending_and_idempotent(tmp_path: Path) -> None:
    root = repo(tmp_path)
    (root / "README.md").write_text("# Example\n")
    write_dev(root)
    commit(root)

    protocol = prepare_setup(root)

    assert protocol == root / ".ai/setup/protocol.md"
    assert protocol.is_file()
    assert setup_status(root) == "pending"
    lock = load_lock(root)
    assert lock.schema == 2
    assert lock.setup is not None and lock.setup["state"] == "pending"
    assert set(lock.blocks) >= {"AGENTS.md", "CLAUDE.md"}
    assert (root / ".ai-local/setup/original").is_dir()
    manifest_before = (root / ".ai-local/setup/manifest.json").read_bytes()

    # Resuming must not re-baseline or restore user work.
    assert prepare_setup(root) == protocol
    assert (root / ".ai-local/setup/manifest.json").read_bytes() == manifest_before
    assert setup_status(root) == "pending"


def test_prepare_preserves_custom_instructions_modes_and_dirty_work(tmp_path: Path) -> None:
    root = repo(tmp_path)
    agents = root / "AGENTS.md"
    agents.write_text("# House rules\n\nKeep it small.\n")
    os.chmod(agents, 0o640)
    claude = root / "CLAUDE.md"
    claude.write_text("# Claude notes\n\nHuman text.\n")
    os.chmod(claude, 0o600)
    intent = root / ".ai/intent/product.md"
    intent.parent.mkdir(parents=True)
    intent.write_text("# Approved requirements\n\nDo not rewrite me.\n")
    (root / "src").mkdir()
    (root / "src/app.py").write_text("VALUE = 1\n")
    (root / "notes.txt").write_text("untracked work\n")
    write_dev(root)
    commit(root)
    (root / "src/app.py").write_text("VALUE = 1  # dirty local edit\n")
    agents_before = agents.read_text()
    claude_before = claude.read_text()

    prepare_setup(root)

    assert human_bytes(agents.read_text(), "AGENTS.md") == agents_before
    assert human_bytes(claude.read_text(), "CLAUDE.md") == claude_before
    assert "@AGENTS.md" in claude.read_text()
    assert stat.S_IMODE(agents.stat().st_mode) == 0o640
    assert stat.S_IMODE(claude.stat().st_mode) == 0o600
    assert intent.read_text() == "# Approved requirements\n\nDo not rewrite me.\n"
    assert (root / "src/app.py").read_text() == "VALUE = 1  # dirty local edit\n"
    assert (root / "notes.txt").read_text() == "untracked work\n"


def test_prepare_rejects_existing_profile_without_lock(tmp_path: Path) -> None:
    root = repo(tmp_path)
    profile = root / ".ai/profile.toml"
    profile.parent.mkdir(parents=True)
    profile.write_text('schema = 1\nbase = "software"\nmodules = ["learning"]\n')
    commit(root)

    with pytest.raises(SetupError, match="without a compatible ai-kit.lock"):
        prepare_setup(root)
    assert profile.read_text() == 'schema = 1\nbase = "software"\nmodules = ["learning"]\n'


def test_prepare_rejects_block_marker_collision(tmp_path: Path) -> None:
    root = repo(tmp_path)
    (root / "AGENTS.md").write_text(
        "<!-- ai-kit:begin -->\none\n<!-- ai-kit:begin -->\ntwo\n<!-- ai-kit:end -->\n"
    )
    write_dev(root)
    commit(root)

    with pytest.raises(SetupError, match="block markers"):
        prepare_setup(root)


def test_prepare_rejects_symlinked_instruction_file(tmp_path: Path) -> None:
    root = repo(tmp_path)
    outside = tmp_path / "outside.md"
    outside.write_text("human\n")
    (root / "AGENTS.md").symlink_to(outside)
    write_dev(root)
    commit(root)

    with pytest.raises(SetupError, match="symlink"):
        prepare_setup(root)


def test_prepare_rejects_human_skill_collision(tmp_path: Path) -> None:
    root = repo(tmp_path)
    skill = root / ".agents/skills/manage-goal/SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("human skill\n")
    write_dev(root)
    commit(root)

    with pytest.raises(SetupError, match="human-owned file"):
        prepare_setup(root)


def test_resume_rejects_tampered_snapshot(tmp_path: Path) -> None:
    root = repo(tmp_path)
    write_dev(root)
    commit(root)
    prepare_setup(root)

    manifest = json.loads((root / ".ai-local/setup/manifest.json").read_text())
    victim = root / ".ai-local/setup/original" / manifest["entries"][0]["path"]
    victim.write_text("tampered\n")

    with pytest.raises(SetupError, match="snapshot identity|captured"):
        prepare_setup(root)


def test_finalize_requires_report(tmp_path: Path) -> None:
    root = repo(tmp_path)
    write_dev(root)
    commit(root)
    prepare_setup(root)

    with pytest.raises(SetupError, match="report is missing"):
        finalize_setup(root)
    assert setup_status(root) == "pending"


def test_finalize_rejects_unresolved_choices(tmp_path: Path) -> None:
    root = repo(tmp_path)
    write_dev(root)
    commit(root)
    prepare_setup(root)
    write_report(root, unresolved=["which CI provider?"])

    with pytest.raises(SetupError, match="unresolved"):
        finalize_setup(root)
    assert setup_status(root) == "pending"


def test_finalize_rejects_missing_required_check(tmp_path: Path) -> None:
    root = repo(tmp_path)
    write_dev(root)
    commit(root)
    prepare_setup(root)
    write_report(root, checks={"unit": ["./dev", "check"]})

    with pytest.raises(SetupError, match="required check missing"):
        finalize_setup(root)


def test_finalize_detects_protected_source_mutation(tmp_path: Path) -> None:
    root = repo(tmp_path)
    (root / "src").mkdir()
    (root / "src/app.py").write_text("VALUE = 1\n")
    write_dev(root)
    commit(root)
    prepare_setup(root)
    write_report(root)
    (root / "src/app.py").write_text("VALUE = 2\n")

    with pytest.raises(SetupError, match="protected content changed: src/app.py"):
        finalize_setup(root)
    assert setup_status(root) == "pending"


def test_finalize_detects_new_source_outside_integration(tmp_path: Path) -> None:
    root = repo(tmp_path)
    write_dev(root)
    commit(root)
    prepare_setup(root)
    write_report(root)
    (root / "extra.py").write_text("print('new')\n")

    with pytest.raises(SetupError, match="new path outside the integration scope: extra.py"):
        finalize_setup(root)


def test_finalize_detects_check_mutating_dirty_source(tmp_path: Path) -> None:
    root = repo(tmp_path)
    (root / "dirty.txt").write_text("value 1\n")
    write_dev(root)
    commit(root)
    (root / "dirty.txt").write_text("value 1  # dirty\n")
    prepare_setup(root)
    write_report(root)

    real = setup._run_argv

    def fake(target: Path, argv: tuple[str, ...], timeout: float):
        if Path(target) == root:
            (root / "dirty.txt").write_text("value 2  # dirty\n")
        return real(target, argv, timeout)

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr("ai_kit.setup._run_argv", fake)
    try:
        with pytest.raises(SetupError, match="dirty.txt"):
            finalize_setup(root)
    finally:
        monkeypatch.undo()


def test_finalize_refuses_without_snapshot(tmp_path: Path) -> None:
    root = repo(tmp_path)
    write_dev(root)
    commit(root)
    prepare_setup(root)
    write_report(root)
    import shutil

    shutil.rmtree(root / ".ai-local/setup/original")

    with pytest.raises(SetupError, match="snapshot"):
        finalize_setup(root)
    assert setup_status(root) == "pending"


def test_finalize_success_clears_pending_and_keeps_blocks(tmp_path: Path) -> None:
    root = repo(tmp_path)
    (root / "AGENTS.md").write_text("# House rules\n")
    (root / "README.md").write_text("# Example\n")
    write_dev(root)
    commit(root)
    prepare_setup(root)
    write_report(root, context=["README.md", "AGENTS.md"])

    finalize_setup(root)

    assert setup_status(root) == "completed"
    lock = load_lock(root)
    assert lock.setup is not None and lock.setup["state"] == "completed"
    assert set(lock.blocks) >= {"AGENTS.md", "CLAUDE.md"}
    assert sync_project(root, check=True).clean
    # Repeated finalization is safe and does not rewrite the snapshot.
    finalize_setup(root)
    assert setup_status(root) == "completed"


def test_webapp_profile_requires_acceptance_and_runtime_errors(tmp_path: Path) -> None:
    root = repo(tmp_path)
    profile = root / ".ai/profile.toml"
    profile.parent.mkdir(parents=True)
    profile.write_text('schema = 1\nbase = "software"\nmodules = ["webapp"]\n')
    write_dev(root)
    commit(root)
    # An existing profile without a lock is a conflict, so initialise then sync.
    from ai_kit.project import init_project, set_profile_modules

    (root / ".ai").rename(tmp_path / "stash")
    init_project(root)
    set_profile_modules(root, ("webapp",))
    prepare_setup(root)
    write_report(root, checks={"verify": ["./dev", "verify"]})

    with pytest.raises(SetupError, match="acceptance"):
        finalize_setup(root)


def test_baseline_custom_discovery_measured_on_original(tmp_path: Path) -> None:
    root = repo(tmp_path)
    write_dev(root)
    commit(root)
    prepare_setup(root)
    command = ["python3", "-c", "import sys; sys.exit(0)"]
    write_report(root, baseline={"unit": command})

    finalize_setup(root)

    baseline = json.loads((root / ".ai/baseline.json").read_text())
    assert baseline["checks"]["unit"]["status"] == "pass"
    assert baseline["checks"]["unit"]["command"] == command
    assert baseline["checks"]["unit"]["discovery"] is True
    assert setup_status(root) == "completed"


def test_inherited_dev_failure_remains_recorded(tmp_path: Path) -> None:
    root = repo(tmp_path)
    write_dev(root, FAILING_DEV)
    commit(root)
    prepare_setup(root)
    write_dev(root, WORKING_DEV)
    write_report(root)

    finalize_setup(root)

    baseline = json.loads((root / ".ai/baseline.json").read_text())
    assert baseline["checks"]["dev"]["status"] == "fail"
    assert baseline["checks"]["dev"]["discovery"] is True


def test_finalize_blocks_on_failing_required_check(tmp_path: Path) -> None:
    root = repo(tmp_path)
    write_dev(root, FAILING_DEV)
    commit(root)
    prepare_setup(root)
    write_report(root)

    with pytest.raises(SetupError, match="verify"):
        finalize_setup(root)
    assert setup_status(root) == "pending"


def test_finalize_blocks_on_missing_context_reference(tmp_path: Path) -> None:
    root = repo(tmp_path)
    write_dev(root)
    commit(root)
    prepare_setup(root)
    write_report(root, context=["docs/missing.md"])

    with pytest.raises(SetupError, match="context reference does not exist"):
        finalize_setup(root)


def test_sync_preserves_pending_metadata_and_blocks(tmp_path: Path) -> None:
    root = repo(tmp_path)
    write_dev(root)
    commit(root)
    prepare_setup(root)
    lock_before = tomllib.loads((root / "ai-kit.lock").read_text())
    assert lock_before["setup"]["state"] == "pending"

    sync_project(root)

    lock_after = tomllib.loads((root / "ai-kit.lock").read_text())
    assert lock_after["setup"] == lock_before["setup"]
    assert lock_after["blocks"] == lock_before["blocks"]
    assert sync_project(root, check=True).clean


def test_setup_status_absent_without_lock(tmp_path: Path) -> None:
    root = repo(tmp_path)
    assert setup_status(root) == "absent"


def test_block_drift_blocks_sync_and_finalize(tmp_path: Path) -> None:
    root = repo(tmp_path)
    write_dev(root)
    commit(root)
    prepare_setup(root)
    agents = root / "AGENTS.md"
    agents.write_text(
        agents.read_text().replace("<!-- ai-kit:end -->", "edited\n<!-- ai-kit:end -->")
    )
    write_report(root)

    result = sync_project(root, check=True)
    assert any("drift" in problem for problem in result.problems)
    with pytest.raises(SetupError, match="protected content changed: AGENTS.md|drift"):
        finalize_setup(root)


def test_prepare_unborn_repository(tmp_path: Path) -> None:
    root = repo(tmp_path)

    prepare_setup(root)

    assert setup_status(root) == "pending"
    lock = load_lock(root)
    assert lock.setup is not None and lock.setup["head"] == ""
    assert (root / ".gitignore").read_text() == ".ai-local/\n"


def test_existing_dev_is_never_silently_replaced(tmp_path: Path) -> None:
    root = repo(tmp_path)
    write_dev(root)
    commit(root)
    before = (root / "dev").read_text()

    prepare_setup(root)

    assert (root / "dev").read_text() == before


def test_internal_symlink_is_captured_as_a_link(tmp_path: Path) -> None:
    root = repo(tmp_path)
    (root / "real.txt").write_text("real\n")
    (root / "link.txt").symlink_to("real.txt")
    write_dev(root)
    commit(root)

    prepare_setup(root)

    manifest = json.loads((root / ".ai-local/setup/manifest.json").read_text())
    entry = next(item for item in manifest["entries"] if item["path"] == "link.txt")
    assert entry["type"] == "symlink"
    assert entry["target"] == "real.txt"
    captured = root / ".ai-local/setup/original/link.txt"
    assert captured.is_symlink() and os.readlink(captured) == "real.txt"
    write_report(root)
    finalize_setup(root)
    assert setup_status(root) == "completed"


def test_untracked_cache_churn_is_excluded_but_tracked_files_are_kept(tmp_path: Path) -> None:
    root = repo(tmp_path)
    cache = root / ".venv"
    cache.mkdir()
    (cache / "keep.txt").write_text("tracked\n")
    write_dev(root)
    commit(root)
    assert git(root, "ls-files", ".venv/keep.txt") == ".venv/keep.txt"

    prepare_setup(root)

    manifest = json.loads((root / ".ai-local/setup/manifest.json").read_text())
    assert ".venv/keep.txt" in {item["path"] for item in manifest["entries"]}
    (cache / "junk.txt").write_text("noise\n")
    write_report(root)
    finalize_setup(root)
    assert setup_status(root) == "completed"


def test_unsupported_preservation_shape_is_rejected(tmp_path: Path) -> None:
    root = repo(tmp_path)
    os.mkfifo(root / "pipe")

    with pytest.raises(SetupError, match="unsupported preservation shape"):
        prepare_setup(root)
