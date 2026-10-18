# Current status

Goal: none (no new goal contract).
Current change: automatic agent-assisted adoption (uncommitted).
State: IMPLEMENTED
Completed: plain `adopt` reuses harness-assisted setup, prompts for and saves an installed harness when needed, and requires confirmation before a deterministic `adopt --safe` fallback. Explicit safe adoption is unchanged; cancellation, noninteractive invocation, and recursive launch refuse before repository writes. Preference persistence distinguishes an explicit Codex choice from its implicit default. Documentation and ADR 0005 reflect the approved behavior.
Verification: all 226 tests, formatting, lint, lockfile, and managed-file checks pass. Independent review found no actionable issues. Full `./dev verify` cannot pass its doctor gate while accepted goal evidence is stale for these product/policy changes.
Remaining: commit-bound reverification of harness-setup, optmem, terminal-menu, and post-mvp-hardening before goal completion. Real model sessions were not launched; terminal integration uses executable stubs for all four harnesses. OptMem remains disabled.
Historical evidence: the four accepted goals passed at da7bb3f; those records are retained as history, not current completion evidence. Future lifecycle work remains described in docs/architecture-direction.md.
Blocked: current completion evidence is stale; implementation tests are available.
