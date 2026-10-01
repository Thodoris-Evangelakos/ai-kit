"""Harness-assisted preparation, resumption, and finalization of a safe setup.

This module owns the deterministic half of an integration that a preferred
harness completes semantically.  It captures a private snapshot of the original
repository before any integration write, records a pending operation in
``ai-kit.lock``, exposes a shared Markdown protocol, and finalizes only when the
structured report, the preservation manifest, and the declared project checks
all agree.  It never imports the harness launcher or the terminal menu.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .adoption import (
    CheckResult,
    _recognized_check,
    _run_check,
    _stored_check,
    adopt_safe,
    compare_baseline,
)
from .project import (
    LOCK_SCHEMA_SETUP,
    PROFILE_SCHEMA,
    Profile,
    ProjectError,
    _lock_text,
    _managed_path,
    apply_managed_block,
    block_content,
    doctor_project,
    human_bytes,
    load_lock,
    load_profile,
    managed_block,
    read_managed_block,
    render_codex,
    sync_project,
)
from .system import (
    GitRepository,
    GitStatus,
    UnsafeWriteError,
    atomic_write_text,
    read_text_exact,
    run_git,
    sha256_file,
    sha256_text,
)

PROTOCOL_REL = ".ai/setup/protocol.md"
REPORT_REL = ".ai/setup/report.json"
SETUP_DIR_REL = ".ai/setup"
SNAPSHOT_REL = ".ai-local/setup/original"
MANIFEST_REL = ".ai-local/setup/manifest.json"
PENDING_REL = ".ai-local/setup/pending.json"
EVIDENCE_REL = ".ai-local/setup/evidence.json"
BASELINE_REL = ".ai/baseline.json"
CUSTODY_REL = ".ai/CUSTODY.toml"

# Finalization rewrites these management files; they are not part of the
# product content bound into the evidence digest.
_EVIDENCE_EXCLUDED = frozenset({"ai-kit.lock", BASELINE_REL})

# New paths a setup integration may introduce. Anything else is treated as
# unauthorized new source (or new authoritative intent/ADR/goals).
_NEW_ALLOWED_EXACT = frozenset(
    {
        ".gitignore",
        "dev",
        "ai-kit.lock",
        "AGENTS.md",
        "AGENTS.override.md",
        "CLAUDE.md",
        PROTOCOL_REL,
        REPORT_REL,
        MANIFEST_REL,
        PENDING_REL,
        EVIDENCE_REL,
        BASELINE_REL,
        CUSTODY_REL,
        ".ai/profile.toml",
        ".ai/current/STATUS.md",
        ".ai/current/FINDINGS.md",
        ".ai/intent/README.md",
        ".ai/decisions/README.md",
        ".ai/adoption/inventory.md",
        ".github/workflows/verify.yml",
    }
)
_NEW_ALLOWED_PREFIXES = (
    ".ai-local/",
    ".ai/setup/",
    ".ai/adoption/",
    ".ai/current/",
    ".agents/skills/",
)

# Untracked dependency/cache trees are excluded from the preservation manifest.
# Tracked files inside them are still captured.
_CACHE_DIRS = frozenset(
    {".venv", "node_modules", "__pycache__", ".pytest_cache", ".ruff_cache", ".ai-local"}
)

_DEFAULT_MODULES = ("strict-verification",)

# Profile modules that require named acceptance evidence before finalization.
_PROFILE_REQUIRED_CHECKS = {"webapp": ("acceptance", "runtime_errors")}


class SetupError(RuntimeError):
    """A setup operation is invalid, unsafe, or not yet verifiable."""


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def _git_root(root: Path) -> Path:
    candidate = Path(root).expanduser()
    repository = GitRepository.discover(candidate)
    if repository is None:
        raise SetupError(f"{candidate} is not inside a Git repository")
    resolved = repository.root
    if candidate.exists() and candidate.resolve() != resolved:
        raise SetupError(f"{candidate} is not a Git repository root (found {resolved})")
    return resolved


def _require_git_root(root: Path) -> Path:
    try:
        return _git_root(root)
    except ProjectError as exc:  # pragma: no cover - defensive
        raise SetupError(str(exc)) from exc


def _assert_no_symlink_prefix(root: Path, relative: str) -> Path:
    path = Path(relative)
    if path.is_absolute() or ".." in path.parts:
        raise SetupError(f"unsafe setup path: {relative}")
    current = root
    for part in path.parts:
        current = current / part
        if current.is_symlink():
            raise SetupError(f"refusing to write through symlink: {relative}")
    return root / path


def _tracked_paths(root: Path) -> set[str]:
    completed = run_git(["ls-files", "-z"], cwd=root, check=False)
    if completed.returncode != 0:
        raise SetupError("Git could not enumerate tracked files for the original snapshot")
    return {item for item in completed.stdout.split("\0") if item}


def _has_tracked(tracked: set[str], relative: str) -> bool:
    prefix = relative.rstrip("/") + "/"
    return any(item == relative or item.startswith(prefix) for item in tracked)


def _entry(root: Path, relative: str, tracked: set[str]) -> dict[str, object]:
    path = root / relative
    info = os.lstat(path)
    mode = f"{stat.S_IMODE(info.st_mode):04o}"
    if stat.S_ISLNK(info.st_mode):
        return {
            "path": relative,
            "type": "symlink",
            "target": os.readlink(path),
            "tracked": relative in tracked,
        }
    if not stat.S_ISREG(info.st_mode):
        raise SetupError(f"unsupported preservation shape (not a file or symlink): {relative}")
    return {
        "path": relative,
        "type": "file",
        "mode": mode,
        "digest": sha256_file(path),
        "tracked": relative in tracked,
    }


def _current_entries(root: Path, tracked: set[str] | None = None) -> list[dict[str, object]]:
    if tracked is None:
        tracked = _tracked_paths(root)
    entries: list[dict[str, object]] = []
    for dirpath, dirnames, filenames in os.walk(root, topdown=True, followlinks=False):
        current = Path(dirpath)
        relative_dir = current.relative_to(root)
        if relative_dir == Path("."):
            dirnames[:] = [name for name in dirnames if name != ".git"]
        for name in list(dirnames):
            child = current / name
            child_rel = (relative_dir / name).as_posix()
            if child.is_symlink():
                entries.append(_entry(root, child_rel, tracked))
                dirnames.remove(name)
            elif name in _CACHE_DIRS and not _has_tracked(tracked, child_rel):
                dirnames.remove(name)
        for name in filenames:
            child_rel = (relative_dir / name).as_posix()
            child = current / name
            if child.is_symlink():
                entries.append(_entry(root, child_rel, tracked))
                continue
            first = Path(child_rel).parts[0]
            if first in _CACHE_DIRS and child_rel not in tracked:
                continue
            entries.append(_entry(root, child_rel, tracked))
    entries.sort(key=lambda item: str(item["path"]))
    return entries


def _tree_digest(entries: list[dict[str, object]]) -> str:
    hasher = hashlib.sha256()
    for entry in sorted(entries, key=lambda item: str(item["path"])):
        hasher.update(
            "\0".join(
                str(entry.get(field, ""))
                for field in ("path", "type", "mode", "digest", "target", "tracked")
            ).encode("utf-8", "surrogateescape")
        )
        hasher.update(b"\n")
    return "sha256:" + hasher.hexdigest()


# ---------------------------------------------------------------------------
# Snapshot capture and validation
# ---------------------------------------------------------------------------


def _snapshot_dir(root: Path) -> Path:
    return root / SNAPSHOT_REL


def _write_snapshot(root: Path, entries: list[dict[str, object]]) -> None:
    snapshot = _snapshot_dir(root)
    if snapshot.exists():
        shutil.rmtree(snapshot)
    snapshot.mkdir(parents=True, mode=0o700)
    os.chmod(snapshot, 0o700)
    for entry in entries:
        destination = snapshot / str(entry["path"])
        destination.parent.mkdir(parents=True, exist_ok=True)
        if entry["type"] == "symlink":
            os.symlink(str(entry["target"]), destination)
        else:
            source = root / str(entry["path"])
            with open(source, "rb") as handle:
                data = handle.read()
            destination.write_bytes(data)
            os.chmod(destination, int(str(entry["mode"]), 8))


def _install_snapshot_git_metadata(root: Path, snapshot: Path) -> None:
    """Give the private snapshot its own Git metadata.

    Commands measured against the original tree must see the original HEAD and
    index (including staged and detached state) and must never resolve up into
    the live containing repository. A no-checkout, no-hardlink clone plus the
    original index and objects reproduces both without touching the source.
    """

    repository = GitRepository.discover(root)
    if repository is None:
        return
    destination = snapshot / ".git"
    if repository.head() is None:
        # An unborn original still needs local metadata so snapshot commands
        # cannot resolve up into the containing live repository.
        completed = run_git(["init", "-q"], cwd=snapshot, check=False)
        if completed.returncode != 0:
            raise SetupError("Git could not initialize the original snapshot")
    else:
        with tempfile.TemporaryDirectory(prefix="ai-kit-snapshot-") as temporary:
            clone = Path(temporary) / "clone"
            completed = run_git(
                ["clone", "--no-checkout", "--no-hardlinks", "--quiet", str(root), str(clone)],
                cwd=root,
                check=False,
            )
            cloned_git = clone / ".git"
            if completed.returncode != 0 or not cloned_git.is_dir():
                raise SetupError("Git could not copy the original snapshot metadata")
            if destination.exists():
                shutil.rmtree(destination)
            shutil.move(str(cloned_git), str(destination))
    objects = repository.git_dir / "objects"
    if objects.is_dir():
        shutil.copytree(objects, destination / "objects", dirs_exist_ok=True)
    names = ("HEAD", "index", "MERGE_HEAD", "CHERRY_PICK_HEAD", "ORIG_HEAD", "packed-refs")
    names += tuple(path.name for path in repository.git_dir.glob("sharedindex.*"))
    for name in names:
        source = repository.git_dir / name
        if source.is_file() and not source.is_symlink():
            shutil.copy2(source, destination / name)


def _capture_snapshot(root: Path) -> tuple[list[dict[str, object]], str, str, str]:
    _assert_no_symlink_prefix(root, SNAPSHOT_REL)
    _assert_no_symlink_prefix(root, MANIFEST_REL)
    _assert_no_symlink_prefix(root, PENDING_REL)
    tracked = _tracked_paths(root)
    entries = _current_entries(root, tracked)
    snapshot_digest = _tree_digest(entries)
    _write_snapshot(root, entries)
    _install_snapshot_git_metadata(root, _snapshot_dir(root))
    repository = GitRepository.discover(root)
    head = repository.head() if repository is not None else None
    _write_manifest(root, entries, head or "")
    manifest_digest = sha256_file(root / MANIFEST_REL)
    return entries, snapshot_digest, manifest_digest, head or ""


def _write_manifest(root: Path, entries: list[dict[str, object]], head: str = "") -> None:
    path = root / MANIFEST_REL
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"schema": 1, "head": head, "entries": entries}
    atomic_write_text(path, json.dumps(payload, indent=2, sort_keys=True) + "\n")


def _load_manifest(root: Path) -> tuple[str, list[dict[str, object]]]:
    path = root / MANIFEST_REL
    if path.is_symlink() or not path.is_file():
        raise SetupError(f"original preservation manifest is missing: {path}")
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError) as exc:
        raise SetupError(f"invalid preservation manifest {path}: {exc}") from exc
    if not isinstance(data, dict) or data.get("schema") != 1:
        raise SetupError(f"invalid preservation manifest schema: {path}")
    entries = data.get("entries")
    if not isinstance(entries, list) or any(not isinstance(item, dict) for item in entries):
        raise SetupError(f"invalid preservation manifest entries: {path}")
    head = data.get("head", "")
    if not isinstance(head, str):
        raise SetupError(f"invalid preservation manifest head: {path}")
    return head, list(entries)


def _validate_snapshot(
    root: Path, stored_digest: str, stored_manifest: str, expected_head: str | None = None
) -> list[dict[str, object]]:
    _assert_no_symlink_prefix(root, MANIFEST_REL)
    _assert_no_symlink_prefix(root, SNAPSHOT_REL)
    manifest_path = root / MANIFEST_REL
    actual_manifest = sha256_file(manifest_path) if manifest_path.is_file() else None
    if actual_manifest != stored_manifest:
        raise SetupError(
            "the private original manifest changed since preparation; "
            "re-run setup in a fresh copy of the repository"
        )
    head, entries = _load_manifest(root)
    if _tree_digest(entries) != stored_digest:
        raise SetupError("the captured snapshot identity no longer matches its manifest")
    if expected_head is not None and head != expected_head:
        raise SetupError("the captured original HEAD no longer matches the pending record")
    snapshot = _snapshot_dir(root)
    if not snapshot.is_dir():
        raise SetupError(f"the private original snapshot is missing: {snapshot}")
    repository = GitRepository.discover(snapshot)
    if (
        (snapshot / ".git").is_symlink()
        or repository is None
        or repository.root != snapshot
        or repository.head() != (head or None)
    ):
        raise SetupError("the original snapshot Git metadata or HEAD changed")
    for entry in entries:
        captured = snapshot / str(entry["path"])
        if entry["type"] == "symlink":
            if not captured.is_symlink() or os.readlink(captured) != str(entry["target"]):
                raise SetupError(f"captured symlink changed: {entry['path']}")
            continue
        if not captured.is_file() or captured.is_symlink():
            raise SetupError(f"captured file missing: {entry['path']}")
        captured_mode = f"{stat.S_IMODE(os.stat(captured).st_mode):04o}"
        if captured_mode != entry.get("mode"):
            raise SetupError(f"captured file mode changed: {entry['path']}")
        if sha256_file(captured) != entry["digest"]:
            raise SetupError(f"captured file changed: {entry['path']}")
    return entries


# ---------------------------------------------------------------------------
# Preflight and integration writes
# ---------------------------------------------------------------------------


def _load_lock_or_none(root: Path):
    if (root / "ai-kit.lock").is_symlink():
        raise SetupError("refusing symlinked ai-kit.lock")
    if not (root / "ai-kit.lock").is_file():
        return None
    try:
        return load_lock(root)
    except ProjectError as exc:
        raise SetupError(str(exc)) from exc


def _require_setup_record(setup: dict[str, object]) -> dict[str, object]:
    """Bounded format/type validation so malformed locks fail actionably."""

    problems: list[str] = []
    for key in ("snapshot_digest", "manifest_digest"):
        value = setup.get(key)
        if not isinstance(value, str) or not value:
            problems.append(key)
    if not isinstance(setup.get("head", ""), str):
        problems.append("head")
    if problems:
        raise SetupError(
            "the pending setup lock is missing or invalid; re-run ai-kit setup: "
            + ", ".join(problems)
        )
    return setup


def _head_failures(root: Path, setup: dict[str, object]) -> list[str]:
    recorded = str(setup.get("head", ""))
    current = _head(root) or ""
    if recorded != current:
        return [
            "the repository HEAD changed after preparation; commits are not allowed during setup"
        ]
    return []


def _index_failures(root: Path, snapshot: Path) -> list[str]:
    if not (snapshot / ".git").exists():
        return []
    live = run_git(["ls-files", "--stage", "-z"], cwd=root, check=False)
    original = run_git(["ls-files", "--stage", "-z"], cwd=snapshot, check=False)
    if live.returncode != 0 or original.returncode != 0:
        return ["Git could not independently confirm the original repository index"]
    if live.stdout != original.stdout:
        return [
            "the repository index changed after preparation; staging is not allowed during setup"
        ]
    return []


def _integration_write_paths(root: Path, lock) -> list[str]:
    rendered = render_codex(Profile(PROFILE_SCHEMA, "software", _DEFAULT_MODULES))
    paths = {PROTOCOL_REL, REPORT_REL, MANIFEST_REL, PENDING_REL, EVIDENCE_REL, "ai-kit.lock"}
    paths.update(rendered)
    paths.update(
        {
            ".ai/profile.toml",
            ".ai/current/STATUS.md",
            ".ai/current/FINDINGS.md",
            ".ai/intent/README.md",
            ".ai/decisions/README.md",
            ".gitignore",
            "dev",
            "AGENTS.md",
            "CLAUDE.md",
            "AGENTS.override.md",
        }
    )
    if lock is not None:
        paths.update(lock.generated)
        paths.update(lock.blocks)
    return sorted(paths)


def _preflight(root: Path, lock) -> None:
    for relative in _integration_write_paths(root, lock):
        _assert_no_symlink_prefix(root, relative)
    profile = root / ".ai/profile.toml"
    if lock is None and (profile.exists() or profile.is_symlink()):
        raise SetupError(
            ".ai/profile.toml exists without a compatible ai-kit.lock; "
            "keep the custom profile and adopt manually, or move it aside and re-run setup"
        )
    if lock is None:
        rendered = render_codex(Profile(PROFILE_SCHEMA, "software", _DEFAULT_MODULES))
        for relative, _content in rendered.items():
            if relative == "AGENTS.md":
                continue
            if (root / relative).exists() and not (root / relative).is_symlink():
                raise SetupError(
                    f"{relative} already exists as a human-owned file; "
                    "move it aside or choose a different profile before setup"
                )
    # Marker collisions in pre-existing instruction files are never resolved silently.
    for relative in ("AGENTS.md", "AGENTS.override.md", "CLAUDE.md"):
        path = root / relative
        if path.is_file() and not path.is_symlink():
            try:
                read_managed_block(read_text_exact(path), relative)
            except ProjectError as exc:
                raise SetupError(str(exc)) from exc


def _write_if_absent(root: Path, relative: str, content: str) -> None:
    path = root / relative
    if path.exists() or path.is_symlink():
        return
    atomic_write_text(path, content)


def _ensure_gitignore(root: Path) -> None:
    path = root / ".gitignore"
    if path.is_symlink():
        raise SetupError("refusing symlinked .gitignore")
    if not path.exists():
        atomic_write_text(path, ".ai-local/\n")
        return
    original = read_text_exact(path)
    if ".ai-local/" in original.splitlines():
        return
    addition = ("" if original.endswith("\n") or not original else "\n") + ".ai-local/\n"
    atomic_write_text(path, original + addition, expected_content=original)


def _ensure_dev(root: Path) -> None:
    path = root / "dev"
    if path.exists() or path.is_symlink():
        return
    atomic_write_text(path, _dev_stub())
    os.chmod(path, 0o755)


def _dev_stub() -> str:
    return (
        "#!/bin/sh\n"
        "set -eu\n"
        'case "${1:-}" in\n'
        "  setup|check|verify)\n"
        '    echo "./dev $1 is unconfigured; add this project\'s real checks" >&2\n'
        "    exit 2 ;;\n"
        "  *)\n"
        '    echo "usage: ./dev {setup|check|verify}" >&2\n'
        "    exit 2 ;;\n"
        "esac\n"
    )


def _configure_blocks(root: Path, lock, modules: tuple[str, ...]) -> tuple[dict[str, str], bool]:
    profile = Profile(PROFILE_SCHEMA, "software", modules)
    targets = ["AGENTS.md", "CLAUDE.md"]
    if (root / "AGENTS.override.md").is_file():
        targets.append("AGENTS.override.md")
    blocks: dict[str, str] = {}
    converted = False
    for relative in targets:
        path = root / relative
        content = block_content(relative, profile, setup=True)
        if path.is_symlink():
            raise SetupError(f"refusing symlinked instruction file: {relative}")
        if not path.exists():
            atomic_write_text(path, managed_block(content))
        else:
            text = read_text_exact(path)
            digest_before = sha256_file(path)
            try:
                existing = read_managed_block(text, relative)
            except ProjectError as exc:
                raise SetupError(str(exc)) from exc
            if existing is not None:
                atomic_write_text(
                    path,
                    apply_managed_block(text, relative, content),
                    expected_digest=digest_before,
                )
            elif (
                lock is not None
                and relative in lock.generated
                and sha256_text(text) == lock.generated[relative]
            ):
                # An explicit, reviewed conversion of a clean whole-file router.
                atomic_write_text(path, managed_block(content), expected_digest=digest_before)
                converted = True
            elif lock is not None and relative in lock.generated:
                raise SetupError(
                    f"{relative} is a drifted AI Kit router; resolve the drift before conversion"
                )
            else:
                atomic_write_text(
                    path,
                    apply_managed_block(text, relative, content),
                    expected_digest=digest_before,
                )
        blocks[relative] = sha256_text(managed_block(content))
    return blocks, converted


def _integrate(root: Path, lock, modules: tuple[str, ...]) -> tuple[dict[str, str], bool]:
    for relative in _integration_write_paths(root, lock):
        _assert_no_symlink_prefix(root, relative)
    for directory in (
        ".ai",
        ".ai/current",
        ".ai/intent",
        ".ai/decisions",
        ".ai/goals/active",
        ".ai/goals/accepted",
        ".ai/setup",
        ".ai-local",
        ".ai-local/learning",
    ):
        _assert_no_symlink_prefix(root, directory).mkdir(parents=True, exist_ok=True)
    module_list = ", ".join(f'"{name}"' for name in modules)
    _write_if_absent(
        root, ".ai/profile.toml", f'schema = 1\nbase = "software"\nmodules = [{module_list}]\n'
    )
    _write_if_absent(
        root,
        ".ai/current/STATUS.md",
        "# Current status\n\nGoal: none\nState: idle\nCompleted: none\n"
        "Remaining: none\nBlocked: no\n",
    )
    _write_if_absent(
        root, ".ai/current/FINDINGS.md", "# Current findings\n\nNo current findings.\n"
    )
    _write_if_absent(
        root,
        ".ai/intent/README.md",
        "# Approved intent\n\nPlace human-approved requirements and invariants here. "
        "Agents propose changes; they do not silently rewrite intent.\n",
    )
    _write_if_absent(
        root,
        ".ai/decisions/README.md",
        "# Accepted decisions\n\nPlace durable, accepted architectural decisions here. "
        "Git keeps the history.\n",
    )
    rendered = render_codex(Profile(PROFILE_SCHEMA, "software", modules))
    rendered.pop("AGENTS.md", None)
    for relative, content in sorted(rendered.items()):
        path = _managed_path(root, relative)
        if path.exists() or path.is_symlink():
            if not path.is_file() or path.is_symlink():
                raise SetupError(f"cannot install generated file over {relative}")
            if sha256_text(read_text_exact(path)) != sha256_text(content):
                raise SetupError(
                    f"{relative} already exists and differs from the generated file; "
                    "move the human file aside before setup"
                )
            continue
        atomic_write_text(path, content)
    _ensure_gitignore(root)
    _ensure_dev(root)
    _write_if_absent(root, PROTOCOL_REL, PROTOCOL_TEXT)
    return _configure_blocks(root, lock, modules)


def _determine_modules(root: Path, lock) -> tuple[str, ...]:
    if lock is not None:
        try:
            return load_profile(root).modules
        except ProjectError as exc:
            raise SetupError(str(exc)) from exc
    return _DEFAULT_MODULES


def _write_lock(root: Path, lock, blocks: dict[str, str], setup: dict[str, object]) -> None:
    profile_digest = sha256_file(root / ".ai/profile.toml")
    modules = load_profile(root).modules
    rendered = render_codex(Profile(PROFILE_SCHEMA, "software", modules))
    rendered.pop("AGENTS.md", None)
    generated = {path: sha256_text(content) for path, content in rendered.items()}
    text = _lock_text(
        profile_digest, generated, blocks=blocks, setup=setup, schema=LOCK_SCHEMA_SETUP
    )
    path = root / "ai-kit.lock"
    if path.exists():
        atomic_write_text(path, text, expected_content=path.read_text())
    else:
        atomic_write_text(path, text)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def _locked_state_failures(root: Path, lock) -> list[str]:
    """Validate locked blocks and generated files before any reconciliation."""

    failures: list[str] = []
    for relative, digest in sorted(lock.blocks.items()):
        path = root / relative
        if not path.is_file() or path.is_symlink():
            failures.append(f"managed instruction file missing: {relative}")
            continue
        try:
            block = read_managed_block(read_text_exact(path), relative)
        except ProjectError as exc:
            failures.append(str(exc))
            continue
        if block is None:
            failures.append(f"managed block missing: {relative}")
        elif sha256_text(block) != digest:
            failures.append(f"managed block drift: {relative}")
    for relative, digest in sorted(lock.generated.items()):
        path = root / relative
        if not path.is_file() or path.is_symlink():
            failures.append(f"generated file missing: {relative}")
            continue
        if sha256_file(path) != digest:
            failures.append(f"generated file drift: {relative}")
    return failures


def _write_pending_marker(root: Path, record: dict[str, object]) -> None:
    path = root / PENDING_REL
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(record, indent=2, sort_keys=True) + "\n"
    if path.is_file():
        atomic_write_text(path, text, expected_content=read_text_exact(path))
    else:
        atomic_write_text(path, text)


def _load_pending_marker(root: Path) -> dict[str, object]:
    path = root / PENDING_REL
    if path.is_symlink() or not path.is_file():
        raise SetupError(f"pending setup marker is missing: {path}")
    try:
        data = json.loads(read_text_exact(path))
    except (OSError, ValueError) as exc:
        raise SetupError(f"invalid pending setup marker: {exc}") from exc
    if not isinstance(data, dict) or data.get("schema") != 1:
        raise SetupError(f"invalid pending setup marker schema: {path}")
    for key in ("snapshot_digest", "manifest_digest", "head"):
        if not isinstance(data.get(key, ""), str):
            raise SetupError(f"invalid pending setup marker field {key}: {path}")
    if not data.get("snapshot_digest") or not data.get("manifest_digest"):
        raise SetupError(f"pending setup marker is incomplete: {path}")
    return data


def _router_converted(root: Path, lock) -> bool:
    """Whether a clean legacy whole-file router was converted to a block."""

    if lock is None or "AGENTS.md" not in lock.generated:
        return False
    captured = _snapshot_dir(root) / "AGENTS.md"
    if not captured.is_file() or captured.is_symlink():
        return False
    return sha256_file(captured) == lock.generated["AGENTS.md"]


def _adopt_initial_baseline(root: Path, timeout: float, source: GitStatus) -> None:
    """Measure and record the original baseline before the harness runs.

    Eligible: an unmanaged, committed repository without an adoption baseline
    yet. Custom ``.ai`` documents are preserved because adoption only adds its
    own artifacts. The private snapshot is captured first, so adoption records
    are never mistaken for original protected content.
    """

    if (
        (root / BASELINE_REL).exists()
        or (root / CUSTODY_REL).exists()
        or (root / ".ai/adoption").exists()
    ):
        return
    repository = GitRepository.discover(root)
    if repository is None or repository.head() is None:
        return
    adopt_safe(root, timeout=timeout, original_source=source)


def _baseline_core_digest(root: Path) -> str | None:
    """Digest the recorded obligations, ignoring setup discovery appendices.

    The pending record stores this so a harness cannot later delete or rewrite
    the inherited obligations it was handed. Discovery entries appended during
    finalization are excluded, keeping interrupted re-finalization idempotent.
    """

    path = root / BASELINE_REL
    if path.is_symlink() or not path.is_file():
        return None
    try:
        data = json.loads(read_text_exact(path))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    core = dict(data)
    checks = core.get("checks")
    if isinstance(checks, dict):
        core["checks"] = {
            key: value
            for key, value in checks.items()
            if not (isinstance(value, dict) and value.get("discovery") is True)
        }
    return sha256_text(json.dumps(core, sort_keys=True))


def _resume_from_marker(
    root: Path, lock, pending: dict[str, object]
) -> tuple[dict[str, str], dict[str, object]]:
    head = str(pending.get("head") or "")
    _validate_snapshot(
        root,
        str(pending["snapshot_digest"]),
        str(pending["manifest_digest"]),
        expected_head=head,
    )
    modules = _determine_modules(root, lock)
    blocks, _converted = _integrate(root, lock, modules)
    record = _pending_record(
        str(pending["snapshot_digest"]),
        str(pending["manifest_digest"]),
        head,
        _router_converted(root, lock),
        _baseline_core_digest(root),
    )
    return blocks, record


def prepare_setup(root: Path, *, timeout: float = 60.0) -> Path:
    """Prepare or resume a pending integration and return the protocol path."""

    if timeout <= 0:
        raise ValueError("timeout must be positive")
    repository_root = _require_git_root(root)
    lock = _load_lock_or_none(repository_root)
    if lock is not None and lock.setup and lock.setup.get("state") == "completed":
        return repository_root / PROTOCOL_REL
    pending_path = repository_root / PENDING_REL
    if lock is not None and lock.setup and lock.setup.get("state") == "pending":
        # Resuming a committed pending setup: validate ownership before any writes.
        setup = _require_setup_record(dict(lock.setup))
        failures = _locked_state_failures(repository_root, lock)
        if failures:
            raise SetupError(
                "cannot resume setup; the repository drifted from AI Kit ownership: "
                + "; ".join(failures)
            )
        _validate_snapshot(
            repository_root,
            str(setup["snapshot_digest"]),
            str(setup["manifest_digest"]),
            expected_head=str(setup.get("head", "")),
        )
        modules = _determine_modules(repository_root, lock)
        blocks, _converted = _integrate(repository_root, lock, modules)
        _write_lock(repository_root, lock, blocks, setup)
        return repository_root / PROTOCOL_REL

    if pending_path.is_file() and (lock is None or lock.setup is None):
        # Interrupted after the snapshot but before the schema-2 lock was written.
        # A legacy schema-1 lock may still be in place; resume without recapturing.
        pending = _load_pending_marker(repository_root)
        blocks, record = _resume_from_marker(repository_root, lock, pending)
        _write_lock(repository_root, lock, blocks, record)
        return repository_root / PROTOCOL_REL

    _preflight(repository_root, lock)
    modules = _determine_modules(repository_root, lock)
    repository = GitRepository.discover(repository_root)
    if repository is None:
        raise SetupError("Git repository disappeared before snapshot preparation")
    source = repository.status()
    entries, snapshot_digest, manifest_digest, head = _capture_snapshot(repository_root)
    del entries
    # Measure the inherited baseline before the harness receives the protocol.
    if lock is None:
        _adopt_initial_baseline(repository_root, timeout, source)
    _write_pending_marker(
        repository_root,
        {
            "schema": 1,
            "head": head,
            "snapshot_digest": snapshot_digest,
            "manifest_digest": manifest_digest,
        },
    )
    blocks, _converted = _integrate(repository_root, lock, modules)
    converted = _router_converted(repository_root, lock)
    _write_lock(
        repository_root,
        lock,
        blocks,
        _pending_record(
            snapshot_digest,
            manifest_digest,
            head,
            converted,
            _baseline_core_digest(repository_root),
        ),
    )
    return repository_root / PROTOCOL_REL


def _pending_record(
    snapshot_digest: object,
    manifest_digest: object,
    head: str,
    converted: bool,
    baseline_core_digest: str | None = None,
) -> dict[str, object]:
    record: dict[str, object] = {
        "kind": "setup",
        "state": "pending",
        "protocol": PROTOCOL_REL,
        "protocol_digest": sha256_text(PROTOCOL_TEXT),
        "report": REPORT_REL,
        "snapshot": SNAPSHOT_REL,
        "snapshot_digest": str(snapshot_digest),
        "manifest": MANIFEST_REL,
        "manifest_digest": str(manifest_digest),
        "head": head,
        "converted_router": "true" if converted else "false",
        "steps": ["snapshot", "scaffolding", "instructions", "protocol"],
    }
    if baseline_core_digest:
        record["baseline_core_digest"] = baseline_core_digest
    return record


def setup_status(root: Path) -> str:
    """Return ``pending``, ``completed``, or ``absent`` for the setup operation."""

    candidate = Path(root)
    if (candidate / "ai-kit.lock").is_symlink():
        return "absent"
    if not (candidate / "ai-kit.lock").is_file():
        return "absent"
    try:
        lock = load_lock(candidate)
    except (ProjectError, OSError, ValueError):
        return "absent"
    if lock.setup and lock.setup.get("state") == "completed":
        # A completed lock is only honest when its independently written
        # evidence exists; otherwise the setup is still pending.
        evidence = candidate / EVIDENCE_REL
        return "completed" if evidence.is_file() and not evidence.is_symlink() else "pending"
    if lock.setup and lock.setup.get("state") == "pending":
        return "pending"
    return "absent"


# ---------------------------------------------------------------------------
# Finalization
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Report:
    context: tuple[str, ...]
    checks: dict[str, tuple[str, ...]]
    baseline_checks: dict[str, tuple[str, ...]]
    unresolved: tuple[str, ...]


def _required_check_names(modules: tuple[str, ...]) -> set[str]:
    required = {"verify"}
    for module in modules:
        required.update(_PROFILE_REQUIRED_CHECKS.get(module, ()))
    return required


def _as_argv(value: object, label: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not value or any(not isinstance(item, str) for item in value):
        raise SetupError(f"{label}: expected a non-empty list of strings (argv), got {value!r}")
    return tuple(value)


def _load_report(root: Path) -> _Report:
    path = root / REPORT_REL
    if path.is_symlink() or not path.is_file():
        raise SetupError(f"setup report is missing: {path}")
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError) as exc:
        raise SetupError(f"invalid setup report {path}: {exc}") from exc
    if not isinstance(data, dict) or data.get("schema") != 1:
        raise SetupError(f"unsupported setup report schema in {path}")
    context = data.get("context", [])
    unresolved = data.get("unresolved", [])
    if not isinstance(context, list) or any(not isinstance(item, str) for item in context):
        raise SetupError(f"{path}: context must be a list of repo-relative strings")
    if not isinstance(unresolved, list) or any(not isinstance(item, str) for item in unresolved):
        raise SetupError(f"{path}: unresolved must be a list of strings")
    raw_checks = data.get("checks")
    if not isinstance(raw_checks, dict) or not raw_checks:
        raise SetupError(f"{path}: checks must be a non-empty object")
    checks = {
        name: _as_argv(value, f"{path}: checks[{name}]") for name, value in raw_checks.items()
    }
    raw_baseline = data.get("baseline_checks", {})
    if not isinstance(raw_baseline, dict):
        raise SetupError(f"{path}: baseline_checks must be an object")
    baseline = {
        name: _as_argv(value, f"{path}: baseline_checks[{name}]")
        for name, value in raw_baseline.items()
    }
    return _Report(tuple(context), checks, baseline, tuple(unresolved))


def _validate_context(root: Path, report: _Report) -> list[str]:
    failures: list[str] = []
    for reference in report.context:
        candidate = Path(reference)
        if candidate.is_absolute() or ".." in candidate.parts:
            failures.append(f"context reference escapes the repository: {reference}")
            continue
        path = root / candidate
        if not path.exists() or path.is_symlink() or not path.resolve().is_relative_to(root):
            failures.append(f"context reference does not exist: {reference}")
    return failures


def _required_check_failures(modules: tuple[str, ...], report: _Report) -> list[str]:
    failures: list[str] = []
    for name in sorted(_required_check_names(modules)):
        if name not in report.checks:
            failures.append(f"required check missing from report: {name}")
    if report.unresolved:
        failures.append("report has unresolved items: " + "; ".join(report.unresolved))
    return failures


_INSTRUCTION_FILES = ("AGENTS.md", "AGENTS.override.md", "CLAUDE.md")


def _is_integration_path(relative: str) -> bool:
    if relative in _NEW_ALLOWED_EXACT:
        return True
    return any(relative.startswith(prefix) for prefix in _NEW_ALLOWED_PREFIXES)


def _gitignore_failures(root: Path, entries: list[dict[str, object]]) -> list[str]:
    path = root / ".gitignore"
    if path.is_symlink():
        return ["gitignore became a symlink"]
    original = next((entry for entry in entries if entry["path"] == ".gitignore"), None)
    if original is None or original["type"] != "file":
        # A repository without a .gitignore gains an AI Kit-managed
        # ``.ai-local/`` line; removing it would expose private state.
        if not path.is_file():
            return ["gitignore is missing"]
        if b".ai-local/" not in path.read_bytes().splitlines():
            return ["gitignore no longer ignores .ai-local/"]
        return []
    captured = _snapshot_dir(root) / ".gitignore"
    try:
        original_bytes = captured.read_bytes()
        current_bytes = path.read_bytes()
    except OSError:
        return ["gitignore is missing"]
    allowed = {original_bytes}
    if b".ai-local/" not in original_bytes.splitlines():
        addition = (
            b"" if original_bytes.endswith(b"\n") or not original_bytes else b"\n"
        ) + b".ai-local/\n"
        allowed.add(original_bytes + addition)
    if current_bytes not in allowed:
        return ["gitignore human bytes were modified beyond adding .ai-local/"]
    if f"{stat.S_IMODE(os.stat(path).st_mode):04o}" != original.get("mode"):
        return ["gitignore mode changed from the original"]
    return []


def _dev_failures(root: Path, entries: list[dict[str, object]]) -> list[str]:
    original = next((entry for entry in entries if entry["path"] == "dev"), None)
    if original is None:
        return []
    path = root / "dev"
    if original["type"] == "symlink":
        if not path.is_symlink() or os.readlink(path) != str(original["target"]):
            return ["inherited dev symlink changed"]
        return []
    if path.is_symlink() or not path.is_file():
        return ["inherited dev was removed or replaced by a non-file"]
    original_mode = int(str(original.get("mode", "0")), 8)
    current_mode = stat.S_IMODE(path.stat().st_mode)
    if original_mode & 0o111:
        if current_mode != original_mode:
            return ["inherited executable dev mode changed from the original"]
    elif current_mode & ~0o111 != original_mode:
        return ["inherited dev permissions changed beyond adding execute permission"]
    return []


def _preservation_failures(
    root: Path, entries: list[dict[str, object]], lock, setup: dict[str, object]
) -> list[str]:
    failures: list[str] = []
    original = {str(entry["path"]): entry for entry in entries}
    current_entries = _current_entries(root)
    current = {str(entry["path"]): entry for entry in current_entries}
    managed = set(lock.blocks)
    converted = str(setup.get("converted_router", "false")) == "true"
    for relative, before in original.items():
        after = current.get(relative)
        if after is None:
            failures.append(f"protected path was removed: {relative}")
            continue
        if relative in managed:
            continue
        if relative == "ai-kit.lock":
            # AI Kit owns and deterministically rewrites the lock; it is
            # validated by the lock reader, not treated as protected content.
            continue
        if relative == ".gitignore":
            continue
        if relative == "dev":
            continue
        if (
            after["type"] != before["type"]
            or after.get("digest") != before.get("digest")
            or after.get("target") != before.get("target")
            or after.get("mode") != before.get("mode")
        ):
            failures.append(f"protected content changed: {relative}")
    for relative in current:
        if relative not in original and not _is_integration_path(relative):
            failures.append(f"new path outside the integration scope: {relative}")
    failures.extend(_gitignore_failures(root, entries))
    failures.extend(_dev_failures(root, entries))
    failures.extend(_block_failures(root, lock))
    failures.extend(_instruction_failures(root, entries, managed, converted))
    return failures


def _block_failures(root: Path, lock) -> list[str]:
    failures: list[str] = []
    for relative, digest in sorted(lock.blocks.items()):
        path = root / relative
        if not path.is_file() or path.is_symlink():
            failures.append(f"managed instruction file missing: {relative}")
            continue
        try:
            block = read_managed_block(read_text_exact(path), relative)
        except ProjectError as exc:
            failures.append(str(exc))
            continue
        if block is None:
            failures.append(f"managed block missing: {relative}")
        elif sha256_text(block) != digest:
            failures.append(f"managed block drift: {relative}")
    return failures


def _instruction_failures(
    root: Path,
    entries: list[dict[str, object]],
    managed: set[str],
    converted: bool,
) -> list[str]:
    failures: list[str] = []
    original = {str(entry["path"]): entry for entry in entries}
    for relative in _INSTRUCTION_FILES:
        before = original.get(relative)
        path = root / relative
        if before is None:
            continue
        if relative not in managed:
            continue
        if not path.is_file():
            failures.append(f"instruction file missing: {relative}")
            continue
        mode = f"{stat.S_IMODE(os.stat(path).st_mode):04o}"
        if mode != before.get("mode"):
            failures.append(f"instruction file mode changed: {relative}")
        if relative == "AGENTS.md" and converted:
            # A converted whole-file router becomes managed content wholesale,
            # so only its mode is compared; the block digest is checked above.
            continue
        captured = _snapshot_dir(root) / relative
        if before["type"] == "symlink":
            continue
        try:
            original_text = read_text_exact(captured)
            current_text = read_text_exact(path)
        except OSError as exc:  # pragma: no cover - defensive
            failures.append(f"could not read instruction file {relative}: {exc}")
            continue
        if human_bytes(current_text, relative) != human_bytes(original_text, relative):
            failures.append(
                f"human instruction bytes changed outside the managed block: {relative}"
            )
    return failures


def _check_passes(result: CheckResult) -> str | None:
    if result.status == "pass":
        return None
    if result.exit_code is None:
        return f"check unavailable ({result.reason})"
    return f"check {result.status} with exit code {result.exit_code} ({result.reason})"


def _run_argv(root: Path, argv: tuple[str, ...], timeout: float) -> CheckResult:
    return _run_check(root, argv, timeout)


def _record_baseline(
    root: Path, records: dict[str, CheckResult], resolved: set[str] | None = None
) -> None:
    path = root / BASELINE_REL
    if path.is_symlink():
        raise SetupError(f"refusing symlinked baseline: {path}")
    if path.is_file():
        original_text = read_text_exact(path)
        try:
            data = json.loads(original_text)
        except ValueError as exc:
            raise SetupError(f"invalid adoption baseline {path}: {exc}") from exc
        if not isinstance(data, dict) or data.get("schema") != 1:
            raise SetupError(f"invalid adoption baseline: {path}")
    else:
        original_text = None
        data = {
            "schema": 1,
            "commit": str(_head(root) or ""),
            "source": {},
            "status": "unknown",
            "checks": {},
        }
    checks = data.setdefault("checks", {})
    if not isinstance(checks, dict):
        raise SetupError(f"invalid adoption baseline checks: {path}")
    resolved = resolved or set()
    for name, result in records.items():
        existing = checks.get(name)
        # Idempotent re-recording keeps the same key so repeated finalization
        # never duplicates a discovered obligation.
        if existing is None or (isinstance(existing, dict) and existing.get("discovery") is True):
            key = name
        else:
            key = f"discovered/{name}"
        entry = {
            "status": result.status,
            "command": list(result.command) if result.command else None,
            "exit_code": result.exit_code,
            "output": result.output,
            "reason": result.reason,
            "counts": result.counts,
            "discovery": True,
        }
        if isinstance(existing, dict) and existing.get("status") == "unknown" and name in resolved:
            # Keep the original unknown observation as history and record the
            # concrete command that deterministically resolved it.
            entry["resolves"] = name
            entry["resolved_from"] = {
                "status": existing.get("status"),
                "reason": existing.get("reason"),
            }
        checks[key] = entry
    text = json.dumps(data, indent=2, sort_keys=True) + "\n"
    if original_text is not None:
        atomic_write_text(path, text, expected_content=original_text)
    else:
        atomic_write_text(path, text)


def _head(root: Path) -> str | None:
    repository = GitRepository.discover(root)
    return repository.head() if repository is not None else None


def _discovered_baseline(
    root: Path,
    snapshot: Path,
    name: str,
    argv: tuple[str, ...],
    timeout: float,
    *,
    strict: bool = False,
) -> tuple[CheckResult, CheckResult, list[str]]:
    failures: list[str] = []
    before = _run_argv(snapshot, argv, timeout)
    after = _run_argv(root, argv, timeout)
    if strict:
        # A discovered obligation is only a deterministic resolution when the
        # concrete command passes unchanged against the original snapshot and
        # the integrated tree. Unknown or failing measurements still block.
        if before.status != "pass":
            failures.append(
                f"baseline check '{name}' did not pass on the original snapshot: "
                f"{before.status} ({before.reason})"
            )
        if after.status != "pass":
            failures.append(
                f"baseline check '{name}' did not pass on the current tree: "
                f"{after.status} ({after.reason})"
            )
        return before, after, failures
    if before.exit_code is None:
        failures.append(
            f"baseline check '{name}' could not run against the original snapshot: {before.reason}"
        )
    comparison = compare_baseline({name: before}, {name: after})
    if comparison.status == "fail":
        failures.extend(f"baseline check '{name}': {item}" for item in comparison.regressions)
    elif before.status == "pass" and after.status != "pass":
        failures.append(f"baseline check '{name}': expected pass, got {after.status}")
    return before, after, failures


def _content_digest(root: Path) -> str:
    entries = [entry for entry in _current_entries(root) if entry["path"] not in _EVIDENCE_EXCLUDED]
    return _tree_digest(entries)


def _protocol_failures(root: Path, setup: dict[str, object]) -> list[str]:
    path = root / PROTOCOL_REL
    if path.is_symlink() or not path.is_file():
        return [f"the setup protocol is missing: {PROTOCOL_REL}"]
    expected = setup.get("protocol_digest")
    if isinstance(expected, str) and sha256_file(path) != expected:
        return ["the setup protocol changed after preparation"]
    return []


def _structural_failures(root: Path) -> list[str]:
    """Validate synchronization and doctor gates without mutating the project."""

    failures: list[str] = []
    try:
        sync = sync_project(root, check=True)
    except (ProjectError, UnsafeWriteError, OSError, ValueError) as exc:
        return [f"managed synchronization cannot be validated: {exc}"]
    failures.extend(sync.problems)
    failures.extend(f"managed synchronization pending: {change}" for change in sync.changes)
    try:
        doctor = doctor_project(root)
    except (ProjectError, UnsafeWriteError, OSError, ValueError, RuntimeError) as exc:
        failures.append(f"doctor could not validate the project: {exc}")
        return failures
    for check in doctor:
        if not check.ok:
            failures.append(f"doctor {check.name}: {check.detail}")
    return failures


def _dev_required_failures(root: Path, timeout: float) -> list[str]:
    """Independently run the project's own ``./dev check`` and ``./dev verify``."""

    dev = root / "dev"
    if dev.is_symlink() or not dev.is_file() or not os.access(dev, os.X_OK):
        return ["required ./dev check and ./dev verify are missing or not executable"]
    failures: list[str] = []
    for argv in (("./dev", "check"), ("./dev", "verify")):
        result = _run_argv(root, argv, timeout)
        problem = _check_passes(result)
        if problem is None:
            continue
        detail = problem
        if result.output.strip():
            tail = result.output.strip().splitlines()[-1]
            detail += f" [{tail}]"
        failures.append(f"required {' '.join(argv)}: {detail}")
    return failures


