---
name: manage-goal
description: Create or update an explicit goal acceptance contract before implementation.
---

# Manage a goal

Use `ai-kit goal new ID --title TITLE --intent INTENT --accept CRITERION` and
add required `--check NAME=COMMAND` entries. Start from approved intent, not
from the implementation. Keep criteria observable and independent of code
structure. Commit the contract. Move READY → ACTIVE → IMPLEMENTED with
`ai-kit goal state ID STATE`. Do not change accepted intent or decisions
silently. `ai-kit goal show ID` displays the contract.
