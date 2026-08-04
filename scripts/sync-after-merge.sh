#!/usr/bin/env bash
#
# Sync host + containers after a PR merge.
#
# Problem this guards against: containers volume-mount ./:/app, so they execute
# whichever branch the host has checked out. Merging a fix to main does NOT
# deploy it if the host is parked on a feature branch.
#
# Services are recreated (up -d --force-recreate), not restarted, because a
# restart keeps the container's original command and environment — compose
# changes such as a worker's --queues or --concurrency would silently not apply.
#
# Usage:
#   scripts/sync-after-merge.sh                     # recreate all task services
#   scripts/sync-after-merge.sh api                 # recreate only api
#   scripts/sync-after-merge.sh worker beat         # recreate specific services
#
# Steps:
#   1. Verify (or perform) checkout to main
#   2. Fast-forward pull from origin
#   3. Stop beat, recreate runtime services, and wait for readiness
#   4. Gate on exact queue topology + scheduled task registration, then
#      recreate beat last (see scripts/_sync_queue_check.py)

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

services=("$@")
if [ "${#services[@]}" -eq 0 ]; then
    services=(api worker inference-worker ml-worker training-worker beat)
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

runtime_services=()
start_beat=0
for svc in "${services[@]}"; do
    if [ "$svc" = "beat" ]; then
        start_beat=1
    else
        runtime_services+=("$svc")
    fi
done

if [ "$start_beat" -eq 1 ]; then
    echo "[sync] stopping beat until workers pass registry gates"
    docker compose --profile tasks stop beat
fi
if [ "${#runtime_services[@]}" -gt 0 ]; then
    echo "[sync] recreating runtime services: ${runtime_services[*]}"
    docker compose --profile tasks up -d --force-recreate "${runtime_services[@]}"
fi

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
for svc in "${runtime_services[@]}"; do
    case "$svc" in
        worker|inference-worker|ml-worker|training-worker)
            container="bid_vector_${svc//-/_}"
            wait_for_ready "$container" "celery@.* ready" 60 || overall_ok=1
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
    echo "[sync] runtime readiness failed; beat remains stopped"
    exit 1
fi

# Gate beat on the live cluster matching the declared per-worker queue topology
# and having every beat-scheduled task registered on its routed queue's
# consumers. Expectations come from settings + beat schedule, never from a list
# duplicated here; scripts/_sync_queue_check.py holds the checks.
verify_worker_registry() {
    local registered_json active_queues_json
    registered_json="$(docker compose --profile tasks exec -T worker \
        celery -A app.tasks.celery_app.celery_app inspect registered \
        --timeout=10 --json)"
    active_queues_json="$(docker compose --profile tasks exec -T worker \
        celery -A app.tasks.celery_app.celery_app inspect active_queues \
        --timeout=10 --json)"
    # celery exits non-zero with empty stdout when no node replies in time.
    if [ -z "$registered_json" ] || [ -z "$active_queues_json" ]; then
        echo "[sync] no worker replied to celery inspect within the timeout" >&2
        return 1
    fi
    printf '{"registered": %s, "active_queues": %s}' \
        "$registered_json" "$active_queues_json" \
        | docker compose --profile tasks exec -T worker \
            python -m scripts._sync_queue_check || return 1
    echo "[sync] worker registry and queue gates passed"
}

if [ "$start_beat" -eq 1 ]; then
    verify_worker_registry || {
        echo "[sync] registry gate failed; beat remains stopped" >&2
        exit 1
    }
    echo "[sync] recreating beat last"
    docker compose --profile tasks up -d --force-recreate beat
    wait_for_ready "bid_vector_beat" "beat: Starting" 60 || {
        echo "[sync] beat failed readiness" >&2
        exit 1
    }
fi
echo "[sync] done"
