"""Conservative custody of an existing Git repository."""

from __future__ import annotations

import ctypes
import errno
import importlib.util
import json
import os
import platform
import re
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import tomllib
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from ai_kit import __version__
from ai_kit.system import GitRepository, GitStatus, UnsafeWriteError, atomic_write_text

EvidenceState = Literal["CLAIMED", "OBSERVED", "INFERRED", "UNKNOWN"]
CheckStatus = Literal["pass", "fail", "unknown"]

# Linux's generic syscall numbers are shared by x86_64 and aarch64.
_LANDLOCK_SYSCALLS = {"x86_64": (444, 445, 446), "aarch64": (444, 445, 446)}
_WRITE_RIGHTS = sum(1 << bit for bit in (1, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14))


class _PathBeneath(ctypes.Structure):
    _pack_ = 1
    _fields_ = [("allowed_access", ctypes.c_uint64), ("parent_fd", ctypes.c_int32)]


@dataclass(frozen=True, slots=True)
class Evidence:
    state: EvidenceState
    statement: str
    source: str


@dataclass(frozen=True, slots=True)
class CheckResult:
    status: CheckStatus
    command: tuple[str, ...] | None
    exit_code: int | None
    output: str
    reason: str
    counts: dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class AdoptionResult:
    root: Path
    source_commit: str
    evidence: tuple[Evidence, ...]
    checks: dict[str, CheckResult]
    status: CheckStatus
    baseline_path: Path
    custody_path: Path
    inventory_path: Path


@dataclass(frozen=True, slots=True)
class BaselineComparison:
    status: CheckStatus
    regressions: tuple[str, ...]
    unresolved: tuple[str, ...]


def compare_baseline(
    baseline: Mapping[str, CheckResult], current: Mapping[str, CheckResult]
) -> BaselineComparison:
    """Compare measured checks without equating unchanged failures with success."""

    regressions: list[str] = []
    unresolved: list[str] = []
    for name, before in baseline.items():
        after = current.get(name)
        if after is None:
            unresolved.append(f"{name}: check missing")
            continue
        if after.status == "unknown":
            unresolved.append(f"{name}: current result unknown")
            if after.reason:
                unresolved.append(f"{name}: {after.reason}")
            continue
        if before.status == "pass" and after.status == "fail":
            regressions.append(f"{name}: pass -> fail")
        if before.status == after.status == "fail" and not before.counts:
            if before.exit_code != after.exit_code or before.output != after.output:
                unresolved.append(f"{name}: unquantified failure changed")
        for metric in ("failed", "skipped", "violations"):
            if metric not in before.counts:
                continue
            if metric not in after.counts and after.status != "pass":
                unresolved.append(f"{name}: {metric} count missing")
            elif after.counts.get(metric, 0) > before.counts[metric]:
                regressions.append(f"{name}: {metric} increased")
        if "passed" in before.counts:
            if "passed" not in after.counts:
                unresolved.append(f"{name}: passed count missing")
            elif after.counts["passed"] < before.counts["passed"]:
                regressions.append(f"{name}: passed decreased")
    for name, check in current.items():
        if name not in baseline and check.status == "unknown":
            unresolved.append(f"{name}: current result unknown")
    if regressions or any(check.status == "fail" for check in current.values()):
        status: CheckStatus = "fail"
    elif unresolved or not baseline or not current:
        status = "unknown"
    else:
        status = "pass"
    return BaselineComparison(status, tuple(regressions), tuple(unresolved))


def _check_target(path: Path) -> None:
    if path.is_symlink() or path.exists():
        raise UnsafeWriteError(f"refusing to overwrite existing adoption artifact {path}")


def _inventory(root: Path, head: str, dirty: bool) -> list[Evidence]:
    facts = [
        Evidence("OBSERVED", f"Git HEAD is {head}", "git rev-parse HEAD"),
        Evidence(
            "OBSERVED",
            f"Working tree was {'dirty' if dirty else 'clean'} before adoption",
            "git status",
        ),
    ]
    for name in ("README.md", "pyproject.toml", "package.json", "tests", "dev"):
        if (root / name).exists():
            facts.append(Evidence("OBSERVED", f"{name} exists", name))
    if (root / "pyproject.toml").is_file():
        facts.append(
            Evidence("INFERRED", "Repository appears to contain Python code", "pyproject.toml")
        )
    if (root / "package.json").is_file():
        facts.append(
            Evidence("INFERRED", "Repository appears to contain Node code", "package.json")
        )
    readme = root / "README.md"
    if readme.is_file() and not readme.is_symlink():
        for line in readme.read_text(encoding="utf-8", errors="replace").splitlines():
            if (
                re.search(r"(?<!\w)(?:npm test|pytest|\./dev (?:check|verify))\b", line)
                and len(line) < 300
            ):
                facts.append(Evidence("CLAIMED", f"README documents: {line.strip()}", "README.md"))
                if sum(fact.state == "CLAIMED" for fact in facts) == 3:
                    break
    facts.append(
        Evidence(
            "UNKNOWN", "User-facing acceptance behavior has not been verified", "safe adoption"
        )
    )
    return facts


