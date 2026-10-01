from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from ai_kit.setup import SetupError, finalize_setup, prepare_setup, setup_status
from test_setup import commit, git, repo, write_dev, write_report


def test_unknown_discovered_baseline_cannot_finalize(tmp_path: Path) -> None:
    root = repo(tmp_path)
    write_dev(root)
    commit(root)
    prepare_setup(root)
    write_report(root, baseline={"additional": [sys.executable, "-c", "print('1 skipped')"]})

    with pytest.raises(SetupError, match="unknown|skipped|baseline"):
        finalize_setup(root)
    assert setup_status(root) == "pending"


def test_converted_legacy_router_mode_remains_protected(tmp_path: Path) -> None:
    from ai_kit.project import init_project

    root = repo(tmp_path)
    init_project(root)
    write_dev(root)
    commit(root)
    prepare_setup(root)
    write_report(root)
    path = root / "AGENTS.md"
    path.chmod(path.stat().st_mode ^ 0o100)

    with pytest.raises(SetupError, match="mode|AGENTS"):
        finalize_setup(root)
    assert setup_status(root) == "pending"


def test_harness_cannot_finalize_by_editing_lock_state(tmp_path: Path) -> None:
    root = repo(tmp_path)
    write_dev(root)
    commit(root)
    prepare_setup(root)
    write_report(root)
    lock = root / "ai-kit.lock"
    lock.write_text(lock.read_text().replace('state = "pending"', 'state = "completed"'))

    with pytest.raises(SetupError, match="evidence|pending|completed"):
        finalize_setup(root)
    assert not (root / ".ai-local/setup/evidence.json").exists()


def test_instruction_blocks_route_fresh_agents_to_pending_setup(tmp_path: Path) -> None:
    root = repo(tmp_path)
    prepare_setup(root)

    instructions = (root / "AGENTS.md").read_text()
    assert "ai-kit.lock" in instructions
    assert ".ai/setup/protocol.md" in instructions
    assert "pending" in instructions


def test_snapshot_git_copy_failure_cannot_be_treated_as_an_original_repo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import ai_kit.setup as setup

    root = repo(tmp_path)
    write_dev(root)
    commit(root)
    run_git = setup.run_git

    def fail_clone(args: list[str], **kwargs):
        if args[0] == "clone":
            return subprocess.CompletedProcess(args, 1, "", "clone unavailable")
        return run_git(args, **kwargs)

    monkeypatch.setattr(setup, "run_git", fail_clone)
    with pytest.raises(SetupError, match="Git|snapshot"):
        prepare_setup(root)
    assert not (root / "AGENTS.md").exists()


def test_snapshot_git_head_is_validated_before_finalization(tmp_path: Path) -> None:
    root = repo(tmp_path)
    write_dev(root)
    commit(root)
    prepare_setup(root)
    write_report(root)
    (root / ".ai-local/setup/original/.git/HEAD").write_text("ref: refs/heads/missing\n")

    with pytest.raises(SetupError, match="HEAD|snapshot"):
        finalize_setup(root)


@pytest.mark.parametrize("committed", [False, True])
def test_snapshot_preserves_staged_and_dirty_work(tmp_path: Path, committed: bool) -> None:
    root = repo(tmp_path)
    write_dev(root)
    source = root / "app.py"
    source.write_text("VALUE = 1\n")
    if committed:
        commit(root)
        git(root, "checkout", "--detach")
    source.write_text("VALUE = 2\n")
    git(root, "add", ".")
    source.write_text("VALUE = 3\n")
    original_index = git(root, "ls-files", "--stage")

    prepare_setup(root)
    snapshot = root / ".ai-local/setup/original"
    assert git(snapshot, "ls-files", "--stage") == original_index
    assert (snapshot / "app.py").read_bytes() == source.read_bytes()
    write_report(root)
    finalize_setup(root)
    assert git(root, "ls-files", "--stage") == original_index
    assert source.read_text() == "VALUE = 3\n"


@pytest.mark.parametrize("mode", [0o700, 0o755])
def test_existing_executable_dev_permissions_are_preserved(tmp_path: Path, mode: int) -> None:
    root = repo(tmp_path)
    write_dev(root, mode=mode)
    commit(root)
    prepare_setup(root)
    write_report(root)
    (root / "dev").chmod(0o777)

    with pytest.raises(SetupError, match="mode|permissions"):
        finalize_setup(root)
    assert setup_status(root) == "pending"


