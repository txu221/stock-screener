# Production Deployment Runbook

## Status, scope, and stop rule

This is the single supported production deployment path for the Market
Intelligence MVP. It deploys a reviewed backend/frontend pair from GHCR by
immutable digest. It never uses `latest`, a branch tag, or a bare semantic tag
as the production release identity. Initial scope is fixed to
`ENABLED_MARKETS=US` and `COMPOSE_PROFILES=backup`.

Current status is **Real Production Deployment Pending**. Every item explicitly
marked **REQUIRES VPS VALIDATION** is unverified until an operator records its
output on the real host. Stop at the first failed gate. Do not proceed with a
degraded database, unknown migration state, unverified image identity, failed
first snapshot, invalid TLS, failed restart, or failed backup.

Never run `docker compose down -v` during deployment, upgrade, rollback, or
routine maintenance: `-v` deletes persistent PostgreSQL, Redis, and Caddy data.

## Fixed production architecture

Traffic follows DNS -> Caddy (80/443 and automatic TLS) -> nginx frontend ->
FastAPI. PostgreSQL 16, Redis 7, backend, nginx, workers, Beat, and backups stay
on the private Compose network. The exact Compose layering is:

```text
docker-compose.yml
docker-compose.prod.yml
docker-compose.release.yml
docker-compose.https.yml
```

The release overlay requires:

```text
BACKEND_IMAGE_REF=ghcr.io/<owner>/stockscreenclaude-backend@sha256:<64-hex>
FRONTEND_IMAGE_REF=ghcr.io/<owner>/stockscreenclaude-frontend@sha256:<64-hex>
RELEASE_GIT_SHA=<40-hex>
```

Backend and frontend are promoted or rolled back as one release pair.

## 1. Collect and freeze inputs

Obtain every required item in
[`production-required-inputs.md`](../production-required-inputs.md). At minimum:
VPS/IP/SSH access, domain and DNS access, the reviewed full Git SHA, both GHCR
digest references, three independent database/authentication secrets, a
private-GHCR read token if needed, off-host backup storage, a monitoring target,
and an approved deployment/rollback window.

Confirm the CI run that built the images is green and that both digests belong
to the same `RELEASE_GIT_SHA`. Do not invent a digest, secret, IP, or domain.

## 2. Provision and harden Ubuntu

Use Ubuntu 24.04 LTS. Recommended: 4 vCPU, 8 GB RAM, 80 GB SSD. Minimum for
light single-user use: 2 vCPU, 4 GB RAM, 40 GB SSD with `WEB_CONCURRENCY=2` and
close memory/disk monitoring. Ensure the disk can hold image layers,
PostgreSQL, Redis AOF, logs, seven dumps, and one temporary restored database.

Log in through the provider console or initial SSH identity, create a named
operator, install the supplied public key, and prove a second SSH session works
before disabling password or root login. Then patch and install operator tools:

```bash
sudo apt-get update
sudo DEBIAN_FRONTEND=noninteractive apt-get -y dist-upgrade
sudo apt-get install -y ca-certificates curl git jq python3 openssh-server ufw chrony
sudo timedatectl set-timezone UTC
timedatectl status
systemctl is-active chrony
```

Apply the organization's SSH policy in an `/etc/ssh/sshd_config.d/` drop-in.
Validate before reload and keep the proven session open:

```bash
sudo sshd -t
sudo systemctl reload ssh
```

Set provider firewall and UFW rules for the actual approved SSH source range,
then HTTP/HTTPS. Replace the CIDR; do not expose SSH to the world by copying an
example blindly.

```bash
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw allow from <ADMIN_SOURCE_CIDR> to any port 22 proto tcp
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw enable
sudo ufw status verbose
```

Docker-published ports can bypass UFW processing. Confirm only Caddy publishes
80/443 and enforce any additional source restrictions in the provider firewall
or Docker `DOCKER-USER` chain. Host firewall behavior is **REQUIRES VPS
VALIDATION**.

## 3. Install Docker Engine and Compose

Use Docker's official Ubuntu apt repository, not an unofficial convenience
script:

```bash
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc
. /etc/os-release
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu ${UBUNTU_CODENAME:-$VERSION_CODENAME} stable" | sudo tee /etc/apt/sources.list.d/docker.list >/dev/null
sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
sudo systemctl enable --now docker
sudo docker run --rm hello-world
sudo docker compose version
```

