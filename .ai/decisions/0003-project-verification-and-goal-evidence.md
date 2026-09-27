# ADR 0003: Project-owned verification and commit-bound goals

Status: Accepted

Context: Completion must be reproducible locally and remotely, with evidence tied to the product and acceptance contract that were checked.

Decision: The repository owns `./dev`. `./dev check` provides fast iterative feedback; `./dev verify` performs full completion verification, including `check`. CI runs `./dev setup` then `./dev verify`, with read-only repository permissions and external actions pinned to upstream-confirmed commit SHAs with release comments. AI Kit creates only a failing starter for an unconfigured `dev` and never regenerates that project-owned file.

Commit the goal contract before implementation. Verification requires committed product changes, runs every required command, and binds results to HEAD and a contract digest. Completion rejects missing, failing, unknown, or stale evidence and reruns the required checks. Failures and unknown results return nonzero. A command pass still requires review against the stated user outcome.

Consequences: Goal acceptance and current-state bookkeeping may be committed afterward without invalidating evidence when product and policy are unchanged and the verified commit remains an ancestor. CI needs full Git history to evaluate this. Use merge commits for this workflow; squash/rebase merges can discard the evidence commit and require reverification. Later product or policy changes invalidate DONE evidence and require reopening the goal. The webapp profile requires acceptance and runtime-error checks, but the actual browser harness remains a future vertical slice.