def test_nonexecutable_dev_may_gain_execute_permission(tmp_path: Path) -> None:
    root = repo(tmp_path)
    write_dev(root, mode=0o640)
    prepare_setup(root)
    (root / "dev").chmod(0o740)
    write_report(root)

    finalize_setup(root)
    assert setup_status(root) == "completed"


@pytest.mark.parametrize("resume", [False, True])
def test_integration_rejects_symlinked_directory_targets(tmp_path: Path, resume: bool) -> None:
    root = repo(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    if resume:
        prepare_setup(root)
        directory = root / ".ai/current"
        directory.rename(root / ".ai-local/current-preserved")
    else:
        (root / ".ai").mkdir()
        directory = root / ".ai/goals"
    directory.symlink_to(outside, target_is_directory=True)

    with pytest.raises(SetupError, match="symlink"):
        prepare_setup(root)
    assert list(outside.iterdir()) == []


def test_split_index_snapshot_is_self_contained(tmp_path: Path) -> None:
    root = repo(tmp_path)
    write_dev(root)
    source = root / "app.py"
    source.write_text("VALUE = 1\n")
    commit(root)
    source.write_text("VALUE = 2\n")
    git(root, "add", ".")
    git(root, "update-index", "--split-index")
    original_index = git(root, "ls-files", "--stage")

    prepare_setup(root)
    snapshot = root / ".ai-local/setup/original"
    assert git(snapshot, "ls-files", "--stage") == original_index
    write_report(root)
    finalize_setup(root)
    assert setup_status(root) == "completed"
    assert git(root, "ls-files", "--stage") == original_index


def test_skipped_checks_cannot_hide_behind_truncated_logs(tmp_path: Path) -> None:
    root = repo(tmp_path)
    write_dev(
        root,
        f"#!{sys.executable}\nprint('1 passed, 1 skipped in 0.01s')\nprint('x' * 5000)\n",
    )
    commit(root)
    prepare_setup(root)
    write_report(root)

    with pytest.raises(SetupError, match="skipped|unknown"):
        finalize_setup(root)
    assert setup_status(root) == "pending"


def test_lock_cannot_promote_source_to_managed_instructions(tmp_path: Path) -> None:
    from ai_kit.project import _lock_text, block_content, load_lock, load_profile, managed_block
    from ai_kit.system import sha256_text

    root = repo(tmp_path)
    write_dev(root)
    source = root / "app.py"
    source.write_text("VALUE = 1\n")
    commit(root)
    prepare_setup(root)
    write_report(root)
    block = managed_block(block_content("app.py", load_profile(root), setup=True))
    source.write_text(block + "VALUE = 2\n")
    lock = load_lock(root)
    (root / "ai-kit.lock").write_text(
        _lock_text(
            lock.profile_digest,
            lock.generated,
            blocks={**lock.blocks, "app.py": sha256_text(block)},
            setup=lock.setup,
            schema=lock.schema,
        )
    )

    with pytest.raises(SetupError, match="block|protected"):
        finalize_setup(root)


@pytest.mark.parametrize("tamper", ["replace", "remove", "append"])
def test_inherited_failure_cannot_be_replaced_or_removed(tmp_path: Path, tamper: str) -> None:
    import json

    root = repo(tmp_path)
    (root / "pyproject.toml").write_text("[tool.pytest.ini_options]\n")
    (root / "tests").mkdir()
    (root / "tests/test_legacy.py").write_text("def test_legacy():\n    assert False\n")
    commit(root)
    prepare_setup(root)
    write_dev(root)
    if tamper != "replace":
        path = root / ".ai/baseline.json"
        data = json.loads(path.read_text())
        if tamper == "remove":
            data["checks"] = {}
        else:
            data["checks"]["discovered/unit"] = {
                "discovery": True,
                "resolves": "unit",
                "status": "pass",
                "command": ["true"],
                "exit_code": 0,
                "output": "",
                "reason": "",
            }
        path.write_text(json.dumps(data))
        write_report(root)
    else:
        write_report(root, baseline={"unit": ["true"]})

    with pytest.raises(SetupError, match="baseline|inherited"):
        finalize_setup(root)
    assert setup_status(root) == "pending"
