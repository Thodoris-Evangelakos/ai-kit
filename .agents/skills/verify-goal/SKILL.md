---
name: verify-goal
description: Verify a goal at the current Git commit and apply the completion gate.
---

# Verify a goal

Run `./dev check` during development. Run `./dev verify` before completion if
the contract requires it. `ai-kit goal verify ID` executes every command in
the goal contract and records results at the current commit. Review the
acceptance criteria and actual product behavior. Then run
`ai-kit goal complete ID`; it refuses missing, failing, unknown, or stale evidence. Keep
failure artifacts under ignored `.ai-local/artifacts/`.
