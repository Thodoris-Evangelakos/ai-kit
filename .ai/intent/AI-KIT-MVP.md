# AI Kit MVP intent

AI Kit makes a new, existing, or damaged software repository legible, reproducible, and continuously verifiable to a fresh coding agent. Months of work must not depend on chat history or optimistic test results.

## Durable rules

- Preserve user work. Do not silently overwrite human-edited managed files, discard source changes, rewrite accepted intent or decisions, or weaken failing tests to create a green baseline.
- Keep approved requirements in `.ai/intent/`, accepted architectural decisions in `.ai/decisions/`, and short-lived agent handoff state in `.ai/current/`. Delete stale working memory; Git holds history.
- Keep root `AGENTS.md` small. Skills describe reusable workflows; tools and project commands perform deterministic operations; tests are evidence; policy specifies required evidence.
- `./dev check` gives fast feedback. `./dev verify` supports a completion claim. Failure, unknown, and silent errors return nonzero.
- A goal is an acceptance contract prepared from intent before implementation. DONE requires every required check to pass against the current Git commit. Old-commit, absent, failing, and unknown evidence cannot complete a goal.
- Web acceptance must cover real user behavior and relevant frontend/backend/runtime failures when the `webapp` module applies. Tests alone are not the user outcome.
- Safe adoption distinguishes CLAIMED, OBSERVED, INFERRED, and UNKNOWN evidence and records existing failures honestly, without broad repair.

## MVP outcomes

1. A new Git repository can initialize a valid profile, minimal Codex router, current-state files, `ai-kit.lock`, ignored local artifacts, and a stable `./dev` interface. Repeating init and sync is safe.
2. Manual edits to a managed file are detected by `sync --check`; ordinary sync preserves them and reports a resolution path.
3. A goal cannot reach DONE without passing current-commit verification. Product or contract changes invalidate old evidence.
4. An existing repository can be inventoried and baselined without erasing failing evidence or modifying its source files.
5. A fresh agent can find project purpose, authority, current status, goal contracts, and verification commands without a large instruction document.

The first implementation supports Linux, Python, and Codex. Other agent adapters, orchestration, technology radar, and a web dashboard are deferred.