def _load_recorded_checks(root: Path) -> dict[str, object]:
    path = root / BASELINE_REL
    if path.is_symlink():
        raise SetupError(f"refusing symlinked baseline: {path}")
    if not path.is_file():
        return {}
    try:
        data = json.loads(read_text_exact(path))
    except (OSError, ValueError) as exc:
        raise SetupError(f"invalid adoption baseline {path}: {exc}") from exc
    if not isinstance(data, dict) or data.get("schema") != 1:
        raise SetupError(f"invalid adoption baseline: {path}")
    raw_checks = data.get("checks")
    if not isinstance(raw_checks, dict):
        raise SetupError(f"invalid adoption baseline checks: {path}")
    return raw_checks


def _recorded_obligation_failures(root: Path, timeout: float, resolved: set[str]) -> list[str]:
    """Re-measure every inherited obligation recorded during preparation."""

    raw_checks = _load_recorded_checks(root)
    if not raw_checks:
        return []
    discovered = {
        name
        for name, value in raw_checks.items()
        if isinstance(value, dict) and value.get("discovery") is True
    }
    before: dict[str, CheckResult] = {}
    for name, value in raw_checks.items():
        if name in resolved:
            continue
        before[name] = _stored_check(value)
    if not before:
        return []
    repository = GitRepository.discover(root)
    source = repository.status() if repository is not None else None
    current: dict[str, CheckResult] = {}
    for name, check in before.items():
        if check.command is None:
            current[name] = CheckResult(
                "unknown", None, None, "", check.reason or "no recorded command"
            )
            continue
        if not (_recognized_check(root, check.command) or name in discovered):
            current[name] = CheckResult(
                "unknown",
                check.command,
                None,
                "",
                "Recorded command is unavailable or unrecognized",
            )
            continue
        current[name] = _run_check(root, check.command, timeout)
    after = repository.status() if repository is not None else None
    if (
        source is not None
        and after is not None
        and (after.head != source.head or after.entries != source.entries)
    ):
        return ["inherited baseline recheck: repository changed during recheck"]
    comparison = compare_baseline(before, current)
    if comparison.status == "pass":
        return []
    failures: list[str] = []
    if comparison.regressions:
        failures.extend(f"inherited baseline regression: {item}" for item in comparison.regressions)
    if comparison.unresolved:
        failures.extend(f"inherited baseline unresolved: {item}" for item in comparison.unresolved)
    for name, check in sorted(current.items()):
        if check.status == "fail":
            failures.append(f"inherited baseline check still failing: {name}")
        elif check.status == "unknown":
            failures.append(f"inherited baseline check is unknown: {name} ({check.reason})")
    if not failures:
        failures.append("the inherited baseline could not be independently confirmed")
    return failures


