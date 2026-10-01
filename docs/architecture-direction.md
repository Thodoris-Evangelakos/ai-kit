# AI Kit architecture direction

Date: 2026-10-01. Inspected implementation: `b335631` plus the existing
uncommitted init/usage changes.

This is an architectural assessment and a chosen direction for the next project
model, not a claim that the model is implemented. The user's context authorizes
architectural judgment, but is explicitly not a feature specification. Existing
accepted intent and ADRs remain the authority for the shipped model until a
recorded transition supersedes them. The supplied context ends mid-sentence in
the agent capability section; no requirements are inferred from that ending.

## Decision

AI Kit should be a small deterministic repository layer with agent protocols for
semantic work. The harness owns the live task, plan, goal, session, and worker
lifecycle. AI Kit owns the discovery of durable project context, managed
integration, preservation boundaries, project verification policy, and evidence
validation. It should neither become a task orchestrator nor pretend that Python
can infer approved project meaning from filenames and keyword matches.

The minimum durable contract is authority, ownership, provenance, and required
evidence. The directory layout is a default, not the contract.

## What is actually here

| Area | Current implementation | Implication |
| --- | --- | --- |
| Initialization and rendering | `project.py:init_project`, `render_codex`, `sync_project` create and hash a fixed Codex router, skills, optional CI, and a project-owned failing `dev` starter. | Retain the deterministic renderer and conflict protection. Initialization is structural, not a configured or verified project. |
| Task ownership | `goals.py` stores contracts, seven lifecycle states, transitions, completion, and accepted history; CLI, router, and both default skills depend on it. | The native-harness direction has not been implemented. Removing only the CLI commands would remove verification guarantees without replacing them. |
| Verification | `goals.py:_require_profile_checks`, `verify_goal`, `complete_goal`, `is_evidence_fresh` enforce named checks and commit/contract evidence inside goals. `verification.py` only classifies failure names. | There is no standalone project policy or project verification runner. `.ai/verification.toml` does not exist. |
| Adoption | `adoption.py:_inventory`, `_command`, `adopt_safe`, `recheck_baseline` observe a few known files and checks, preserve a baseline, and run in a temporary copy with Landlock write confinement. CLI then calls `init_project(adopted=True)`. | Valuable preservation machinery exists, but adoption is mechanical and narrowly recognized. It does not understand existing requirements or integrate custom conventions semantically. |
| Ownership | `project.py:_sync_plan` protects entire generated files. `preflight_adoption` rejects existing router/managed-path conflicts. `init_project` rejects an unmanaged pre-existing `.ai`. | Safe refusals are preferable to data loss, but common brownfield repositories cannot yet be integrated naturally. |
| Project evolution | Profile, lock, adapter, and adoption schemas are version 1. Unsupported schemas are rejected; `sync` applies installed templates without a project-model version gate. | Package version metadata is not a project-model migration mechanism. No project-model field or upgrade protocol exists; the transition must add both. |
| Agent discovery | The short router points to intent, ADRs, current state, and local skills. Optional webapp guidance describes browser verification. | Keep progressive disclosure. A skill describes a workflow; its presence is not evidence that the workflow happened. |

Accepted ADRs 0002 and 0003 explicitly require AI Kit-owned goals. ADR 0004
describes conservative mechanical adoption. They must be superseded explicitly
during implementation, preserving their safety outcomes. Intent item 2 still
requires discoverable acceptance contracts: native session storage alone cannot
satisfy that for a fresh session. The handoff rule below preserves that outcome
without another task state machine; do not remove the discoverability requirement.

## Alternatives considered

1. **Extend the existing goal framework and Python adoption heuristics.** This
   reuses the most code, but duplicates harness state and turns semantic
   interpretation into increasingly fragile recognition rules. Reject it.
2. **Make AI Kit only a collection of agent instructions.** This is flexible,
   but loses objective drift detection, preservation checks, evidence freshness,
   and local/CI parity. Reject it.
3. **Keep deterministic invariants and make semantic operations agent-mediated.**
   This preserves the useful machinery while allowing projects to retain their
   actual conventions. Choose this, with an explicit validation boundary rather
   than trusting an agent's final message.

## Who performs each operation

