# Market Intelligence Merge and Post-Merge Validation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Merge PR #1 only after all release gates pass, then exhaust every production-like verification available without installing system services or claiming a public deployment.

**Architecture:** Use the feature branch for the pre-merge security gate and PR verification. Merge through GitHub without force-push or branch deletion, switch the existing checkout to `main`, and combine local Windows process evidence with GitHub-hosted PostgreSQL 16, Redis 7, Celery, Yahoo canary, and SLO evidence.

**Tech Stack:** Git/GitHub CLI, Python 3.12 local and Python 3.11 CI, FastAPI, SQLAlchemy, PostgreSQL 16, Redis 7, Celery, React, Vite, Vitest, Playwright, Yahoo Finance adapter.

## Global Constraints

- Do not add product features.
- Do not force-push or delete `feat/market-intelligence-engine`.
- Do not install Docker, WSL distributions, PostgreSQL, Redis, or modify Windows system settings.
- Do not run `npm audit fix`; security remediation must be narrow and independently verified.
- Do not claim public production deployment without a server, domain, TLS, and production credentials.
- Preserve SUCCEEDED/PARTIAL/FAILED atomic-publication invariants.
- Stop after the final report; do not start News, AI, Options Flow, or provider expansion.

---

### Task 1: Close the pre-merge security gate

**Files:**
- Modify: `frontend/package.json`
- Modify: `frontend/package-lock.json`
- Modify: `docs/dependency-security-assessment.md`
- Create: `docs/superpowers/plans/2026-09-07-market-intelligence-merge-post-merge-validation.md`

**Interfaces:**
- Consumes: npm advisory `GHSA-5xrq-8626-4rwp`, existing Vite 6 configuration.
- Produces: a full dependency graph with zero Critical advisories and an unchanged production runtime dependency set.

- [x] **Step 1: Record full and production-only npm audit totals.**

Run `npm audit --json` and `npm audit --omit=dev --json` from `frontend`; require zero Critical findings before merge.

- [x] **Step 2: Upgrade only Vitest within major version 4.**

Run `npm install --save-dev vitest@^4.1.11`; do not run an automated audit fix or upgrade Vite.

- [x] **Step 3: Verify the frontend toolchain.**

Run `npm run lint`, `npm run test:run`, `npm run test:smoke`, and `npm run build`; require exit code 0 for all commands.

- [x] **Step 4: Refresh the security assessment and commit.**

Record production/dev classification, reachability, and remaining recommendations. Commit the plan, manifest, lockfile, and assessment as one security-gate change.

### Task 2: Re-run pre-merge gates and merge PR #1

**Files:**
- No production file changes expected.

**Interfaces:**
- Consumes: GitHub PR #1 and both required workflows.
- Produces: merged PR with feature branch retained.

- [ ] **Step 1: Push the security-gate commit and wait for both workflows.**

Require standard CI and Market Intelligence Integration to complete successfully on the same feature HEAD.

- [ ] **Step 2: Verify repository state and PR mergeability.**

Require `MERGEABLE/CLEAN`, local HEAD equal to the remote feature HEAD, no uncommitted files, and full audit Critical count equal to zero.

- [ ] **Step 3: Merge through GitHub.**

Run `gh pr merge 1 --merge`; do not pass a branch-deletion or force option.

- [ ] **Step 4: Confirm PR state.**

Require PR state `MERGED`, record the merge commit, and confirm the remote feature branch still exists.

### Task 3: Verify the merged main branch

**Files:**
- No changes expected unless a real defect is discovered.

**Interfaces:**
- Consumes: merged `origin/main`.
- Produces: fresh local and CI post-merge evidence.

- [ ] **Step 1: Switch to and fast-forward local main.**

Run `git checkout main` and `git pull --ff-only origin main`; require a clean worktree.

- [ ] **Step 2: Run backend verification.**

Run the complete supported backend test command, `pip check`, and focused Market Intelligence invariant/API/Data Health suites. Classify known Windows-only baseline failures separately; do not hide regressions.

- [ ] **Step 3: Run frontend verification.**

Run `npm ci`, lint, all Vitest tests, Playwright smoke, and the production Vite build.

- [ ] **Step 4: Run service-backed post-merge CI.**

Dispatch Market Intelligence Integration on `main` and require PostgreSQL migration/rollback, Redis fallback/connectivity, concurrency/publication, API, deterministic suite, frontend build, and performance SLO success.

- [ ] **Step 5: Run the live Yahoo/Celery path.**

Dispatch the opt-in integration input on `main` and run the read-only scheduled-canary workflow manually. Record provider results without writing production data.

### Task 4: Execute local production-like smoke validation

**Files:**
- No changes expected unless a real defect is discovered.

**Interfaces:**
- Consumes: existing Windows Python/Node runtimes, local fallback configuration, real Yahoo read-only provider.
- Produces: startup, snapshot, publication-invariant, API, Data Health, and frontend evidence that is honest about missing PostgreSQL/Redis services.

- [ ] **Step 1: Confirm the environment limitation.**

Record that Docker, PostgreSQL, and Redis are unavailable and no WSL distribution is installed. Use CI for those production services rather than installing them.

- [ ] **Step 2: Exercise the local backend/API path.**

Start the existing FastAPI application with its supported local fallback configuration, probe liveness/readiness and Market Intelligence API/Data Health routes, and stop the process cleanly.

- [ ] **Step 3: Exercise real market-data calculation.**

Run the read-only Yahoo validation for SPY plus the 11 sector ETFs and record candidate/manual/replay status.

- [ ] **Step 4: Re-prove publication semantics.**

Run focused tests covering SUCCEEDED, PARTIAL, FAILED, retry/idempotency, and the invariant that incomplete runs cannot replace the last complete published snapshot.

- [ ] **Step 5: Exercise the frontend against a production bundle.**

Serve the production build locally, run the existing Playwright UI smoke, and record that rendering succeeds. Do not describe this as a public deployment.

### Task 5: Close out documentation and state

**Files:**
- Modify: `docs/market-intelligence-production-hardening-report.md`
- Modify: `docs/dependency-security-assessment.md`
- Modify: `docs/superpowers/plans/2026-09-07-market-intelligence-merge-post-merge-validation.md`

**Interfaces:**
- Consumes: all post-merge commands and GitHub Actions run IDs.
- Produces: final status separating code, merge, production-like validation, deployment, and monitoring.

- [ ] **Step 1: Update the final report.**

Explicitly state `Code Complete`, `Merged to Main`, `Production-like Validation Complete`, `Real Production Deployment Pending`, and `Long-term Production Monitoring Pending` with evidence.

- [ ] **Step 2: Commit and push documentation to main.**

Use a normal commit and push; do not force. Wait for triggered checks if repository policy starts them.

- [ ] **Step 3: Run final verification.**

Confirm main HEAD, merged PR state, CI conclusions, retained feature branch, clean worktree, no Critical audit finding, and `git diff --check` exit 0.

- [ ] **Step 4: Stop.**

Report the real remaining external work and blockers without starting another product milestone.
