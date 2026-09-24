"""Commit-bound acceptance contracts for managed repositories."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import tomllib
from dataclasses import dataclass, replace
from pathlib import Path

from .system import UnsafeWriteError, atomic_write_text

SCHEMA = 1
STATES = {"DRAFT", "READY", "ACTIVE", "IMPLEMENTED", "VERIFYING", "BLOCKED", "DONE"}
ID_RE = re.compile(r"[a-z0-9][a-z0-9_-]*\Z")
MANUAL_TRANSITIONS = {
    "DRAFT": {"READY"},
    "READY": {"ACTIVE"},
    "ACTIVE": {"IMPLEMENTED", "BLOCKED"},
    "IMPLEMENTED": {"ACTIVE", "BLOCKED"},
    "VERIFYING": {"ACTIVE"},
    "BLOCKED": {"ACTIVE"},
}
CODE_SUFFIXES = {
    ".c",
    ".cc",
    ".cpp",
    ".cs",
    ".css",
    ".go",
    ".h",
    ".hpp",
    ".html",
    ".java",
    ".js",
    ".jsx",
    ".kt",
    ".mjs",
    ".php",
    ".py",
    ".rb",
    ".rs",
    ".scss",
    ".sh",
    ".sql",
    ".svelte",
    ".swift",
    ".ts",
    ".tsx",
    ".vue",
}


class GoalError(ValueError):
    """A goal contract or lifecycle operation is invalid."""


@dataclass(frozen=True)
class CheckResult:
    name: str
    status: str
    exit_code: int | None
    error: str = ""


@dataclass(frozen=True)
class Evidence:
    commit: str
    contract_sha256: str
    result: str
    checks: tuple[CheckResult, ...]
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class Goal:
    id: str
    title: str
    state: str
    intent: str
    acceptance: tuple[str, ...]
    checks: dict[str, str]
    evidence: Evidence | None = None


def _text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise GoalError(f"{field} must be a nonempty string")
    return value


def _identifier(value: object, field: str) -> str:
    value = _text(value, field)
    if not ID_RE.fullmatch(value):
        raise GoalError(f"{field} must be a lowercase slug starting with a letter or digit")
    return value


def _contract(goal: Goal) -> dict[str, object]:
    return {
        "id": goal.id,
        "title": goal.title,
        "intent": goal.intent,
        "acceptance": goal.acceptance,
        "checks": goal.checks,
    }


def _contract_hash(goal: Goal) -> str:
    canonical = json.dumps(
        _contract(goal), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _validate(goal: Goal) -> Goal:
    _identifier(goal.id, "id")
    _text(goal.title, "title")
    _text(goal.intent, "intent")
    if not isinstance(goal.state, str) or goal.state not in STATES:
        raise GoalError(f"unknown goal state: {goal.state}")
    if not goal.acceptance or any(
        not isinstance(item, str) or not item.strip() for item in goal.acceptance
    ):
        raise GoalError("acceptance must contain at least one nonempty criterion")
    if not goal.checks:
        raise GoalError("at least one required check is needed")
    for name, command in goal.checks.items():
        _identifier(name, "check name")
        _text(command, f"command for {name}")
    if goal.state in {"VERIFYING", "DONE"} and not _passing_evidence(goal):
        raise GoalError(f"{goal.state} requires passing evidence")
    return goal


def _passing_evidence(goal: Goal) -> bool:
    evidence = goal.evidence
    return bool(
        evidence
        and evidence.result == "pass"
        and {result.name for result in evidence.checks} == set(goal.checks)
        and all(result.status == "pass" and result.exit_code == 0 for result in evidence.checks)
    )


def _path(root: Path, goal_id: str, directory: str) -> Path:
    _identifier(goal_id, "id")
    base = Path(root)
    for part in (".ai", "goals", directory):
        base = base / part
        if base.is_symlink():
            raise GoalError(f"goal directory is a symlink: {base}")
    return base / f"{goal_id}.toml"


def _parse(raw: bytes) -> Goal:
    try:
        data = tomllib.loads(raw.decode("utf-8"))
    except (UnicodeError, tomllib.TOMLDecodeError) as exc:
        raise GoalError(f"invalid goal TOML: {exc}") from exc
    allowed = {"schema", "id", "title", "state", "intent", "acceptance", "checks", "evidence"}
    if set(data) - allowed or (allowed - {"evidence"}) - set(data):
        raise GoalError("goal has missing or unknown fields")
    if type(data["schema"]) is not int or data["schema"] != SCHEMA:
        raise GoalError(f"goal schema must be {SCHEMA}")
    acceptance = data["acceptance"]
    checks = data["checks"]
    if not isinstance(acceptance, list) or not isinstance(checks, dict):
        raise GoalError("acceptance must be an array and checks must be a table")
    if any(not isinstance(item, str) for item in acceptance):
        raise GoalError("acceptance criteria must be strings")
    if any(
        not isinstance(name, str) or not isinstance(command, str)
        for name, command in checks.items()
    ):
        raise GoalError("check commands must be strings")
    evidence = _parse_evidence(data["evidence"]) if "evidence" in data else None
    goal = Goal(
        id=data["id"],
        title=data["title"],
        state=data["state"],
        intent=data["intent"],
        acceptance=tuple(acceptance),
        checks=checks,
        evidence=evidence,
    )
    return _validate(goal)


def _parse_evidence(data: object) -> Evidence:
    if not isinstance(data, dict) or set(data) != {
        "commit",
        "contract_sha256",
        "result",
        "warnings",
        "results",
    }:
        raise GoalError("evidence has missing or unknown fields")
    if not isinstance(data["warnings"], list) or any(
        not isinstance(item, str) for item in data["warnings"]
    ):
        raise GoalError("evidence warnings must be strings")
    if not isinstance(data["results"], list) or not data["results"]:
        raise GoalError("evidence results must be a nonempty array")
    results = []
    for item in data["results"]:
        if (
            not isinstance(item, dict)
            or set(item) - {"name", "status", "exit_code", "error"}
            or not {"name", "status"} <= set(item)
        ):
            raise GoalError("invalid check result")
        name = _identifier(item["name"], "result name")
        status = item["status"]
        if not isinstance(status, str) or status not in {"pass", "fail", "unknown"}:
            raise GoalError(f"invalid result status: {status}")
        code = item.get("exit_code")
        if code is not None and type(code) is not int:
            raise GoalError("exit_code must be an integer")
        if (
            status == "pass"
            and code != 0
            or status == "fail"
            and (code is None or code == 0)
            or status == "unknown"
            and code is not None
        ):
            raise GoalError(f"inconsistent status and exit_code for {name}")
        error = item.get("error", "")
        if not isinstance(error, str):
            raise GoalError("check error must be a string")
        results.append(CheckResult(name, status, code, error))
    if len({item.name for item in results}) != len(results):
        raise GoalError("duplicate check results")
    if not isinstance(data["result"], str) or data["result"] not in {"pass", "fail", "unknown"}:
        raise GoalError("invalid evidence result")
    expected = (
        "unknown"
        if any(item.status == "unknown" for item in results)
        else "fail"
        if any(item.status == "fail" for item in results)
        else "pass"
    )
    if data["result"] != expected:
        raise GoalError("evidence result does not match check outcomes")
    commit = _text(data["commit"], "evidence commit")
    contract_sha256 = _text(data["contract_sha256"], "contract hash")
    if not re.fullmatch(r"[0-9a-f]{40,64}", commit) or not re.fullmatch(
        r"[0-9a-f]{64}", contract_sha256
    ):
        raise GoalError("invalid evidence commit or contract hash")
    return Evidence(
        commit, contract_sha256, data["result"], tuple(results), tuple(data["warnings"])
    )


def _quote(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _render(goal: Goal) -> bytes:
    lines = [
        f"schema = {SCHEMA}",
        f"id = {_quote(goal.id)}",
        f"title = {_quote(goal.title)}",
        f"state = {_quote(goal.state)}",
        f"intent = {_quote(goal.intent)}",
        "acceptance = [",
        *(f"  {_quote(item)}," for item in goal.acceptance),
        "]",
        "",
        "[checks]",
        *(f"{_quote(name)} = {_quote(command)}" for name, command in sorted(goal.checks.items())),
    ]
    if goal.evidence:
        evidence = goal.evidence
        lines += [
            "",
            "[evidence]",
            f"commit = {_quote(evidence.commit)}",
            f"contract_sha256 = {_quote(evidence.contract_sha256)}",
            f"result = {_quote(evidence.result)}",
            "warnings = [",
            *(f"  {_quote(item)}," for item in evidence.warnings),
            "]",
        ]
        for result in evidence.checks:
            lines += [
                "",
                "[[evidence.results]]",
                f"name = {_quote(result.name)}",
                f"status = {_quote(result.status)}",
            ]
            if result.exit_code is not None:
                lines.append(f"exit_code = {result.exit_code}")
            if result.error:
                lines.append(f"error = {_quote(result.error)}")
    return ("\n".join(lines) + "\n").encode("utf-8")


def _read(path: Path) -> Goal:
    if path.is_symlink() or not path.is_file():
        raise GoalError(f"goal file is missing or is a symlink: {path}")
    goal = _parse(path.read_bytes())
    if path.stem != goal.id:
        raise GoalError(f"goal id does not match filename: {path}")
    if (
        path.parent.name == "accepted"
        and goal.state != "DONE"
        or path.parent.name == "active"
        and goal.state == "DONE"
    ):
        raise GoalError(f"goal state does not match directory: {path}")
    return goal


def _write(path: Path, goal: Goal, original: bytes | None = None) -> None:
    try:
        atomic_write_text(
            path,
            _render(goal).decode("utf-8"),
            expected_content=original.decode("utf-8") if original is not None else None,
        )
    except UnsafeWriteError as exc:
        raise GoalError(str(exc)) from exc


def create_goal(
    root: Path, goal_id: str, title: str, intent: str, acceptance: list[str], checks: dict[str, str]
) -> Goal:
    if not isinstance(acceptance, list) or not isinstance(checks, dict):
        raise GoalError("acceptance must be a list and checks must be a dictionary")
    goal = _validate(Goal(goal_id, title, "READY", intent, tuple(acceptance), dict(checks)))
    active = _path(root, goal_id, "active")
    accepted = _path(root, goal_id, "accepted")
    if accepted.exists() or accepted.is_symlink():
        raise GoalError(f"goal already accepted: {goal_id}")
    _write(active, goal)
    return goal


def load_goal(root: Path, goal_id: str) -> Goal:
    active = _path(root, goal_id, "active")
    accepted = _path(root, goal_id, "accepted")
    active_present = active.exists() or active.is_symlink()
    accepted_present = accepted.exists() or accepted.is_symlink()
    if active_present and accepted_present:
        raise GoalError(f"goal exists in both active and accepted: {goal_id}")
    return _read(active if active_present else accepted)


def list_goals(root: Path) -> list[Goal]:
    paths = [
        *_path(root, "placeholder", "active").parent.glob("*.toml"),
        *_path(root, "placeholder", "accepted").parent.glob("*.toml"),
    ]
    goals = [_read(path) for path in paths]
    if len({goal.id for goal in goals}) != len(goals):
        raise GoalError("duplicate goal IDs across active and accepted")
    return sorted(goals, key=lambda goal: goal.id)


def set_goal_state(root: Path, goal_id: str, state: str) -> Goal:
    """Advance or reopen work; verification and completion own their own transitions."""
    path = _path(root, goal_id, "active")
    goal = _read(path)
    if not isinstance(state, str) or state not in MANUAL_TRANSITIONS.get(goal.state, set()):
        raise GoalError(f"illegal goal transition: {goal.state} -> {state}")
    updated = replace(goal, state=state, evidence=None)
    _write(path, updated, path.read_bytes())
    return updated


def _git(root: Path, *args: str, allow_failure: bool = False) -> subprocess.CompletedProcess[bytes]:
    try:
        result = subprocess.run(["git", *args], cwd=root, capture_output=True, check=False)
    except OSError as exc:
        raise GoalError(f"cannot run git: {exc}") from exc
    if result.returncode and not allow_failure:
        detail = result.stderr.decode("utf-8", "replace").strip()
        raise GoalError(f"git {' '.join(args)} failed: {detail or result.returncode}")
    return result


def _head(root: Path) -> str:
    top = Path(os.fsdecode(_git(root, "rev-parse", "--show-toplevel").stdout).strip()).resolve()
    if top != root.resolve():
        raise GoalError("goal root must be the Git repository root")
    return os.fsdecode(_git(root, "rev-parse", "--verify", "HEAD").stdout).strip()


def _dirty_product_paths(root: Path) -> list[str]:
    tracked = _git(root, "diff", "--name-only", "-z", "HEAD", "--").stdout
    untracked = _git(root, "ls-files", "--others", "--exclude-standard", "-z").stdout
    paths = {os.fsdecode(path) for path in (tracked + untracked).split(b"\0") if path}

    def is_goal_record(path: str) -> bool:
        return path.endswith(".toml") and (
            path.startswith(".ai/goals/active/") or path.startswith(".ai/goals/accepted/")
        )

    return sorted(path for path in paths if not is_goal_record(path))


def _require_clean_product(root: Path) -> None:
    dirty = _dirty_product_paths(root)
    if dirty:
        raise GoalError(
            "uncommitted product files prevent commit-bound verification: " + ", ".join(dirty)
        )


def _require_committed_contract(root: Path, goal: Goal) -> None:
    relative = f".ai/goals/active/{goal.id}.toml"
    committed = _git(root, "show", f"HEAD:{relative}", allow_failure=True)
    if committed.returncode:
        raise GoalError("commit the goal contract before verification")
    if _contract_hash(_parse(committed.stdout)) != _contract_hash(goal):
        raise GoalError(
            "goal contract differs from HEAD; commit acceptance and checks before verification"
        )


def _require_profile_checks(root: Path, goal: Goal) -> None:
    profile_path = root / ".ai/profile.toml"
    if not (profile_path.exists() or profile_path.is_symlink()):
        return
    from .project import ProjectError, load_profile

    try:
        modules = set(load_profile(root).modules)
    except ProjectError as exc:
        raise GoalError(str(exc)) from exc
    required = set()
    if "strict-verification" in modules:
        required.add("verify")
    if "webapp" in modules:
        required.update({"acceptance", "runtime_errors"})
    missing = required - goal.checks.keys()
    if missing:
        raise GoalError("profile requires goal checks: " + ", ".join(sorted(missing)))


def _metadata_only(path: str) -> bool:
    return path.startswith(".ai/goals/") or path.startswith(".ai/current/")


def is_evidence_fresh(root: Path, goal: Goal) -> bool:
    """Whether passing evidence still covers the current product and policy tree."""
    evidence = goal.evidence
    if not _passing_evidence(goal) or evidence.contract_sha256 != _contract_hash(goal):
        return False
    root = Path(root).resolve()
    try:
        head = _head(root)
        if any(not _metadata_only(path) for path in _dirty_product_paths(root)):
            return False
        if _git(
            root, "merge-base", "--is-ancestor", evidence.commit, head, allow_failure=True
        ).returncode:
            return False
        changed = _git(root, "diff", "--name-only", "-z", evidence.commit, head, "--").stdout
    except GoalError:
        return False
    return not any(not _metadata_only(os.fsdecode(path)) for path in changed.split(b"\0") if path)


def _changed_paths(root: Path, commit: str) -> list[str]:
    changed = _git(
        root, "diff-tree", "--root", "--no-commit-id", "--name-only", "-r", "-m", "-z", commit
    ).stdout
    return [os.fsdecode(path) for path in changed.split(b"\0") if path]


def _is_assertion(path: str) -> bool:
    parts = path.lower().split("/")
    name = parts[-1]
    return (
        "tests" in parts[:-1]
        or "e2e" in parts[:-1]
        or name.startswith("test_")
        and name.endswith(".py")
        or any(marker in name for marker in (".test.", ".spec.", ".e2e."))
    )


def _is_product_code(path: str) -> bool:
    name = path.lower().rsplit("/", 1)[-1]
    suffix = "." + name.rsplit(".", 1)[-1] if "." in name else ""
    return not path.startswith(".ai/") and not _is_assertion(path) and suffix in CODE_SUFFIXES


def _cochange_warnings(root: Path, goal: Goal) -> tuple[str, ...]:
    relative = f".ai/goals/active/{goal.id}.toml"
    commits = os.fsdecode(_git(root, "log", "--format=%H", "--", relative).stdout).splitlines()
    warnings = []
    anchor = None
    for commit in commits:
        current = _git(root, "show", f"{commit}:{relative}", allow_failure=True)
        previous = _git(root, "show", f"{commit}^:{relative}", allow_failure=True)
        if current.returncode:
            continue
        try:
            now = _parse(current.stdout)
            before = _parse(previous.stdout) if not previous.returncode else None
        except GoalError:
            return ("Earlier acceptance history could not be parsed; review it manually",)
        if before and (now.acceptance, now.checks, now.intent) == (
            before.acceptance,
            before.checks,
            before.intent,
        ):
            continue
        anchor = commit
        others = [
            path
            for path in _changed_paths(root, commit)
            if path != relative and not path.startswith(".ai/goals/")
        ]
        if others:
            detail = ", ".join(others)
            warnings.append(
                f"Acceptance contract and other files changed together in {commit[:12]}: {detail}",
            )
        break
    if anchor:
        scan_from = anchor
        if (
            goal.evidence
            and not _git(
                root,
                "merge-base",
                "--is-ancestor",
                goal.evidence.commit,
                anchor,
                allow_failure=True,
            ).returncode
        ):
            scan_from = goal.evidence.commit
        later = os.fsdecode(_git(root, "rev-list", "--reverse", f"{scan_from}..HEAD").stdout)
        for commit in [scan_from, *later.splitlines()]:
            changed = _changed_paths(root, commit)
            assertions = [path for path in changed if _is_assertion(path)]
            product = [path for path in changed if _is_product_code(path)]
            if assertions and product:
                warnings.append(
                    f"Product code and acceptance assertions changed together in {commit[:12]}: "
                    f"{', '.join(product)}; {', '.join(assertions)}"
                )
    return tuple(warnings)


def verify_goal(root: Path, goal_id: str) -> Goal:
    root = Path(root).resolve()
    path = _path(root, goal_id, "active")
    goal = _read(path)
    if goal.state not in {"IMPLEMENTED", "VERIFYING"}:
        raise GoalError("goal must be IMPLEMENTED before verification")
    commit = _head(root)
    _require_clean_product(root)
    _require_committed_contract(root, goal)
    _require_profile_checks(root, goal)
    original = path.read_bytes()
    warnings = _cochange_warnings(root, goal)
    results = []
    for name, command in sorted(goal.checks.items()):
        try:
            process = subprocess.run(
                command,
                shell=True,
                executable="/bin/sh",
                cwd=root,
                capture_output=True,
                check=False,
            )
            results.append(
                CheckResult(name, "pass" if process.returncode == 0 else "fail", process.returncode)
            )
        except OSError as exc:
            results.append(CheckResult(name, "unknown", None, str(exc)))
    if _head(root) != commit:
        raise GoalError("Git HEAD changed during verification; run it again")
    _require_clean_product(root)
    if path.read_bytes() != original:
        raise GoalError("goal file changed during verification; run it again")
    status = (
        "unknown"
        if any(item.status == "unknown" for item in results)
        else "fail"
        if any(item.status == "fail" for item in results)
        else "pass"
    )
    evidence = Evidence(commit, _contract_hash(goal), status, tuple(results), warnings)
    verified = replace(
        goal,
        state="VERIFYING" if status == "pass" else "BLOCKED" if status == "unknown" else "ACTIVE",
        evidence=evidence,
    )
    _write(path, verified, original)
    return verified


def complete_goal(root: Path, goal_id: str) -> Goal:
    root = Path(root).resolve()
    path = _path(root, goal_id, "active")
    goal = _read(path)
    original = path.read_bytes()
    if goal.state != "VERIFYING" or goal.evidence is None:
        raise GoalError("goal has no successful verification ready for completion")
    commit = _head(root)
    _require_clean_product(root)
    _require_committed_contract(root, goal)
    evidence = goal.evidence
    if evidence.commit != commit or evidence.contract_sha256 != _contract_hash(goal):
        raise GoalError("verification is stale for this commit or acceptance contract")
    if (
        evidence.result != "pass"
        or {result.name for result in evidence.checks} != set(goal.checks)
        or any(result.status != "pass" or result.exit_code != 0 for result in evidence.checks)
    ):
        raise GoalError("every required check must pass before completion")
    # The goal file is editable; re-run the checks so a forged or obsolete local
    # evidence record cannot by itself authorize DONE.
    goal = verify_goal(root, goal_id)
    if goal.evidence is None or goal.evidence.result != "pass":
        raise GoalError("completion recheck failed; inspect the new verification result")
    original = path.read_bytes()
    baseline = root / ".ai/baseline.json"
    if baseline.is_symlink():
        raise GoalError(f"baseline is a symlink: {baseline}")
    if baseline.exists():
        from .adoption import recheck_baseline

        try:
            comparison = recheck_baseline(root)
        except (OSError, RuntimeError, ValueError) as exc:
            raise GoalError(f"baseline recheck unavailable: {exc}") from exc
        if comparison.regressions or comparison.unresolved or comparison.status == "unknown":
            problems = (*comparison.regressions, *comparison.unresolved)
            raise GoalError(
                "baseline recheck prevents completion: "
                + ("; ".join(problems) if problems else "unknown result")
            )
    destination = _path(root, goal_id, "accepted")
    completed = replace(goal, state="DONE")
    _require_clean_product(root)
    if path.read_bytes() != original or _head(root) != commit:
        raise GoalError("goal file or Git HEAD changed during completion")
    _write(destination, completed)
    if path.read_bytes() != original:
        raise GoalError("goal file changed during completion; accepted copy needs review")
    path.unlink()
    return completed


def reopen_goal(root: Path, goal_id: str) -> Goal:
    """Move an accepted goal back to implementation while keeping its old evidence visible."""
    root = Path(root).resolve()
    accepted = _path(root, goal_id, "accepted")
    goal = _read(accepted)
    if goal.state != "DONE":
        raise GoalError("only DONE goals can be reopened")
    original = accepted.read_bytes()
    active = _path(root, goal_id, "active")
    reopened = replace(goal, state="IMPLEMENTED")
    _write(active, reopened)
    if accepted.read_bytes() != original:
        raise GoalError("accepted goal changed during reopen; active copy needs review")
    accepted.unlink()
    return reopened
