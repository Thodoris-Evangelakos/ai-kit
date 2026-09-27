"""Project-wide requirements and deterministic, commit-bound verification evidence."""

from __future__ import annotations

import json
import os
import re
import shutil
import signal
import subprocess
import tempfile
import tomllib
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path

from .system import GitRepository, atomic_write_text, run_git, sha256_bytes, sha256_file

POLICY_PATH = ".ai/verification.toml"
EVIDENCE_PATH = ".ai-local/evidence/latest.json"
CAPABILITY = re.compile(r"[a-z][a-z0-9-]*(?::[a-z][a-z0-9-]*)?\Z")


class FailureClass(StrEnum):
    STATIC = "STATIC"
    UNIT = "UNIT"
    CONTRACT = "CONTRACT"
    INTEGRATION = "INTEGRATION"
    ACCEPTANCE = "ACCEPTANCE"
    OBSERVABILITY = "OBSERVABILITY"
    INFRA = "INFRA"
    FLAKY = "FLAKY"
    HARNESS = "HARNESS"
    BASELINE_REGRESSION = "BASELINE_REGRESSION"
    UNKNOWN = "UNKNOWN"


def classify_check(name: str, reason: str = "") -> FailureClass:
    if "landlock" in reason.lower() or "sandbox" in reason.lower():
        return FailureClass.INFRA
    return {
        "lint": FailureClass.STATIC,
        "static": FailureClass.STATIC,
        "unit": FailureClass.UNIT,
        "tests": FailureClass.UNIT,
        "contract": FailureClass.CONTRACT,
        "integration": FailureClass.INTEGRATION,
        "acceptance": FailureClass.ACCEPTANCE,
        "browser": FailureClass.ACCEPTANCE,
        "runtime_errors": FailureClass.OBSERVABILITY,
        "runtime-errors": FailureClass.OBSERVABILITY,
        "baseline": FailureClass.BASELINE_REGRESSION,
    }.get(name.split(":", 1)[0], FailureClass.UNKNOWN)


class VerificationError(ValueError):
    """Verification cannot establish evidence for this repository."""


@dataclass(frozen=True)
class Requirement:
    id: str
    command: tuple[str, ...] | None = None
    timeout_seconds: int = 300


@dataclass(frozen=True)
class Policy:
    requirements: tuple[Requirement, ...]
    digest: str


def default_policy(modules: tuple[str, ...], *, adopted: bool = False) -> str:
    required = ["static", "tests"]
    if "webapp" in modules:
        required += ["browser", "runtime-errors"]
    if adopted:
        required.append("baseline")
    return (
        "# Project-owned policy. Configure real commands before verification can pass.\n"
        f"schema = 1\nrequired = {json.dumps(required)}\n\n"
        "# Profiles seed defaults only; this file is the effective policy.\n"
        "# Commands are argv arrays, run from the repository root without a shell.\n"
        "# Never call ./dev verify or ai-kit verify from a requirement.\n"
        '# [requirements.static]\n# command = ["./dev", "lint"]\n'
        '# [requirements.tests]\n# command = ["./dev", "test"]\n'
        "# timeout_seconds = 300\n"
    )


def load_policy(root: Path) -> Policy:
    path = root / POLICY_PATH
    if (root / ".ai").is_symlink() or path.is_symlink():
        raise VerificationError(f"refusing symlinked policy: {path}")
    try:
        raw = path.read_bytes()
        data = tomllib.loads(raw.decode("utf-8"))
    except (OSError, UnicodeError, tomllib.TOMLDecodeError) as exc:
        raise VerificationError(f"invalid or missing {POLICY_PATH}: {exc}") from exc
    if set(data) - {"schema", "required", "requirements"} or not {"schema", "required"} <= set(
        data
    ):
        raise VerificationError("policy has missing or unknown fields")
    if type(data["schema"]) is not int or data["schema"] != 1:
        raise VerificationError("verification policy schema must be 1")
    required = data["required"]
    if (
        not isinstance(required, list)
        or not required
        or any(not isinstance(name, str) or not CAPABILITY.fullmatch(name) for name in required)
        or len(set(required)) != len(required)
    ):
        raise VerificationError("required must be a nonempty array of unique capability IDs")
    implementations = data.get("requirements", {})
    if not isinstance(implementations, dict) or set(implementations) - set(required):
        raise VerificationError("requirements must configure only IDs listed in required")
    requirements = []
    for name in required:
        config = implementations.get(name, {})
        if not isinstance(config, dict) or set(config) - {"command", "timeout_seconds"}:
            raise VerificationError(f"{name}: expected command and optional timeout_seconds")
        if name == "baseline" and config:
            raise VerificationError("baseline uses the built-in comparison, not a custom command")
        command = config.get("command")
        if command is not None and (
            not isinstance(command, list)
            or not command
            or any(not isinstance(arg, str) or "\0" in arg for arg in command)
            or not command[0].strip()
        ):
            raise VerificationError(f"{name}: command must be a nonempty argv array")
        timeout = config.get("timeout_seconds", 300)
        if type(timeout) is not int or timeout <= 0:
            raise VerificationError(f"{name}: timeout_seconds must be a positive integer")
        requirements.append(
            Requirement(name, tuple(command) if command is not None else None, timeout)
        )
    if (
        any(
            (root / f".ai/{name}").exists() or (root / f".ai/{name}").is_symlink()
            for name in ("baseline.json", "CUSTODY.toml")
        )
        and "baseline" not in required
    ):
        raise VerificationError("adopted repositories must require baseline in verification policy")
    # Hash the exact bytes, including comments; edits always invalidate prior evidence.
    return Policy(tuple(requirements), sha256_bytes(raw))


