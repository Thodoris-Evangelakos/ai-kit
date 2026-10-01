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
