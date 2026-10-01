from __future__ import annotations

import os
import pty
import select
import struct
import subprocess
import sys
import termios
import time
from pathlib import Path

import pytest

from ai_kit import project
from ai_kit.project import (
    ProjectError,
    init_project,
    load_profile,
    set_profile_modules,
    sync_project,
)

SOURCE = str(Path(__file__).resolve().parents[1] / "src")


def repo(tmp_path: Path) -> Path:
    subprocess.run(["git", "init", "-q", "-b", "main", str(tmp_path)], check=True)
    return tmp_path


def child_env() -> dict[str, str]:
    return {
        **os.environ,
        "PYTHONPATH": SOURCE + os.pathsep + os.environ.get("PYTHONPATH", ""),
        "TERM": "xterm-256color",
        "COLUMNS": "100",
        "LINES": "30",
    }


def cli(
    root: Path,
    *args: str,
    exit_code: int = 0,
    timeout: float = 30.0,
) -> str:
    result = subprocess.run(
        [sys.executable, "-m", "ai_kit.cli", *args],
        cwd=root,
        env=child_env(),
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    assert result.returncode == exit_code, (args, result.stdout, result.stderr)
    return result.stdout + result.stderr


def test_set_profile_modules_writes_generated_files_and_clean_sync(tmp_path: Path) -> None:
    root = repo(tmp_path)
    init_project(root)

    result = set_profile_modules(root, ("webapp", "learning"))

    assert load_profile(root).modules == ("learning", "webapp")
    assert (root / ".agents/skills/verify-webapp/SKILL.md").is_file()
    assert (root / ".agents/skills/maintain-learning-journal/SKILL.md").is_file()
    assert (root / ".ai-local/learning").is_dir()
    assert any("verify-webapp" in change for change in result.changes)
    assert sync_project(root, check=True).clean


def test_set_profile_modules_unchanged_selection_is_a_noop(tmp_path: Path) -> None:
    root = repo(tmp_path)
    init_project(root, modules=("learning", "webapp"))
    profile = root / ".ai/profile.toml"
    before = profile.read_bytes()

    result = set_profile_modules(root, ("webapp", "learning"))

    assert not result.changes
    assert not result.problems
    assert profile.read_bytes() == before
    assert sync_project(root, check=True).clean


def test_set_profile_modules_preserves_comments_and_multiline_array(tmp_path: Path) -> None:
    root = repo(tmp_path)
    init_project(root)
    profile = root / ".ai/profile.toml"
    profile.write_text(
        "# chosen by a human\n"
        "schema = 1\n"
        'base   = "software"   # keep me\n'
        "modules = [\n"
        '    "strict-verification",\n'
        "]\n"
    )

    set_profile_modules(root, ("strict-verification", "learning", "webapp"))

    text = profile.read_text()
    assert "# chosen by a human" in text
    assert 'base   = "software"   # keep me' in text
    assert 'modules = [\n    "strict-verification",\n    "learning",\n    "webapp",\n]' in text
    assert load_profile(root).modules == ("strict-verification", "learning", "webapp")


def test_set_profile_modules_refuses_manual_drift_without_writing(tmp_path: Path) -> None:
    root = repo(tmp_path)
    init_project(root, modules=("professional-repository",))
    workflow = root / ".github/workflows/verify.yml"
    workflow.write_text(workflow.read_text() + "# human tweak\n")
    profile = root / ".ai/profile.toml"
    before = profile.read_bytes()

    with pytest.raises(ProjectError, match="drift"):
        set_profile_modules(root, ("professional-repository", "learning"))

    assert profile.read_bytes() == before
    assert workflow.read_text().endswith("# human tweak\n")
    assert not (root / ".agents/skills/maintain-learning-journal").exists()


def test_set_profile_modules_rejects_symlink_and_unmanaged_collision(tmp_path: Path) -> None:
    root = repo(tmp_path)
    init_project(root)
    profile = root / ".ai/profile.toml"
    real = root / ".ai/profile.real"
    profile.rename(real)
    profile.symlink_to(real)
    with pytest.raises(ProjectError, match="symlink"):
        set_profile_modules(root, ("learning",))
    profile.unlink()
    real.rename(profile)

    target = root / ".agents/skills/verify-webapp/SKILL.md"
    target.parent.mkdir(parents=True)
    target.write_text("human-owned\n")
    with pytest.raises(ProjectError, match="unmanaged"):
        set_profile_modules(root, ("strict-verification", "webapp"))
    assert load_profile(root).modules == ("strict-verification",)
    assert target.read_text() == "human-owned\n"


def test_set_profile_modules_refuses_unfamiliar_formatting(tmp_path: Path) -> None:
    root = repo(tmp_path)
    init_project(root)
    profile = root / ".ai/profile.toml"
    profile.write_text(
        'schema = 1\nbase = "software"\nmodules = [\n  # nested note\n  "strict-verification",\n]\n'
    )
    before = profile.read_bytes()

    with pytest.raises(ProjectError, match="unfamiliar"):
        set_profile_modules(root, ("learning",))
    assert profile.read_bytes() == before


def test_set_profile_modules_rejects_unsupported_module(tmp_path: Path) -> None:
    root = repo(tmp_path)
    init_project(root)
    with pytest.raises(ProjectError, match="unsupported"):
        set_profile_modules(root, ("not-a-real-module",))


def test_set_profile_modules_refuses_symlinked_ai_local(tmp_path: Path) -> None:
    root = repo(tmp_path)
    init_project(root)
    (root / ".ai-local").rmdir()
    real = root / "local-real"
    real.mkdir()
    (root / ".ai-local").symlink_to(real, target_is_directory=True)
    before = (root / ".ai/profile.toml").read_bytes()

    with pytest.raises(ProjectError, match="symlinked .ai-local"):
        set_profile_modules(root, ("learning",))
    assert (root / ".ai/profile.toml").read_bytes() == before


def test_set_profile_modules_refuses_symlinked_profile_directory(tmp_path: Path) -> None:
    root = repo(tmp_path / "repo")
    init_project(root)
    external = tmp_path / "external-ai"
    (root / ".ai").rename(external)
    (root / ".ai").symlink_to(external, target_is_directory=True)
    before = (external / "profile.toml").read_bytes()

    with pytest.raises(ProjectError, match="symlink"):
        set_profile_modules(root, ("learning",))
    assert (external / "profile.toml").read_bytes() == before
    assert not (root / ".agents/skills/maintain-learning-journal").exists()


def test_set_profile_modules_reports_concurrent_edit_without_clobbering(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = repo(tmp_path)
    init_project(root)
    profile = root / ".ai/profile.toml"
    concurrent = profile.read_text() + "# concurrent human edit\n"
    write = project.atomic_write_text

    def edit_before_write(path: Path, content: str, **kwargs):
        if path == profile:
            path.write_text(concurrent)
        return write(path, content, **kwargs)

    monkeypatch.setattr(project, "atomic_write_text", edit_before_write)
    with pytest.raises(ProjectError, match="expected content"):
        set_profile_modules(root, ("learning",))
    assert profile.read_text() == concurrent
    assert not (root / ".agents/skills/maintain-learning-journal").exists()


def test_failed_sync_keeps_partial_changes_visible(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = repo(tmp_path)
    init_project(root)
    write = project.atomic_write_text

    def fail_lock_write(path: Path, content: str, **kwargs):
        if path == root / "ai-kit.lock":
            raise OSError("disk write failed")
        return write(path, content, **kwargs)

    monkeypatch.setattr(project, "atomic_write_text", fail_lock_write)
    with pytest.raises(ProjectError, match="partial"):
        set_profile_modules(root, ("learning",))
    assert load_profile(root).modules == ("learning",)
    assert not sync_project(root, check=True).clean


def test_menu_command_and_bare_invocation_without_tty_do_not_hang(tmp_path: Path) -> None:
    root = repo(tmp_path)
    init_project(root)

    menu_output = cli(root, "menu", "-C", str(root), exit_code=1)
    assert "interactive terminal" in menu_output

    bare_output = cli(root, exit_code=0)
    assert "Usage" in bare_output
    assert "menu" in bare_output


def test_help_and_version_keep_working(tmp_path: Path) -> None:
    root = repo(tmp_path)
    assert "menu" in cli(root, "--help")
    assert "0.1.0" in cli(root, "--version")
    assert "Usage" in cli(root, "menu", "--help")


class PtySession:
    """Drive an AI Kit process through a real pseudo-terminal."""

    def __init__(self, root: Path, *args: str, rows: int = 30, cols: int = 100) -> None:
        self.master, slave = pty.openpty()
        import fcntl

        fcntl.ioctl(self.master, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
        self.before = termios.tcgetattr(self.master)
        self.during: list[float] | None = None
        self.after: list[float] | None = None
        self.buffer = b""
        self.pos = 0
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "ai_kit.cli", *args],
            cwd=root,
            env=child_env(),
            stdin=slave,
            stdout=slave,
            stderr=slave,
            close_fds=True,
        )
        os.close(slave)

    def text(self) -> str:
        return self.buffer.decode(errors="replace")

    def send(self, data: bytes) -> None:
        os.write(self.master, data)

    def _read(self, timeout: float) -> None:
        ready, _, _ = select.select([self.master], [], [], timeout)
        if not ready:
            return
        try:
            chunk = os.read(self.master, 4096)
        except OSError:
            return
        self.buffer += chunk

    def expect(self, needle: str, timeout: float = 15.0) -> None:
        encoded = needle.encode()
        deadline = time.monotonic() + timeout
        while True:
            found = self.buffer.find(encoded, self.pos)
            if found != -1:
                self.pos = found + len(encoded)
                return
            if time.monotonic() >= deadline:
                raise AssertionError(
                    f"timed out waiting for {needle!r}; recent output: {self.buffer[-600:]!r}"
                )
            self._read(0.2)

    def capture_during(self) -> None:
        self.during = termios.tcgetattr(self.master)

    def close(self, timeout: float = 15.0) -> None:
        try:
            self.proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait()
            raise AssertionError("menu process did not exit") from None
        finally:
            self.after = termios.tcgetattr(self.master)

    def cleanup(self) -> None:
        if self.proc.poll() is None:
            self.proc.kill()
            self.proc.wait()
        os.close(self.master)


def test_pty_menu_applies_modules_runs_action_and_restores_terminal(tmp_path: Path) -> None:
    root = repo(tmp_path / "project")
    init_project(root)
    session = PtySession(root, "menu", "-C", str(root))
    try:
        session.expect("Choose an action")
        assert str(root) in session.text()
        assert "strict-verification" in session.text()

        session.send(b"\r")  # open Profile modules
        session.expect("Space toggle")
        assert "declarative" in session.text()
        session.capture_during()
        assert session.during is not None
        assert not session.during[3] & termios.ICANON

        session.send(b"\x1bOB")  # down arrow -> learning
        session.send(b" ")
        session.send(b"\x1bOB")  # down arrow -> webapp
        session.send(b" ")
        session.send(b"\r")  # apply
        session.expect("Profile updated")
        session.expect("Press Enter to return")
        session.send(b"\r")

        session.expect("Choose an action")
        session.send(b"\x1bOB\x1bOB")  # Goal status -> Doctor
        session.send(b"\r")
        session.expect("AI Kit doctor")
        session.expect("Press Enter to return")
        session.send(b"\r")

        session.expect("Choose an action")
        session.send(b"q")
        session.close()
    finally:
        session.cleanup()

    assert session.proc.returncode == 0
    assert session.after is not None
    assert session.after[:4] == session.before[:4]
    assert set(load_profile(root).modules) == {"strict-verification", "learning", "webapp"}
    assert sync_project(root, check=True).clean


def test_pty_menu_cancel_discards_selection(tmp_path: Path) -> None:
    root = repo(tmp_path / "project")
    init_project(root)
    before = (root / ".ai/profile.toml").read_bytes()
    session = PtySession(root, "menu", "-C", str(root))
    try:
        session.expect("Choose an action")
        session.send(b"\r")
        session.expect("Space toggle")
        session.send(b"\x1bOB")
        session.send(b" ")  # change something
        session.send(b"q")  # cancel without applying
        session.expect("Choose an action")
        session.send(b"q")  # exit
        session.close()
    finally:
        session.cleanup()

    assert session.proc.returncode == 0
    assert (root / ".ai/profile.toml").read_bytes() == before
    assert load_profile(root).modules == ("strict-verification",)


def test_pty_menu_reports_nonzero_command_failure(tmp_path: Path) -> None:
    root = repo(tmp_path / "project")
    init_project(root)  # the starter ./dev exits nonzero, so the action must fail loudly
    session = PtySession(root, "menu", "-C", str(root))
    try:
        session.expect("Choose an action")
        session.send(
            b"\x1bOB\x1bOB\x1bOB\x1bOB"
        )  # Profile -> Goal status -> Doctor -> Sync -> check
        session.send(b"\r")
        session.expect("FAILED: exit code 2")
        assert "Passed." not in session.text()
        session.expect("Press Enter to return")
        session.send(b"\r")
        session.expect("Choose an action")
        session.send(b"q")
        session.close()
    finally:
        session.cleanup()

    assert session.proc.returncode == 0
