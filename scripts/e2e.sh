#!/usr/bin/env bash
# Run the Playwright suite against a fresh Hearth deployment.
#
#   scripts/e2e.sh                 build image, start container, run all specs
#   SKIP_BUILD=1 scripts/e2e.sh    reuse the existing hearth:e2e image
#   scripts/e2e.sh video           run only the named spec(s)
#
# Hearth is two processes on one database (docker/compose.yaml): `hearth api`
# serves HTTP, `hearth worker` runs scans and every other background job. The
# API enqueues; only the worker completes, so a single-container run would
# leave every scan pending forever.
#
# Both containers share a host directory for /config (SQLite + migrations).
# web/e2e/fixtures/media is mounted read-only at /media, which is where the
# specs expect the scanned libraries to live.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
IMAGE="${HEARTH_IMAGE:-hearth:e2e}"
FIXTURES="$REPO_ROOT/web/e2e/fixtures/media"
SUFFIX="$$"
API_CONTAINER="hearth-e2e-api-$SUFFIX"
WORKER_CONTAINER="hearth-e2e-worker-$SUFFIX"
SECRET_KEY="e2e-only-secret-key-0123456789abcdef"

# /config must be a real shared directory: SQLite needs a filesystem that both
# containers can write, so a tmpfs per container would give each its own empty
# database. It is world-writable because the container runs as uid 1000 while
# the host user may not (CI runners are uid 1001); a 0700 mktemp dir would
# make the migration lock fail with EACCES.
CONFIG_DIR="$(mktemp -d "${TMPDIR:-/tmp}/hearth-e2e-config.XXXXXX")"
chmod 0777 "$CONFIG_DIR"

log() { echo "e2e: $*" >&2; }

_cleanup() {
	local status=$?
	if [ "$status" -ne 0 ]; then
		log "failed (exit $status); api logs:"
		docker logs "$API_CONTAINER" >&2 || true
		log "worker logs:"
		docker logs "$WORKER_CONTAINER" >&2 || true
	fi
	docker rm -f "$API_CONTAINER" "$WORKER_CONTAINER" >/dev/null 2>&1 || true
	rm -rf "$CONFIG_DIR"
	exit "$status"
}
trap _cleanup EXIT

if [ "${SKIP_BUILD:-0}" != "1" ]; then
	log "building $IMAGE"
	docker build -q -f "$REPO_ROOT/docker/Dockerfile" -t "$IMAGE" "$REPO_ROOT" >/dev/null
fi

common_args=(
	--read-only
	--tmpfs "/transcode:size=256m,mode=1777"
	--tmpfs "/tmp:size=64m"
	-v "$CONFIG_DIR:/config"
	-v "$FIXTURES:/media:ro"
	-e "HEARTH_SECRET_KEY=$SECRET_KEY"
	-e HEARTH_LOG_LEVEL=WARNING
)

log "starting worker $WORKER_CONTAINER"
docker run -d --name "$WORKER_CONTAINER" "${common_args[@]}" "$IMAGE" hearth worker >/dev/null

log "starting api $API_CONTAINER"
docker run -d --name "$API_CONTAINER" "${common_args[@]}" -p "127.0.0.1::8000" "$IMAGE" >/dev/null

published="$(docker port "$API_CONTAINER" 8000/tcp | head -n 1)"
BASE_URL="http://$published"
log "waiting for $BASE_URL"
for _ in $(seq 1 90); do
	if curl -fsS "$BASE_URL/api/v1/health" >/dev/null 2>&1; then
		break
	fi
	sleep 1
done
curl -fsS "$BASE_URL/api/v1/health" >/dev/null || {
	log "server never became healthy"
	exit 1
}

cd "$REPO_ROOT/web"
if [ "$#" -gt 0 ]; then
	HEARTH_E2E_BASE_URL="$BASE_URL" npx playwright test "$@"
else
	# The first-run spec needs the pristine server, so it always goes first;
	# the rest seed through the API and are order-independent.
	log "running first-run acceptance spec"
	HEARTH_E2E_BASE_URL="$BASE_URL" npx playwright test first-run
	log "running remaining specs"
	HEARTH_E2E_BASE_URL="$BASE_URL" npx playwright test auth books music video
fi
log "all specs passed"
