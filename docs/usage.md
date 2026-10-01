# Usage

A concise guide to installing AI Kit, initializing a project, running the
project workflow, verifying goals, and adopting an existing repository.

## Install and initialize

```sh
uv tool install /path/to/ai-kit
ai-kit --version
```

Initialize from inside the target repository:

```sh
cd /path/to/repo
ai-kit init
```

The bare form targets the current working directory. The target must be a Git
repository root.

## CWD forms

All three forms below target the current working directory and behave
identically:

```sh
ai-kit init        # bare form, defaults to the current directory
ai-kit init .      # explicit current directory
ai-kit init -C .   # option form, still supported
```

The positional form also accepts an explicit repository root. `--path` / `-C`
remain compatible for targeting a repository without changing directories:

```sh
ai-kit init /path/to/repo
ai-kit init --path /path/to/repo
ai-kit init -C /path/to/repo
```

Whatever form you use, the resolved target must be a Git repository root;
pointing at a subdirectory or a non-repository fails.

## Project setup

`init` writes a small `AGENTS.md`, `.ai/profile.toml`, the `.ai/` working
state, two Codex skills, `ai-kit.lock`, an ignored `.ai-local/` area, and a
`./dev` starter. Replace the starter's failing `setup`, `check`, and `verify`
commands with real commands for the project before claiming completion;
`./dev` is project-owned and never regenerated.

```sh
ai-kit init --module learning            # optional profile modules
ai-kit doctor                            # deterministic health checks
ai-kit sync                              # regenerate managed files
ai-kit sync --check                      # report drift; nonzero on drift
```

## Terminal menu

Running `ai-kit` with no arguments at a Git repository root in an interactive terminal opens a
keyboard-driven menu; in a pipe or CI job the same command prints help instead
of waiting for input. Open it explicitly, or target another repository, with:

```sh
ai-kit menu
ai-kit menu -C /path/to/repo
```

The main screen lists the repository, its enabled modules, preferred setup harness, and these actions:
Profile modules, Goal status, Doctor, Sync managed files, Run `./dev check`, Run
`./dev verify`, Setup harness, Set up / resume setup, and Exit. Move with the arrow keys or `j`/`k`, press Enter to
choose, and press `q`, Esc, Ctrl-C, or Ctrl-D to leave.

Profile modules shows every supported module with a checkbox. Space toggles the
highlighted module, Enter applies the selection, and Esc or `q` cancels without
changing anything. Modules that are currently declarative only are labelled,
and the footer explains the effect of `strict-verification` (every goal
contract needs a `verify` check) and `webapp` (every goal contract needs
`acceptance` and `runtime_errors` checks). Applying rewrites only the `modules`
array, keeps comments and other formatting, regenerates managed files and
`ai-kit.lock` through the usual safety checks, and treats an unchanged
selection as a no-op. Manual drift, symlinks, or unmanaged files block the
change before the profile is touched.

Unfamiliar formatting, including comments inside the modules array, is left
untouched with guidance for editing it manually. If a write fails during sync,
saved modules and any partial updates remain visible; inspect `ai-kit doctor`
and `ai-kit sync --check` before retrying.

Every action restores the terminal first, runs the real command, prints its
output and true exit status, and waits for Enter before returning to the menu;
nonzero results are reported as failures. The menu needs a real TTY and a
capable `TERM`; otherwise it fails with a clear message and never hangs, and a
window that is too small shows a resize prompt instead of a broken screen.

## Setup with your preferred harness

Choose **Setup harness** in the menu to select Codex, `codex_ds`, OpenCode, or
Claude Code. The selector shows executable availability; Enter saves the
highlighted choice and Esc cancels. Saving never launches a session. Preferences
are user-wide in `$XDG_CONFIG_HOME/ai-kit/config.toml`, with the usual
`~/.config/ai-kit/config.toml` fallback.

Choose **Set up / resume setup** to prepare the repository and open that harness
interactively. Equivalent CLI commands are:

```sh
ai-kit harness                  # show preference and available executables
ai-kit harness codex_ds          # save a preference
ai-kit setup -C /path/to/repo
ai-kit setup --harness claude    # one-run override, not a saved preference
ai-kit setup --no-launch         # prepare a pending manual handoff
ai-kit setup --finalize          # validate without launching
```

Install and authenticate your preferred harness normally. AI Kit inherits its
configuration and never supplies permission-bypass flags. `codex_ds` must be an
installed Codex-compatible wrapper; model/provider selection stays in that wrapper.
Claude Code uses the `claude` executable. An unavailable or incompatible CLI fails
with actionable guidance.

Setup works in new repositories, existing unmanaged repositories, and already
managed projects. It snapshots original user files before integration and records
a pending operation. Existing human instructions and custom context survive;
manual drift inside AI Kit blocks requires resolution. The harness reads the
printed shared protocol, reuses real project commands, and writes the documented
structured report. Claims in documentation do not become approved requirements.

The parent process validates after the session exits; you can also run
`--finalize` from the session or after a manual handoff. Missing reports, unresolved
questions, failures, unavailable or skipped checks, and protected source changes
leave setup pending. Keep `.ai-local/setup/` until setup finishes: it contains the
original snapshot needed for baseline measurement and preservation checks. Resume
does not replace intervening work with that snapshot.

`--no-launch` is the scripted preparation path and returns after recording pending
integration. Its success means preparation succeeded. Finalization is a separate
gate. No live model invocation is required by AI Kit's launcher tests: executable
stubs exercise the four command interfaces; a missing local Claude installation
does not establish a successful real Claude setup run.

## Goal verification

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

Verification runs the required commands and records their exit status at the
current Git commit. `complete` refuses absent, failed, unknown, or stale
evidence. A later product commit makes a DONE goal's evidence stale; run
`ai-kit goal reopen ID` to move it back for fresh verification.

## Safe adoption

```sh
ai-kit adopt --safe       # inventory an existing repo and record a baseline
ai-kit baseline compare   # rerun the inherited check, report regressions
```

Safe adoption invents nothing: failed checks stay failed and unknown checks
stay unknown, and it does not repair source code. If confinement is
unavailable, verification stays UNKNOWN. Aggressive repair is deferred.
