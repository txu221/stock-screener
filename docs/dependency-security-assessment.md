# Dependency security assessment

Date: 2026-09-02
Scope: Market Intelligence Production Hardening v2 on `feat/market-intelligence-engine`

## Executive assessment

`npm audit --json` reports the current frontend advisory-database view of 22
vulnerable package nodes: 1 critical, 17 high, 3 moderate, and 1 low. The count
increased by one high-severity `browserslist` advisory while the lockfile was
unchanged; this is an advisory-feed change, not a hardening dependency change.
The hardening range
from `6d75e8a4` through the assessed commit changes no Python or npm dependency
manifest or lockfile. `python -m pip check` reports no broken requirements.

No automatic remediation was run. In particular, this work did not run
`npm audit fix`, add an override, or combine dependency upgrades with the
Market Intelligence correctness changes.

## Audit evidence

Commands:

```text
cd frontend
npm audit --json
npm audit --omit=dev --json
npm ls axios react-router-dom react-router @remix-run/router vite vitest \
  rollup postcss undici lodash brace-expansion flatted form-data js-yaml \
  minimatch nanoid picomatch --all --json

cd ..
backend/venv/Scripts/python.exe -m pip check
git diff 6d75e8a4..HEAD -- backend/requirements*.txt \
  frontend/package.json frontend/package-lock.json pyproject.toml poetry.lock
```

Observed totals:

| Scope | Critical | High | Moderate | Low | Total |
| --- | ---: | ---: | ---: | ---: | ---: |
| Full installed frontend graph | 1 | 17 | 3 | 1 | 22 |
| `--omit=dev` production graph | 0 | 6 | 2 | 0 | 8 |

The full graph contains 623 packages according to npm metadata: 239 production,
385 development, and 50 optional entries (npm categories can overlap). The
Python environment reports `No broken requirements found`.

## Critical finding

| Package | Installed | Dependency class | Current reachability assessment | Recommendation |
| --- | --- | --- | --- | --- |
| `vitest` | 4.0.18 | Direct dev dependency | The reported critical issue requires a listening Vitest UI server. CI uses `vitest run`; the application production bundle does not include Vitest. Local watch mode is development-only, but exposing its server would create risk. | Upgrade to a fixed `>=4.1.0` release in a dedicated tooling change, rerun all frontend tests, and keep any test UI bound to a trusted interface. |

This critical package is not present in the `--omit=dev` audit graph, so it is
not a deployed browser-runtime dependency. It remains actionable developer and
CI tooling debt.

## High findings in the production dependency graph

| Package path | Installed | Direct/transitive | Reachability assessment |
| --- | --- | --- | --- |
| `axios` | 1.13.2 | Direct | The browser client is used, but its base URL is fixed to `/api/v1`; no application path supplies proxy configuration or a Node HTTP adapter. Many reported SSRF, proxy, streamed-upload, and `form-data` issues are Node-adapter-specific. Prototype-pollution/config-merge classes are defense-in-depth relevant. npm identifies 1.20.0 as a non-major fix target. |
| `form-data` via `axios` | 4.0.5 | Transitive | It belongs to Axios's Node path. The shipped React client uses the browser adapter and does not directly import `form-data`; no current production-browser reachability was found. |
| `react-router-dom` -> `react-router` -> `@remix-run/router` | 6.30.2 / 6.30.2 / 1.23.1 | Direct plus transitive | The product uses client-side routing. Current redirects are fixed to `/`; ticker navigation applies `encodeURIComponent`. No user-controlled external redirect target or SSR hydration/deserialization path was found. The packages are nevertheless shipped runtime code and should be upgraded to fixed compatible releases. |
| `lodash` via `recharts` | 4.17.21 | Transitive runtime | Application code does not import Lodash directly, and no path exposing vulnerable template imports or `unset`/`omit` keys to untrusted input was found. It remains in the deployed visualization dependency graph. |

"No current path found" is a code-reachability assessment, not a claim that a
vulnerable package is safe indefinitely. Direct runtime packages should be the
first isolated remediation batch.

## High findings limited to development/build/test paths

The remaining high package nodes are `vite`, `rollup`, `postcss`, `browserslist`, `nanoid`,
`picomatch`, `undici`, `js-yaml`, `flatted`, `minimatch`, and
`brace-expansion`. They are reached through Vite/Vitest/jsdom/ESLint and related
build or test tooling. Their reported attack classes include development-server
file exposure or path traversal, build-time file write/read, WebSocket/network
client denial of service, YAML/config parsing complexity, and glob/cache parser
denial of service.

These packages are not part of the static browser application runtime produced
by `vite build`, but they execute on developer machines or CI over repository
inputs. Treat pull-request code and build inputs as untrusted, do not expose dev
servers publicly, and upgrade them in a coordinated tooling PR.

## Moderate and low findings

- `follow-redirects` is in the production dependency graph through Axios's Node
  path; the browser application does not directly use that adapter.
- `ajv` and `yaml` are transitive tooling/config dependencies in the assessed
  graph.
- `@babel/core` is the single low-severity package node and is build tooling.

## Recommended remediation sequence

1. Create a small dependency-only branch after Production Hardening v2. Upgrade
   Axios to the npm-recommended non-major fixed release and update the React
   Router family to a compatible fixed set; run navigation, auth/session, API,
   frontend unit, Playwright smoke, and production-build checks.
2. Upgrade Vitest first among dev tools, then Vite/Rollup/PostCSS and their
   transitive graph as one tested toolchain batch. Do not expose local test or
   Vite servers beyond trusted interfaces.
3. Refresh or constrain the Recharts/Lodash path only after verifying charts and
   bundle output; prefer an upstream fixed dependency over a blind override.
4. Re-run both full and `--omit=dev` audits after each isolated batch and record
   any remaining reachable advisory by package path.

## Hardening-v2 disposition

The advisories predate this hardening range, no dependency was added or changed,
and no new dependency attack surface was introduced. Production Hardening v2
therefore records and prioritizes the risk without expanding into an unrelated
dependency migration. The direct runtime packages remain recommended near-term
follow-up work.
