"""Tests for the preferred setup-harness configuration and launcher.

The launcher is exercised with executable stubs, so no real model call runs.
"""

from __future__ import annotations

import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

from ai_kit import harness
from ai_kit.harness import (
    ACTIVE_AGENT_MARKERS,
    HARNESS_CHOICES,
    SETUP_ACTIVE_MARKER,
    HarnessError,
    get_setup_harness,
    harness_available,
    launch_setup_harness,
    set_setup_harness,
)

STUB_SCRIPT = """#!/bin/sh
if [ "$1" = "--help" ]; then
  printf '%s\\n' "${AI_KIT_STUB_HELP:-usage: [PROMPT]}"
  exit "${AI_KIT_STUB_HELP_EXIT:-0}"
fi
{
  printf '%s\\n' "$0"
  for arg in "$@"; do printf '%s\\n' "$arg"; done
} > "$AI_KIT_STUB_DIR/argv"
pwd > "$AI_KIT_STUB_DIR/cwd"
printf '%s\\n' "${AI_KIT_SETUP_ACTIVE-<unset>}" > "$AI_KIT_STUB_DIR/marker"
printf '%s\\n' "${AI_KIT_RETAINED-<unset>}" > "$AI_KIT_STUB_DIR/retained"
exit "${AI_KIT_STUB_EXIT:-0}"
"""

MARKERS = (SETUP_ACTIVE_MARKER, *ACTIVE_AGENT_MARKERS)


class _Tty:
    def isatty(self) -> bool:
        return True


class _NotTty:
    def isatty(self) -> bool:
        return False


@pytest.fixture(autouse=True)
def isolated_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    for marker in MARKERS:
        monkeypatch.delenv(marker, raising=False)
    monkeypatch.delenv("AI_KIT_RETAINED", raising=False)


def force_tty(monkeypatch: pytest.MonkeyPatch) -> None:
    """Patch stdin/stdout during the call phase (pytest capture is per-phase)."""

    monkeypatch.setattr(sys, "stdin", _Tty())
    monkeypatch.setattr(sys, "stdout", _Tty())


def config_path(tmp_path: Path) -> Path:
    return tmp_path / "xdg" / "ai-kit" / "config.toml"


def make_stub(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    name: str = "codex",
    help_text: str = "usage: [PROMPT] --prompt PROMPT",
    help_exit: int = 0,
    exit_code: int = 0,
) -> Path:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    executable = bin_dir / name
    executable.write_text(STUB_SCRIPT)
    executable.chmod(0o755)
    log_dir = tmp_path / "log"
    log_dir.mkdir(exist_ok=True)
    monkeypatch.setenv("PATH", str(bin_dir))
    monkeypatch.setenv("AI_KIT_STUB_DIR", str(log_dir))
    monkeypatch.setenv("AI_KIT_STUB_HELP", help_text)
    monkeypatch.setenv("AI_KIT_STUB_HELP_EXIT", str(help_exit))
    monkeypatch.setenv("AI_KIT_STUB_EXIT", str(exit_code))
    return executable


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


def test_harness_choices_mapping_and_order() -> None:
    assert list(HARNESS_CHOICES.items()) == [
        ("codex", "Codex"),
        ("codex_ds", "codex_ds"),
        ("opencode", "OpenCode"),
        ("claude", "Claude Code"),
    ]


def test_get_defaults_to_codex_when_missing(tmp_path: Path) -> None:
    assert not config_path(tmp_path).exists()
    assert get_setup_harness() == "codex"


@pytest.mark.parametrize("name", list(HARNESS_CHOICES))
def test_set_then_get_roundtrip(tmp_path: Path, name: str) -> None:
    assert set_setup_harness(name) is True
    assert get_setup_harness() == name
    assert f'setup_harness = "{name}"' in config_path(tmp_path).read_text()


