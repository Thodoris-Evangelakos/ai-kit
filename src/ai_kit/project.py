"""Canonical managed-project files and the Codex renderer."""

from __future__ import annotations

import os
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

from . import __version__
from .system import GitRepository, UnsafeWriteError, atomic_write_text, sha256_file, sha256_text

PROFILE_SCHEMA = 1
LOCK_SCHEMA = 1
ADAPTER_SCHEMA = 1
MODULES = frozenset(
    {
        "learning",
        "professional-repository",
        "strict-verification",
        "webapp",
        "production",
        "public-oss",
        "research",
        "experimental",
        "security-sensitive",
    }
)


class ProjectError(RuntimeError):
    """A project is invalid or a requested operation would lose work."""


@dataclass(frozen=True, slots=True)
class Profile:
    schema: int
    base: str
    modules: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Lock:
    profile_digest: str
    generated: dict[str, str]


@dataclass(frozen=True, slots=True)
class SyncResult:
    changes: tuple[str, ...]
    problems: tuple[str, ...]

    @property
    def clean(self) -> bool:
        return not self.changes and not self.problems


@dataclass(frozen=True, slots=True)
class DoctorCheck:
    name: str
    ok: bool
    detail: str


ROUTER = """# Project agent guide

This repository is managed by AI Kit.

Read `.ai/profile.toml` before substantial work. For the current task, read
`.ai/current/STATUS.md` and relevant `.ai/current/FINDINGS.md` entries.

Requirements in `.ai/intent/` are authoritative. Accepted architectural
decisions live in `.ai/decisions/`. Project verification policy lives in
`.ai/verification.toml`. Do not promote assumptions into requirements or decisions.

Use the harness's native goal/task mechanisms and relevant `.agents/skills/`.
Before claiming completion, inspect the verification policy, ensure meaningful
tests/proofs cover the change, and run `./dev verify`. Required evidence must
pass at the current commit; failing, unknown, or stale evidence is not success.
Use `./dev check` for fast feedback.

Record only high-signal, current discoveries in `.ai/current/FINDINGS.md`;
delete stale entries. Git preserves history.
"""

VERIFY_PROJECT = """---
name: verify-project
description: Design meaningful checks and run the repository verification policy.
---

# Verify project work

Use the harness's goal/task for the requested outcome and task-specific
acceptance. Inspect `.ai/verification.toml` and the affected system before
choosing tests or proofs. The agent designs the verification; AI Kit enforces
that the project-required mechanisms actually run.

Permanent tests should protect accepted user behavior, system invariants,
important interfaces, meaningful failure modes, security properties,
production regressions, recurring defects, or expensive-to-discover breakage.
Do not add tests merely because a line changed, a branch exists, coverage can
increase, or a dependency can be mocked. Prefer real behavior and integration
evidence to implementation-detail assertions or mock-only confidence.

Run `./dev check` while iterating. Commit the product and policy, then run
`./dev verify` before claiming completion. It delegates to `ai-kit verify`,
which always reruns required mechanisms, records logs and commit/policy-bound
evidence in ignored `.ai-local/evidence/`, and rejects fail/unknown results.
Policy commands must invoke underlying checks, never `./dev verify` itself.
Review results against the requested outcome; a green command cannot prove an
untested outcome. Product/test co-changes are a review signal, not a failure.
Any new commit or dirty tracked/unignored work requires fresh verification.
"""

LEARNING_SKILL = """---
name: maintain-learning-journal
description: Record meaningful implementation lessons in a private learning project.
---

# Learning journal

During substantial implementation, write short entries incrementally under
ignored `.ai-local/learning/`. For each meaningful entry, explain the work
with correct terminology, then in plain language; explain why it works,
trade-offs, and surprising debugging lessons. This journal is private and
never becomes project truth. Do not fabricate a retrospective diary.
"""

WEBAPP_SKILL = """---
name: verify-webapp
description: Design browser and runtime-error evidence for web application changes.
---

# Verify a web application

Configure `browser` and `runtime-errors` in `.ai/verification.toml` with real
verification commands. AI Kit does not supply a browser harness yet.
Exercise the user flow against a real running backend. Assert
the mutation response, persisted backend state, and state after reload.
Collect page errors, unexpected console errors, failed requests, mutation
4xx/5xx responses, backend exits, and assertion failures. Fail on unexpected
errors; use reviewed allowlists for expected noise. Save trace, screenshot,
and relevant logs on failure under `.ai-local/artifacts/`.
"""

