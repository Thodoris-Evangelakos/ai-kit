"""Reusable system primitives for AI Kit.

This module is deliberately limited to deterministic, low-level operations that
other AI Kit components can share:

* Linux XDG base-directory resolution for AI Kit's global state;
* discovery and inspection of Git repositories via the installed ``git`` CLI;
* safe, atomic text writes that refuse to overwrite unexpected content.

It depends only on the Python standard library so that it stays cheap to import
and easy to test against real temporary repositories.
"""

from __future__ import annotations

import hashlib
import os
import stat
import subprocess
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

APP_NAME = "ai-kit"

XDG_CONFIG_HOME = "XDG_CONFIG_HOME"
XDG_DATA_HOME = "XDG_DATA_HOME"
XDG_CACHE_HOME = "XDG_CACHE_HOME"
XDG_STATE_HOME = "XDG_STATE_HOME"

_DEFAULT_CONFIG_RELATIVE = Path(".config")
_DEFAULT_DATA_RELATIVE = Path(".local/share")
_DEFAULT_CACHE_RELATIVE = Path(".cache")
_DEFAULT_STATE_RELATIVE = Path(".local/state")

_DIGEST_ALGORITHM = "sha256"
_DIGEST_PREFIX = f"{_DIGEST_ALGORITHM}:"


class GitError(RuntimeError):
    """Raised when a Git command fails or Git cannot be executed."""

    def __init__(self, message: str, *, returncode: int, stderr: str = "") -> None:
        super().__init__(message)
        self.returncode = returncode
        self.stderr = stderr


class DirtyRepositoryError(RuntimeError):
    """Raised when an operation requires a clean work tree but changes exist."""


class UnsafeWriteError(RuntimeError):
    """Raised when a write would overwrite content the caller did not expect."""


# ---------------------------------------------------------------------------
# XDG paths
# ---------------------------------------------------------------------------


def _xdg_home(
    env_var: str,
    default_relative: Path,
    *,
    env: Mapping[str, str],
    home: Path,
) -> Path:
    """Return an absolute XDG directory, honouring the base-directory spec.

    Relative or empty values are ignored, matching the XDG specification.
    """

    candidate = env.get(env_var, "").strip()
    if candidate and Path(candidate).is_absolute():
        return Path(candidate)
    return home / default_relative


@dataclass(frozen=True, slots=True)
class XdgPaths:
    """Resolved Linux XDG base directories and AI Kit's namespaced subdirectories."""

    config_home: Path
    data_home: Path
    cache_home: Path
    state_home: Path

    @classmethod
    def resolve(
        cls,
        *,
        env: Mapping[str, str] | None = None,
        home: Path | str | None = None,
    ) -> XdgPaths:
        """Resolve XDG directories from ``env`` with sensible Linux fallbacks."""

        environment = os.environ if env is None else env
        if home is None:
            home_value = environment.get("HOME", "").strip()
            home_path = Path(home_value) if home_value else Path.home()
        else:
            home_path = Path(home).expanduser()
        return cls(
            config_home=_xdg_home(
                XDG_CONFIG_HOME, _DEFAULT_CONFIG_RELATIVE, env=environment, home=home_path
            ),
            data_home=_xdg_home(
                XDG_DATA_HOME, _DEFAULT_DATA_RELATIVE, env=environment, home=home_path
            ),
            cache_home=_xdg_home(
                XDG_CACHE_HOME, _DEFAULT_CACHE_RELATIVE, env=environment, home=home_path
            ),
            state_home=_xdg_home(
                XDG_STATE_HOME, _DEFAULT_STATE_RELATIVE, env=environment, home=home_path
            ),
        )

    @property
    def config_dir(self) -> Path:
        return self.config_home / APP_NAME

    @property
    def data_dir(self) -> Path:
        return self.data_home / APP_NAME

    @property
    def cache_dir(self) -> Path:
        return self.cache_home / APP_NAME

    @property
    def state_dir(self) -> Path:
        return self.state_home / APP_NAME

    def ensure(self) -> XdgPaths:
        """Create AI Kit's global directories and return ``self``."""

        for directory in (self.config_dir, self.data_dir, self.cache_dir, self.state_dir):
            directory.mkdir(parents=True, exist_ok=True)
        return self


def resolve_xdg_paths(
    *,
    env: Mapping[str, str] | None = None,
    home: Path | str | None = None,
) -> XdgPaths:
    """Convenience wrapper around :meth:`XdgPaths.resolve`."""

    return XdgPaths.resolve(env=env, home=home)


# ---------------------------------------------------------------------------
# Git
# ---------------------------------------------------------------------------


