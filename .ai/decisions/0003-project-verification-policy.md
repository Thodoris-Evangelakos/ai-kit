# ADR 0003: Harness-owned work and project-wide verification

Status: Accepted; replaces the earlier commit-bound goal design.

Context: AI Kit's task lifecycle duplicated harness functionality and tied evidence freshness to moving acceptance files and Git ancestry. Useful verification mechanisms must survive without a competing task tracker.

Decision: **The harness owns what work is being pursued. AI Kit owns what evidence the repository requires before that work may be considered verified.** **Verification policy is project-wide. Task-specific acceptance belongs to the harness goal and the intelligent agent's verification design.**

`.ai/verification.toml` schema 1 lists required capability IDs and their deterministic command implementations. Namespaced IDs accommodate future browser, property, fuzz, security, and formal mechanisms without a registry or plugin framework. Unconfigured capabilities are UNKNOWN. Profiles seed visible defaults at initialization; the project owns subsequent policy edits. The command adapter uses argv arrays and bounded execution; the baseline adapter retains confined adoption comparison.

The repository owns `./dev`: `check` provides fast iterative feedback, and `verify` delegates once to `ai-kit verify`. Policy commands invoke underlying checks, never either verification entry point. CI runs `./dev setup` then `./dev verify`, uses read-only repository permissions and pinned actions, and uploads ignored evidence as artifacts. Doctor validates configuration without running verification. Sync validates policy structure and preserves project-owned files.

Every verification reruns required mechanisms, records pass/fail/unknown, and exits nonzero unless all requirements pass. Evidence and logs live under ignored `.ai-local/evidence/`, bound to repository path, exact HEAD, and policy/profile byte digests. Dirty tracked/unignored state is rejected; changes during a run invalidate it. Any new commit needs fresh verification. Evidence is local and inspectable, not a signed attestation; saved freshness checks cannot establish unchanged external runtime state. No ancestry exception, accepted-record archive, or task-state transition is needed.

Consequences: Git retains old acceptance records and decisions. The former hardening acceptance remains in repository history and PR #1; no unresolved task is discarded in this migration. The old lifecycle implementation, commands, skills, and lifecycle tests are removed. Merge commits remain usable, but evidence no longer requires ancestry preservation or any particular merge strategy. The agent designs meaningful tests/proofs; policy enforces that required classes run. Product/test co-changes in HEAD are an advisory review signal. A green command still needs assessment against the user outcome. Browser and formal integrations remain future capabilities.
