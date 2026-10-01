# Current status

Goal: terminal-menu
State: DONE
Completed: keyboard menu, profile module selection with guarded saves, common actions, documentation, and independent review. ./dev check and ./dev verify pass with 81 tests. Real PTY checks cover saved selections, cancellation, action failures, all five common actions, small terminals, and terminal restoration; smoke transcript is in ignored .ai-local/artifacts/terminal-menu-smoke.txt.
Remaining: none for terminal-menu. Both terminal-menu and post-mvp-hardening are accepted with passing evidence at bf4c419. Future architecture work remains described in docs/architecture-direction.md; accepted ADRs 0001–0004 still govern the shipped model.
Blocked: no
