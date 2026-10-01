from __future__ import annotations

from pathlib import Path

import pytest

from ai_kit.project import init_project, load_lock
from ai_kit.setup import SetupError, finalize_setup, prepare_setup, setup_status
from test_setup import commit, repo, write_dev, write_report


def prepared_project(tmp_path: Path) -> Path:
    root = repo(tmp_path)
    write_dev(root)
    commit(root)
    prepare_setup(root)
    write_report(root)
    return root


def test_managed_skill_drift_blocks_setup_finalization(tmp_path: Path) -> None:
    root = prepared_project(tmp_path)
    skill = root / ".agents/skills/manage-goal/SKILL.md"
    skill.write_text(skill.read_text() + "\nUnreviewed change.\n")

    with pytest.raises(SetupError, match="drift"):
        finalize_setup(root)
    assert setup_status(root) == "pending"


def test_report_cannot_bypass_unconfigured_project_commands(tmp_path: Path) -> None:
    root = repo(tmp_path)
    prepare_setup(root)
    write_report(root, checks={"verify": ["true"]})

    with pytest.raises(SetupError, match="verify|unconfigured"):
        finalize_setup(root)
    assert setup_status(root) == "pending"


def test_setup_preserves_human_gitignore_lines(tmp_path: Path) -> None:
    root = repo(tmp_path)
    (root / ".gitignore").write_text("private.env\n")
    write_dev(root)
    commit(root)
    prepare_setup(root)
    write_report(root)
    (root / ".gitignore").write_text(".ai-local/\n")

    with pytest.raises(SetupError, match="gitignore"):
        finalize_setup(root)


def test_resume_refuses_to_replace_edited_instruction_block(tmp_path: Path) -> None:
    root = prepared_project(tmp_path)
    path = root / "AGENTS.md"
    changed = path.read_text().replace("# Project agent guide", "# Changed guide")
    path.write_text(changed)

    with pytest.raises(SetupError, match="drift"):
        prepare_setup(root)
    assert path.read_text() == changed


def test_setup_preserves_crlf_instruction_bytes(tmp_path: Path) -> None:
    root = repo(tmp_path)
    original = b"# Human instructions\r\n\r\nKeep this text.\r\n"
    (root / "AGENTS.md").write_bytes(original)
    write_dev(root)
    commit(root)

    prepare_setup(root)

    assert (root / "AGENTS.md").read_bytes().endswith(original)
    write_report(root)
    finalize_setup(root)


def test_adoption_records_original_baseline_before_agent_work(tmp_path: Path) -> None:
    root = repo(tmp_path)
    write_dev(root, "#!/bin/sh\necho inherited\nexit 7\n")
    commit(root)

    prepare_setup(root)

    import json

    baseline = json.loads((root / ".ai/baseline.json").read_text())
    assert baseline["checks"]["verify"]["exit_code"] == 7
    assert baseline["source"] == {"dirty": False, "changes": []}
    assert "inventory" in (root / ".ai/adoption/inventory.md").read_text().lower()
    assert (
        "Working tree was clean before adoption" in (root / ".ai/adoption/inventory.md").read_text()
    )


def test_inherited_pytest_failure_cannot_be_omitted_from_report(tmp_path: Path) -> None:
    root = repo(tmp_path)
    (root / "pyproject.toml").write_text("[tool.pytest.ini_options]\n")
    (root / "tests").mkdir()
    (root / "tests/test_old.py").write_text("def test_old():\n    assert False\n")
    commit(root)
    prepare_setup(root)
    write_dev(root)
    write_report(root)

    with pytest.raises(SetupError, match="baseline|inherited"):
        finalize_setup(root)
    assert setup_status(root) == "pending"


def test_interrupted_legacy_conversion_resumes_original_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from ai_kit import setup

    root = repo(tmp_path)
    init_project(root)
    write_dev(root)
    commit(root)
    real = setup._write_lock

    def interrupted(*args, **kwargs):
        raise OSError("interrupted before lock")

    with monkeypatch.context() as scoped:
        scoped.setattr(setup, "_write_lock", interrupted)
        with pytest.raises(OSError, match="interrupted"):
            prepare_setup(root)
    assert load_lock(root).schema == 1
    snapshot = root / ".ai-local/setup/manifest.json"
    original = snapshot.read_bytes()
    assert setup._write_lock is real

    prepare_setup(root)

    assert snapshot.read_bytes() == original
    assert setup_status(root) == "pending"