def _mark_custody_managed(root: Path) -> None:
    """Mark safe custody managed only after every gate has passed."""

    path = root / CUSTODY_REL
    if path.is_symlink() or not path.is_file():
        return
    content = read_text_exact(path)
    header = "schema = 1\nmanaged = false\n\n[custody]\n"
    if content.startswith(header):
        atomic_write_text(
            path,
            content.replace(header, header.replace("false", "true"), 1),
            expected_content=content,
        )


def _evidence_failures(root: Path, setup: dict[str, object]) -> list[str]:
    """Validate the evidence that a completed lock must be bound to."""

    path = root / EVIDENCE_REL
    if path.is_symlink() or not path.is_file():
        return ["completed setup evidence is missing"]
    try:
        data = json.loads(read_text_exact(path))
    except (OSError, ValueError) as exc:
        return [f"completed setup evidence is invalid: {exc}"]
    if not isinstance(data, dict) or data.get("schema") != 1:
        return ["completed setup evidence has an unsupported schema"]
    if data.get("snapshot_digest") != setup.get("snapshot_digest") or data.get(
        "manifest_digest"
    ) != setup.get("manifest_digest"):
        return ["completed setup evidence does not match the recorded snapshot"]
    recorded = data.get("content_digest")
    if not isinstance(recorded, str) or recorded != _content_digest(root):
        return ["completed setup evidence is stale; re-run setup and finalize"]
    return []


