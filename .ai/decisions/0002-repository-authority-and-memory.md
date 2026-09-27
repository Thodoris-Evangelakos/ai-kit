# ADR 0002: Separate authority from working memory

Status: Accepted

Context: A fresh agent must distinguish approved project truth from implementation choices and temporary handoff notes.

Decision: Keep requirements in `.ai/intent/`, accepted architecture in `.ai/decisions/`, project verification policy in `.ai/verification.toml`, local evidence in ignored `.ai-local/evidence/`, and concise current state in `.ai/current/`. Root `AGENTS.md` is a small router to these sources. Task-specific acceptance belongs to the harness (ADR 0003). Skills describe reusable workflows; tools perform deterministic operations; policy selects evidence and tests supply it.

Consequences: Remove stale current-state entries rather than maintaining a permanent agent diary. Git provides history. Local learning notes and failure artifacts belong in ignored `.ai-local/`. Human approval remains the authority for requirements and decisions; generation does not promote assumptions into either.
