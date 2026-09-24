"""Tests for AI Kit's shared system primitives."""

from __future__ import annotations

import os
import stat
from collections.abc import Iterator
from pathlib import Path

import pytest

from ai_kit.system import (
    DirtyRepositoryError,
    GitError,
    GitRepository,
    UnsafeWriteError,
    XdgPaths,
    atomic_write_text,
    discover_repository,
    run_git,
    sha256_file,
    sha256_text,
)


@pytest.fixture
def git_env() -> dict[str, str]:
    """Git environment isolated from the developer's real configuration."""

    env = dict(os.environ)
    env.update(
        {
            "GIT_AUTHOR_NAME": "AI Kit Test",
            "GIT_AUTHOR_EMAIL": "ai-kit-test@example.com",
            "GIT_COMMITTER_NAME": "AI Kit Test",
            "GIT_COMMITTER_EMAIL": "ai-kit-test@example.com",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_SYSTEM": os.devnull,
            "GIT_TERMINAL_PROMPT": "0",
        }
    )
    return env


@pytest.fixture
def repo(tmp_path: Path, git_env: dict[str, str]) -> Iterator[Path]:
    root = tmp_path / "repo"
    root.mkdir()
    run_git(["init", "-b", "main"], cwd=root, env=git_env)
    (root / "README.md").write_text("initial\n", encoding="utf-8")
    run_git(["add", "README.md"], cwd=root, env=git_env)
    run_git(["commit", "-m", "initial"], cwd=root, env=git_env)
    yield root


# ---------------------------------------------------------------------------
# XDG paths
# ---------------------------------------------------------------------------


def test_xdg_defaults_when_variables_are_unset(tmp_path: Path) -> None:
    paths = XdgPaths.resolve(env={"HOME": str(tmp_path)}, home=None)

    assert paths.config_home == tmp_path / ".config"
    assert paths.data_home == tmp_path / ".local/share"
    assert paths.cache_home == tmp_path / ".cache"
    assert paths.state_home == tmp_path / ".local/state"
    assert paths.config_dir == tmp_path / ".config/ai-kit"
    assert paths.data_dir == tmp_path / ".local/share/ai-kit"
    assert paths.cache_dir == tmp_path / ".cache/ai-kit"
    assert paths.state_dir == tmp_path / ".local/state/ai-kit"


def test_xdg_absolute_overrides_and_relative_values(tmp_path: Path) -> None:
    home = tmp_path / "home"
    paths = XdgPaths.resolve(
        env={
            "HOME": str(home),
            "XDG_CONFIG_HOME": str(tmp_path / "config"),
            "XDG_DATA_HOME": "relative/data",
            "XDG_CACHE_HOME": "",
            "XDG_STATE_HOME": str(tmp_path / "state"),
        },
        home=None,
    )

    assert paths.config_home == tmp_path / "config"
    assert paths.data_home == home / ".local/share"
    assert paths.cache_home == home / ".cache"
    assert paths.state_home == tmp_path / "state"


def test_xdg_explicit_home_overrides_environment(tmp_path: Path) -> None:
    paths = XdgPaths.resolve(env={"HOME": "/nonexistent"}, home=tmp_path)

    assert paths.config_dir == tmp_path / ".config/ai-kit"


def test_xdg_ensure_creates_only_ai_kit_directories(tmp_path: Path) -> None:
    paths = XdgPaths.resolve(env={"HOME": str(tmp_path)}, home=None)

    assert paths.ensure() is paths
    assert paths.config_dir.is_dir()
    assert paths.data_dir.is_dir()
    assert paths.cache_dir.is_dir()
    assert paths.state_dir.is_dir()
    assert not (tmp_path / ".config/other").exists()


# ---------------------------------------------------------------------------
# Git discovery, status and HEAD
# ---------------------------------------------------------------------------