def run_git(
    args: Sequence[str],
    *,
    cwd: Path | str,
    env: Mapping[str, str] | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    """Run ``git`` in ``cwd`` and return the completed process.

    ``run_git`` never invokes a shell, so arguments are passed through verbatim.
    When ``check`` is true a non-zero exit code raises :class:`GitError`.
    """

    command = ["git", "-C", str(Path(cwd)), *args]
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=None if env is None else dict(env),
            check=False,
        )
    except FileNotFoundError as exc:  # pragma: no cover - git is a hard requirement
        raise GitError("git executable not found", returncode=127) from exc

    if check and completed.returncode != 0:
        stderr = completed.stderr.strip()
        detail = stderr or completed.stdout.strip() or "git command failed"
        rendered = " ".join(args)
        raise GitError(
            f"git {rendered} failed with exit code {completed.returncode}: {detail}",
            returncode=completed.returncode,
            stderr=stderr,
        )
    return completed


@dataclass(frozen=True, slots=True)
class GitStatusEntry:
    """One entry from ``git status --porcelain=v2``."""

    status: str
    path: str
    raw: str


@dataclass(frozen=True, slots=True)
class GitStatus:
    """Typed snapshot of a repository's work tree and HEAD."""

    root: Path
    branch: str | None
    head: str | None
    entries: tuple[GitStatusEntry, ...]

    @property
    def is_dirty(self) -> bool:
        return bool(self.entries)

    @property
    def is_clean(self) -> bool:
        return not self.entries

    @property
    def changed_paths(self) -> tuple[str, ...]:
        return tuple(entry.path for entry in self.entries)


def _parse_status_line(line: str) -> GitStatusEntry:
    if line.startswith("? "):
        return GitStatusEntry(status="??", path=line[2:], raw=line)
    if line.startswith("! "):
        return GitStatusEntry(status="!!", path=line[2:], raw=line)
    if line.startswith("1 "):
        fields = line.split(" ", 8)
        return GitStatusEntry(status=fields[1], path=fields[8], raw=line)
    if line.startswith("2 "):
        fields = line.split(" ", 9)
        path = fields[9].split("\t", 1)[0]
        return GitStatusEntry(status=fields[1], path=path, raw=line)
    if line.startswith("u "):
        fields = line.split(" ", 10)
        return GitStatusEntry(status=fields[1], path=fields[10], raw=line)
    return GitStatusEntry(status="", path=line, raw=line)


@dataclass(frozen=True, slots=True)
class GitRepository:
    """A discovered Git repository identified by its work-tree root."""

    root: Path
    git_dir: Path

    @classmethod
    def discover(
        cls,
        start: Path | str = ".",
        *,
        env: Mapping[str, str] | None = None,
    ) -> GitRepository | None:
        """Find the repository containing ``start``, or return ``None``.

        ``start`` may point at a file or a directory that does not exist yet; the
        nearest existing ancestor is used for discovery.
        """

        probe = _nearest_existing_path(Path(start))
        if probe is None:
            return None

        top_level = run_git(["rev-parse", "--show-toplevel"], cwd=probe, env=env, check=False)
        if top_level.returncode != 0:
            return None

        git_dir = run_git(["rev-parse", "--absolute-git-dir"], cwd=probe, env=env, check=False)
        if git_dir.returncode != 0:
            return None

        root = Path(top_level.stdout.strip()).resolve()
        return cls(root=root, git_dir=Path(git_dir.stdout.strip()).resolve())

    def status(self, *, env: Mapping[str, str] | None = None) -> GitStatus:
        """Return HEAD, branch and work-tree entries in a single Git call."""

        completed = run_git(
            ["status", "--porcelain=v2", "--branch", "--untracked-files=all"],
            cwd=self.root,
            env=env,
        )
        head: str | None = None
        branch: str | None = None
        entries: list[GitStatusEntry] = []
        for line in completed.stdout.splitlines():
            if line.startswith("# branch.oid "):
                value = line[len("# branch.oid ") :]
                head = None if value == "(initial)" else value
            elif line.startswith("# branch.head "):
                value = line[len("# branch.head ") :]
                branch = None if value == "(detached)" else value
            elif line and not line.startswith("# "):
                entries.append(_parse_status_line(line))
        return GitStatus(root=self.root, branch=branch, head=head, entries=tuple(entries))

    def head(self, *, env: Mapping[str, str] | None = None) -> str | None:
        """Return the current HEAD commit, or ``None`` for an unborn branch."""

        completed = run_git(["rev-parse", "--verify", "HEAD"], cwd=self.root, env=env, check=False)
        if completed.returncode != 0:
            return None
        return completed.stdout.strip()

    def is_dirty(self, *, env: Mapping[str, str] | None = None) -> bool:
        return self.status(env=env).is_dirty

    def ensure_clean(self, *, env: Mapping[str, str] | None = None) -> None:
        """Raise :class:`DirtyRepositoryError` when the work tree has changes."""

        status = self.status(env=env)
        if status.is_dirty:
            paths = ", ".join(status.changed_paths)
            raise DirtyRepositoryError(f"repository {self.root} has uncommitted changes: {paths}")


def _nearest_existing_path(start: Path) -> Path | None:
    path = start.expanduser()
    if not path.is_absolute():
        path = Path.cwd() / path
    while not path.exists():
        if path == path.parent:
            return None
        path = path.parent
    return path


