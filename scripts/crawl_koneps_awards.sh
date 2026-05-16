#!/usr/bin/env bash
set -euo pipefail

# Backfill KONEPS ScsbidInfoService award/opening-result rows.
# Defaults intentionally skip goods/foreign rows to match the current service focus.

BASE_URL="${BASE_URL:-http://localhost:8000}"
SOURCE="${SOURCE:-koneps-scsbid}"
START_DATE="${1:-$(date +%F)}"
END_DATE="${2:-$START_DATE}"
MAX_ITEMS="${3:-100}"
DB_SERVICE="${DB_SERVICE:-db}"
DB_USER="${DB_USER:-biduser}"
DB_NAME="${DB_NAME:-bid_vector_db}"
VERBOSE="${VERBOSE:-false}"

CATEGORIES=(
  "construction"
  "service"
)

date_to_epoch() {
  if date -j -f "%Y-%m-%d" "$1" "+%s" >/dev/null 2>&1; then
    date -j -f "%Y-%m-%d" "$1" "+%s"
  else
    date -d "$1" "+%s"
  fi
}

epoch_to_date() {
  if date -r "$1" "+%F" >/dev/null 2>&1; then
    date -r "$1" "+%F"
  else
    date -d "@$1" "+%F"
  fi
}

start_epoch="$(date_to_epoch "$START_DATE")"
end_epoch="$(date_to_epoch "$END_DATE")"
if (( start_epoch > end_epoch )); then
  echo "[error] START_DATE must be before or equal to END_DATE" >&2
  exit 1
fi

printf "[awards] base_url=%s source=%s start=%s end=%s max_items=%s\n" \
  "$BASE_URL" "$SOURCE" "$START_DATE" "$END_DATE" "$MAX_ITEMS"

overall_failed=0
current_epoch="$start_epoch"

while (( current_epoch <= end_epoch )); do
  target_date="$(epoch_to_date "$current_epoch")"
  for category in "${CATEGORIES[@]}"; do
    printf "\n==> date=%s category=%s\n" "$target_date" "$category"

    payload=$(printf '{"source":"%s","category":"%s","target_date":"%s","max_items":%s}' \
      "$SOURCE" "$category" "$target_date" "$MAX_ITEMS")

    response_file="$(mktemp)"
    http_code="$(curl -sS -o "$response_file" -w "%{http_code}" \
      -X POST "$BASE_URL/api/v1/operations/crawl" \
      -H "Content-Type: application/json" \
      -d "$payload")"

    if [[ "$VERBOSE" == "true" ]]; then
      cat "$response_file"
      echo
    else
      python3 - "$response_file" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as f:
    payload = json.load(f)

metadata = payload.get("metadata") if isinstance(payload, dict) else {}
print(
    "[summary] "
    f"job_status={payload.get('job_status')} "
    f"collected_count={payload.get('collected_count')} "
    f"openapi_total_count={metadata.get('openapi_total_count')} "
    f"reserve_detail_collected_count={metadata.get('reserve_detail_collected_count')} "
    f"reserve_detail_error_count={metadata.get('reserve_detail_error_count')}"
)
PY
    fi

    if [[ "$http_code" -lt 200 || "$http_code" -ge 300 ]]; then
      printf "[error] date=%s category=%s http_status=%s\n" \
        "$target_date" "$category" "$http_code" >&2
      overall_failed=1
    else
      printf "[ok] date=%s category=%s http_status=%s\n" \
        "$target_date" "$category" "$http_code"
    fi

    rm -f "$response_file"
  done
  current_epoch=$((current_epoch + 86400))
done

if [[ "$overall_failed" -ne 0 ]]; then
  echo "\n[awards] completed with errors"
  exit 1
fi

echo "\n[awards] completed successfully"

echo "\n[awards] latest DB summary"
docker compose exec -T "$DB_SERVICE" psql -U "$DB_USER" -d "$DB_NAME" -c "
select id, source, target_date, status, result_count, completed_at
from crawl_jobs
order by id desc
limit 10;

select
  category,
  count(*) as samples,
  count(*) filter (where bid_rate > 0) as bid_rate_samples,
  count(*) filter (where reserve_prices is not null and reserve_prices not in ('[]', '')) as reserve_samples,
  count(*) filter (where selected_numbers is not null and selected_numbers not in ('[]', '')) as selected_samples
from historical_data
group by category
order by count(*) desc;

select result_status, count(*)
from tender_results
group by result_status
order by count(*) desc;
"
