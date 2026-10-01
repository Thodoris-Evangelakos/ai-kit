# AI Kit

AI Kit makes a repository legible, reproducible, and verifiable to a fresh coding agent. It keeps approved intent, accepted decisions, temporary working state, and goal acceptance evidence in separate places.

The MVP is Linux first, Python 3.11+, and Codex focused. It uses Git for history and project-owned commands for verification. It has no daemon, web UI, or service dependency.

See [docs/usage.md](docs/usage.md) for a concise guide to installing, initializing, setting up a project, verifying goals, and adopting an existing repository.

## Install and develop

```sh
uv tool install /path/to/ai-kit
# From this checkout:
./dev setup
./dev verify
```

An editable global development install is also possible with `uv tool install --editable .`.

## Start a project

Run these commands at a Git repository root:

```sh
ai-kit init        # targets the current working directory
ai-kit init .      # identical: explicit current directory
ai-kit init -C .   # identical: option form, still supported
ai-kit doctor
ai-kit sync --check
```

`init` targets the current working directory by default; `--path` / `-C` remain compatible for targeting another path. The resolved target must be a Git repository root. It creates a small `AGENTS.md`, `.ai/profile.toml`, current-state files, two Codex skills, `ai-kit.lock`, an ignored `.ai-local/` area, and a `./dev` starter. Replace the starter's failing `setup`, `check`, and `verify` commands with real commands for that project before claiming completion. The `dev` file is project-owned; AI Kit does not regenerate it.

Use `--module learning`, `--module webapp`, or `--module professional-repository` to enable optional behavior. The professional module renders a GitHub Actions workflow that calls `./dev setup` then `./dev verify`. Full verification includes `./dev check`, which remains the fast local feedback command. The webapp module provides a focused browser-verification skill; the project must provide its actual Playwright acceptance command.

`sync` regenerates managed files from the profile. It checks hashes in `ai-kit.lock` and refuses to overwrite manual edits. `sync --check` and `doctor` return nonzero for drift or invalid state. Profile comments and formatting are left alone.

## Goals

```sh
ai-kit goal new example --title "Example outcome" --intent "A user can do X" \
  --accept "X persists after reload" --check verify="./dev verify"
git add .ai/goals && git commit -m "Define example goal"
ai-kit goal state example ACTIVE
# Implement the outcome, then:
ai-kit goal state example IMPLEMENTED
ai-kit goal verify example
ai-kit goal complete example
```

The contract must be committed before verification. Verification runs the required commands and records their exit status at the current Git commit. `complete` refuses absent, failed, unknown, or stale evidence and reruns the checks before DONE. A later product commit makes a DONE goal's evidence stale; `ai-kit goal reopen ID` moves it back for fresh verification. Acceptance criteria are written before implementation; passing a command alone does not prove an untested user outcome.

## Existing repositories

`ai-kit adopt --safe` inventories an existing Git repository, distinguishes claims from observations, runs recognized checks in a Landlock-confined temporary copy, and records a baseline and custody boundary. If confinement is unavailable, verification stays UNKNOWN. Failed checks stay failed; unknown checks stay unknown. It does not repair source code. Aggressive repair is deferred. `ai-kit baseline compare` reruns the inherited check and reports measured regressions; goal completion also refuses regressions or unknown comparisons.

## Project files

| Path | Purpose |
| --- | --- |
| `.ai/intent/` | Human-approved requirements and invariants |
| `.ai/decisions/` | Accepted, durable architectural decisions |
| `.ai/goals/` | Goal contracts and verification evidence |
| `.ai/current/` | Curated temporary handoff state |
| `.ai-local/` | Ignored learning and failure artifacts |
| `ai-kit.lock` | AI Kit generation, profile digest, and managed file hashes |
| `./dev` | Repository-owned fast check and completion verification |

AI Kit's own source uses `./dev check` and `./dev verify`; CI calls the same interface.
