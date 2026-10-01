# Current findings

- The shipped model still owns a seven-state goal lifecycle; required checks and completion evidence live in goal contracts, not a standalone `.ai/verification.toml`. [Architecture direction](../../docs/architecture-direction.md) selects native harness tasks and project-owned verification for a future model; it does not supersede accepted ADRs or implement that transition.
- Historical DONE evidence also drives `doctor`: dirty product/docs paths make `post-mvp-hardening` stale, so the current worktree passes 65 tests but fails `./dev verify` at doctor. Git ancestry is required as well; squash/rebase can invalidate identical verified content. Preserve the legacy gate until its replacement is exercised.
- The supplied architecture context ends mid-sentence in its capability-layering section. Decisions use the complete sections; no requirements were inferred from the missing ending.
- The inherited positional `init` change silently takes precedence over `-C`/`--path` when both are supplied. Its docs/test/source edits remain uncommitted; the architecture assessment did not modify them.
