# Current findings

- Evidence now binds to exact HEAD and policy/profile digests; all new commits require a rerun. Ignored local evidence needs no Git commit and no ancestry-preserving merge rule.
- Adopted repositories must keep inherited baseline checks separate from the new `./dev verify` delegator. Recursive policy verification is rejected; unchanged inherited failures remain visible in comparison evidence.
