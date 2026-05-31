#!/usr/bin/env bash
#
# Sync host + containers after a PR merge.
#
# Problem this guards against: containers volume-mount ./:/app, so they execute
# whichever branch the host has checked out. Merging a fix to main does NOT
# deploy it if the host is parked on a feature branch.
#
# Usage:
#   scripts/sync-after-merge.sh                     # restart all task services
#   scripts/sync-after-merge.sh api                 # restart only api
#   scripts/sync-after-merge.sh worker beat         # restart specific services
#
# Steps:
#   1. Verify (or perform) checkout to main
#   2. Fast-forward pull from origin
#   3. Restart the specified compose services (default: api worker beat)
#   4. Spot-check that the latest main commit's hash shows up in container logs

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

services=("$@")
if [ "${#services[@]}" -eq 0 ]; then
    services=(api worker beat)
fi

current_branch="$(git branch --show-current)"
if [ "$current_branch" != "main" ]; then
    echo "[sync] host is on '$current_branch' — switching to main"
    if ! git diff --quiet || ! git diff --cached --quiet; then
        echo "[sync] ERROR: uncommitted changes on '$current_branch'. Commit or stash first." >&2
        exit 1
    fi
    git checkout main
fi

echo "[sync] pulling main"
git pull --rebase origin main

latest_sha="$(git rev-parse --short HEAD)"
latest_subject="$(git log -1 --format='%s')"
echo "[sync] main HEAD: $latest_sha  '$latest_subject'"

echo "[sync] restarting services: ${services[*]}"
docker compose --profile tasks restart "${services[@]}"

echo "[sync] waiting for services to become ready"
sleep 3

# Poll a single container until it reports ready (or timeout).
#   $1: container name
#   $2: probe — either "health" (Docker healthcheck) or a grep pattern against logs
#   $3: timeout seconds (default 60)
wait_for_ready() {
    local container="$1"
    local probe="$2"
    local timeout="${3:-60}"
    local elapsed=0
    while [ "$elapsed" -lt "$timeout" ]; do
        if [ "$probe" = "health" ]; then
            local status
            status="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "$container" 2>/dev/null || echo "missing")"
            case "$status" in
                healthy)  echo "[sync]   $container   healthy (${elapsed}s)"; return 0 ;;
                unhealthy) echo "[sync]   $container   UNHEALTHY — abort"; return 1 ;;
                none)     echo "[sync]   $container   running (no healthcheck defined)"; return 0 ;;
            esac
        else
            if docker logs --tail 30 "$container" 2>&1 | grep -q "$probe"; then
                echo "[sync]   $container  ready (${elapsed}s)"
                return 0
            fi
        fi
        sleep 2
        elapsed=$((elapsed + 2))
    done
    echo "[sync]   $container  NOT ready after ${timeout}s (check 'docker logs $container')"
    return 1
}

overall_ok=0
for svc in "${services[@]}"; do
    case "$svc" in
        worker|ml-worker|training-worker)
            container="bid_vector_${svc//-/_}"
            wait_for_ready "$container" "celery@.* ready" 60 || overall_ok=1
            ;;
        beat)
            wait_for_ready "bid_vector_beat" "beat: Starting" 60 || overall_ok=1
            ;;
        api)
            wait_for_ready "bid_vector_api" "health" 90 || overall_ok=1
            ;;
        *)
            echo "[sync]   $svc  (no health probe defined)"
            ;;
    esac
done

if [ "$overall_ok" -ne 0 ]; then
    echo "[sync] done WITH WARNINGS"
    exit 1
fi
echo "[sync] done"
