# ADR 0005: Harness-assisted setup with deterministic finalization

Status: Accepted

Context: The approved harness-setup plan adds interactive semantic integration
without replacing the existing goal lifecycle. Existing repositories have custom
instructions and verification commands that mechanical recognition cannot fully
interpret.

Decision: Add an explicit `ai-kit setup` preparation, harness handoff, and
finalization flow. Codex, codex_ds, OpenCode, and Claude Code are supported setup
launchers. Save the preference in user-wide XDG configuration; preserve the
user's harness permissions, authentication, and model settings. The harness
interprets repository conventions and configures integration, while AI Kit
validates file preservation, ownership, references, reports, and executed checks.

Capture the actual original worktree, including dirty and untracked user files,
before integration. Keep setup pending until independent validation succeeds;
resume must preserve intervening work. Managed instruction blocks preserve human
bytes outside the block. Newly discovered baseline commands are measured against
the captured original repository; inherited failing evidence cannot be discarded.

Consequences: This extends ADRs 0001 and 0004 for the new setup entry point.
Legacy init and safe adoption retain their existing semantics, and ADRs 0002
and 0003 still govern authority and goal completion. Setup evidence is a local
observation, not a tamper-proof attestation or a replacement for goal acceptance.
Source repair and the broader native-task/standalone-verification migration remain
separate work.