def _command(root: Path) -> tuple[str, tuple[str, ...] | None, str]:
    dev = root / "dev"
    if dev.is_file() and not dev.is_symlink():
        if os.access(dev, os.X_OK):
            return "verify", ("./dev", "verify"), "Project verification command"
        return "verify", None, "dev exists but is not executable"

    tests = root / "tests"
    has_python_tests = tests.is_dir() and any(tests.rglob("test_*.py"))
    if has_python_tests and (root / "pyproject.toml").is_file():
        if importlib.util.find_spec("pytest") is not None:
            return (
                "unit",
                (sys.executable, "-m", "pytest", "-q", "-s", "-o", "log_file=.ai-kit-pytest.log"),
                "Python test suite",
            )
        return "unit", None, "pytest is unavailable in the current Python environment"

    package = root / "package.json"
    if package.is_file() and not package.is_symlink():
        try:
            script = json.loads(package.read_text(encoding="utf-8")).get("scripts", {}).get("test")
        except (ValueError, AttributeError):
            return "unit", None, "package.json could not be parsed"
        if script == "node --test":
            return "unit", ("node", "--test"), "Node built-in test runner"
        return "unit", None, "No recognized safe Node test command"

    return "verify", None, "No recognized verification command"


def _copy_for_check(root: Path, destination: Path) -> None:
    # ponytail: copying costs disk space; use a filesystem sandbox for large repos.
    def ignored(_directory: str, names: list[str]) -> set[str]:
        return set(names) & {".ai-local", ".venv", "node_modules", "__pycache__", ".pytest_cache"}

    shutil.copytree(root, destination, symlinks=True, ignore=ignored)
    # A linked worktree's .git file points outside the copy. Give checks local metadata.
    git_pointer = destination / ".git"
    if git_pointer.is_file() or git_pointer.is_symlink():
        git_pointer.unlink()
        subprocess.run(["git", "init", "-q", str(destination)], check=True, capture_output=True)
    for path in destination.rglob("*"):
        if path.is_symlink() and not path.resolve().is_relative_to(destination):
            raise ValueError(f"verification copy contains an external symlink: {path}")


def _landlock_ruleset(directory: Path) -> tuple[ctypes.CDLL, int, int]:
    """Preflight an ABI 3+ write policy before launching project code."""

    numbers = _LANDLOCK_SYSCALLS.get(platform.machine()) if sys.platform == "linux" else None
    if numbers is None or not hasattr(os, "O_PATH"):
        raise OSError(errno.ENOTSUP, "Landlock write confinement is unavailable on this platform")
    create, add, restrict = numbers
    libc = ctypes.CDLL(None, use_errno=True)
    libc.syscall.restype = ctypes.c_long
    abi = libc.syscall(create, ctypes.c_void_p(), ctypes.c_size_t(0), 1)
    if abi < 0:
        raise OSError(ctypes.get_errno(), "Landlock ABI probe failed")
    if abi < 3:
        raise OSError(errno.ENOTSUP, f"Landlock ABI {abi} cannot confine truncation")

    rights = _WRITE_RIGHTS | ((1 << 15) if abi >= 5 else 0)
    ruleset_attr = ctypes.c_uint64(rights)
    ruleset_fd = libc.syscall(create, ctypes.byref(ruleset_attr), ctypes.sizeof(ruleset_attr), 0)
    if ruleset_fd < 0:
        raise OSError(ctypes.get_errno(), "Landlock ruleset creation failed")
    try:
        parent_fd = os.open(directory, os.O_PATH | os.O_CLOEXEC)
        try:
            rule = _PathBeneath(rights, parent_fd)
            if libc.syscall(add, ruleset_fd, 1, ctypes.byref(rule), 0) < 0:
                raise OSError(ctypes.get_errno(), "Landlock path rule failed")
        finally:
            os.close(parent_fd)
    except BaseException:
        os.close(ruleset_fd)
        raise
    return libc, restrict, ruleset_fd