CI_WORKFLOW = """name: Verify
on: [push, pull_request]
permissions:
  contents: read
jobs:
  verify:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@11d5960a326750d5838078e36cf38b85af677262 # v4.4.0
        with:
          fetch-depth: 2 # Parent diff supplies an advisory product/test co-change signal.
      - uses: astral-sh/setup-uv@c18668ad3cf93ea998bef934396af7bb5c839dc7 # v10.2.0
        with:
          version: "0.12.13"
      - name: Setup
        run: ./dev setup
      - name: Verify
        run: ./dev verify
      - name: Verification evidence
        if: always()
        uses: actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02 # v4.6.2
        with:
          name: verification-evidence
          path: .ai-local/evidence/
          include-hidden-files: true
          if-no-files-found: warn
"""

DEV_STUB = """#!/bin/sh
set -eu
case "${1:-}" in
  verify) ai-kit verify ;;
  setup|check|lint|test)
    echo "./dev $1 is unconfigured; add this project's real checks before claiming success" >&2
    exit 2 ;;
  *)
    echo "usage: ./dev {setup|check|lint|test|verify}" >&2
    exit 2 ;;
esac
"""


def _root(root: Path) -> Path:
    root = root.resolve()
    repo = GitRepository.discover(root)
    if repo is None or repo.root != root:
        raise ProjectError(f"{root} is not a Git repository root")
    return root


def _profile_text(modules: tuple[str, ...]) -> str:
    values = ", ".join(f'"{name}"' for name in modules)
    return f'schema = 1\nbase = "software"\nmodules = [{values}]\n'


def load_profile(root: Path) -> Profile:
    path = root / ".ai/profile.toml"
    if path.is_symlink():
        raise ProjectError(f"refusing symlinked profile: {path}")
    try:
        data = tomllib.loads(path.read_text())
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ProjectError(f"invalid or missing {path}: {exc}") from exc
    if set(data) != {"schema", "base", "modules"}:
        raise ProjectError(f"{path}: expected only schema, base, and modules")
    if type(data["schema"]) is not int or data["schema"] != PROFILE_SCHEMA:
        raise ProjectError(f"{path}: unsupported profile schema {data['schema']!r}")
    if data["base"] != "software":
        raise ProjectError(f"{path}: unsupported base {data['base']!r}")
    modules = data["modules"]
    if not isinstance(modules, list) or any(not isinstance(x, str) for x in modules):
        raise ProjectError(f"{path}: modules must be a string list")
    if len(set(modules)) != len(modules) or set(modules) - MODULES:
        raise ProjectError(f"{path}: duplicate or unsupported modules: {modules!r}")
    return Profile(schema=PROFILE_SCHEMA, base="software", modules=tuple(modules))


def render_codex(profile: Profile) -> dict[str, str]:
    generated = {
        "AGENTS.md": ROUTER,
        ".agents/skills/verify-project/SKILL.md": VERIFY_PROJECT,
    }
    if "learning" in profile.modules:
        generated[".agents/skills/maintain-learning-journal/SKILL.md"] = LEARNING_SKILL
    if "webapp" in profile.modules:
        generated[".agents/skills/verify-webapp/SKILL.md"] = WEBAPP_SKILL
    if "professional-repository" in profile.modules:
        generated[".github/workflows/verify.yml"] = CI_WORKFLOW
    return generated


def _lock_text(profile_digest: str, generated: dict[str, str]) -> str:
    lines = [
        f"schema = {LOCK_SCHEMA}",
        f'ai_kit_version = "{__version__}"',
        f"profile_schema = {PROFILE_SCHEMA}",
        f"adapter_schema = {ADAPTER_SCHEMA}",
        f'profile_digest = "{profile_digest}"',
        "",
        "[adapters.codex]",
        "enabled = true",
        f"version = {ADAPTER_SCHEMA}",
        "",
        "[generated]",
    ]
    lines.extend(f'"{path}" = "{digest}"' for path, digest in sorted(generated.items()))
    return "\n".join(lines) + "\n"