def _configuration_problem(root: Path, requirement: Requirement) -> str:
    if requirement.id == "baseline":
        try:
            from .adoption import _recognized_check, load_baseline

            checks = load_baseline(root)
            if not checks:
                return "baseline has no measured checks"
            if any(
                check.command is None or not _recognized_check(root, check.command)
                for check in checks.values()
            ):
                return "baseline command unavailable or unrecognized"
        except (OSError, ValueError, RuntimeError) as exc:
            return str(exc)
        return ""
    if requirement.command is None:
        return "no implementation configured"
    executable = requirement.command[0]
    if "/" in executable:
        path = root / executable
        available = path.is_file() and os.access(path, os.X_OK)
    else:
        available = shutil.which(executable) is not None
    return "" if available else f"command unavailable: {executable}"


def configuration_problems(root: Path, policy: Policy) -> tuple[str, ...]:
    return tuple(
        f"{item.id}: {problem}"
        for item in policy.requirements
        if (problem := _configuration_problem(root, item))
    )


def _repository(root: Path) -> GitRepository:
    repository = GitRepository.discover(root)
    if repository is None or repository.root != root:
        raise VerificationError("verification must run at a Git repository root")
    return repository


def _snapshot(root: Path) -> tuple[str, str, str]:
    from .project import load_profile

    load_profile(root)
    policy = load_policy(root)
    status = _repository(root).status()
    if not status.head:
        raise VerificationError("commit the project and verification policy before verification")
    if status.is_dirty:
        raise VerificationError(
            "uncommitted files prevent commit-bound verification: "
            + ", ".join(status.changed_paths)
        )
    tracked = run_git(
        ["ls-files", "--error-unmatch", "--", POLICY_PATH, ".ai/profile.toml"],
        cwd=root,
        check=False,
    )
    if tracked.returncode:
        raise VerificationError(
            "commit the verification policy and profile; ignored files are not evidence inputs"
        )
    return status.head, policy.digest, sha256_file(root / ".ai/profile.toml")


def read_evidence(root: Path) -> dict:
    try:
        evidence = json.loads((root / EVIDENCE_PATH).read_text())
    except (OSError, ValueError):
        return {}
    return evidence if isinstance(evidence, dict) else {}


def is_evidence_fresh(root: Path, evidence: dict) -> bool:
    """Inspect saved results; only a new verification run establishes new evidence."""
    root = root.resolve()
    try:
        commit, policy_digest, profile_digest = _snapshot(root)
        policy = load_policy(root)
        required = [item.id for item in policy.requirements]
        results = evidence["requirements"]
        return (
            evidence["schema"] == 1
            and evidence["repository"] == str(root)
            and evidence["commit"] == commit
            and evidence["policy_sha256"] == policy_digest
            and evidence["profile_sha256"] == profile_digest
            and evidence["result"] == "pass"
            and evidence["required"] == required
            and isinstance(results, dict)
            and set(results) == set(required)
            and all(
                isinstance(results[name], dict)
                and results[name].get("status") == "pass"
                and results[name].get("exit_code") == 0
                for name in required
            )
        )
    except (OSError, ValueError, RuntimeError, KeyError, TypeError):
        return False


def _cochange_warnings(root: Path) -> list[str]:
    changed = run_git(
        ["diff-tree", "--root", "--no-commit-id", "--name-only", "-r", "-m", "-z", "HEAD"], cwd=root
    ).stdout.split("\0")

    def is_test(path: str) -> bool:
        return bool(re.search(r"(^|/)(tests?|e2e)/|(^|/)test_[^/]+\.py$|\.(test|spec|e2e)\.", path))

    tests = [path for path in changed if is_test(path)]
    product = [
        path
        for path in changed
        if not is_test(path)
        and not path.startswith(".ai/")
        and Path(path).suffix
        in {
            ".py",
            ".js",
            ".ts",
            ".tsx",
            ".jsx",
            ".go",
            ".rs",
            ".sh",
            ".java",
            ".c",
            ".cpp",
            ".sql",
            ".vue",
            ".svelte",
        }
    ]
    # ponytail: inspect HEAD only; use a PR diff if cross-commit review signals become necessary.
    return (
        ["Product code and tests changed together in HEAD; review behavior and assertions."]
        if tests and product
        else []
    )