| Operation | Deterministic code | Agent judgment |
| --- | --- | --- |
| `init` | Validate root and ownership; install minimal defaults and managed integration. | Later configure meaningful project commands and capture actual user intent. Do not invent intent at initialization. |
| `sync` | Reconcile generated content within the same project model; preserve custom files and detect conflicts. | Resolve semantic conflicts through a disclosed protocol; ordinary sync does not interpret or migrate meaning. |
| `doctor` | Validate schemas, paths, ownership, links, pending operations, and evidence applicability. | Explain findings and propose repairs. A structural OK is not behavioral verification. |
| `adopt` | Observe facts, preserve source/baseline, bootstrap the protocol, validate the resulting integration. | Interpret existing documentation and workflows, propose authority mappings, configure useful verification, and preserve unresolved claims. |
| `upgrade` | Identify old/new project models; run explicit mechanical migrations; validate invariants and finalize the version. | Resolve changes to meaning, custom workflows, and context mappings that a migration cannot prove mechanically. |
| Verification | Execute declared checks, collect outcomes, compare baselines, and bind evidence to the checked inputs. | Select tests that prove the requested behavior and review whether evidence covers the user outcome. |
| Durable context maintenance | Check references and ownership; expose relevant instructions. | Decide what is durable, what is stale, and what needs user approval under the project's actual delegation rules. |

Do not give deterministic code a semantic classifier for approved intent, stale
TODOs, architectural truth, or meaningful test coverage. Conversely, an agent
report is not a substitute for exit codes, file hashes, or preserved baseline
observations.

## Flexible context with explicit authority

Keep `.ai/intent/`, `.ai/decisions/`, and `.ai/current/` as new-project defaults.
Allow the project profile to point to existing repository-local sources instead
of moving or duplicating them. Start with a small optional context index in the
profile: path, purpose, authority, and provenance reference. Built-in locations
remain implicit defaults. Resolve paths within the repository; reject path
escapes and unsafe symlink writes. References may identify documents or
directories, with an explicit rule for which entries a directory includes.

Authority must be stated from actual approval or delegated decision power, not
derived from a file's path, model strength, or generation status. A profile
mapping identifies a source; it does not confer approval on its contents.
The authority field records a provenance-backed classification for readers,
not a permission grant. Validation checks that the cited source exists and the
classification is explicit; semantic review establishes whether that source
actually contains approval. Unsubstantiated mappings stay advisory/proposed.
Conflicting authoritative sources are surfaced for resolution, not selected by
an arbitrary path ordering.

Additional directories and project skills are allowed and preserved. A custom
index entry can disclose project-specific worker instructions without turning
them into requirements or application ADRs. For example, a persistent
implementation-choice document can record its author, source task or approved
constraints, scope, and a review trigger. Workers may follow it within that
scope, but it remains advisory unless the user has delegated that kind of
decision. A code or policy change invalidating its assumptions requires review;
AI Kit must not infer that every advisory document is stale after every commit.

Do not require every document to adopt a universal metadata format. Use index
metadata and ordinary Markdown links first. Unknown custom paths survive sync
and upgrades. Project-owned skills remain project-owned; generated skills have
distinct names and known ownership. No plugin registry or generic ontology is
needed for the first implementation.

