# Production Required Inputs and Secrets

## Current state

No value in this document is a real credential. Required operator inputs have
not been supplied, so **Real Production Deployment Pending** is the only valid
deployment status.

Use a password manager or managed secret store as the recovery source. The VPS
copy of `.env.docker` is mode 0600, Git ignored, never pasted into logs, and
never committed. The GHCR token belongs to Docker's host credential store, not
the application environment. Examples describe format only.

## Infrastructure and release inputs

| Input | Required | Example format (not a value) | Supplier / owner | Storage and exposure | Rotation / recovery |
|---|---|---|---|---|---|
| VPS provider and host | Yes | Ubuntu 24.04 instance identifier | User / infrastructure owner | Provider account; not Compose | Provider MFA, recovery console, billing alert |
| public IPv4 | Yes | `<VPS_PUBLIC_IPV4>` | VPS provider | DNS A record and operator inventory | Update DNS after host replacement |
| public IPv6 | Optional | `<VPS_PUBLIC_IPV6>` | VPS provider | DNS AAAA only when fully reachable | Remove AAAA before IPv6 retirement |
| SSH administrator identity | Yes | `<DEPLOY_USER>` | User | Named non-root sudo account | Maintain break-glass console path |
| SSH public key | Yes | `ssh-ed25519 <public-material> <label>` | User/operator | VPS `authorized_keys`; public half only | Replace on personnel/device change |
| Allowed SSH source CIDR | Yes | `<ADMIN_SOURCE_CIDR>` | User/network owner | Provider firewall and UFW | Review after network change |
| domain/subdomain | Yes | `stocks.<owned-domain>` | User | `DOMAIN`, `CORS_ORIGINS`, DNS, certificate | Document renewal/registrar recovery |
| DNS provider access | Yes | Delegated role or scoped token | User/DNS owner | DNS provider, never Compose | Least privilege; rotate per provider policy |
| Repository URL | Yes | `https://github.com/<owner>/<repo>.git` | Engineering | Checkout command, non-secret if public | Keep origin ownership documented |
| `RELEASE_GIT_SHA` | Yes | 40 lowercase hexadecimal characters | Release owner | `.env.docker` and non-secret release manifest | New value per reviewed release |
| `BACKEND_IMAGE_REF` | Yes | `ghcr.io/<owner>/<backend>@sha256:<64-hex>` | Release owner | `.env.docker` and non-secret release manifest | Promote/rollback only as release pair |
| `FRONTEND_IMAGE_REF` | Yes | `ghcr.io/<owner>/<frontend>@sha256:<64-hex>` | Release owner | `.env.docker` and non-secret release manifest | Promote/rollback only as release pair |
| GHCR username | Conditional | `<github-username>` | User/release owner | Docker login only | Review with token rotation |
| GHCR read token | Conditional for private packages | Classic PAT with `read:packages` | User/release owner | Docker host credential store; never `.env.docker` | Revoke/rotate; retain package access recovery |
| deployment window | Yes | UTC start/end plus rollback cutoff | User/operator | Change ticket/operator log | Reapprove for each release |

`latest`, branch tags, and bare release tags are not inputs. Both digest refs
must resolve to images whose OCI revision label equals `RELEASE_GIT_SHA`.

## Application and database secrets

| Input | Required | Example format (not a value) | Supplier / owner | Storage and exposure | Rotation / recovery |
|---|---|---|---|---|---|
| `POSTGRES_DB` | Yes | safe identifier such as `stockscanner` | Operator | `.env.docker`; database network only | Stable name; document DR mapping |
| `POSTGRES_USER` | Yes | safe identifier such as `stockscanner` | Operator | `.env.docker`; database network only | Rotate deliberately with ownership migration |
| `POSTGRES_PASSWORD` | Yes | independent random 32+ characters | User/operator | Secret manager and mode-0600 `.env.docker`; PostgreSQL/app containers | Rotate in approved window; test app reconnect and backup |
| `SERVER_AUTH_PASSWORD` | Yes | independent random 32+ characters | User/operator | Secret manager and mode-0600 `.env.docker`; backend only | Recreate backend; notify authorized users |
| `SERVER_AUTH_SESSION_SECRET` | Yes | different independent random 32+ characters | User/operator | Secret manager and mode-0600 `.env.docker`; backend only | Rotation invalidates sessions; schedule accordingly |
| `GITHUB_DATA_TOKEN` | Optional | scoped GitHub token for existing release-data reads | User | Secret manager and `.env.docker`; app containers | Leave empty unless capability is intentionally used |
| `ADMIN_API_KEY` | Optional | independent random admin credential | User | Secret manager and `.env.docker`; app containers | Leave empty unless config API is intentionally used |

Do not reuse the three required secrets. The committed
`.env.production.example` contains only `CHANGE_ME` sentinels and is not a
secret source.

## Backup, monitoring, and operations inputs

| Input | Required | Example format (not a value) | Supplier / owner | Storage and exposure | Rotation / recovery |
|---|---|---|---|---|---|
| off-host backup destination | Yes | encrypted object-storage bucket or separate backup host | User/infrastructure owner | Backup agent configuration outside Git | Versioning/retention enabled; document regional/account recovery |
| off-host backup credential | Yes | write/read scope limited to backup prefix | User/infrastructure owner | Host secret store or backup agent; not app containers | Rotate and run a restore after rotation |
| backup encryption control | Yes | provider-managed key or approved client-side key | User/security owner | KMS/secret manager, separate from VPS | Preserve recovery principals and key backup |
| monitoring destination | Yes | alert channel/on-call target | User/operator | Monitoring platform | Test alert after every material change |
| log destination | Recommended | managed log sink or hardened remote syslog | User/operator | Log agent configuration; redact secrets | Define retention/access review |
| deployment operator | Yes | named person and contact | User | Change record | Ensure primary and rollback owner availability |
| rollback decision owner | Yes | named approver | User | Change record | Must be reachable during deployment window |

The local retention contract is seven verified dumps. Disaster recovery also
requires the latest dump plus `.sha256`, the current and previous
`release-manifest.env`, and recoverable production secrets outside the failed
VPS.

## Explicitly not required

Yahoo sector ingestion requires no API key. **LLM, News, AI, and Options Flow credentials are not required** for this MVP deployment. Multi-provider and
non-US-market credentials are also out of scope. Leave unrelated optional keys
empty; do not create fake production credentials.

## Pre-deployment handoff gate

Before provisioning, the user supplies or assigns ownership for every required
row. The operator then follows
[`runbooks/production-deployment.md`](runbooks/production-deployment.md) without
substituting a source-build or tag-based release path. Any missing required
input blocks real deployment, but it does not block repository-level deployment
preparation.