Adding an account to group `docker` grants root-equivalent control. If the
operator accepts that risk, add only the deployment account and re-login:

```bash
sudo usermod -aG docker "$USER"
```

Configure Docker daemon JSON log rotation if the host policy does not already
manage it. Docker installation, boot enablement, and resource behavior are
**REQUIRES VPS VALIDATION**.

## 4. Check out the reviewed release

Use one stable directory and the operator-supplied repository URL and SHA:

```bash
sudo install -d -o "$USER" -g "$USER" /opt/stockscanner
git clone <REPOSITORY_URL> /opt/stockscanner/app
cd /opt/stockscanner/app
git fetch --tags --prune origin
git checkout --detach <RELEASE_GIT_SHA>
test "$(git rev-parse HEAD)" = "<RELEASE_GIT_SHA>"
git status --short
```

The status output must be empty. An upgrade uses `git fetch` followed by a
detached checkout of the new reviewed SHA; never deploy an uncommitted tree.

## 5. Authenticate to GHCR when packages are private

Public GHCR images can be pulled anonymously. For private packages, use a
classic PAT with only `read:packages`, entered without echo and retained only by
Docker's host credential mechanism:

```bash
read -rsp "GHCR read token: " GHCR_READ_TOKEN
echo
printf '%s' "$GHCR_READ_TOKEN" | docker login ghcr.io -u <GITHUB_USERNAME> --password-stdin
unset GHCR_READ_TOKEN
```

Do not put this token in `.env.docker`, Compose, a release manifest, shell
history, or application containers. A clean-VPS private image pull is
**REQUIRES VPS VALIDATION**.

## 6. Create and validate the production environment

```bash
cd /opt/stockscanner/app
cp .env.production.example .env.docker
chmod 600 .env.docker
${EDITOR:-vi} .env.docker
git check-ignore -q .env.docker
test -z "$(git ls-files .env.docker)"
python3 backend/scripts/validate_production_deployment.py --env-file .env.docker
```

Replace every `CHANGE_ME` value. Keep `ENABLED_MARKETS=US`,
`COMPOSE_PROFILES=backup`, `CELERY_TIMEZONE=America/New_York`, secure cookies
enabled, API docs disabled, and static export disabled. Generate
`POSTGRES_PASSWORD`, `SERVER_AUTH_PASSWORD`, and
`SERVER_AUTH_SESSION_SECRET` independently. Do not print their values.

All following commands use this single shell array. Define it once per session:

```bash
COMPOSE_PROD=(
  scripts/docker-compose-enabled-markets.sh
  --env-file .env.docker
  -f docker-compose.yml -f docker-compose.prod.yml
  -f docker-compose.release.yml -f docker-compose.https.yml
)
```

Its canonical one-line form is retained here for audit and automation review:

```bash
scripts/docker-compose-enabled-markets.sh --env-file .env.docker -f docker-compose.yml -f docker-compose.prod.yml -f docker-compose.release.yml -f docker-compose.https.yml config --quiet
```

Validate again before every release:

```bash
python3 backend/scripts/validate_production_deployment.py --env-file .env.docker
"${COMPOSE_PROD[@]}" config --quiet
```

Rendered Compose on a clean Linux host is **REQUIRES VPS VALIDATION**.

## 7. Pull and prove immutable image identity

Read only non-secret release identifiers from the validated env file:

```bash
BACKEND_REF="$(sed -n 's/^BACKEND_IMAGE_REF=//p' .env.docker)"
FRONTEND_REF="$(sed -n 's/^FRONTEND_IMAGE_REF=//p' .env.docker)"
RELEASE_SHA="$(sed -n 's/^RELEASE_GIT_SHA=//p' .env.docker)"
test -n "$BACKEND_REF" && test -n "$FRONTEND_REF" && test -n "$RELEASE_SHA"
"${COMPOSE_PROD[@]}" pull
test "$(docker image inspect --format '{{ index .Config.Labels "org.opencontainers.image.revision" }}' "$BACKEND_REF")" = "$RELEASE_SHA"
test "$(docker image inspect --format '{{ index .Config.Labels "org.opencontainers.image.revision" }}' "$FRONTEND_REF")" = "$RELEASE_SHA"
```

If either OCI revision label differs or is absent, stop and resolve the CI/image
provenance issue. Record the pair outside Git:

