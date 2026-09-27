# AI Kit

AI Kit makes a repository legible, reproducible, and verifiable to a fresh coding agent. The harness owns what work is being pursued. AI Kit owns what evidence the repository requires before that work may be considered verified.

The MVP is Linux first, Python 3.11+, and Codex focused. It uses Git for history and project-owned commands for verification. It has no daemon, task tracker, web UI, or service dependency.

## Install and develop

```sh
uv tool install /path/to/ai-kit
# From this checkout:
./dev setup
./dev check
# Commit changes before collecting completion evidence:
./dev verify
```

An editable global development install is also possible with `uv tool install --editable .`.

## Start a project

Run at a Git repository root:

```sh
ai-kit init
# Configure .ai/verification.toml and the project-owned ./dev commands.
ai-kit sync --check
ai-kit doctor
git add . && git commit -m "Configure project verification"
./dev verify
```

`init` creates a small `AGENTS.md`, profile, project-owned verification policy, current-state files, a verification-design skill, `ai-kit.lock`, ignored `.ai-local/`, and a `./dev` starter. The starter delegates `verify` to `ai-kit verify`; its other commands deliberately fail until implemented. Initial policy requirements are unconfigured, so `doctor` and verification remain nonzero until real mechanisms are supplied. AI Kit never regenerates the project's `dev` or policy.

`--module webapp` seeds `browser` and `runtime-errors` requirements and supplies design guidance. Both remain UNKNOWN until configured; a browser harness is not included. `--module professional-repository` renders CI using `./dev setup` then `./dev verify` and uploads verification evidence even after failure. `--module learning` adds a private learning skill. Profiles seed policy only at initialization; later profile edits never silently alter it. Other modules currently have no additional deterministic requirements. The effective, overrideable policy is always visible in one file.

`sync` checks hashes in `ai-kit.lock`, refuses manual drift, and updates generated files. `sync --check` also validates policy structure. `doctor` checks configuration, required command availability, managed files, and the executable `dev`; it never executes the suite or requires saved passing evidence.

## Verification policy

`.ai/verification.toml` schema 1 separates required capabilities from their implementations:

```toml
schema = 1
required = ["static", "tests"]

[requirements.static]
command = ["./dev", "lint"]

[requirements.tests]
command = ["./dev", "test"]
timeout_seconds = 300
```

`required` is a nonempty list of unique lowercase capability IDs, optionally namespaced with `:`. `requirements` is an optional table mapping those IDs to implementations. Each implementation has a nonempty `command` argv array and an optional positive integer `timeout_seconds` (default 300). Arguments run directly from the repository root, without shell expansion; use an explicit shell command when needed. Unknown fields, invalid schemas, and misspelled configuration keys outside `required` are rejected.

IDs such as `integration`, `property:hypothesis`, `fuzz:libfuzzer`, `security`, `secret-scan`, `formal:bend`, `formal:tla`, `browser:playwright`, and `custom:behavior` use the same command adapter when configured. AI Kit does not supply those tools. An unconfigured ID, including an unfamiliar one, produces UNKNOWN and nonzero, never an implicit pass. `baseline` is the one built-in requirement, using the inherited adoption comparison instead of a command override.

`./dev check` provides fast iterative feedback. `./dev verify` delegates to `ai-kit verify`, which reads the profile and policy and always reruns every required mechanism. Configure underlying commands such as `./dev lint` and `./dev test`; do not call either verification entry point from a requirement. A recursion guard rejects accidental delegation loops. Local verification and CI use this same policy. AI Kit's own policy also checks dependency-lock consistency, managed-file sync, and doctor.

A zero exit code passes a command requirement. Nonzero fails; unavailable commands (including shell exits 126/127), timeouts, missing implementations, or inconclusive baseline comparisons are UNKNOWN. Any required fail or unknown returns nonzero. Invalid configuration or dirty input state returns nonzero before running checks. A check that leaves changed tracked/unignored files or changes HEAD makes the run UNKNOWN. All requirements still run when another requirement fails, and their individual outcomes remain visible.

