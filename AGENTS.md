# Project agent guide

This repository is managed by AI Kit.

Read `.ai/profile.toml` before substantial work. For the current task, read
`.ai/current/STATUS.md` and relevant `.ai/current/FINDINGS.md` entries.

Requirements in `.ai/intent/` are authoritative. Accepted architectural
decisions live in `.ai/decisions/`. Project verification policy lives in
`.ai/verification.toml`. Do not promote assumptions into requirements or decisions.

Use the harness's native goal/task mechanisms and relevant `.agents/skills/`.
Before claiming completion, inspect the verification policy, ensure meaningful
tests/proofs cover the change, and run `./dev verify`. Required evidence must
pass at the current commit; failing, unknown, or stale evidence is not success.
Use `./dev check` for fast feedback.

Record only high-signal, current discoveries in `.ai/current/FINDINGS.md`;
delete stale entries. Git preserves history.
