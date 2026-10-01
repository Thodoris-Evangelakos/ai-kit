from __future__ import annotations

import os
import re
import subprocess
import tomllib
from pathlib import Path

import pytest

from ai_kit.project import (
    LOCK_SCHEMA_SETUP,
    ProjectError,
    _lock_text,
    apply_managed_block,
    doctor_project,
    human_bytes,
    init_project,
    load_lock,
    load_profile,
    managed_block,
    read_managed_block,
    set_profile_modules,
    sync_project,
)


def repo(tmp_path: Path) -> Path:
    subprocess.run(["git", "init", "-q", "-b", "main", str(tmp_path)], check=True)
    return tmp_path


def test_init_sync_doctor_and_idempotence(tmp_path: Path) -> None:
    root = repo(tmp_path)
    result = init_project(root)
    assert not result.problems
    assert load_profile(root).modules == ("strict-verification",)
    assert "AGENTS.md" in load_lock(root).generated
    assert (root / ".agents/skills/manage-goal/SKILL.md").is_file()
    assert (root / ".ai/current/STATUS.md").is_file()
    assert (root / "dev").stat().st_mode & 0o111
    assert ".ai-local/" in (root / ".gitignore").read_text()
    assert not init_project(root).changes
    assert sync_project(root, check=True).clean
    assert all(check.ok for check in doctor_project(root))


@pytest.mark.parametrize("relative", ["AGENTS.md", ".github/workflows/verify.yml"])
def test_drift_is_reported_and_never_overwritten(tmp_path: Path, relative: str) -> None:
    root = repo(tmp_path)
    init_project(root, modules=("professional-repository",))
    path = root / relative
    path.write_text(path.read_text() + "\nHuman addition.\n")
    before = path.read_text()
    assert any(relative in item for item in sync_project(root, check=True).problems)
    assert sync_project(root).problems
    assert path.read_text() == before
    assert not all(check.ok for check in doctor_project(root))


def test_init_preflights_existing_router(tmp_path: Path) -> None:
    root = repo(tmp_path)
    (root / "AGENTS.md").write_text("human instructions\n")
    result = init_project(root)
    assert result.problems
    assert not (root / ".ai").exists()
    assert (root / "AGENTS.md").read_text() == "human instructions\n"


def test_profile_change_renders_module_and_preserves_human_toml(tmp_path: Path) -> None:
    root = repo(tmp_path)
    init_project(root)
    profile = root / ".ai/profile.toml"
    profile.write_text('schema = 1\nbase = "software"\nmodules = ["learning", "webapp"] # chosen\n')
    before = profile.read_text()
    assert sync_project(root, check=True).changes
    assert not sync_project(root).problems
    assert profile.read_text() == before
    assert (root / ".agents/skills/verify-webapp/SKILL.md").is_file()
    assert (root / ".agents/skills/maintain-learning-journal/SKILL.md").is_file()
    assert (root / ".ai-local/learning").is_dir()
    assert sync_project(root, check=True).clean


def test_invalid_profile_and_non_repo_are_rejected(tmp_path: Path) -> None:
    with pytest.raises(ProjectError, match="Git repository root"):
        init_project(tmp_path)
    root = repo(tmp_path)
    init_project(root)
    (root / ".ai/profile.toml").write_text('schema = 99\nbase = "software"\nmodules = []\n')
    with pytest.raises(ProjectError, match="unsupported profile schema"):
        sync_project(root, check=True)


def test_doctor_rejects_invalid_current_status(tmp_path: Path) -> None:
    root = repo(tmp_path)
    init_project(root)
    (root / ".ai/current/STATUS.md").write_text("# stale notes\n")
    assert not all(check.ok for check in doctor_project(root))


def test_developer_file_is_not_managed(tmp_path: Path) -> None:
    root = repo(tmp_path)
    (root / "dev").write_text("#!/bin/sh\nexit 1\n")
    os.chmod(root / "dev", 0o755)
    init_project(root)
    assert (root / "dev").read_text() == "#!/bin/sh\nexit 1\n"
    assert "dev" not in load_lock(root).generated