def load_lock(root: Path) -> Lock:
    path = root / "ai-kit.lock"
    if path.is_symlink():
        raise ProjectError(f"refusing symlinked lock: {path}")
    try:
        data = tomllib.loads(path.read_text())
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ProjectError(f"invalid or missing {path}: {exc}") from exc
    expected = {
        "schema",
        "ai_kit_version",
        "profile_schema",
        "adapter_schema",
        "profile_digest",
        "adapters",
        "generated",
    }
    if set(data) != expected or type(data["schema"]) is not int or data["schema"] != LOCK_SCHEMA:
        raise ProjectError(f"{path}: invalid or unsupported lock schema")
    if (
        type(data["profile_schema"]) is not int
        or data["profile_schema"] != PROFILE_SCHEMA
        or type(data["adapter_schema"]) is not int
        or data["adapter_schema"] != ADAPTER_SCHEMA
    ):
        raise ProjectError(f"{path}: unsupported profile or adapter schema; upgrade AI Kit")
    if data["adapters"] != {"codex": {"enabled": True, "version": ADAPTER_SCHEMA}}:
        raise ProjectError(f"{path}: unsupported adapters")
    digest = data["profile_digest"]
    generated = data["generated"]
    if not isinstance(digest, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
        raise ProjectError(f"{path}: invalid profile digest")
    if not isinstance(generated, dict) or any(
        not isinstance(k, str)
        or not isinstance(v, str)
        or not re.fullmatch(r"sha256:[0-9a-f]{64}", v)
        for k, v in generated.items()
    ):
        raise ProjectError(f"{path}: invalid generated digests")
    return Lock(profile_digest=digest, generated=generated)


def _managed_path(root: Path, relative: str) -> Path:
    path = root / relative
    if Path(relative).is_absolute() or ".." in Path(relative).parts or path.is_symlink():
        raise ProjectError(f"unsafe managed path in lock: {relative}")
    if any(parent.is_symlink() for parent in path.parents if parent != root.parent):
        raise ProjectError(f"symlink in managed path: {relative}")
    return path


def _sync_plan(root: Path, rendered: dict[str, str], lock: Lock | None) -> SyncResult:
    changes: list[str] = []
    problems: list[str] = []
    old = lock.generated if lock else {}
    for relative in sorted(set(old) | set(rendered)):
        path = _managed_path(root, relative)
        if relative in old:
            if not path.is_file():
                problems.append(f"managed file missing: {relative}")
                continue
            actual = sha256_file(path)
            if actual != old[relative]:
                problems.append(f"manual drift: {relative} ({actual}, expected {old[relative]})")
                continue
        elif path.exists():
            problems.append(f"unmanaged file blocks generation: {relative}")
            continue
        desired = sha256_text(rendered[relative]) if relative in rendered else None
        if old.get(relative) != desired:
            changes.append(f"{'remove' if desired is None else 'write'} {relative}")
    return SyncResult(tuple(changes), tuple(problems))


def sync_project(root: Path, *, check: bool = False) -> SyncResult:
    root = _root(root)
    profile = load_profile(root)
    lock = load_lock(root)
    rendered = render_codex(profile)
    plan = _sync_plan(root, rendered, lock)
    from .verification import VerificationError, load_policy

    try:
        load_policy(root)
    except VerificationError as exc:
        plan = SyncResult(plan.changes, (*plan.problems, str(exc)))
    local = root / ".ai-local"
    if local.is_symlink():
        plan = SyncResult(plan.changes, (*plan.problems, "symlinked .ai-local path"))
    learning = root / ".ai-local/learning"
    if "learning" in profile.modules and (not learning.is_dir() or learning.is_symlink()):
        if learning.exists() or learning.is_symlink():
            plan = SyncResult(plan.changes, (*plan.problems, "invalid .ai-local/learning path"))
        else:
            plan = SyncResult((*plan.changes, "create .ai-local/learning"), plan.problems)
    profile_digest = sha256_file(root / ".ai/profile.toml")
    desired_lock = _lock_text(
        profile_digest, {path: sha256_text(content) for path, content in rendered.items()}
    )
    if profile_digest != lock.profile_digest and not plan.changes:
        plan = SyncResult((*plan.changes, "update ai-kit.lock"), plan.problems)
    if (root / "ai-kit.lock").read_text() != desired_lock and not plan.changes:
        plan = SyncResult((*plan.changes, "update ai-kit.lock"), plan.problems)
    if check or plan.problems:
        return plan
    if "learning" in profile.modules:
        learning.mkdir(parents=True, exist_ok=True)
    for relative in sorted(set(lock.generated) | set(rendered)):
        path = _managed_path(root, relative)
        if relative not in rendered:
            path.unlink()
        elif relative in lock.generated:
            if sha256_text(rendered[relative]) != lock.generated[relative]:
                atomic_write_text(
                    path, rendered[relative], expected_digest=lock.generated[relative]
                )
        else:
            atomic_write_text(path, rendered[relative])
    lock_path = root / "ai-kit.lock"
    atomic_write_text(lock_path, desired_lock, expected_content=lock_path.read_text())
    return SyncResult(plan.changes, ())


def _create(path: Path, content: str) -> bool:
    if path.exists() or path.is_symlink():
        return False
    atomic_write_text(path, content)
    return True


def preflight_adoption(root: Path) -> None:
    """Check whether safe custody can install AI Kit before writing artifacts."""

    root = _root(root)
    if (root / ".ai").exists() or (root / ".ai").is_symlink():
        raise ProjectError(".ai already exists; inspect it before adoption")
    if (root / "ai-kit.lock").exists() or (root / "ai-kit.lock").is_symlink():
        raise ProjectError("ai-kit.lock already exists; use doctor or sync")
    profile = Profile(PROFILE_SCHEMA, "software", ("strict-verification",))
    plan = _sync_plan(root, render_codex(profile), None)
    if plan.problems:
        raise ProjectError(
            "adoption install would overwrite user work: " + "; ".join(plan.problems)
        )
    if any((root / name).is_symlink() for name in (".gitignore", "dev", ".ai-local")):
        raise ProjectError("refusing symlinked .gitignore, dev, or .ai-local")


def init_project(
    root: Path,
    *,
    modules: tuple[str, ...] = ("strict-verification",),
    adopted: bool = False,
) -> SyncResult:
    root = _root(root)
    if len(set(modules)) != len(modules) or set(modules) - MODULES:
        raise ProjectError(f"duplicate or unsupported modules: {modules!r}")
    if (root / "ai-kit.lock").is_symlink():
        raise ProjectError("refusing symlinked ai-kit.lock")
    if (root / "ai-kit.lock").exists():
        return sync_project(root)
    profile_path = root / ".ai/profile.toml"
    ai = root / ".ai"
    if ai.is_symlink() or profile_path.is_symlink():
        raise ProjectError("refusing symlinked .ai path")
    if adopted:
        allowed = {"baseline.json", "CUSTODY.toml", "adoption"}
        if not ai.is_dir() or {item.name for item in ai.iterdir()} != allowed:
            raise ProjectError("adoption artifacts are missing or .ai contains unknown files")
        adoption = ai / "adoption"
        if adoption.is_symlink() or {item.name for item in adoption.iterdir()} != {"inventory.md"}:
            raise ProjectError("adoption inventory is missing or contains unknown files")
    elif profile_path.exists() or ai.exists():
        raise ProjectError(
            ".ai already exists without ai-kit.lock; inspect it before initialization"
        )
    profile = Profile(PROFILE_SCHEMA, "software", modules)
    rendered = render_codex(profile)
    plan = _sync_plan(root, rendered, None)
    if plan.problems:
        return plan
    if any((root / name).is_symlink() for name in (".gitignore", "dev", ".ai-local")):
        raise ProjectError("refusing symlinked .gitignore, dev, or .ai-local")
    created: list[str] = []
    _create(profile_path, _profile_text(modules))
    created.append(".ai/profile.toml")
    for relative, content in {
        ".ai/current/STATUS.md": (
            "# Current status\n\nWork: none\nCompleted: none\nRemaining: none\nBlocked: no\n"
        ),
        ".ai/current/FINDINGS.md": "# Current findings\n\nNo current cross-agent findings.\n",
        ".ai/intent/README.md": (
            "# Approved intent\n\nPlace human-approved requirements and invariants here. "
            "Agents propose changes; they do not silently rewrite intent.\n"
        ),
        ".ai/decisions/README.md": (
            "# Accepted decisions\n\nPlace durable, accepted architectural decisions here. "
            "Git keeps the history.\n"
        ),
    }.items():
        if _create(root / relative, content):
            created.append(relative)
    from .verification import default_policy

    _create(root / ".ai/verification.toml", default_policy(modules, adopted=adopted))
    created.append(".ai/verification.toml")
    (root / ".ai-local").mkdir(parents=True, exist_ok=True)
    if "learning" in modules:
        (root / ".ai-local/learning").mkdir(parents=True, exist_ok=True)
    ignore = root / ".gitignore"
    old_ignore = ignore.read_text() if ignore.exists() else None
    if old_ignore is None:
        atomic_write_text(ignore, ".ai-local/\n")
        created.append(".gitignore")
    elif ".ai-local/" not in old_ignore.splitlines():
        atomic_write_text(
            ignore,
            old_ignore + ("" if old_ignore.endswith("\n") else "\n") + ".ai-local/\n",
            expected_content=old_ignore,
        )
        created.append(".gitignore")
    dev = root / "dev"
    if _create(dev, DEV_STUB):
        os.chmod(dev, 0o755)
        created.append("dev")
    for relative, content in sorted(rendered.items()):
        atomic_write_text(_managed_path(root, relative), content)
        created.append(relative)
    lock_text = _lock_text(
        sha256_file(profile_path),
        {relative: sha256_text(content) for relative, content in rendered.items()},
    )
    atomic_write_text(root / "ai-kit.lock", lock_text)
    created.append("ai-kit.lock")
    return SyncResult(tuple(created), ())


def doctor_project(root: Path) -> tuple[DoctorCheck, ...]:
    checks: list[DoctorCheck] = []
    try:
        root = _root(root)
        repository = GitRepository.discover(root)
        if repository is None:
            raise ProjectError("Git repository disappeared during doctor")
        status = repository.status()
        head = status.head[:12] if status.head else "unborn"
        checks.append(
            DoctorCheck("git", True, f"HEAD {head}; {len(status.entries)} worktree changes")
        )
    except ProjectError as exc:
        return (DoctorCheck("git", False, str(exc)),)
    try:
        profile = load_profile(root)
        checks.append(DoctorCheck("profile", True, f"schema {profile.schema}"))
    except ProjectError as exc:
        checks.append(DoctorCheck("profile", False, str(exc)))
        return tuple(checks)
    try:
        lock = load_lock(root)
        checks.append(DoctorCheck("lock", True, f"{len(lock.generated)} managed files"))
        sync = sync_project(root, check=True)
        checks.append(
            DoctorCheck(
                "sync",
                sync.clean,
                "clean" if sync.clean else "; ".join((*sync.problems, *sync.changes)),
            )
        )
    except (ProjectError, UnsafeWriteError) as exc:
        checks.append(DoctorCheck("lock", False, str(exc)))
    for relative in (".ai/current/STATUS.md", ".ai/current/FINDINGS.md"):
        path = root / relative
        exists = path.is_file() and not path.is_symlink()
        if relative.endswith("STATUS.md") and exists:
            content = path.read_text()
            exists = all(f"{field}:" in content for field in ("Completed", "Remaining", "Blocked"))
        checks.append(DoctorCheck(relative, exists, "valid" if exists else "missing or invalid"))
    dev = root / "dev"
    checks.append(
        DoctorCheck(
            "dev",
            dev.is_file() and os.access(dev, os.X_OK),
            "executable"
            if dev.is_file() and os.access(dev, os.X_OK)
            else "missing or not executable",
        )
    )
    try:
        from .verification import configuration_problems, load_policy

        policy = load_policy(root)
        problems = configuration_problems(root, policy)
        checks.append(
            DoctorCheck("verification", not problems, "; ".join(problems) or "configured")
        )
    except (OSError, ValueError, RuntimeError) as exc:
        checks.append(DoctorCheck("verification", False, str(exc)))
    return tuple(checks)
