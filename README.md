# AI Kit

AI Kit makes a repository legible, reproducible, and verifiable to a fresh coding agent. It keeps approved intent, accepted decisions, temporary working state, and goal acceptance evidence in separate places.

The MVP is Linux first and Python 3.11+. It uses Git for history and project-owned commands for verification. Setup can open Codex, `codex_ds`, OpenCode, or Claude Code with your existing harness configuration. It has no daemon, web UI, or service dependency.

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

## Terminal menu

Running `ai-kit` with no arguments at a Git repository root inside an interactive terminal opens a keyboard-driven menu. It only starts when stdin and stdout are real terminals; a pipe, redirect, or CI job gets the help text instead of a blocked process. `ai-kit menu` opens it explicitly, and `-C`/`--path` targets another repository: `ai-kit menu -C /path/to/repo`.

The main screen shows the repository, enabled modules, and preferred setup harness. It offers Profile modules, Goal status, Doctor, Sync managed files, Run `./dev check`, Run `./dev verify`, Setup harness, Set up / resume setup, and Exit. Move with the arrow keys (or `j`/`k`), select with Enter, and leave with `q`, Esc, Ctrl-C, or Ctrl-D.

Profile modules opens a checklist of every supported module. Space toggles the highlighted module, Enter applies the selection, and Esc or `q` cancels without changing anything. Modules whose behavior is still declarative are labelled so a choice is not misleading, and the footer explains the current effect of `strict-verification` (a `verify` check in every goal contract) and `webapp` (acceptance and runtime-error checks). Applying re-renders managed files and the lock through the same safety checks as `sync`: comments and non-module formatting are preserved, selecting the current set is a no-op, and manual drift, symlinks, or unmanaged collisions are refused before anything is written.

Each action restores the terminal, runs the real command, prints its output and true exit status, and waits for Enter before returning to the menu. A failed command is shown as failed rather than passed. On a terminal that is too small, or when `TERM` is `dumb` or unset, the menu shows a clear message instead of a broken screen.

## Harness-assisted setup

```sh
ai-kit harness              # show your preference and executable availability
ai-kit harness opencode     # save a user-wide preference
ai-kit setup                # prepare or resume setup, then open the harness
ai-kit setup --harness claude  # override the harness for this run
ai-kit setup --no-launch    # prepare a manual handoff without opening a session
ai-kit setup --finalize     # independently validate the integration
```

The menu's Setup harness selector saves the same preference in `$XDG_CONFIG_HOME/ai-kit/config.toml` (default `~/.config/ai-kit/config.toml`). Enter saves; Esc cancels. Saving a choice never launches it. Unavailable executables are labelled and may be selected for installation later.

`setup` captures the original repository before preparing a pending integration. The harness follows one shared protocol to inspect existing instructions, documentation, CI, and tests; configure useful project commands; and report unresolved questions. Application source, tests, approved intent, and accepted decisions remain protected. Existing instructions and custom `.ai` documents are preserved; instruction updates use hash-managed blocks.

The harness uses your normal interactive permissions, model, and authentication settings. Install the selected CLI yourself; AI Kit does not install harnesses or change their approval settings. Outside an interactive terminal, use `--no-launch` and ask your harness to follow the printed protocol. Interruptions, missing evidence, changed protected files, and failed or unknown checks leave setup pending. Rerun `setup` to resume. A successful harness exit alone cannot finalize adoption.

Finalization checks preserved inputs, instruction ownership, the structured report, project checks, and inherited baselines. Newly discovered check commands are measured against the captured original repository. Doctor reports pending integration separately from structural health. The existing `init` and `adopt --safe` commands retain their deterministic behavior.

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