```bash
sudo install -d -m 700 /var/lib/stockscanner/releases
MANIFEST="/tmp/release-manifest.env"
umask 077
printf 'RELEASE_GIT_SHA=%s\nBACKEND_IMAGE_REF=%s\nFRONTEND_IMAGE_REF=%s\nDEPLOYED_AT=%s\n' \
  "$RELEASE_SHA" "$BACKEND_REF" "$FRONTEND_REF" "$(date -u +%FT%TZ)" > "$MANIFEST"
sudo install -m 600 "$MANIFEST" "/var/lib/stockscanner/releases/release-manifest.candidate.env"
rm -f "$MANIFEST"
```

On upgrades, first copy the accepted `release-manifest.env` to
`release-manifest.previous.env`. Keep the new pair as
`release-manifest.candidate.env` until all acceptance gates through backup pass.

## 8. Start PostgreSQL and Redis

```bash
"${COMPOSE_PROD[@]}" up -d postgres redis
"${COMPOSE_PROD[@]}" ps postgres redis
"${COMPOSE_PROD[@]}" exec -T postgres sh -c 'pg_isready -U "$POSTGRES_USER" -d "$POSTGRES_DB"'
"${COMPOSE_PROD[@]}" exec -T redis redis-cli ping
```

Require healthy PostgreSQL and `PONG`. Confirm only named volumes/private
networks are used and no database/broker port is public. PostgreSQL and Redis
persistence under a full cold start are **REQUIRES VPS VALIDATION**.

## 9. Run the explicit migration gate

FastAPI also serializes migration-at-startup with a PostgreSQL advisory lock,
but production first deploys run an observable one-shot migration before any
worker starts:

```bash
"${COMPOSE_PROD[@]}" run --rm --no-deps backend alembic upgrade head
"${COMPOSE_PROD[@]}" run --rm --no-deps backend alembic current --verbose
```

The reported revision must be the repository head. On failure, stop, preserve
logs/database, and diagnose. Never automatically run `alembic downgrade` on a
live database.

## 10. Start application services in stages

```bash
"${COMPOSE_PROD[@]}" up -d backend frontend
"${COMPOSE_PROD[@]}" exec -T backend curl -fsS http://127.0.0.1:8000/livez
"${COMPOSE_PROD[@]}" exec -T backend curl -fsS http://127.0.0.1:8000/readyz
"${COMPOSE_PROD[@]}" exec -T frontend wget -qO- http://127.0.0.1/nginx-health

"${COMPOSE_PROD[@]}" up -d celery-general celery-datafetch celery-marketjobs-us celery-userscans celery-userscans-us celery-beat db-backup
"${COMPOSE_PROD[@]}" exec -T backend celery -A app.celery_app inspect ping
"${COMPOSE_PROD[@]}" exec -T backend celery -A app.celery_app inspect active_queues
"${COMPOSE_PROD[@]}" logs --tail=100 celery-marketjobs-us celery-beat db-backup
```

Only `market-us` services may be active. `/livez` proves process liveness.
`/readyz` treats PostgreSQL/schema failure as fatal; Redis or missing/stale
Market Intelligence data can be reported as degraded without pretending the
dependency is healthy. These backend probes are intentionally checked inside
the private network; nginx does not publish them as public API routes.

Worker/Beat health and correct queue binding are **REQUIRES VPS VALIDATION**.

## 11. Point DNS and start HTTPS

Create an A record for `DOMAIN` pointing to the VPS public IPv4. Create an AAAA
record only if IPv6 is configured and reachable. Wait until public resolvers
return exactly the host addresses:

```bash
DOMAIN_NAME="$(sed -n 's/^DOMAIN=//p' .env.docker)"
dig +short A "$DOMAIN_NAME"
dig +short AAAA "$DOMAIN_NAME"
```

Then start Caddy:

```bash
"${COMPOSE_PROD[@]}" up -d caddy
"${COMPOSE_PROD[@]}" ps
"${COMPOSE_PROD[@]}" logs --tail=200 caddy
curl -fsS "https://${DOMAIN_NAME}/nginx-health"
curl -sSIL "http://${DOMAIN_NAME}" | head
openssl s_client -connect "${DOMAIN_NAME}:443" -servername "$DOMAIN_NAME" </dev/null 2>/dev/null | openssl x509 -noout -subject -issuer -dates
```