def _enforce_landlock(libc: ctypes.CDLL, restrict: int, ruleset_fd: int) -> None:
    if libc.prctl(38, 1, 0, 0, 0) < 0:  # PR_SET_NO_NEW_PRIVS
        raise OSError(ctypes.get_errno(), "could not set no_new_privs")
    if libc.syscall(restrict, ruleset_fd, 0) < 0:
        raise OSError(ctypes.get_errno(), "could not enforce Landlock")
    os.close(ruleset_fd)


def _run_check(root: Path, command: tuple[str, ...], timeout: float) -> CheckResult:
    with tempfile.TemporaryDirectory(prefix="ai-kit-adopt-") as temporary:
        copy = Path(temporary) / "repo"
        try:
            _copy_for_check(root, copy)
        except (OSError, ValueError, subprocess.CalledProcessError) as exc:
            return CheckResult("unknown", command, None, "", f"Could not copy repository: {exc}")
        sandbox = Path(tempfile.mkdtemp(prefix=".ai-kit-check-", dir=copy))
        for name in ("home", "tmp", "cache", "config", "data", "state"):
            (sandbox / name).mkdir()
        environment = {
            name: os.environ[name]
            for name in ("PATH", "LANG", "LC_ALL", "LC_CTYPE", "TERM", "TZ")
            if name in os.environ
        }
        environment.update(
            HOME=str(sandbox / "home"),
            TMPDIR=str(sandbox / "tmp"),
            XDG_CACHE_HOME=str(sandbox / "cache"),
            XDG_CONFIG_HOME=str(sandbox / "config"),
            XDG_DATA_HOME=str(sandbox / "data"),
            XDG_STATE_HOME=str(sandbox / "state"),
            PWD=str(copy),
            PYTHONPATH=os.pathsep.join((str(copy), str(copy / "src"))),
            PYTHONDONTWRITEBYTECODE="1",
            PYTEST_DISABLE_PLUGIN_AUTOLOAD="1",
        )
        try:
            libc, restrict, ruleset_fd = _landlock_ruleset(copy)
        except OSError as exc:
            return CheckResult("unknown", command, None, "", f"Landlock unavailable: {exc}")
        with tempfile.NamedTemporaryFile(dir=copy) as output:
            try:
                try:
                    process = subprocess.Popen(
                        command,
                        cwd=copy,
                        env=environment,
                        stdin=subprocess.DEVNULL,
                        stdout=output,
                        stderr=subprocess.STDOUT,
                        start_new_session=True,
                        pass_fds=(ruleset_fd,),
                        preexec_fn=lambda: _enforce_landlock(libc, restrict, ruleset_fd),
                    )
                except (OSError, subprocess.SubprocessError) as exc:
                    return CheckResult(
                        "unknown", command, None, "", f"Check did not start under Landlock: {exc}"
                    )
            finally:
                os.close(ruleset_fd)
            try:
                exit_code = process.wait(timeout=timeout)
                reason = f"Exited with code {exit_code}"
                status: CheckStatus = "pass" if exit_code == 0 else "fail"
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait()
                exit_code = None
                reason = f"Timed out after {timeout:g} seconds"
                status = "unknown"
            # Classify all diagnostics; the tail below is only stored evidence.
            size = output.tell()
            counts: dict[str, int] = {}
            skipped = False
            output.seek(0)
            while output.tell() < size:
                raw_line = output.readline(size - output.tell())
                if not raw_line:
                    break
                line = raw_line.decode("utf-8", errors="replace")
                if command[1:3] == ("-m", "pytest") and " in " in line:
                    reported = {
                        kind: int(count)
                        for count, kind in re.findall(
                            r"(\d+) (passed|failed|skipped|xfailed)", line
                        )
                    }
                    if reported:
                        counts = reported
                if re.search(r"\b[1-9]\d* (?:skipped|xfailed)\b|(?m:^# skip [1-9]\d*\b)", line):
                    skipped = True
            output.seek(max(0, size - 4000))
            summary = output.read().decode("utf-8", errors="replace")
            if size > 4000:
                summary = "[earlier output truncated]\n" + summary
            if status == "pass" and (counts.get("skipped", 0) or counts.get("xfailed", 0)):
                status = "unknown"
                reason = "Pytest reported skipped or expected-failure tests"
            if status == "pass" and skipped:
                status = "unknown"
                reason = "Check output reports skipped tests"
            return CheckResult(status, command, exit_code, summary, reason, counts)


