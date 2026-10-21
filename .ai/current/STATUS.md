# Current status

Goal: none (no new goal contract).
Current change: Windows support on top of automatic agent-assisted adoption.
State: IMPLEMENTED
Completed: native Windows curses menu, Python dev starter and dev.cmd launchers, Windows batch harness launching, platform shell goal checks, CRLF-tolerant managed hashes with byte-preserving writes, junction refusal, and Windows/Linux CI. Existing project commands and XDG configuration paths are preserved. ADR 0006 and usage documentation record the platform boundary. Automatic adoption behavior from ADR 0005 remains implemented.
Verification: Windows suite passes (178 passed, 59 platform/privilege skips); the final batch-command change also passes its focused rerun (59 passed, 17 skipped). Formatting, lint, lockfile, and managed-file checks pass. The real Windows console menu opened without TERM, entered and cancelled its profile selector, and exited successfully. Full verification exits nonzero only at doctor's existing stale DONE-evidence gate.
Remaining: commit-bound reverification of harness-setup, optmem, terminal-menu, and post-mvp-hardening before goal completion. Confined adoption checks and setup finalization require Linux Landlock; Windows records UNKNOWN and leaves setup pending. Linux execution and real model sessions were not exercised in this change. OptMem remains disabled.
Historical evidence: the four accepted goals passed at da7bb3f; those records are retained as history, not current completion evidence. Future lifecycle work remains described in docs/architecture-direction.md.
Blocked: current completion evidence is stale; implementation tests are available.