def test_discover_repository_from_nested_directory_and_planned_file(
    repo: Path,
) -> None:
    nested = repo / "src" / "pkg"
    nested.mkdir(parents=True)

    from_directory = discover_repository(nested)
    from_missing_file = discover_repository(nested / "not-yet-created.py")

    assert from_directory is not None
    assert from_missing_file is not None
    assert from_directory.root == repo.resolve()
    assert from_missing_file.root == repo.resolve()
    assert from_directory.git_dir == (repo / ".git").resolve()


def test_discover_repository_returns_none_outside_a_repository(
    tmp_path: Path, git_env: dict[str, str]
) -> None:
    plain = tmp_path / "plain"
    plain.mkdir()
    env = dict(git_env)
    env["GIT_CEILING_DIRECTORIES"] = str(tmp_path)

    assert discover_repository(plain, env=env) is None


def test_repository_is_found_from_inside_a_subdirectory(repo: Path) -> None:
    nested = repo / "a" / "b"
    nested.mkdir(parents=True)

    repository = GitRepository.discover(nested)

    assert repository is not None
    assert repository.root == repo.resolve()


def test_status_reports_clean_repository_head_and_branch(
    repo: Path, git_env: dict[str, str]
) -> None:
    repository = GitRepository.discover(repo)
    assert repository is not None

    expected_head = run_git(["rev-parse", "HEAD"], cwd=repo, env=git_env).stdout.strip()
    status = repository.status(env=git_env)

    assert status.branch == "main"
    assert status.head == expected_head
    assert repository.head(env=git_env) == expected_head
    assert status.is_clean
    assert not status.is_dirty
    assert status.changed_paths == ()
    repository.ensure_clean(env=git_env)


def test_status_reports_untracked_and_modified_paths(repo: Path, git_env: dict[str, str]) -> None:
    repository = GitRepository.discover(repo)
    assert repository is not None

    (repo / "notes.txt").write_text("scratch\n", encoding="utf-8")
    untracked = repository.status(env=git_env)
    assert untracked.is_dirty
    assert "notes.txt" in untracked.changed_paths

    run_git(["add", "notes.txt"], cwd=repo, env=git_env)
    run_git(["commit", "-m", "add notes"], cwd=repo, env=git_env)
    (repo / "notes.txt").write_text("changed\n", encoding="utf-8")

    modified = repository.status(env=git_env)
    assert modified.is_dirty
    assert "notes.txt" in modified.changed_paths


def test_ensure_clean_refuses_dirty_work_tree(repo: Path) -> None:
    repository = GitRepository.discover(repo)
    assert repository is not None
    (repo / "uncommitted.txt").write_text("do not lose me\n", encoding="utf-8")

    with pytest.raises(DirtyRepositoryError, match="uncommitted.txt"):
        repository.ensure_clean()


def test_head_is_none_for_unborn_repository(tmp_path: Path, git_env: dict[str, str]) -> None:
    root = tmp_path / "fresh"
    root.mkdir()
    run_git(["init", "-b", "main"], cwd=root, env=git_env)
    repository = GitRepository.discover(root)
    assert repository is not None

    status = repository.status(env=git_env)

    assert repository.head(env=git_env) is None
    assert status.head is None
    assert status.branch == "main"
    assert status.is_clean


def test_run_git_raises_git_error_on_failure(repo: Path, git_env: dict[str, str]) -> None:
    with pytest.raises(GitError) as excinfo:
        run_git(["rev-parse", "--verify", "does-not-exist"], cwd=repo, env=git_env)

    assert excinfo.value.returncode != 0
    assert "rev-parse" in str(excinfo.value)


# ---------------------------------------------------------------------------
# Safe atomic writes
# ---------------------------------------------------------------------------


def test_atomic_write_creates_file_and_parents(tmp_path: Path) -> None:
    target = tmp_path / "nested" / "dir" / "file.txt"

    result = atomic_write_text(target, "hello\n")

    assert result.created
    assert result.changed
    assert result.previous_digest is None
    assert result.digest == sha256_text("hello\n") == sha256_file(target)
    assert target.read_text(encoding="utf-8") == "hello\n"
    assert [path.name for path in target.parent.iterdir()] == ["file.txt"]