def discover_repository(
    start: Path | str = ".",
    *,
    env: Mapping[str, str] | None = None,
) -> GitRepository | None:
    """Convenience wrapper around :meth:`GitRepository.discover`."""

    return GitRepository.discover(start, env=env)


# ---------------------------------------------------------------------------
# Safe atomic writes
# ---------------------------------------------------------------------------


def _sha256_bytes(data: bytes) -> str:
    return _DIGEST_PREFIX + hashlib.sha256(data).hexdigest()


def sha256_bytes(data: bytes) -> str:
    """Return a ``sha256:<hex>`` digest for ``data``."""

    return _sha256_bytes(data)


def sha256_text(content: str, *, encoding: str = "utf-8") -> str:
    """Return a ``sha256:<hex>`` digest for ``content``."""

    return _sha256_bytes(content.encode(encoding))


def sha256_file(path: Path | str, *, chunk_size: int = 1 << 20) -> str:
    """Return a ``sha256:<hex>`` digest for the file at ``path``."""

    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        while chunk := handle.read(chunk_size):
            digest.update(chunk)
    return _DIGEST_PREFIX + digest.hexdigest()


def _normalize_digest(value: str) -> str:
    candidate = value.strip().lower()
    if candidate.startswith(_DIGEST_PREFIX):
        candidate = candidate[len(_DIGEST_PREFIX) :]
    if len(candidate) != 64 or any(char not in "0123456789abcdef" for char in candidate):
        raise ValueError(f"not a valid sha256 digest: {value!r}")
    return _DIGEST_PREFIX + candidate


@dataclass(frozen=True, slots=True)
class WriteResult:
    """Outcome of an :func:`atomic_write_text` call."""

    path: Path
    digest: str
    previous_digest: str | None
    created: bool

    @property
    def changed(self) -> bool:
        return self.previous_digest != self.digest


def atomic_write_text(
    path: Path | str,
    content: str,
    *,
    expected_content: str | None = None,
    expected_digest: str | None = None,
    encoding: str = "utf-8",
    create_parents: bool = True,
) -> WriteResult:
    """Write ``content`` to ``path`` atomically, guarding existing content.

    Safety semantics:

    * If ``path`` already exists, the caller must pass exactly one of
      ``expected_content`` or ``expected_digest``. The write proceeds only when
      the current file matches the expectation; otherwise
      :class:`UnsafeWriteError` is raised and the file is left untouched.
    * If ``path`` does not exist, expectations are treated as a failed guard:
      the caller claimed content that is not on disk.
    * The new content is written to a temporary file in the target directory,
      flushed, and moved into place with :func:`os.replace`, so readers never
      observe a partially written file.
    * The mode of an existing file is preserved across the replacement.
    """

    if expected_content is not None and expected_digest is not None:
        raise ValueError("provide at most one of expected_content or expected_digest")

    target = Path(path)
    if target.is_symlink():
        raise UnsafeWriteError(f"refusing to overwrite symlink {target}")
    exists = target.exists()
    previous_digest: str | None = None

    if target.is_dir():
        raise UnsafeWriteError(f"refusing to overwrite directory {target}")

    if exists:
        previous_digest = sha256_file(target)
        if expected_content is not None:
            if target.read_bytes() != expected_content.encode(encoding):
                raise UnsafeWriteError(
                    f"refusing to overwrite {target}: content differs from expected content"
                )
        elif expected_digest is not None:
            if previous_digest != _normalize_digest(expected_digest):
                raise UnsafeWriteError(
                    f"refusing to overwrite {target}: digest {previous_digest} "
                    f"does not match expected digest {expected_digest}"
                )
        else:
            raise UnsafeWriteError(
                f"refusing to overwrite existing file {target} without "
                "expected_content or expected_digest protection"
            )
    elif expected_content is not None or expected_digest is not None:
        raise UnsafeWriteError(
            f"refusing to write {target}: guard expects existing content but the file is absent"
        )

    parent = target.parent
    if create_parents:
        parent.mkdir(parents=True, exist_ok=True)
    if not parent.is_dir():
        raise UnsafeWriteError(f"parent directory does not exist: {parent}")

    previous_mode = stat.S_IMODE(target.stat().st_mode) if exists else None
    encoded = content.encode(encoding)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{target.name}.", suffix=".tmp", dir=parent
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        if previous_mode is not None:
            os.chmod(temporary_path, previous_mode)
        os.replace(temporary_path, target)
        _fsync_directory(parent)
    except BaseException:
        temporary_path.unlink(missing_ok=True)
        raise

    return WriteResult(
        path=target,
        digest=_sha256_bytes(encoded),
        previous_digest=previous_digest,
        created=not exists,
    )


def _fsync_directory(path: Path) -> None:
    try:
        descriptor = os.open(path, os.O_RDONLY)
    except OSError:  # pragma: no cover - platform dependent
        return
    try:
        os.fsync(descriptor)
    except OSError:  # pragma: no cover - platform dependent
        pass
    finally:
        os.close(descriptor)