def test_professional_workflow_calls_project_commands(tmp_path: Path) -> None:
    root = repo(tmp_path)
    init_project(root, modules=("professional-repository",))
    workflow = (root / ".github/workflows/verify.yml").read_text()
    assert "contents: read" in workflow
    actions = re.findall(r"uses: (.+)", workflow)
    assert len(actions) == 2
    assert all(re.fullmatch(r"[\w/-]+@[0-9a-f]{40} # v\d+\.\d+\.\d+", action) for action in actions)
    assert "fetch-depth: 0" in workflow
    assert re.findall(r"run: (.+)", workflow) == ["./dev setup", "./dev verify"]
    lock = (root / "ai-kit.lock").read_bytes()
    assert sync_project(root, check=True).clean
    assert sync_project(root).clean
    assert (root / ".github/workflows/verify.yml").read_text() == workflow
    assert (root / "ai-kit.lock").read_bytes() == lock


def test_managed_block_helpers_preserve_human_bytes() -> None:
    text = "# Human guide\n\nKeep the repository small.\n"
    updated = apply_managed_block(text, "AGENTS.md", "ROUTER")
    assert human_bytes(updated, "AGENTS.md") == text
    assert read_managed_block(updated, "AGENTS.md") == managed_block("ROUTER")
    replaced = apply_managed_block(updated, "AGENTS.md", "ROUTER2")
    assert human_bytes(replaced, "AGENTS.md") == text
    assert "ROUTER2" in replaced
    with pytest.raises(ProjectError, match="block markers"):
        human_bytes(
            "<!-- ai-kit:begin -->\nx\n<!-- ai-kit:begin -->\n<!-- ai-kit:end -->\n", "AGENTS.md"
        )


def test_lock_reader_accepts_schema_two(tmp_path: Path) -> None:
    root = repo(tmp_path)
    init_project(root)
    lock = load_lock(root)
    digest = "sha256:" + "0" * 64
    text = _lock_text(
        lock.profile_digest,
        lock.generated,
        blocks={"AGENTS.md": digest},
        setup={"kind": "setup", "state": "pending", "steps": ["snapshot", "protocol"]},
        schema=LOCK_SCHEMA_SETUP,
    )
    (root / "ai-kit.lock").write_text(text)

    upgraded = load_lock(root)

    assert upgraded.schema == LOCK_SCHEMA_SETUP
    assert upgraded.blocks == {"AGENTS.md": digest}
    assert upgraded.setup is not None and upgraded.setup["state"] == "pending"
    assert upgraded.setup["steps"] == ["snapshot", "protocol"]
    # A schema 1 lock still round-trips through the shared reader.
    assert load_lock(root).generated == lock.generated


def test_setup_upgrade_converts_router_and_sync_manages_blocks(tmp_path: Path) -> None:
    from ai_kit.setup import prepare_setup, setup_status

    root = repo(tmp_path)
    init_project(root)

    prepare_setup(root)

    assert setup_status(root) == "pending"
    lock = load_lock(root)
    assert lock.schema == LOCK_SCHEMA_SETUP
    assert set(lock.blocks) >= {"AGENTS.md", "CLAUDE.md"}
    assert "<!-- ai-kit:begin -->" in (root / "AGENTS.md").read_text()
    assert sync_project(root, check=True).clean
    checks = {check.name: check for check in doctor_project(root)}
    assert checks["setup"].ok is True
    assert "pending" in checks["setup"].detail


def test_setup_upgrade_refuses_drifted_router(tmp_path: Path) -> None:
    from ai_kit.setup import SetupError, prepare_setup

    root = repo(tmp_path)
    init_project(root)
    agents = root / "AGENTS.md"
    agents.write_text(agents.read_text() + "\nHuman edit.\n")

    with pytest.raises(SetupError, match="drift"):
        prepare_setup(root)
    assert "Human edit." in agents.read_text()


def test_profile_module_change_preserves_pending_setup_metadata(tmp_path: Path) -> None:
    from ai_kit.setup import prepare_setup, setup_status

    root = repo(tmp_path)
    init_project(root)
    prepare_setup(root)
    before = tomllib.loads((root / "ai-kit.lock").read_text())

    result = set_profile_modules(root, ("learning",))

    assert not result.problems
    after = tomllib.loads((root / "ai-kit.lock").read_text())
    assert after["setup"] == before["setup"]
    assert after["blocks"] == before["blocks"]
    assert setup_status(root) == "pending"
    assert sync_project(root, check=True).clean