Require valid hostname coverage, trusted issuance, HTTP-to-HTTPS redirect, and
no Caddy ACME errors. Public DNS propagation is **REQUIRES VPS VALIDATION**.
TLS issuance and renewal are **REQUIRES VPS VALIDATION**. External
accessibility from a network outside the VPS is **REQUIRES VPS VALIDATION**.

## 12. Run the first Yahoo sector snapshot

Dispatch the existing US-only task to its explicit queue and retain the task
ID. Do not provide a date unless the operator has reviewed it as a completed US
trading session.

```bash
TASK_ID="$("${COMPOSE_PROD[@]}" exec -T backend celery -A app.celery_app call app.tasks.market_intelligence_tasks.calculate_sector_intelligence_snapshot --queue market_jobs_us | tail -n 1 | tr -d '\r')"
test -n "$TASK_ID"
printf 'Task ID: %s\n' "$TASK_ID"
"${COMPOSE_PROD[@]}" exec -T backend celery -A app.celery_app result "$TASK_ID"
"${COMPOSE_PROD[@]}" logs --since=15m celery-marketjobs-us
```

Require result `status=SUCCEEDED` and `published=true`. A PARTIAL or FAILED run
is retained for audit and must not replace the last complete pointer. On a
first-ever deployment it means there is no publishable snapshot, so stop launch
acceptance and diagnose provider/validation/history evidence. Live Yahoo
ingestion is **REQUIRES VPS VALIDATION**.

The GitHub workflow `.github/workflows/market-intelligence-yahoo-canary.yml`
runs a separate read-only provider contract check at `23:30 UTC` Monday-Friday.
Confirm it remains enabled after merge. Its first natural scheduled run is
**REQUIRES VPS VALIDATION** as post-deployment operational evidence; it does not
publish application data.

## 13. Verify API, Data Health, and frontend

Authenticate without putting the password on the command line:

```bash
COOKIE_JAR="$(mktemp)"
read -rsp "Server login password: " AUTH_PASSWORD
echo
AUTH_PAYLOAD="$(jq -n --arg password "$AUTH_PASSWORD" '{password:$password}')"
unset AUTH_PASSWORD
curl -fsS -c "$COOKIE_JAR" -H 'Content-Type: application/json' -d "$AUTH_PAYLOAD" "https://${DOMAIN_NAME}/api/v1/auth/login" | jq -e '.authenticated == true'
unset AUTH_PAYLOAD

curl -fsS -b "$COOKIE_JAR" "https://${DOMAIN_NAME}/api/v1/market-intelligence/sectors/latest" | tee /tmp/sectors-latest.json | jq
curl -fsS -b "$COOKIE_JAR" "https://${DOMAIN_NAME}/api/v1/market-intelligence/sectors/history?limit=2" | tee /tmp/sectors-history.json | jq
curl -fsS -b "$COOKIE_JAR" "https://${DOMAIN_NAME}/api/v1/market-intelligence/sectors/health" | tee /tmp/sectors-health.json | jq

jq -e '.status == "SUCCEEDED" and .run_status == "SUCCEEDED" and .benchmark.symbol == "SPY" and (.sectors | length) == 11' /tmp/sectors-latest.json
jq -e '.universe_expected == 12 and .latest_published.status == "SUCCEEDED" and .latest_published.counters.symbols_received == 12 and .latest_published.counters.rejected_bars == 0 and (.latest_published.missing_symbols | length) == 0' /tmp/sectors-health.json
jq -e --slurpfile latest /tmp/sectors-latest.json '.items[0].as_of == $latest[0].as_of' /tmp/sectors-history.json
rm -f "$COOKIE_JAR" /tmp/sectors-latest.json /tmp/sectors-history.json /tmp/sectors-health.json
```

In a browser, log in and open `https://DOMAIN/market-intelligence`; confirm
Today, Sectors, and Data Health render the same as-of date, provider, metric
version, and stable published snapshot. Check `/livez` and `/readyz` internally
as in step 10 and public `/nginx-health` through Caddy. API/frontend production
data rendering is **REQUIRES VPS VALIDATION**.

## 14. Prove restart persistence

Record release and published identities, restart without deleting volumes, then
repeat health/API checks:

