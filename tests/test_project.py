from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from ai_kit.project import (
    ProjectError,
    doctor_project,
    init_project,
    load_lock,
    load_profile,
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


def test_drift_is_reported_and_never_overwritten(tmp_path: Path) -> None:
    root = repo(tmp_path)
    init_project(root)
    path = root / "AGENTS.md"
    path.write_text(path.read_text() + "\nHuman addition.\n")
    before = path.read_text()
    assert any("AGENTS.md" in item for item in sync_project(root, check=True).problems)
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
    assert "astral-sh/setup-uv@v10.2.0" in workflow
    assert "./dev setup" in workflow
    assert "./dev check" in workflow
    assert "./dev verify" in workflow