def test_set_preserves_unrelated_config_and_formatting(tmp_path: Path) -> None:
    path = config_path(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text("# my ai-kit config\neditor = \"vim\"  # keep\n\n[menu]\ntheme = 'dark'\n")

    assert set_setup_harness("opencode") is True

    text = path.read_text()
    assert "# my ai-kit config" in text
    assert 'editor = "vim"  # keep' in text
    assert "[menu]" in text
    assert "theme = 'dark'" in text
    data = tomllib.loads(text)
    assert data["setup_harness"] == "opencode"
    assert data["editor"] == "vim"
    assert data["menu"]["theme"] == "dark"


@pytest.mark.parametrize("newline", ["\n", "\r\n"])
def test_set_replaces_existing_value_in_place(tmp_path: Path, newline: str) -> None:
    path = config_path(tmp_path)
    path.parent.mkdir(parents=True)
    original = newline.join(['setup_harness = "codex"  # chosen', 'editor = "vim"', ""])
    path.write_bytes(original.encode())

    assert set_setup_harness("claude") is True

    text = path.read_text()
    assert 'setup_harness = "claude"  # chosen' in text
    assert 'editor = "vim"' in text
    assert path.read_bytes() == original.replace('"codex"', '"claude"').encode()
    assert get_setup_harness() == "claude"


def test_setting_harness_preserves_table_text_inside_multiline_value(tmp_path: Path) -> None:
    path = config_path(tmp_path)
    path.parent.mkdir(parents=True)
    original = 'notes = """\n[example]\nsetup_harness = "claude"\n"""\n'
    path.write_text(original)

    assert set_setup_harness("opencode") is True
    data = tomllib.loads(path.read_text())
    assert data["setup_harness"] == "opencode"
    assert data["notes"] == tomllib.loads(original)["notes"]


def test_set_unchanged_is_noop(tmp_path: Path) -> None:
    path = config_path(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text('setup_harness = "opencode"\n')
    before = path.read_bytes()

    assert set_setup_harness("opencode") is False
    assert path.read_bytes() == before


def test_set_rejects_unknown_name(tmp_path: Path) -> None:
    with pytest.raises(HarnessError):
        set_setup_harness("cursor")
    assert not config_path(tmp_path).exists()


def test_get_and_set_reject_invalid_selected_config(tmp_path: Path) -> None:
    path = config_path(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text('setup_harness = "cursor"\n')

    with pytest.raises(HarnessError):
        get_setup_harness()
    with pytest.raises(HarnessError):
        set_setup_harness("opencode")
    assert path.read_text() == 'setup_harness = "cursor"\n'


def test_set_rejects_invalid_toml(tmp_path: Path) -> None:
    path = config_path(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text("this is not = = toml\n")

    with pytest.raises(HarnessError):
        set_setup_harness("opencode")


def test_set_and_get_reject_symlinked_config_file(tmp_path: Path) -> None:
    target = tmp_path / "real.toml"
    target.write_text('setup_harness = "codex"\n')
    path = config_path(tmp_path)
    path.parent.mkdir(parents=True)
    path.symlink_to(target)

    with pytest.raises(HarnessError):
        get_setup_harness()
    with pytest.raises(HarnessError):
        set_setup_harness("opencode")
    assert target.read_text() == 'setup_harness = "codex"\n'


def test_set_rejects_symlinked_ancestor(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    real = tmp_path / "real-config"
    real.mkdir()
    linked = tmp_path / "linked-config"
    linked.symlink_to(real)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(linked))

    with pytest.raises(HarnessError):
        set_setup_harness("opencode")
    assert not (real / "ai-kit" / "config.toml").exists()


# ---------------------------------------------------------------------------
# Availability
# ---------------------------------------------------------------------------


def test_harness_available_uses_which(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    make_stub(tmp_path, monkeypatch, name="codex")
    assert harness_available("codex") is True
    assert harness_available("opencode") is False
    assert harness_available("cursor") is False


def test_harness_available_resolves_claude_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    make_stub(tmp_path, monkeypatch, name="claude")
    assert harness_available("claude") is True


# ---------------------------------------------------------------------------
# Launcher
# ---------------------------------------------------------------------------


def test_launch_requires_terminal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    make_stub(tmp_path, monkeypatch)
    monkeypatch.setattr(sys, "stdin", _NotTty())
    monkeypatch.setattr(sys, "stdout", _Tty())
    with pytest.raises(HarnessError):
        launch_setup_harness(tmp_path, "codex", tmp_path / "protocol.md")

    monkeypatch.setattr(sys, "stdin", _Tty())
    monkeypatch.setattr(sys, "stdout", _NotTty())
    with pytest.raises(HarnessError):
        launch_setup_harness(tmp_path, "codex", tmp_path / "protocol.md")


@pytest.mark.parametrize("marker", [SETUP_ACTIVE_MARKER, "CODEX_THREAD_ID", "CLAUDECODE"])
def test_launch_refuses_active_session(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    marker: str,
) -> None:
    force_tty(monkeypatch)
    make_stub(tmp_path, monkeypatch)
    monkeypatch.setenv(marker, "1")
    with pytest.raises(HarnessError):
        launch_setup_harness(tmp_path, "codex", tmp_path / "protocol.md")


def test_launch_rejects_unknown_name(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    force_tty(monkeypatch)
    with pytest.raises(HarnessError):
        launch_setup_harness(tmp_path, "cursor", tmp_path / "protocol.md")


def test_launch_missing_binary(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    force_tty(monkeypatch)
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))
    with pytest.raises(HarnessError):
        launch_setup_harness(tmp_path, "codex", tmp_path / "protocol.md")


def test_launch_help_nonzero_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    force_tty(monkeypatch)
    make_stub(tmp_path, monkeypatch, help_exit=2)
    with pytest.raises(HarnessError):
        launch_setup_harness(tmp_path, "codex", tmp_path / "protocol.md")
    assert not (tmp_path / "log" / "argv").exists()


def test_launch_help_without_prompt_capability_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    force_tty(monkeypatch)
    make_stub(tmp_path, monkeypatch, help_text="usage: something unrelated")
    with pytest.raises(HarnessError):
        launch_setup_harness(tmp_path, "codex", tmp_path / "protocol.md")
    assert not (tmp_path / "log" / "argv").exists()


def test_launch_probe_timeout_is_harness_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    force_tty(monkeypatch)
    make_stub(tmp_path, monkeypatch)

    def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        timeout = kwargs.get("timeout")
        assert isinstance(timeout, (int, float)) and timeout > 0
        raise subprocess.TimeoutExpired(argv, float(timeout))

    monkeypatch.setattr(harness.subprocess, "run", fake_run)
    with pytest.raises(HarnessError):
        launch_setup_harness(tmp_path, "codex", tmp_path / "protocol.md")


def test_launch_run_timeout_is_harness_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    force_tty(monkeypatch)
    make_stub(tmp_path, monkeypatch)

    def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        if "--help" in argv:
            return subprocess.CompletedProcess(argv, 0, stdout="usage: [PROMPT]", stderr="")
        raise subprocess.TimeoutExpired(argv, 30)

    monkeypatch.setattr(harness.subprocess, "run", fake_run)
    with pytest.raises(HarnessError):
        launch_setup_harness(tmp_path, "codex", tmp_path / "protocol.md")


def test_launch_oserror_is_harness_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    force_tty(monkeypatch)
    make_stub(tmp_path, monkeypatch)

    def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        if "--help" in argv:
            return subprocess.CompletedProcess(argv, 0, stdout="usage: [PROMPT]", stderr="")
        raise OSError("exec format error")

    monkeypatch.setattr(harness.subprocess, "run", fake_run)
    with pytest.raises(HarnessError):
        launch_setup_harness(tmp_path, "codex", tmp_path / "protocol.md")


def test_launch_keyboard_interrupt_returns_130(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    force_tty(monkeypatch)
    make_stub(tmp_path, monkeypatch)

    def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        if "--help" in argv:
            return subprocess.CompletedProcess(argv, 0, stdout="usage: [PROMPT]", stderr="")
        raise KeyboardInterrupt

    monkeypatch.setattr(harness.subprocess, "run", fake_run)
    assert launch_setup_harness(tmp_path, "codex", tmp_path / "protocol.md") == 130


@pytest.mark.parametrize("exit_code", [0, 1, 7, 130])
def test_launch_propagates_returncode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, exit_code: int
) -> None:
    force_tty(monkeypatch)
    make_stub(tmp_path, monkeypatch, exit_code=exit_code)
    root = tmp_path / "repo"
    root.mkdir()
    protocol = root / "protocol.md"
    protocol.write_text("protocol")
    assert launch_setup_harness(root, "codex", protocol) == exit_code


@pytest.mark.parametrize(
    ("name", "flag"),
    [("codex", None), ("codex_ds", None), ("opencode", "--prompt"), ("claude", None)],
)
def test_launch_argv_cwd_marker_and_environment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    name: str,
    flag: str | None,
) -> None:
    force_tty(monkeypatch)
    executable = make_stub(tmp_path, monkeypatch, name=name)
    monkeypatch.setenv("AI_KIT_RETAINED", "keep-me")
    root = tmp_path / "repo"
    root.mkdir()
    protocol = root / "protocol.md"
    protocol.write_text("protocol")

    assert launch_setup_harness(root, name, protocol) == 0

    log = tmp_path / "log"
    argv = (log / "argv").read_text().splitlines()
    assert argv[0] == str(executable)
    args = argv[1:]
    if flag is None:
        assert len(args) == 1
        prompt = args[0]
    else:
        assert args[0] == flag
        assert len(args) == 2
        prompt = args[1]
    assert "AGENTS.md" in prompt
    assert "protocol.md" in prompt
    assert "ai-kit setup --finalize" in prompt
    assert "uncertain" in prompt.lower()
    assert "recursi" in prompt.lower()
    assert Path((log / "cwd").read_text().strip()).resolve() == root.resolve()
    assert (log / "marker").read_text().strip() == "1"
    assert (log / "retained").read_text().strip() == "keep-me"


def test_prompt_uses_repository_relative_protocol_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    force_tty(monkeypatch)
    make_stub(tmp_path, monkeypatch)
    root = tmp_path / "repo"
    root.mkdir()
    protocol = root / "docs" / "plan" / "setup.md"
    protocol.parent.mkdir(parents=True)
    protocol.write_text("protocol")

    assert launch_setup_harness(root, "codex", protocol) == 0

    prompt = (tmp_path / "log" / "argv").read_text().splitlines()[1]
    assert "docs/plan/setup.md" in prompt
    assert str(root) not in prompt