```bash
"${COMPOSE_PROD[@]}" exec -T postgres sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atc "select version_num from alembic_version"' | tee /tmp/alembic-before
"${COMPOSE_PROD[@]}" restart
"${COMPOSE_PROD[@]}" ps
"${COMPOSE_PROD[@]}" exec -T postgres sh -c 'pg_isready -U "$POSTGRES_USER" -d "$POSTGRES_DB"'
"${COMPOSE_PROD[@]}" exec -T redis redis-cli ping
"${COMPOSE_PROD[@]}" exec -T backend curl -fsS http://127.0.0.1:8000/readyz
"${COMPOSE_PROD[@]}" exec -T postgres sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atc "select version_num from alembic_version"' | diff - /tmp/alembic-before
rm -f /tmp/alembic-before
```

Re-authenticate and prove the same latest `run_id` and as-of date remain
available. Confirm restart counts stabilize and Beat does not duplicate a run.
Production restart/persistence is **REQUIRES VPS VALIDATION**.

## 15. Verify backup and retention

Create a one-shot custom-format dump using the same service, then validate its
checksum and catalog:

```bash
POSTGRES_BACKUP_RUN_ONCE=1 POSTGRES_BACKUP_INITIAL_DELAY_SECONDS=0 "${COMPOSE_PROD[@]}" run --rm db-backup
LATEST_DUMP="$(find data/backups -maxdepth 1 -type f -name 'stockscanner_*.dump' -print | sort | tail -n 1)"
test -n "$LATEST_DUMP" && test -s "$LATEST_DUMP"
(cd "$(dirname "$LATEST_DUMP")" && sha256sum -c "$(basename "$LATEST_DUMP").sha256")
"${COMPOSE_PROD[@]}" run --rm --no-deps --entrypoint sh db-backup -c 'latest=$(find /app/data/backups -maxdepth 1 -type f -name "stockscanner_*.dump" -print | sort | tail -n 1); pg_restore --list "$latest" >/dev/null'
```

The recurring service retains seven verified dumps by default and only prunes
after a new dump passes validation. Copy the dump, `.sha256`, and current and
previous `release-manifest.env` files to encrypted off-host storage. Confirm a
restore does not depend on this VPS remaining available.

### Disposable restore drill

Pause Beat/workers during a production restore exercise. The following drill
uses a separate database and does not modify the live database:

```bash
"${COMPOSE_PROD[@]}" stop celery-beat celery-general celery-datafetch celery-marketjobs-us celery-userscans celery-userscans-us
"${COMPOSE_PROD[@]}" run --rm --no-deps --entrypoint sh db-backup -c '
  set -eu
  latest=$(find /app/data/backups -maxdepth 1 -type f -name "stockscanner_*.dump" -print | sort | tail -n 1)
  drill="${POSTGRES_DB}_restore_drill"
  dropdb --if-exists --host=postgres --username="$POSTGRES_USER" "$drill"
  createdb --host=postgres --username="$POSTGRES_USER" "$drill"
  pg_restore --exit-on-error --no-owner --no-privileges --host=postgres --username="$POSTGRES_USER" --dbname="$drill" "$latest"
  psql --host=postgres --username="$POSTGRES_USER" --dbname="$drill" -Atc "select version_num from alembic_version"
  dropdb --host=postgres --username="$POSTGRES_USER" "$drill"
'
"${COMPOSE_PROD[@]}" up -d celery-general celery-datafetch celery-marketjobs-us celery-userscans celery-userscans-us celery-beat
```

Inspect restored Market Intelligence run/pointer row counts before dropping the
drill database when conducting the real exercise. Backup/restore drill and
off-host recovery are **REQUIRES VPS VALIDATION**.

After every gate above passes, promote the candidate manifest atomically and
copy it off-host with the dump. For an upgrade, preserve the prior accepted
manifest first:

```bash
if sudo test -s /var/lib/stockscanner/releases/release-manifest.env; then
  sudo cp -p /var/lib/stockscanner/releases/release-manifest.env /var/lib/stockscanner/releases/release-manifest.previous.env
fi
sudo mv /var/lib/stockscanner/releases/release-manifest.candidate.env /var/lib/stockscanner/releases/release-manifest.env
```

## 16. Rollback procedure

Rollback changes application images as a pair; it never silently downgrades the
database.

1. Stop Beat and workers so no new job begins.
2. Capture `ps`, logs, current manifest, health JSON, and a verified database
   backup.
3. Read the previous manifest and decide migration compatibility. If the prior
   image supports the current schema, restore its two digest refs and SHA in
   `.env.docker`. If it does not, restore the matching pre-deploy database backup
   to a new database/host; do not downgrade the live database automatically.