def adopt_safe(
    root: Path, *, timeout: float = 60, original_source: GitStatus | None = None
) -> AdoptionResult:
    """Inspect and baseline a Git repo without running checks in its worktree.

    Only new AI Kit adoption artifacts are written. Existing artifacts are never
    overwritten; a failed or unavailable check remains failed or unknown.
    Setup supplies the status captured before its private snapshot writes.
    """

    if timeout <= 0:
        raise ValueError("timeout must be positive")
    root = Path(root).expanduser().resolve()
    repository = GitRepository.discover(root)
    if repository is None or repository.root != root:
        raise ValueError(f"{root} is not a Git repository root")
    if any(
        path.exists() or path.is_symlink()
        for path in (root / "ai-kit.lock", root / ".ai/profile.toml")
    ):
        raise ValueError("repository already has AI Kit management files; use sync")
    source = repository.status()
    if source.head is None:
        raise ValueError("adoption requires an existing Git commit; use init for a new repository")
    original_source = original_source or source
    if original_source.head != source.head:
        raise RuntimeError("repository HEAD changed before adoption")

    ai = root / ".ai"
    adoption = ai / "adoption"
    if ai.is_symlink() or adoption.is_symlink():
        raise UnsafeWriteError("refusing to write through a symlinked .ai directory")
    if (ai.exists() and not ai.is_dir()) or (adoption.exists() and not adoption.is_dir()):
        raise UnsafeWriteError("refusing to write through a non-directory .ai path")
    baseline_path = ai / "baseline.json"
    custody_path = ai / "CUSTODY.toml"
    inventory_path = adoption / "inventory.md"
    for path in (baseline_path, custody_path, inventory_path):
        _check_target(path)

    evidence = _inventory(root, source.head, original_source.is_dirty)
    name, command, description = _command(root)
    check = (
        _run_check(root, command, timeout)
        if command is not None
        else CheckResult("unknown", None, None, "", description)
    )
    checks = {name: check}
    evidence.append(
        Evidence(
            "OBSERVED" if command is not None else "UNKNOWN", f"{name}: {check.status}", description
        )
    )
    # A unit suite is useful baseline evidence, not full project verification.
    status: CheckStatus = "unknown" if name != "verify" and check.status == "pass" else check.status

    after = repository.status()
    if after.head != source.head or after.entries != source.entries:
        raise RuntimeError("repository changed during adoption; no artifacts were written")

    baseline = {
        "schema": 1,
        "commit": source.head,
        "source": {
            "dirty": original_source.is_dirty,
            "changes": [
                {"status": item.status, "path": item.path} for item in original_source.entries
            ],
        },
        "status": status,
        "checks": {name: asdict(check)},
    }
    baseline_text = json.dumps(baseline, indent=2, sort_keys=True) + "\n"
    custody_text = (
        "schema = 1\nmanaged = false\n\n[custody]\n"
        'mode = "safe"\n'
        f'source_commit = "{source.head}"\n'
        f'adopted_at = "{datetime.now(UTC).isoformat(timespec="seconds")}"\n'
        f'ai_kit_version = "{__version__}"\n\n'
        "[baseline]\n"
        'file = ".ai/baseline.json"\n'
        f'commit = "{source.head}"\n'
    )
    inventory_lines = [
        "# Adoption inventory",
        "",
        "Temporary discovery record. Claims are not authoritative project intent.",
        "Runnable checks require Landlock write confinement in a temporary copy.",
        "Landlock is not a full security sandbox.",
        "",
        f"Source commit: `{source.head}`",
        f"Overall verification status: **{status}**",
        "",
        "## Evidence",
        "",
    ]
    inventory_lines.extend(
        f"- **{fact.state}** {json.dumps(fact.statement)} ({fact.source})" for fact in evidence
    )
    inventory_lines.extend(
        ["", "## Verification", "", f"- {name}: **{check.status}** — {check.reason}"]
    )
    if check.command:
        inventory_lines.append(f"- Command: `{shlex.join(check.command)}`")
    inventory_text = "\n".join(inventory_lines) + "\n"

    for path in (baseline_path, custody_path, inventory_path):
        _check_target(path)
    atomic_write_text(baseline_path, baseline_text)
    atomic_write_text(custody_path, custody_text)
    atomic_write_text(inventory_path, inventory_text)
    return AdoptionResult(
        root,
        source.head,
        tuple(evidence),
        checks,
        status,
        baseline_path,
        custody_path,
        inventory_path,
    )