def _run_requirement(root: Path, requirement: Requirement, directory: Path, index: int) -> dict:
    result = {
        "adapter": "baseline" if requirement.id == "baseline" else "command",
        "command": list(requirement.command) if requirement.command else None,
        "timeout_seconds": requirement.timeout_seconds,
        "status": "unknown",
        "exit_code": None,
        "reason": "",
        "artifacts": [],
    }
    problem = _configuration_problem(root, requirement)
    if problem:
        result["reason"] = problem
        return result
    if requirement.id == "baseline":
        from .adoption import recheck_baseline

        try:
            comparison = recheck_baseline(root, timeout=requirement.timeout_seconds)
            result["comparison"] = asdict(comparison)
            # An unchanged inherited failure remains visible, but is not a regression.
            status = (
                "fail"
                if comparison.regressions
                else "unknown"
                if comparison.unresolved or comparison.status == "unknown"
                else "pass"
            )
            result.update(
                status=status,
                exit_code=0 if status == "pass" else 1 if status == "fail" else None,
                reason="; ".join((*comparison.regressions, *comparison.unresolved))
                or f"No measured regression; inherited status: {comparison.status}",
            )
        except (OSError, RuntimeError, ValueError) as exc:
            result["reason"] = f"baseline comparison unavailable: {exc}"
        return result
    log = directory / f"{index}.log"
    result["artifacts"] = [str(log.relative_to(root))]
    with log.open("xb") as output:
        try:
            process = subprocess.Popen(
                requirement.command,
                cwd=root,
                env={**os.environ, "AI_KIT_VERIFY_ROOT": str(root)},
                stdin=subprocess.DEVNULL,
                stdout=output,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        except OSError as exc:
            result["reason"] = str(exc)
            return result
        try:
            code = process.wait(timeout=requirement.timeout_seconds)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait()
            result["reason"] = f"timed out after {requirement.timeout_seconds}s"
            return result
    status = "pass" if code == 0 else "unknown" if code in (126, 127) else "fail"
    result.update(
        status=status, exit_code=code, reason=f"exit {code}; log: {log.relative_to(root)}"
    )
    return result


def verify_project(root: Path) -> dict:
    """Always rerun the policy. Evidence never changes tracked repository state."""
    root = root.resolve()
    if os.environ.get("AI_KIT_VERIFY_ROOT") == str(root) or os.environ.get(
        "AI_KIT_BASELINE_RECHECK"
    ):
        raise VerificationError(
            "recursive verification: configure underlying checks, not ai-kit verify or ./dev verify"
        )
    snapshot = _snapshot(root)
    policy = load_policy(root)
    directory = root / ".ai-local/evidence"
    for path in (root / ".ai-local", directory):
        if path.is_symlink():
            raise VerificationError(f"refusing symlinked evidence directory: {path}")
    if run_git(["check-ignore", "-q", "--", EVIDENCE_PATH], cwd=root, check=False).returncode:
        raise VerificationError("ignore .ai-local/ before recording verification evidence")
    directory.mkdir(parents=True, exist_ok=True)
    directory = Path(tempfile.mkdtemp(prefix="run-", dir=directory))
    results = {
        item.id: _run_requirement(root, item, directory, index)
        for index, item in enumerate(policy.requirements)
    }
    status = (
        "fail"
        if any(item["status"] == "fail" for item in results.values())
        else "unknown"
        if any(item["status"] == "unknown" for item in results.values())
        else "pass"
    )
    reason = ""
    try:
        if _snapshot(root) != snapshot:
            reason = "commit or configuration changed during verification; rerun"
    except (OSError, RuntimeError, ValueError) as exc:
        reason = f"repository changed during verification: {exc}"
    evidence = {
        "schema": 1,
        "repository": str(root),
        "commit": snapshot[0],
        "policy_sha256": snapshot[1],
        "profile_sha256": snapshot[2],
        "recorded_at": datetime.now(UTC).isoformat(),
        "required": [item.id for item in policy.requirements],
        "requirements": results,
        "result": "unknown" if reason else status,
        "reason": reason,
        "warnings": _cochange_warnings(root),
    }
    content = json.dumps(evidence, indent=2, sort_keys=True) + "\n"
    atomic_write_text(directory / "evidence.json", content)
    latest = root / EVIDENCE_PATH
    atomic_write_text(
        latest, content, expected_content=latest.read_text() if latest.exists() else None
    )
    return evidence