4. Validate, pull, prove both OCI revision labels, run `alembic current`, and
   recreate backend/frontend/workers/Beat/Caddy with the four-file stack.
5. Repeat health, stable snapshot, API, frontend, restart, and backup checks.

```bash
sudo test -s /var/lib/stockscanner/releases/release-manifest.previous.env
sudo cat /var/lib/stockscanner/releases/release-manifest.previous.env
${EDITOR:-vi} .env.docker
python3 backend/scripts/validate_production_deployment.py --env-file .env.docker
"${COMPOSE_PROD[@]}" pull
"${COMPOSE_PROD[@]}" run --rm --no-deps backend alembic current --verbose
"${COMPOSE_PROD[@]}" up -d --no-build
```

The displayed previous manifest contains no secrets, but verify the exact pair
before editing. A failed candidate must not replace the previous accepted
manifest. Rollback execution is **REQUIRES VPS VALIDATION**.

## 17. Disaster recovery to a fresh VPS

1. Provision and harden a fresh Ubuntu 24.04 host using steps 2–3.
2. Check out the Git SHA from the last accepted off-host release manifest.
3. Restore `.env.docker` from a secure secret manager or recreate it from the
   template; never restore a fake/example secret.
4. Authenticate, pull both manifest digests, and verify OCI revision labels.
5. Start only PostgreSQL and Redis. Transfer and checksum the latest off-host
   dump.
6. Stop workers/Beat; restore the dump with `pg_restore --clean --if-exists
   --no-owner --no-privileges` into the configured database.
7. Run `alembic current`; only run `alembic upgrade head` when the selected
   application release and change record require it.
8. Start the remaining services, repeat all health/API/frontend checks, then
   change DNS only after acceptance.
9. Preserve the old host until the rollback window closes.

Fresh-host recovery, restore, and DNS cutover are **REQUIRES VPS VALIDATION**.

## 18. Monitoring and operating cadence

After acceptance, collect Docker JSON logs to the approved destination and
monitor container health/restarts, host load/memory/disk/inodes, PostgreSQL
readiness/connections/storage, Redis PING/AOF/broker health, Celery queue depth
and worker/Beat liveness, `/nginx-health`, internal `/livez` and `/readyz`, TLS
expiry/renewal, Data Health freshness/failure streak, backup age/checksum, and
the scheduled GitHub Yahoo canary. Alert before disk or inode use reaches 80%.

The first 24-hour observation, first natural daily Yahoo run, TLS renewal,
backup retention rotation, and long-term monitoring are **REQUIRES VPS
VALIDATION**.

## Emergency source-build fallback

This is emergency-only, non-primary, and unsuitable for routine releases. Use
it only when GHCR is unavailable and the incident owner approves rebuilding the
exact reviewed `RELEASE_GIT_SHA`. Do not create a second deployment topology.

Build the existing Dockerfiles, record immutable local image IDs, and place
those IDs in an incident-only override; then use the same environment,
PostgreSQL/Redis, migration, worker, Caddy, verification, backup, and rollback
procedure above. Never substitute an unreviewed working tree.

```bash
test "$(git rev-parse HEAD)" = "$RELEASE_SHA"
git status --short
docker build -f backend/Dockerfile -t stockscanner-backend-emergency:"$RELEASE_SHA" .
docker build -f frontend/Dockerfile -t stockscanner-frontend-emergency:"$RELEASE_SHA" frontend
docker image inspect --format '{{.Id}}' stockscanner-backend-emergency:"$RELEASE_SHA"
docker image inspect --format '{{.Id}}' stockscanner-frontend-emergency:"$RELEASE_SHA"
```

This fallback does not change the formal GHCR digest deployment architecture.

## Authoritative platform references

- [Docker Engine on Ubuntu](https://docs.docker.com/engine/install/ubuntu/)
- [Docker Linux post-installation](https://docs.docker.com/engine/install/linux-postinstall/)
- [Docker packet filtering and firewalls](https://docs.docker.com/engine/network/packet-filtering-firewalls/)
- [Docker Compose production guidance](https://docs.docker.com/compose/how-tos/production/)
- [GitHub Container Registry authentication](https://docs.github.com/en/packages/working-with-a-github-packages-registry/working-with-the-container-registry)
- [Caddy automatic HTTPS](https://caddyserver.com/docs/automatic-https)
- [Ubuntu firewall documentation](https://documentation.ubuntu.com/server/how-to/security/firewalls/index.html)
