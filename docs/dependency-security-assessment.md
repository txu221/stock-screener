# Dependency security assessment

Date: 2026-09-07
Scope: Market Intelligence Production Hardening v2 on `feat/market-intelligence-engine`

## Executive assessment

`npm audit --json` reports the current frontend advisory-database view of 22
vulnerable package nodes: 0 critical, 17 high, 4 moderate, and 1 low. The former
critical finding was direct dev dependency `vitest@4.0.18`, affected by
`GHSA-5xrq-8626-4rwp`; the merge-readiness security gate upgraded only Vitest
within major version 4 to `4.1.11`, outside the advisory's `<4.1.0` affected
range. The production dependency graph is unchanged by this update.

The earlier hardening range from `6d75e8a4` through `b4bd33f7` changed no Python
or npm dependency manifest or lockfile. The subsequent merge-readiness change
modifies only the direct dev dependency and its lockfile-resolved Vitest support
packages. `python -m pip check` reports no broken requirements.

No automatic remediation was run. In particular, this work did not run
`npm audit fix`, add an override, or combine dependency upgrades with the
Market Intelligence correctness changes.

## Audit evidence

Commands:

```text
cd frontend
npm audit --json
npm audit --omit=dev --json
npm ls vitest --json
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
| Full installed frontend graph | 0 | 17 | 4 | 1 | 22 |
| `--omit=dev` production graph | 0 | 6 | 2 | 0 | 8 |

The full graph contains 624 package entries according to npm metadata: 239
production, 386 development, 50 optional, and 9 peer entries (npm categories can
overlap). The
Python environment reports `No broken requirements found`.

## Resolved critical finding

| Package | Previous / current | Dependency class | Reachability and resolution |
| --- | --- | --- | --- |
| `vitest` | `4.0.18` / `4.1.11` | Direct dev dependency | The reported arbitrary-file read/execution issue requires a listening Vitest UI server. CI uses `vitest run`, and the application production bundle does not include Vitest. The installed version is now outside the advisory range; local test UI servers must still remain bound to a trusted interface. |

Vitest remains absent from the `--omit=dev` graph. Fresh `npm ci`, full audit,
production-only audit, lint, focused static-route tests, production build, and
Playwright smoke were run after the update. The full native-Windows Vitest run
continues to expose the separately documented `D:\\D:\\...` fixture-path defect
and static-route cold-start contention; the supported Ubuntu CI suite is the
authoritative full-suite gate.

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
- `@humanfs/node`, `ajv`, and `yaml` are transitive tooling/config dependencies
  in the assessed graph.
- `@babel/core` is the single low-severity package node and is build tooling.

## Recommended remediation sequence

1. Create a small dependency-only branch after Production Hardening v2. Upgrade
   Axios to the npm-recommended non-major fixed release and update the React
   Router family to a compatible fixed set; run navigation, auth/session, API,
   frontend unit, Playwright smoke, and production-build checks.
2. The direct Vitest critical is resolved. Upgrade Vite/Rollup/PostCSS and their
   remaining transitive tooling graph later as one tested toolchain batch. Do
   not expose local test or Vite servers beyond trusted interfaces.
3. Refresh or constrain the Recharts/Lodash path only after verifying charts and
   bundle output; prefer an upstream fixed dependency over a blind override.
4. Re-run both full and `--omit=dev` audits after each isolated batch and record
   any remaining reachable advisory by package path.

## Hardening-v2 disposition

The production advisories predate this hardening range, and the production
dependency graph was not changed by the targeted Vitest remediation. No
automatic remediation, override, or major-version toolchain migration was used.
The direct runtime packages remain recommended near-term follow-up work, while
the release gate now has zero Critical findings in both full and production-only
audit scopes.
