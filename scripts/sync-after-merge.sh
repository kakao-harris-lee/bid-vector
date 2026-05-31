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
for svc in "${services[@]}"; do
    case "$svc" in
        worker|ml-worker|training-worker)
            container="bid_vector_${svc//-/_}"
            if docker logs --tail 20 "$container" 2>&1 | grep -q "celery@.* ready"; then
                echo "[sync]   $container  ready"
            else
                echo "[sync]   $container  NOT ready (check 'docker logs $container')"
            fi
            ;;
        beat)
            if docker logs --tail 20 bid_vector_beat 2>&1 | grep -q "beat: Starting"; then
                echo "[sync]   bid_vector_beat  ready"
            else
                echo "[sync]   bid_vector_beat  NOT ready"
            fi
            ;;
        api)
            if docker ps --filter "name=bid_vector_api" --filter "health=healthy" --format '{{.Names}}' | grep -q bid_vector_api; then
                echo "[sync]   bid_vector_api   healthy"
            else
                echo "[sync]   bid_vector_api   not yet healthy (check 'docker logs bid_vector_api')"
            fi
            ;;
        *)
            echo "[sync]   $svc  (no health probe defined)"
            ;;
    esac
done

echo "[sync] done"
