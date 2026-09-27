# ADR 0004: Conservative adoption and explicit file ownership

Status: Accepted

Context: Existing repositories may contain valuable human work, misleading claims, and failing checks. Installing agent support must not erase that evidence.

Decision: Safe adoption inventories an existing Git repository, labels evidence CLAIMED, OBSERVED, INFERRED, or UNKNOWN, and measures recognized checks in a Landlock-confined temporary copy. Unavailable confinement leaves verification UNKNOWN. Record the baseline and adoption custody boundary without broad source repair; compare inherited failures again at goal completion.

Managed files are rendered deterministically from `.ai/profile.toml`. `ai-kit.lock` records their hashes. Sync refuses manual drift or conflicting unmanaged files and reports a resolution path. Intent, decisions, current state, and the project's `dev` remain human-owned.

Consequences: Failed checks stay failed and unknown checks stay unknown; regressions or unknown baseline comparisons block completion. Aggressive adoption remains deferred. An initialized repository has no adoption baseline or `CUSTODY.toml`; AI Kit itself uses ordinary initialization and sync without a self-hosting exception.