The agent decides which tests or proofs establish the requested behavior. AI Kit enforces execution, not test quality or automatic test generation. The generated skill guides risk-based testing and real integration evidence. Product/test changes in the current commit produce an advisory review signal, never an automatic failure.

## Evidence and freshness

Every run writes `.ai-local/evidence/run-*/evidence.json` plus command logs, and updates `.ai-local/evidence/latest.json`. Evidence schema 1 records repository path, exact Git commit, policy and profile SHA-256 digests, required IDs, command/adapter configuration, timeouts, pass/fail/unknown results, exit codes, artifact paths, warnings, and timestamp. Baseline results include measured commands, output, counts, and comparison details. Digests use the `sha256:<hex>` format.

Commit the product, profile, policy, and command configuration first. Verification requires a clean tracked/unignored tree, and `.ai-local/` must be ignored. Evidence itself is never committed or used to mutate tracked product state.

```sh
ai-kit verify          # Rerun all required mechanisms and record evidence.
ai-kit verify --check  # Inspect whether the latest saved passing evidence is current.
```

Freshness requires the same repository path, exact HEAD, identical policy/profile bytes, all required results passing, and a clean tree. Any new commit invalidates old evidence, including metadata-only commits. Ignored local logs do not invalidate it. There are no ancestry scans or moving acceptance files. Merge strategy no longer affects evidence reuse: every resulting commit needs its own verification. CI evidence describes the checkout it actually tested (for PRs, normally GitHub's test merge commit).

Saved evidence is an inspectable local record, not a signed attestation. `--check` does not rerun commands or prove that an external service or ignored runtime input is unchanged. Before claiming completion, run `./dev verify` again and assess the task-specific outcome.

## Existing repositories

`ai-kit adopt --safe` inventories an existing Git repository, distinguishes claims from observations, runs recognized checks in a Landlock-confined temporary copy, and records a baseline and custody boundary. If confinement is unavailable, verification stays UNKNOWN. Failed checks stay failed; unknown checks stay unknown. It does not repair source code. Aggressive repair is deferred.

Adoption seeds `baseline` into the policy. A baseline or custody file requires that capability; removing it from `required` is an error. `ai-kit baseline compare` remains available for inspection. Policy verification reruns the same comparison: measured regressions fail and unresolved comparisons are UNKNOWN. An unchanged known inherited failure may satisfy **no regression**, while the evidence still reports the inherited failure; every new policy requirement must pass. Preserve inherited commands separately when converting an old `./dev verify` into the new delegator. A baseline command that recursively invokes policy verification is rejected.

AI Kit itself is initialized, not adopted: it has no baseline or `CUSTODY.toml`.

## Migrating from the pre-1.0 lifecycle

The old `ai-kit goal` commands and generated goal skills have been removed. Move durable requirements into intent/decisions, general checks into `.ai/verification.toml`, and task-specific acceptance into the harness's native goal/task. Commit meaningful old records before removing them so Git retains the history. Review uncommitted notes first. `sync` never deletes project-owned legacy records and refuses to delete a modified generated skill.

Create the policy before running `ai-kit sync` on an older installation, update the project-owned `./dev verify` to delegate to `ai-kit verify`, then commit and verify. Old verification evidence cannot establish success under the new policy. This repository's prior hardening acceptance and evidence remain in Git history and [PR #1](https://github.com/Thodoris-Evangelakos/ai-kit/pull/1).

## Project files

| Path | Purpose |
| --- | --- |
| `.ai/intent/` | Human-approved requirements and invariants |
| `.ai/decisions/` | Accepted, durable architectural decisions |
| `.ai/profile.toml` | Project modules and generated guidance |
| `.ai/verification.toml` | Project-owned required evidence and mechanisms |
| `.ai/current/` | Curated temporary handoff state |
| `.ai-local/evidence/` | Ignored verification records and command logs |
| `ai-kit.lock` | Generation, profile digest, and managed-file hashes |
| `./dev` | Project-owned development and verification interface |

The next vertical slice is webapp browser/runtime-error verification. Browser, Bend, TLA+, new adapters, and orchestration are not implemented here.