def finalize_setup(root: Path, *, timeout: float = 60.0) -> None:
    """Finalize a pending setup, or raise :class:`SetupError` with all failures."""

    if timeout <= 0:
        raise ValueError("timeout must be positive")
    repository_root = _require_git_root(root)
    lock = _load_lock_or_none(repository_root)
    if lock is None or not lock.setup:
        if setup_status(repository_root) == "completed":
            return
        raise SetupError("no pending setup operation was found; run setup first")
    setup = _require_setup_record(dict(lock.setup))
    if setup.get("state") == "completed":
        problems = _evidence_failures(repository_root, setup)
        if problems:
            raise SetupError("setup claims to be completed, but " + "; ".join(problems))
        return
    if setup.get("state") != "pending":
        raise SetupError(f"unknown setup state: {setup.get('state')!r}")

    entries = _validate_snapshot(
        repository_root,
        str(setup["snapshot_digest"]),
        str(setup["manifest_digest"]),
        expected_head=str(setup.get("head", "")),
    )
    snapshot = _snapshot_dir(repository_root)
    try:
        profile = load_profile(repository_root)
    except ProjectError as exc:
        raise SetupError(f"setup is not valid: {exc}") from exc
    if lock.schema < LOCK_SCHEMA_SETUP or not lock.blocks:
        raise SetupError("setup lock is missing its managed instruction blocks")
    try:
        report = _load_report(repository_root)
    except SetupError as exc:
        raise SetupError(str(exc)) from exc

    failures: list[str] = []
    failures.extend(_protocol_failures(repository_root, setup))
    failures.extend(_head_failures(repository_root, setup))
    failures.extend(_index_failures(repository_root, snapshot))
    recorded_core = setup.get("baseline_core_digest")
    if isinstance(recorded_core, str) and _baseline_core_digest(repository_root) != recorded_core:
        failures.append(
            "the inherited baseline changed after preparation; original failures "
            "and unknowns cannot be removed or replaced"
        )
    failures.extend(_validate_context(repository_root, report))
    failures.extend(_required_check_failures(profile.modules, report))
    failures.extend(_preservation_failures(repository_root, entries, lock, setup))
    # Structural gates (sync + doctor) are validated before running any project check.
    failures.extend(_structural_failures(repository_root))
    if failures:
        raise SetupError(_render_failures(failures))

    before_digest = _content_digest(repository_root)
    results: dict[str, CheckResult] = {}
    for name in sorted(report.checks):
        result = _run_argv(repository_root, report.checks[name], timeout)
        results[name] = result
        problem = _check_passes(result)
        if problem:
            failures.append(f"declared check {name}: {problem}")
    failures.extend(_dev_required_failures(repository_root, timeout))

    discovered_before: dict[str, CheckResult] = {}
    resolved: set[str] = set()
    recorded_checks = _load_recorded_checks(repository_root)
    for name, argv in sorted(report.baseline_checks.items()):
        before, _after, problems = _discovered_baseline(
            repository_root, snapshot, name, argv, timeout, strict=True
        )
        discovered_before[name] = before
        failures.extend(problems)
        recorded = recorded_checks.get(name)
        # Discovery may only resolve an originally *unrecognized* obligation.
        # A previously passing or failing concrete check is never replaced.
        unresolved_original = (
            isinstance(recorded, dict)
            and recorded.get("status") == "unknown"
            and recorded.get("command") is None
        )
        if not problems and before.status == "pass" and unresolved_original:
            resolved.add(name)

    baseline_records: dict[str, CheckResult] = {
        name: before for name, before in discovered_before.items()
    }
    original_dev = next(
        (entry for entry in entries if entry["path"] == "dev" and entry["type"] == "file"),
        None,
    )
    dev_executable = bool(original_dev) and bool(int(str(original_dev.get("mode", "0")), 8) & 0o111)
    if original_dev is not None and dev_executable:
        # Preserve the original program's measured evidence: the inherited `dev`
        # is baselined against the captured snapshot and cannot be replaced by an
        # optimistic result without that observation being recorded.
        before, _after, problems = _discovered_baseline(
            repository_root, snapshot, "dev", ("./dev", "verify"), timeout
        )
        if not problems:
            baseline_records["dev"] = before
        failures.extend(problems)

    failures.extend(_recorded_obligation_failures(repository_root, timeout, resolved))

    after_digest = _content_digest(repository_root)
    if before_digest != after_digest:
        failures.append("product content changed while project checks were running")
    failures.extend(_preservation_failures(repository_root, entries, lock, setup))
    failures.extend(_structural_failures(repository_root))

    if failures:
        raise SetupError(_render_failures(failures))

    reconcile = sync_project(repository_root)
    if reconcile.problems:
        raise SetupError(
            "managed files did not reconcile before finalization: " + "; ".join(reconcile.problems)
        )
    if baseline_records:
        _record_baseline(repository_root, baseline_records, resolved)
    _mark_custody_managed(repository_root)
    final_digest = _content_digest(repository_root)
    evidence = {
        "schema": 1,
        "snapshot_digest": str(setup["snapshot_digest"]),
        "manifest_digest": str(setup["manifest_digest"]),
        "head": str(setup.get("head", "")),
        "checks": {
            name: {
                "status": result.status,
                "command": list(result.command) if result.command else None,
                "exit_code": result.exit_code,
                "reason": result.reason,
            }
            for name, result in sorted(results.items())
        },
        "baseline": {
            name: {
                "status": result.status,
                "command": list(result.command) if result.command else None,
                "exit_code": result.exit_code,
            }
            for name, result in sorted(baseline_records.items())
        },
        "content_digest": final_digest,
    }
    evidence_path = repository_root / EVIDENCE_REL
    evidence_text = json.dumps(evidence, indent=2, sort_keys=True) + "\n"
    if evidence_path.is_symlink():
        raise SetupError(f"refusing symlinked setup evidence: {evidence_path}")
    if evidence_path.is_file():
        # Guarded against concurrent modification but robust to our own earlier
        # interrupted attempt's evidence, which must be safe to replace.
        atomic_write_text(evidence_path, evidence_text, expected_digest=sha256_file(evidence_path))
    else:
        atomic_write_text(evidence_path, evidence_text)
    completed = dict(setup)
    completed["state"] = "completed"
    _write_lock(repository_root, lock, lock.blocks, completed)
    (repository_root / PENDING_REL).unlink(missing_ok=True)


