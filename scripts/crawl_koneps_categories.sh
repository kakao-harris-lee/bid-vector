#!/usr/bin/env bash
set -euo pipefail

# Simple runner for KONEPS OpenAPI crawl by service/construction categories.
# - Includes: general-service, technical-service, construction
# - Excludes: goods (납품/물품)

BASE_URL="${BASE_URL:-http://localhost:8000}"
SOURCE="${SOURCE:-koneps-openapi}"
TARGET_DATE="${1:-$(date +%F)}"
MAX_ITEMS="${2:-100}"
DB_SERVICE="${DB_SERVICE:-db}"
DB_USER="${DB_USER:-biduser}"
DB_NAME="${DB_NAME:-bid_vector_db}"

CATEGORIES=(
  "general-service"
  "technical-service"
  "construction"
)

printf "[crawl] base_url=%s source=%s target_date=%s max_items=%s\n" \
  "$BASE_URL" "$SOURCE" "$TARGET_DATE" "$MAX_ITEMS"

overall_failed=0

for category in "${CATEGORIES[@]}"; do
  printf "\n==> category=%s\n" "$category"

  payload=$(cat <<JSON
{"source":"$SOURCE","category":"$category","target_date":"$TARGET_DATE","max_items":$MAX_ITEMS}
JSON
)

  response_file=$(mktemp)
  http_code=$(curl -sS -o "$response_file" -w "%{http_code}" \
    -X POST "$BASE_URL/api/v1/operations/crawl" \
    -H "Content-Type: application/json" \
    -d "$payload")

  cat "$response_file"
  echo

  if [[ "$http_code" -lt 200 || "$http_code" -ge 300 ]]; then
    printf "[error] category=%s http_status=%s\n" "$category" "$http_code" >&2
    overall_failed=1
  else
    printf "[ok] category=%s http_status=%s\n" "$category" "$http_code"
  fi

  rm -f "$response_file"
done

if [[ "$overall_failed" -ne 0 ]]; then
  echo "\n[crawl] completed with errors"
  exit 1
fi

echo "\n[crawl] completed successfully"

echo "\n[crawl] latest DB summary"
docker compose exec -T "$DB_SERVICE" psql -U "$DB_USER" -d "$DB_NAME" -c "
select id, source, target_date, status, result_count, completed_at
from crawl_jobs
order by id desc
limit 10;

select category, count(*)
from historical_data
group by category
order by count(*) desc;

select category, count(*)
from projects
group by category
order by count(*) desc;
"