def _stored_check(value: object) -> CheckResult:
    if not isinstance(value, dict) or value.get("status") not in ("pass", "fail", "unknown"):
        raise ValueError("invalid baseline check")
    command = value.get("command")
    counts = value.get("counts", {})
    if command is not None and (
        not isinstance(command, list) or not all(isinstance(x, str) for x in command)
    ):
        raise ValueError("invalid baseline check command")
    if not isinstance(counts, dict) or any(
        not isinstance(key, str) or type(number) is not int or number < 0
        for key, number in counts.items()
    ):
        raise ValueError("invalid baseline check counts")
    exit_code = value.get("exit_code")
    if exit_code is not None and type(exit_code) is not int:
        raise ValueError("invalid baseline check exit code")
    if not isinstance(value.get("output"), str) or not isinstance(value.get("reason"), str):
        raise ValueError("invalid baseline check report")
    return CheckResult(
        value["status"],
        tuple(command) if command is not None else None,
        exit_code,
        value["output"],
        value["reason"],
        counts,
    )


def _recognized_check(root: Path, command: tuple[str, ...]) -> bool:
    if command == ("./dev", "verify"):
        dev = root / "dev"
        return dev.is_file() and not dev.is_symlink() and os.access(dev, os.X_OK)
    if command == ("node", "--test"):
        return shutil.which("node") is not None
    return (
        command == (sys.executable, "-m", "pytest", "-q", "-s", "-o", "log_file=.ai-kit-pytest.log")
        and importlib.util.find_spec("pytest") is not None
    )


def recheck_baseline(root: Path, *, timeout: float = 60) -> BaselineComparison:
    """Rerun recorded checks under Landlock and compare with adoption evidence."""

    if timeout <= 0:
        raise ValueError("timeout must be positive")
    root = Path(root).expanduser().resolve()
    repository = GitRepository.discover(root)
    if repository is None or repository.root != root:
        raise ValueError(f"{root} is not a Git repository root")
    path = root / ".ai/baseline.json"
    if path.is_symlink():
        raise UnsafeWriteError(f"refusing symlinked baseline {path}")
    baseline = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(baseline, dict) or baseline.get("schema") != 1:
        raise ValueError(f"invalid adoption baseline: {path}")
    raw_checks = baseline.get("checks")
    if not isinstance(raw_checks, dict) or not all(isinstance(name, str) for name in raw_checks):
        raise ValueError(f"invalid adoption checks: {path}")
    # An originally unknown obligation that setup resolved through a concrete
    # discovered command is superseded by that command, not re-reported unknown.
    superseded = {
        value["resolves"]
        for value in raw_checks.values()
        if isinstance(value, dict)
        and value.get("discovery") is True
        and isinstance(value.get("resolves"), str)
        and isinstance(raw_checks.get(value["resolves"]), dict)
        and raw_checks[value["resolves"]].get("status") == "unknown"
        and raw_checks[value["resolves"]].get("command") is None
    }
    before = {
        name: _stored_check(value) for name, value in raw_checks.items() if name not in superseded
    }
    # Setup-discovered baseline checks are recorded with an explicit marker so
    # they survive as evidence instead of silently disappearing at recheck time.
    discovered = {
        name
        for name, value in raw_checks.items()
        if isinstance(value, dict) and value.get("discovery") is True
    }
    source = repository.status()
    current = {}
    for name, check in before.items():
        recognized = check.command is not None and (
            _recognized_check(root, check.command) or name in discovered
        )
        if not recognized:
            current[name] = CheckResult(
                "unknown",
                check.command,
                None,
                "",
                "Recorded command is unavailable or unrecognized",
            )
        else:
            current[name] = _run_check(root, check.command, timeout)
    after = repository.status()
    if after.head != source.head or after.entries != source.entries:
        current = {
            name: CheckResult(
                "unknown", check.command, None, "", "Repository changed during recheck"
            )
            for name, check in before.items()
        }
    return compare_baseline(before, current)


def mark_managed(result: AdoptionResult) -> Path:
    """Mark custody complete after the caller has installed AI Kit files."""

    path = result.custody_path
    if path.is_symlink():
        raise UnsafeWriteError(f"refusing to update symlinked custody marker {path}")
    content = path.read_text(encoding="utf-8")
    custody = tomllib.loads(content)
    if (
        custody.get("schema") != 1
        or custody.get("custody", {}).get("mode") != "safe"
        or custody.get("custody", {}).get("source_commit") != result.source_commit
        or custody.get("baseline", {}).get("file") != ".ai/baseline.json"
    ):
        raise ValueError(f"invalid AI Kit custody marker: {path}")
    if custody.get("managed") is True:
        return path
    original_header = "schema = 1\nmanaged = false\n\n[custody]\n"
    if custody.get("managed") is not False or not content.startswith(original_header):
        raise ValueError(f"invalid unmanaged custody marker: {path}")
    atomic_write_text(
        path,
        content.replace(original_header, original_header.replace("false", "true"), 1),
        expected_content=content,
    )
    return path
