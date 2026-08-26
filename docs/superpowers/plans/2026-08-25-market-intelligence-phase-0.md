# Market Intelligence Phase 0 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Select and baseline a legally reusable open-source foundation for a US Market Intelligence / Money Flow platform without implementing Phase 1 product features.

**Architecture:** Audit candidate repositories from source, choose one primary base on evidence, import it with upstream history intact, and characterize its reproducible baseline. Produce a deterministic market-intelligence architecture proposal that extends the chosen base and keeps provider ingestion, canonical data, snapshots, metrics, signals, ranking, and UI boundaries explicit.

**Tech Stack:** Determined by the selected upstream repository; Git/GitHub CLI for provenance; native upstream package manager and test/lint/start commands for baseline verification.

## Global Constraints

- Do not implement Market Intelligence features in Phase 0.
- Do not rewrite UI, perform a big-bang refactor, push, or create a PR.
- Audit at least five repositories beyond their READMEs, including all four named by the product brief.
- Do not copy code from repositories with unclear or incompatible licenses.
- Preserve upstream Git history, attribution, and license; configure an `upstream` remote.
- Create and work on branch `feat/market-intelligence-engine`.
- Record pre-existing failures without silently changing upstream behavior.
- Treat OHLCV-derived flow as a proxy, never as measured institutional/order flow.
- All future financial metrics and rankings must be deterministic, documented, traceable, and tested.

---

### Task 1: Repository Research and Base Selection

**Files:**
- Create: `docs/upstream-research.md`

**Interfaces:**
- Consumes: GitHub repository source, metadata, commit history, dependency manifests, tests, licenses, and configuration.
- Produces: Evidence-backed `PRIMARY BASE` selection and `DONOR / REFERENCE PROJECTS` classification.

- [x] Inspect the four required repositories and search GitHub for additional viable MIT/Apache-2.0 candidates.
- [x] Clone candidates into an OS temporary directory and inspect architecture, providers, caching, indicators, mappings, ETF universe, screeners, APIs, data models, tests, debt, and data-quality risks.
- [x] Verify licenses and recent activity from repository files and Git metadata.
- [x] Write `docs/upstream-research.md` with a comparable scorecard, reusable modules, reference-only ideas, and the base decision.

### Task 2: Import the Selected Upstream

**Files:**
- Modify: repository working tree, Git refs, and Git remote configuration.

**Interfaces:**
- Consumes: exact selected upstream URL and default-branch commit from Task 1.
- Produces: current workspace containing upstream history on `feat/market-intelligence-engine`, with remote `upstream` pointing to the source repository.

- [x] Confirm the current workspace has no user files or unrelated changes that would be overwritten.
- [x] Fetch the selected repository into the existing Git directory and check out its default branch without squashing history.
- [x] Rename the source remote to `upstream` or add it if absent.
- [x] Create `feat/market-intelligence-engine` from the exact audited upstream commit.
- [x] Confirm `git rev-parse HEAD`, `git log`, and `git remote -v` preserve provenance.

### Task 3: Reproduce the Upstream Baseline

**Files:**
- Create: `docs/baseline-audit.md`
- Modify: lockfile only if the upstream package manager requires a platform-specific, reproducible install update; otherwise no product files.

**Interfaces:**
- Consumes: upstream dependency manifests and documented native commands.
- Produces: exact environment, install, test, lint, startup, smoke-test, functionality, failure, and debt evidence.

- [x] Detect required runtimes and package manager from manifests and upstream documentation.
- [x] Install dependencies using the repository's locked/frozen mode when supported.
- [x] Run the upstream deterministic test suite and record exact command, exit code, counts, and failures.
- [x] Run the upstream lint/type-check command and record exact command and failures; explicitly record if none exists.
- [x] Start the application using its native command, probe its local health/UI/API surface, capture logs, and terminate it cleanly.
- [x] Write `docs/baseline-audit.md` without modifying upstream behavior to hide failures.

### Task 4: Architecture Proposal

**Files:**
- Create: `docs/market-intelligence-spec.md`

**Interfaces:**
- Consumes: selected base architecture and all product/data-quality requirements in the brief.
- Produces: phased target architecture, canonical models, deterministic metric contracts, API boundaries, lineage/freshness rules, migration strategy, risks, and a bounded Phase 1 recommendation.

- [x] Map the selected base's existing modules to Market Data → Normalized Market Data → Daily Snapshots → Quant Metrics → Signals → Ranking/Change Detection → UI.
- [x] Specify provider-neutral schemas, validation/quarantine, source/freshness/formula-version lineage, and graceful degradation.
- [x] Specify deterministic contracts for flow proxies, movers/liquidity filters, sector rotation, ETF scoring, change detection, and Data Health without implementing them.
- [x] Define incremental milestones that preserve working functionality and separate deterministic unit tests from network integration tests.
- [x] Recommend only the smallest testable Phase 1 vertical slice and stop before implementation.

### Task 5: Phase 0 Verification and Handoff

**Files:**
- Verify: `docs/upstream-research.md`
- Verify: `docs/baseline-audit.md`
- Verify: `docs/market-intelligence-spec.md`

**Interfaces:**
- Consumes: Tasks 1–4 outputs and command evidence.
- Produces: a Phase 0-only handoff with exact upstream SHA, working/startup status, risks, and Phase 1 recommendation.

- [x] Re-run lightweight document, Git provenance, and repository-status checks.
- [x] Confirm no Phase 1 feature code, push, PR, UI rewrite, or broad refactor occurred.
- [x] Confirm all requested handoff items are present and supported by repository evidence.
- [x] Stop and wait for explicit approval before beginning Phase 1.
