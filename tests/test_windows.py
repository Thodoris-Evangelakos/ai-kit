"""Native Windows paths, command launchers and conservative verification."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from ai_kit import menu
from ai_kit.adoption import _run_check, adopt_safe
from ai_kit.project import ProjectError, init_project, set_profile_modules, sync_project
from ai_kit.setup import SetupError, finalize_setup, prepare_setup, setup_status
from ai_kit.system import dev_command, is_link

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="native Windows integration")


def repo(tmp_path: Path) -> Path:
    root = tmp_path / "project with spaces"
    root.mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    return root


def test_menu_accepts_windows_terminal_without_term(monkeypatch):
    monkeypatch.delenv("TERM", raising=False)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
    assert menu.interactive_ready()
    menu._require_terminal()
    monkeypatch.setenv("TERM", "dumb")
    assert not menu.interactive_ready()
    with pytest.raises(menu.MenuError, match="capable terminal"):
        menu._require_terminal()


def test_init_preserves_crlf_and_native_starter_fails_until_configured(tmp_path):
    root = repo(tmp_path)
    (root / ".gitignore").write_bytes(b"# human rules\r\ncustom/\r\n")
    init_project(root)
    assert (root / ".gitignore").read_bytes() == b"# human rules\r\ncustom/\r\n.ai-local/\n"
    result = subprocess.run(dev_command(root, "check"), cwd=root, capture_output=True)
    assert result.returncode == 2
    assert b"unconfigured" in result.stderr
    profile = root / ".ai/profile.toml"
    profile.write_bytes(profile.read_bytes().replace(b"\n", b"\r\n"))
    set_profile_modules(root, ("learning",))
    assert sync_project(root, check=True).clean


def test_native_dev_cmd_is_preserved_and_menu_reports_its_failure(tmp_path, monkeypatch):
    root = repo(tmp_path).rename(tmp_path / "project %PATH% & ! spaces")
    script = b"@echo off\r\nexit /b 7\r\n"
    (root / "dev.cmd").write_bytes(script)
    init_project(root)
    assert (root / "dev.cmd").read_bytes() == script
    shown = []
    monkeypatch.setattr(menu, "_show", lambda lines, **kwargs: shown.extend(lines))
    menu._command_flow(root, menu.Action("check", "dev", ("check",)))
    assert "FAILED: exit code 7" in shown


def test_python_dev_and_legacy_shell_dev_can_run_without_cmd(tmp_path):
    root = repo(tmp_path)
    dev = root / "dev"
    dev.write_bytes(b"#!/usr/bin/env python3\nimport sys\nsys.exit(3)\n")
    assert dev_command(root, "check") == [sys.executable, str(dev), "check"]
    assert subprocess.run(dev_command(root, "check"), cwd=root).returncode == 3
    dev.write_bytes(b"#!/bin/sh\nexit 4\n")
    assert subprocess.run(dev_command(root, "check"), cwd=root).returncode == 4


def test_adoption_never_executes_unconfined_commands(tmp_path):
    root = repo(tmp_path)
    command = (sys.executable, "-c", "from pathlib import Path; Path('unsafe').touch()")
    result = _run_check(root, command, 5)
    assert result.status == "unknown"
    assert "Landlock" in result.reason
    assert not (root / "unsafe").exists()
    (root / "dev.cmd").write_bytes(b"@echo off\r\necho unsafe > unsafe\r\n")
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.invalid",
            "commit",
            "--allow-empty",
            "-qm",
            "original",
        ],
        cwd=root,
        check=True,
    )
    adopted = adopt_safe(root)
    assert adopted.status == "unknown"
    assert not (root / "unsafe").exists()


def test_setup_prepares_and_resumes_but_cannot_finalize_without_confinement(tmp_path):
    root = repo(tmp_path)
    protocol = prepare_setup(root)
    assert protocol.is_file()
    assert (root / "dev.cmd").is_file()
    assert prepare_setup(root) == protocol
    report = {
        "schema": 1,
        "context": [],
        "checks": {"verify": ["./dev", "verify"]},
        "baseline_checks": {},
        "unresolved": [],
    }
    (root / ".ai/setup/report.json").write_text(json.dumps(report))
    with pytest.raises(SetupError, match="Landlock|unknown"):
        finalize_setup(root)
    assert setup_status(root) == "pending"


@pytest.mark.parametrize("directory", [".ai", ".agents"])
def test_junctions_cannot_redirect_integration_writes(tmp_path, directory):
    root = repo(tmp_path)
    outside = tmp_path / "human files"
    outside.mkdir()
    junction = root / directory
    subprocess.run(
        ["cmd.exe", "/d", "/c", "mklink", "/J", str(junction), str(outside)],
        check=True,
        capture_output=True,
    )
    try:
        assert is_link(junction)
        with pytest.raises(ProjectError, match="symlink"):
            init_project(root)
        with pytest.raises((SetupError, ProjectError), match="symlink"):
            prepare_setup(root)
        assert list(outside.iterdir()) == []
    finally:
        os.rmdir(junction)
