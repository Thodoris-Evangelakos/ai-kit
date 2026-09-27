---
name: verify-project
description: Design meaningful checks and run the repository verification policy.
---

# Verify project work

Use the harness's goal/task for the requested outcome and task-specific
acceptance. Inspect `.ai/verification.toml` and the affected system before
choosing tests or proofs. The agent designs the verification; AI Kit enforces
that the project-required mechanisms actually run.

Permanent tests should protect accepted user behavior, system invariants,
important interfaces, meaningful failure modes, security properties,
production regressions, recurring defects, or expensive-to-discover breakage.
Do not add tests merely because a line changed, a branch exists, coverage can
increase, or a dependency can be mocked. Prefer real behavior and integration
evidence to implementation-detail assertions or mock-only confidence.

Run `./dev check` while iterating. Commit the product and policy, then run
`./dev verify` before claiming completion. It delegates to `ai-kit verify`,
which always reruns required mechanisms, records logs and commit/policy-bound
evidence in ignored `.ai-local/evidence/`, and rejects fail/unknown results.
Policy commands must invoke underlying checks, never `./dev verify` itself.
Review results against the requested outcome; a green command cannot prove an
untested outcome. Product/test co-changes are a review signal, not a failure.
Any new commit or dirty tracked/unignored work requires fresh verification.