The always-loaded router should contain only the authority/discovery rules,
verification entry point, and pointers to normal-work and pending-lifecycle
protocols. One normal-work skill should cover the ordinary task flow: discover
relevant context and run cheap structural/version checks; record explicit new
user requirements with their provenance; record architectural choices within
actual delegated authority or keep them as proposals; retain useful current
discoveries and prune stale handoff notes;
then map acceptance to evidence before native task completion. Invoke it for
substantial work, rather than requiring the user to request each bookkeeping
step. Topic details live in focused references. Greenfield setup uses this same
flow to configure the starter from real project needs after `init`.
Codex supports project instruction discovery and progressively loaded repo
skills; that supports this design, but does not guarantee agent compliance.
See the official [instruction discovery documentation](https://learn.chatgpt.com/docs/agent-configuration/agents-md)
and [skills documentation](https://learn.chatgpt.com/docs/build-skills).

## Project verification without a second goal system

Introduce project-owned `.ai/verification.toml`: a schema version and required
named checks with concrete commands and their evidence obligations. Keep
`./dev check` for fast iteration and `./dev verify` for full verification,
including fast checks. Profiles supply initial policy defaults and validation
requirements; the repository supplies working commands. CI uses the same policy.
Unconfigured starters, missing checks, unavailable dependencies, or missing
required artifacts produce FAIL or UNKNOWN, never a verification pass.

Use a standalone `ai-kit verify` runner. It executes policy checks and validates
evidence without creating a task ID, lifecycle state, or completion registry.
Avoid recursion: `./dev verify` runs the project's checks; `ai-kit verify` may
call it, but `./dev verify` must not call `ai-kit verify` back. Expose a separate
read-only applicability check for CI/doctor rather than a recursive runner.

Record each run under ignored `.ai-local/`, including the verified commit and
product content digest, policy digest, exact commands, tool/environment facts
needed for reproducibility, per-check pass/fail/unknown, exit codes, artifact
references, and baseline identity/comparison. Reuse existing hashing, Git,
atomic-write, and baseline functions rather than adding an evidence service.
Local reports are observations, not tamper-proof attestations; an acceptance
gate reruns required commands or relies on a trusted CI run for the same inputs.

The deterministic enforcement point is the verifier's result and exit status:
it cannot produce a current PASS when declared required evidence is absent,
failing, unknown, or stale. Task-scoped runs also capture the native acceptance
snapshot and its criterion-to-evidence mapping; uncovered declared criteria
yield UNKNOWN. These are verification inputs, not AI Kit task states. A trusted
harness integration can use that exit status as a completion gate when its
capabilities support one. The first instruction-based adapter cannot intercept
every native completion action: its completion skill must consume the result,
and AI Kit must disclose that compliance limit. Even today's goal command can
be bypassed by an agent that never invokes it. Do not claim an unbreakable veto
or confuse a harness DONE label with repository verification.

Final accepted evidence initially requires committed inputs. Development runs
may check dirty worktrees, but must identify the exact checked snapshot and must
not authorize a different committed result. Detect source, policy, or command
changes during verification. Default product-identity exclusions are evidence
outputs, temporary `.ai/current/` handoff text, and archived legacy `.ai/goals/`
history after its live obligations have been migrated. Acceptance inputs receive
their own digest even when referenced from current-state files. `.ai-local/`
evidence/caches are excluded; any ignored file that affects a check must instead
be explicitly captured as an input. Do not exclude intent, decisions, policy,
or arbitrary other `.ai` paths.
Bind meaningful context, tests, scripts, configuration, and lockfiles to the
verified product. Equal content can remain applicable after a squash or rebase;
ancestry alone should not decide whether checks still cover the product.

Keep policy weakening visible in review and subject to the project's approval
rules. Deterministic validation can compare changed obligations; it cannot prove
that a user approved a prose document or that a shell command proves a feature.

The native task retains its acceptance criteria. Before a handoff, preserve
unfinished agreed criteria, their provenance, and relevant proof references in
`.ai/current/STATUS.md` or a linked project-owned document. Use a discoverable
native task reference only when a fresh agent actually has access to it; provide
a repository handoff otherwise. Durable accepted behavior belongs in intent and
tests. This is an acceptance snapshot, not duplicated READY/ACTIVE/DONE state.
The completion skill maps each criterion to durable requirements, executable
assertions, or concrete reviewed evidence, runs project policy, and reports
remaining gaps to the harness. A task with no meaningful proof remains
unverified even if all configured commands
pass. This preserves agreement before implementation without another AI Kit
state machine. Historical `.ai/goals/` records stay readable and are never
silently deleted or used as current verification policy.

### Real browser acceptance

The first webapp slice should exercise an actual UI and backend: submit a
mutation, assert its HTTP result, inspect persisted backend state, reload, and
assert the user-visible result. Collect unexpected page/console errors, failed
requests, mutation 4xx/5xx responses, and backend process failures. Reviewed,
narrow allowlists handle expected noise. Missing browser/backend execution is
UNKNOWN; skipped assertions cannot be PASS. Keep traces and logs for failures.
Require a machine-readable execution report for declared acceptance obligations,
including which scenarios/assertions ran and which were skipped. Use established
test-report formats when available. If only an exit code or unrecognized console
text is available, do not claim those obligations executed. The current adoption
runner's limited skip-output regexes are baseline observations, not this future
acceptance gate.

Prove the harness with fault injection: wrong payload, rejected mutation hidden
by the UI, runtime error, and failure to persist must each fail acceptance.
Named checks alone cannot prove this. Browser/runtime policy is the first richer
verification slice; property testing, fuzzing, security tools, formal verification,
and Bend are optional future project obligations when a real project needs them.
Choosing browser/runtime as the first richer verification slice is a product
priority here, not a mandate inferred from the context's illustrative list.

## Adoption bootstrap and preservation

Choose `ai-kit adopt` followed by the user's normal `codex` launch as the default
experience. Adoption prepares a **pending integration**, not a completed semantic
adoption. It records deterministic inventory and baseline evidence, installs a
small lifecycle skill, and adds a pointer to that protocol in the effective
project instruction file. A fresh agent then has the protocol before product
work; no giant copy-pasted prompt or global user configuration is necessary.

Represent the pending operation in an optional `ai-kit.lock` operation table,
with its kind (adopt/upgrade), protocol reference, original/target model, preserved-input
manifest, and completed mechanical steps. The router tells the normal-work
skill to inspect this record before product work; doctor reports pending status.
Keep it until deterministic finalization succeeds. This small operation record
tracks integration progress, not the harness's coding-task lifecycle. Today's
synchronous `CUSTODY.toml managed=false/true` flag does not supply this protocol.

The instruction insertion must be a delimited, hash-checked AI Kit-owned block.
Preserve human text outside it byte-for-byte; sync manages only the block. Inspect
the effective instruction chain, including `AGENTS.override.md`, so a shadowed
root `AGENTS.md` is not mistaken for a working bootstrap. Existing managed
whole-file routers can continue as whole files until an explicit migration.
Ambiguous ownership, marker collisions, or contradictory user guidance require
resolution, not silent replacement. Pre-existing custom `.ai` contents are not
themselves an error: preflight the exact files AI Kit needs to own.
Preserve file modes when editing an instruction file. Drift inside an old
whole-file router requires explicit extraction or reconciliation before block
migration; retain the original bytes until that resolution is reviewed. A
dangling context reference is a structural error with a resolution path, never
silently dropped or reassigned to another document.

An optional adapter launch can pass a short bootstrap prompt referring to the
prepared protocol; it must use an argument vector, preserve the user's harness
sandbox/approval configuration, and avoid recursive harness launch from an
already-running agent. The local Codex CLI 0.159.2 exposes `codex [OPTIONS]
[PROMPT]`, `-C`, and `codex exec` with stdin prompts. This establishes a feasible
fallback, not an implemented AI Kit adapter or a successful adoption run.
See the official [CLI reference](https://learn.chatgpt.com/docs/developer-commands?surface=cli).

The adoption protocol has the agent inspect real source, documentation, existing
instructions, and tests; distinguish approved sources from claims; retain useful
conventions; propose uncertain mappings; configure project commands; and report
preserved behavior and verification gaps. Reading a README is not approval to
turn its claims into intent. Source repair is a separate scoped task, not an
automatic prerequisite hidden inside adoption.

Finalization is deterministic: validate ownership, source preservation, context
references, working integration, policy, required evidence, and comparison with
the original baseline. Compare against the actual initial content, including
dirty and untracked user files, not just HEAD. An unexplained source change,
baseline regression, missing report, interrupted harness, or UNKNOWN required
check leaves adoption pending/unverified. The current baseline stores dirty
status/path entries, not their content hashes; hybrid adoption therefore needs
an initial preservation manifest of file content and modes, including dirty and
untracked user work, before semantic edits begin. This is a missing capability,
not a property already proved by `adopt_safe`. Recognized inherited failures
remain recorded; a structural integration may be installed without claiming that the
application works. Failure-count parity alone does not prove identical failing
behavior when stable test identities are unavailable.

Keep Landlock as write confinement for existing baseline execution, with its
documented limitations. It is not process/network isolation. Arbitrary project
commands are executable code and require the actual trust/sandbox boundary of
the environment. Never relax confinement to manufacture a baseline pass.

## Project-model upgrades

Separate installed package version, project-model version, policy schema, and
adapter format in metadata. `sync` reconciles the same project model; it must not
silently perform semantic upgrades because a newer package happens to be
installed. `doctor` reports an old model and the supported upgrade path.

Use explicit version-to-version migrations, initially ordinary functions for
the real versions supported. Do not build a migration/plugin framework before
there are migrations to run. A migration declares mechanical transformations,
semantic questions, owned paths, and invariants. Mechanical steps use
preflighted expected hashes and a recorded plan; semantic steps use the same
pending-operation lifecycle protocol as adoption.

Preserve original source/context identities and custom files. Record completed
steps so interruption can resume safely; do not replace human edits with an old
backup on resume. Revalidate inputs before continuing. Update the project-model
version only when required transformations and validation succeed. An agent exit
code or polished report cannot finalize an incomplete upgrade. Unsupported
models fail with a concrete supported path; they do not get reinitialized.

The normal-work protocol detects a supported old project model and invokes the
upgrade protocol itself. The user does not have to remember a migration command.
Mechanical reconciliation of known owned content can proceed within existing
authorization; semantic ambiguities or changes to approved meaning follow the
project's delegation/approval rules. Do not force a confirmation for every
mechanical version step or silently broaden authority during an automatic one.

The first migration must extract project check obligations from active legacy
contracts and profiles. Preserve the union of required obligations; conflicting
commands and task-specific checks need semantic resolution. Archive accepted
goal history as history. Do not weaken policy, lose unfinished native task
criteria, or reinterpret every old DONE goal as evidence for today's product.

## Capability layering and portability

A capable supervisor owns semantic interpretation, architecture, acceptance
mapping, and review. Workers get bounded scope, owned files, constraints, relevant
context references, and runnable checks. Persist shared choices only when they
are useful beyond a worker's session; label their delegated/advisory status and
review triggers. Neither a strong model's opinion nor a worker's success report
becomes project authority by itself. Independent verification remains necessary.

AI Kit supplies those protocols and context boundaries. Scheduling, model
selection, authentication, budgets, task state, and worker transport belong to
the harness or a project-owned workflow. The incomplete capability section does
not justify adding a worker orchestrator.

Keep domain schemas and evidence independent of Codex. The first adapter only
renders/discloses protocol entry points and optionally launches Codex. Introduce
a second adapter when supporting a real second harness; no one-implementation
adapter factory is needed now. Check the supported harness version during
bootstrap instead of assuming all future versions share today's discovery.

## Implementation order and proof obligations

These are architectural slices, not an approved feature checklist derived from
the context. Each implementation needs its own observable acceptance criteria.

| Order | Slice | Evidence required before shipping |
| --- | --- | --- |
| 1 | Standalone project policy/evidence and legacy transition | Checks execute independently of goal states; local/CI parity; missing/failing/unknown/skipped/stale checks rejected; policy/source mutation detected; legacy contracts and history preserved; normal product changes do not make structural health depend on every historical DONE goal. |
| 2 | Context mapping and managed instruction blocks | Existing custom documents/skills remain intact; approval is not inferred from paths; conflicting sources exposed; human router bytes preserved; drift/marker/path/symlink cases fail safely; fresh Codex discovers the correct protocol, including override cases. |
| 3 | Hybrid adoption | A realistic existing repo with custom instructions and dirty/untracked work survives preparation and finalization; fresh harness follows the bootstrap; inherited failure/unknown evidence persists; interruption and source mutation cannot finalize; meaningful verification demonstrates the preserved outcome. |
| 4 | First actual project-model upgrade | Old fixtures migrate with custom files and verification obligations intact; unsupported versions refuse; interrupted mechanical/semantic steps resume; failed validation leaves the original version and recoverable work. |
| 5 | Browser/runtime vertical slice | The real UI/backend flow passes, and each motivating fault fails with usable artifacts; skipped or unavailable execution remains unverified. |

Preserve legacy safety checks until their replacement gates are exercised. Do
not ship all slices as one rewrite, add every verification technology, build an
ontology engine, or introduce a daemon. The first useful change is extracting
verification from task state, not deleting the goal module first.

## Verification of this assessment

On the starting worktree, `./dev check` exited 0: formatting and lint passed;
65 tests passed. `./dev verify` exited 1: the same checks passed, lock validation
and managed sync passed, then doctor rejected stale DONE evidence for
`post-mvp-hardening`. `goals.py:is_evidence_fresh` invalidates accepted evidence
for dirty paths outside goal/current metadata, which includes the pre-existing
README, CLI, test, and docs changes. The failure is consistent with the shipped
policy; it is not a test failure or proof that the proposed model is broken.

No product code, accepted ADR, legacy contract, or inherited edit was changed
for this assessment. Green unit tests establish current tested behavior, not
the proposed bootstrap, migration, flexible context, or browser acceptance.
Those remain explicitly unimplemented and have proof obligations above.