def test_atomic_write_replaces_when_expected_digest_matches(tmp_path: Path) -> None:
    target = tmp_path / "file.txt"
    atomic_write_text(target, "first\n")
    digest = sha256_file(target)

    result = atomic_write_text(target, "second\n", expected_digest=digest)

    assert not result.created
    assert result.changed
    assert result.previous_digest == digest
    assert result.digest == sha256_text("second\n")
    assert target.read_text(encoding="utf-8") == "second\n"


def test_atomic_write_accepts_unprefixed_digest_and_reports_unchanged(
    tmp_path: Path,
) -> None:
    target = tmp_path / "file.txt"
    atomic_write_text(target, "same\n")
    digest = sha256_file(target)

    result = atomic_write_text(target, "same\n", expected_digest=digest.removeprefix("sha256:"))

    assert not result.created
    assert not result.changed
    assert target.read_text(encoding="utf-8") == "same\n"


def test_atomic_write_refuses_unprotected_overwrite_and_preserves_content(
    tmp_path: Path,
) -> None:
    target = tmp_path / "file.txt"
    target.write_text("human work\n", encoding="utf-8")

    with pytest.raises(UnsafeWriteError, match="without"):
        atomic_write_text(target, "agent output\n")

    assert target.read_text(encoding="utf-8") == "human work\n"
    assert sorted(path.name for path in tmp_path.iterdir()) == ["file.txt"]


def test_atomic_write_refuses_stale_expected_content(tmp_path: Path) -> None:
    target = tmp_path / "file.txt"
    target.write_text("human edited\n", encoding="utf-8")

    with pytest.raises(UnsafeWriteError, match="content differs"):
        atomic_write_text(target, "replacement\n", expected_content="generated\n")

    assert target.read_text(encoding="utf-8") == "human edited\n"


def test_atomic_write_refuses_stale_expected_digest(tmp_path: Path) -> None:
    target = tmp_path / "file.txt"
    target.write_text("human edited\n", encoding="utf-8")
    stale_digest = sha256_text("previous generated\n")

    with pytest.raises(UnsafeWriteError, match="does not match"):
        atomic_write_text(target, "replacement\n", expected_digest=stale_digest)

    assert target.read_text(encoding="utf-8") == "human edited\n"


def test_atomic_write_refuses_guard_for_missing_file(tmp_path: Path) -> None:
    target = tmp_path / "missing.txt"

    with pytest.raises(UnsafeWriteError, match="absent"):
        atomic_write_text(target, "content\n", expected_content="")

    assert not target.exists()


def test_atomic_write_rejects_two_guards(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="at most one"):
        atomic_write_text(
            tmp_path / "file.txt",
            "content\n",
            expected_content="",
            expected_digest=sha256_text(""),
        )


def test_atomic_write_preserves_file_mode(tmp_path: Path) -> None:
    target = tmp_path / "script.sh"
    atomic_write_text(target, "#!/bin/sh\n")
    os.chmod(target, 0o750)
    digest = sha256_file(target)

    atomic_write_text(target, "#!/bin/sh\necho hi\n", expected_digest=digest)

    assert stat.S_IMODE(target.stat().st_mode) == 0o750


def test_atomic_write_refuses_directory_target(tmp_path: Path) -> None:
    target = tmp_path / "directory"
    target.mkdir()

    with pytest.raises(UnsafeWriteError, match="directory"):
        atomic_write_text(target, "content\n")


def test_atomic_write_refuses_symlink_target(tmp_path: Path) -> None:
    original = tmp_path / "original"
    original.write_text("human work\n")
    link = tmp_path / "link"
    link.symlink_to(original)

    with pytest.raises(UnsafeWriteError, match="symlink"):
        atomic_write_text(link, "replacement\n", expected_content="human work\n")

    assert original.read_text() == "human work\n"
    assert link.is_symlink()


def test_sha256_helpers_are_prefixed_and_agree(tmp_path: Path) -> None:
    target = tmp_path / "file.txt"
    target.write_bytes(b"abc")

    assert sha256_text("abc") == sha256_file(target)
    assert sha256_text("abc").startswith("sha256:")
