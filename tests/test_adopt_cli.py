from pathlib import Path

import pytest
from typer.testing import CliRunner

from ai_kit.cli import app
from ai_kit.harness import set_setup_harness
from test_cli_flow import commit
from test_setup_cli import make_repo, run_cli


@pytest.fixture
def adoption_repo(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    make_repo(root)
    (root / "source.txt").write_text("user work\n")
    (root / "dev").write_text("#!/bin/sh\nexit 0\n")
    (root / "dev").chmod(0o755)
    commit(root, "Original repository", ".")
    monkeypatch.chdir(root)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setattr("ai_kit.menu.interactive_ready", lambda: True)
    monkeypatch.setattr("ai_kit.harness._active_session", lambda: None)
    return root


@pytest.mark.parametrize("configured", [None, "opencode", "claude", "unrelated"])
def test_adopt_uses_selected_harness_and_leaves_failed_session_pending(
    adoption_repo, monkeypatch, configured
):
    from ai_kit.harness import get_setup_harness
    from ai_kit.setup import setup_status

    if configured == "unrelated":
        config = adoption_repo.parent / "config/ai-kit/config.toml"
        config.parent.mkdir(parents=True)
        config.write_text('theme = "dark"\n')
    elif configured:
        set_setup_harness(configured)
    monkeypatch.setattr("ai_kit.harness.harness_available", lambda name: name == "opencode")

    def launch(root, name, protocol):
        assert root == adoption_repo
        assert name == "opencode"
        assert protocol.is_file()
        return 9

    monkeypatch.setattr("ai_kit.harness.launch_setup_harness", launch)
    result = CliRunner().invoke(app, ["adopt"], input="opencode\n")
    assert result.exit_code == 9, result.output
    assert setup_status(adoption_repo) == "pending"
    assert get_setup_harness() == "opencode"
    assert (adoption_repo / "source.txt").read_text() == "user work\n"
    if configured == "opencode":
        assert "Choose" not in result.output
    if configured == "unrelated":
        assert 'theme = "dark"' in config.read_text()


@pytest.mark.parametrize("answer, accepted", [("y\n", True), ("n\n", False), ("\n", False)])
def test_adopt_without_available_harness_requires_safe_fallback_consent(
    adoption_repo, monkeypatch, answer, accepted
):
    monkeypatch.setattr("ai_kit.harness.harness_available", lambda name: False)
    result = CliRunner().invoke(app, ["adopt"], input=answer)
    assert "adopt --safe" in result.output
    assert result.exit_code == (0 if accepted else 1), result.output
    assert (adoption_repo / "ai-kit.lock").exists() == accepted
    assert (adoption_repo / "source.txt").read_text() == "user work\n"
    assert not (adoption_repo / ".ai/setup").exists()
    if not accepted:
        assert not (adoption_repo / ".ai").exists()
        assert not (adoption_repo / ".ai-local").exists()


def test_adopt_can_decline_harness_selection_then_confirm_safe_fallback(adoption_repo, monkeypatch):
    monkeypatch.setattr("ai_kit.harness.harness_available", lambda name: name == "codex")
    result = CliRunner().invoke(app, ["adopt"], input="none\ny\n")
    assert result.exit_code == 0, result.output
    assert "adopt --safe" in result.output
    assert (adoption_repo / "ai-kit.lock").exists()
    assert not (adoption_repo / ".ai/setup").exists()


def test_adopt_noninteractive_requires_explicit_safe_mode_before_writes(tmp_path: Path):
    root = tmp_path / "repo"
    make_repo(root)
    result = run_cli(root, tmp_path / "config", "adopt")
    assert result.returncode == 1
    assert "--safe" in result.stdout + result.stderr
    assert not (root / ".ai").exists()
    assert not (root / ".ai-local").exists()


@pytest.mark.parametrize("answer", ["", "none\nn\n"])
def test_adopt_cancelled_selection_or_fallback_preserves_repository(
    adoption_repo, monkeypatch, answer
):
    monkeypatch.setattr("ai_kit.harness.harness_available", lambda name: name == "codex")
    result = CliRunner().invoke(app, ["adopt"], input=answer)
    assert result.exit_code == 1, result.output
    assert not (adoption_repo / ".ai").exists()
    assert not (adoption_repo / ".ai-local").exists()


def test_adopt_invalid_config_does_not_silently_fall_back(adoption_repo, monkeypatch, tmp_path):
    config = tmp_path / "config/ai-kit/config.toml"
    config.parent.mkdir(parents=True)
    config.write_text('setup_harness = "unknown"\n')
    monkeypatch.setattr("ai_kit.harness.harness_available", lambda name: False)
    result = CliRunner().invoke(app, ["adopt"], input="y\n")
    assert result.exit_code == 1, result.output
    assert "setup_harness" in result.output
    assert not (adoption_repo / ".ai").exists()


def test_adopt_inside_agent_does_not_spawn_or_prompt(adoption_repo, monkeypatch):
    monkeypatch.setattr("ai_kit.harness._active_session", lambda: "active agent")
    result = CliRunner().invoke(app, ["adopt"], input="codex\n")
    assert result.exit_code == 1, result.output
    assert "--no-launch" in result.output
    assert not (adoption_repo / ".ai").exists()


def test_adopt_finishes_safe_fallback_when_harness_becomes_available(adoption_repo, monkeypatch):
    from ai_kit.setup import setup_status

    monkeypatch.setattr("ai_kit.harness.harness_available", lambda name: False)
    fallback = CliRunner().invoke(app, ["adopt"], input="y\n")
    assert fallback.exit_code == 0, fallback.output
    baseline = (adoption_repo / ".ai/baseline.json").read_bytes()
    monkeypatch.setattr("ai_kit.harness.harness_available", lambda name: name == "codex")
    monkeypatch.setattr("ai_kit.harness.launch_setup_harness", lambda *args: 9)
    resumed = CliRunner().invoke(app, ["adopt"], input="missing\ncodex\n")
    assert resumed.exit_code == 9, resumed.output
    assert setup_status(adoption_repo) == "pending"
    assert (adoption_repo / ".ai/baseline.json").read_bytes() == baseline
    assert (adoption_repo / "source.txt").read_text() == "user work\n"


def test_adopt_saves_explicit_codex_choice_in_existing_config(adoption_repo, monkeypatch):
    config = adoption_repo.parent / "config/ai-kit/config.toml"
    config.parent.mkdir(parents=True)
    config.write_text('theme = "dark"\n')
    monkeypatch.setattr("ai_kit.harness.harness_available", lambda name: name == "codex")
    monkeypatch.setattr("ai_kit.harness.launch_setup_harness", lambda *args: 9)
    first = CliRunner().invoke(app, ["adopt"], input="codex\n")
    assert first.exit_code == 9, first.output
    resumed = CliRunner().invoke(app, ["adopt"])
    assert resumed.exit_code == 9, resumed.output
    assert "Choose" not in resumed.output
    assert 'theme = "dark"' in config.read_text()
