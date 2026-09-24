# Project agent guide

This repository is managed by AI Kit.

Read `.ai/profile.toml` before substantial work. For the current task, read
`.ai/current/STATUS.md` and relevant `.ai/current/FINDINGS.md` entries.

Requirements in `.ai/intent/` are authoritative. Accepted architectural
decisions live in `.ai/decisions/`. Goal acceptance contracts live in
`.ai/goals/`. Do not promote assumptions into requirements or decisions.

Use relevant skills in `.agents/skills/`. Before declaring a goal complete,
satisfy its acceptance contract and required verification policy. Use
`./dev check` for fast feedback and `./dev verify` for completion evidence.

Record only high-signal, current discoveries in `.ai/current/FINDINGS.md`;
delete stale entries. Git preserves history.