def _render_failures(failures: list[str]) -> str:
    lines = ["setup cannot be finalized yet:"]
    lines.extend(f"  - {item}" for item in failures)
    return "\n".join(lines)


PROTOCOL_TEXT = """# AI Kit setup protocol

AI Kit has prepared a *pending* integration for this repository. You are the
harness that finishes the semantic half. You must not write application source,
tests, intent, or accepted decisions, must not stage or commit, and must not
launch another harness from inside this session.

## 1. Inventory, baseline, and snapshot

Before any AI Kit write, the preparation step captured the original working
tree privately under `.ai-local/setup/original` and recorded hashes, types,
modes, and HEAD in `.ai-local/setup/manifest.json`. The pending record in
`ai-kit.lock` binds the snapshot and manifest digests. Resuming validates that
these originals are unchanged; never re-baseline or restore user work.

## 2. Inspect the real project

Read the actual source, documentation, CI configuration, and tests. Prefer
observation over claims: a README statement is a CLAIM, not approved intent.
Distinguish user-approved authority from repository claims. Do not promote
assumptions into `.ai/intent/` or `.ai/decisions/`; preserve them byte-for-byte.

## 3. Authority and preservation

The integration may change only:

* the `./dev` wrapper, wired to the project's real commands;
* the AI Kit-managed instruction blocks between `<!-- ai-kit:begin -->` and
  `<!-- ai-kit:end -->`;
* the `.ai-local/` ignore addition in `.gitignore`;
* new AI Kit scaffolding, advisory notes, and the structured report.

It may not change application source or tests, must not replace an inherited
failing or unknown observation with optimistic evidence, and must not stage,
commit, or roll back anyone's edits.

## 4. Commands

Wire `./dev setup`, `./dev check`, and `./dev verify` to meaningful commands.
Reuse the project's current commands. If an executable `dev` already exists
with a recognized baseline command, preserve it or keep its measured baseline
rather than silently replacing inherited evidence. `./dev verify` must include
`./dev check`. Downstream, AI Kit runs `./dev check` and `./dev verify` itself
as independent required checks and refuses a repository whose `./dev` is still
unconfigured, missing, failing, or unknown. A report may not substitute `true`
for an unconfigured wrapper.

When the original repository used an unfamiliar command, declare it under
`baseline_checks` so AI Kit can measure it against the captured original
snapshot and record the result as baseline discovery.

## 5. Report

Write `.ai/setup/report.json` with this exact shape (schema 1):

```json
{
  "schema": 1,
  "context": ["README.md", "docs/architecture.md"],
  "checks": {
    "verify": ["./dev", "verify"],
    "acceptance": ["./dev", "verify", "--acceptance"]
  },
  "baseline_checks": {
    "unit": ["python", "-m", "pytest", "-q"]
  },
  "unresolved": []
}
```

* `context`: existing repo-relative references you actually inspected.
* `checks`: named argv commands required for completion. `verify` is always
  required; profiles such as `webapp` also require `acceptance` and
  `runtime_errors`.
* `baseline_checks`: newly discovered *existing* commands, runnable in the
  original tree. AI Kit measures them against the captured snapshot and then
  against the current integration; inherited failures stay recorded.
* `unresolved`: open questions. This list must be empty before finalization.

AI Kit managed files are changed through `ai-kit sync`, never by hand.

## 6. Advisory notes and unresolved choices

Record durable context references in the profile or protocol, and leave genuine
open choices in `unresolved` with their rationale. When the report is complete
and the checks pass, run `ai-kit setup --finalize`. Finalization independently
re-validates ownership, preservation, snapshot identity, the declared `./dev`
checks, and every inherited obligation before it clears the pending state.
"""


__all__ = ["SetupError", "prepare_setup", "finalize_setup", "setup_status"]
