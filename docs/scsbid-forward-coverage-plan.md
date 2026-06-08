# scsbid 개찰결과 수집 커버리지 — forward 정산 데이터 해자

> 2026-06-08 진단 결과 기반 설계. 관련: `app/services/koneps/collector.py`,
> `app/services/paper_bidding_backtest.py`(정산, PR #77로 starvation 해소),
> `app/tasks/celery_app.py`(beat), `app/core/config.py`.

## 1. 배경 / 문제

forward paper bid는 마감 후 개찰결과(`TenderResult.winning_amount`)가 붙어야 정산된다.
2026-06-08 기준 forward-bid project 210개 중 usable `낙찰` 결과 보유는 **2개(0.95%)**뿐 —
정산 throughput의 진짜 병목.

원인: scsbid 개찰결과 수집(`_collect_scsbid_openapi_items`)이 **일반 피드 수집**이라 우리 공고를 못 잡는다.
- 빈 카테고리 → operation `getScsbidListSttusServc`(**용역 전용**) 하나만 조회 → construction/goods forward 공고 영구 미수집.
- `inqryDiv=1` + **단일 날짜(기본 오늘)** + `pageNo=1` + `max_items=50` → 그날 용역 피드 상위 50건만.
- 실측 collected_count 4~14건/run. construction 피드는 5일치만 totalCount 2057(≈400/일)인데 극소 커버.

## 2. 진단으로 확정된 제약 (설계 전제)

- **API 표적 조회 불가**: award 오퍼레이션(`getScsbidListSttus*`)은 `bidNtceNo` 필터를 무시(요청 공고와 무관한 결과 반환)하고 `inqryDiv=2`+공고번호는 `resultCode 08 필수값 에러`. → **날짜창(`inqryDiv=1` + `inqryBgnDt/inqryEndDt`) 피드 + 클라이언트단 매칭**만 가능.
- **개찰일시 미저장**: `Project`엔 `deadline`(=입찰마감 bidClseDt)만 있고 개찰일시(opengDt)는 메타로만 파싱 후 버림 → 좁은 정밀 날짜창 불가, deadline 프록시로 넓은 창 필요.
- **winning_amount = `sucsfbidAmt` 직접값**(collector.py:828). reserve-detail(복수예비가격)은 보조 enrich일 뿐 정산에 **불필요** → 대량 backfill은 reserve-detail OFF로 호출량 수천→수십.
- **적재 정책 = 전체 award 적재(코퍼스)** — 사용자 결정. 피드의 모든 낙찰을 persist(우리 forward 공고도 자연 포함, historical 백테스트/predictor 코퍼스 성장).

## 3. 설계

### 3.1 스키마 (`app/schemas/schemas.py::CrawlRequest`) — 하위호환 유지, 옵션 추가
- `categories: Optional[list[str]] = None` — scsbid 다중 카테고리. None이면 기존 `category` 단일 동작.
- `start_date: Optional[str] = None`, `end_date: Optional[str] = None` — 날짜 범위(YYYYMMDD 또는 ISO). 없으면 `lookback_days`/`target_date`로 유도.
- `lookback_days: Optional[int] = None` — end=today 기준 롤링 창.
- `page_size: Optional[int] = None` — 페이지당 numOfRows(≤999). 기본 100.
- `max_pages: Optional[int] = None` — 카테고리당 안전 페이지 상한. 기본 30.
- `collect_reserve_detail: bool = True` — False면 per-item reserve-detail 호출 skip.
- `max_items`(기존 le=100)는 페이지네이션과 무관해짐 — 페이지네이션은 collector 내부에서 totalCount까지.

### 3.2 collector (`_collect_scsbid_openapi_items` 리팩토링)
- **카테고리 리스트 해석**: `request.categories` > `[request.category]` > settings 기본 리스트. 각 카테고리 → `_scsbid_operation_for_category`.
- **날짜창 해석**: `start_date/end_date` > `lookback_days`(end=오늘) > `target_date`(단일일, 기존). `inqryBgnDt={start}0000`, `inqryEndDt={end}2359`.
- **페이지네이션**: 카테고리별 `pageNo=1..max_pages`, `numOfRows=page_size`. `totalCount` 도달 또는 빈 페이지면 중단.
- **reserve-detail 토글**: `collect_reserve_detail=False`면 line ~709 호출 skip(detail={}).
- **dedupe**: 전체 run에서 `notice_number` 기준 1회.
- **throttling**: API 호출 간 짧은 sleep(설정값, 기본 ~0.2s) — CLAUDE.md §9 과도요청 금지 준수.
- 반환 metadata에 per-category totalCount/page 수, 호출 수, reserve_detail 여부 기록.

### 3.3 적재
- 기존 persist 경로(`_resolve_project_for_item` → `TenderResult` upsert)는 notice_number로 기존 project에 매칭(중복 notice 없음 확인). 우리 forward 공고 award는 **기존 project에 winning_amount 채움** → (PR #77로 고친) 정산 sweep이 다음 주기에 정산. 매칭 안 되는 award는 신규 project 생성(코퍼스).

### 3.4 스케줄 (`build_scsbid_collection_beat_schedule`)
- request_payload에 `categories`(설정 리스트), `lookback_days`(설정), `page_size`, `max_pages`, `collect_reserve_detail=True` 전달.
- 롤링 창(기본 3일)로 매일 전 카테고리 개찰을 커버.

### 3.5 일회성 backfill 스크립트 `scripts/backfill_scsbid_awards.py`
- args: `--start --end --categories --page-size --max-pages --no-reserve-detail --execution-mode live`.
- collector를 직접 구동(또는 task eager) → persist → 요약 출력(카테고리별 수집/매칭/신규 수).
- 외부 호출 다량 → 사용자 승인 후 수동 실행. 작게(예 2일·1카테고리) 검증 후 전체.

### 3.6 config (`app/core/config.py`) 추가
- `KONEPS_SCSBID_COLLECTION_CATEGORIES: str = "construction,service,goods"` (csv)
- `KONEPS_SCSBID_COLLECTION_LOOKBACK_DAYS: int = 3`
- `KONEPS_SCSBID_COLLECTION_PAGE_SIZE: int = 100`
- `KONEPS_SCSBID_COLLECTION_MAX_PAGES: int = 30`
- `KONEPS_SCSBID_COLLECTION_RESERVE_DETAIL: bool = True`

## 4. 테스트 (mock HTTP, 기존 패턴 재사용)
- 다중 카테고리 순회(operation별 호출 검증).
- 페이지네이션: totalCount > page_size → 다중 페이지 수집, totalCount 도달 시 중단.
- 날짜창 파라미터 빌드(start/end, lookback_days, target_date 각 경로).
- reserve-detail 토글(False면 detail op 미호출).
- notice_number dedupe.
- award가 기존 forward project에 매칭되면 그 project에 winning_amount>0 `TenderResult` 부착(신규 project 미생성).
- 기존 단일일·단일카테고리 동작 회귀(하위호환).

## 5. 비기능 / 안전
- rate-limit: 호출 간 sleep, max_pages 상한, `ENVIRONMENT=test`에서 외부 호출 금지(기존 가드 유지).
- 시크릿(`KONEPS_OPENAPI_SERVICE_KEY`) 코드 노출 금지.
- 정산은 PR #77(starvation fix) 머지 + worker 재시작 후 자동 적용. backfill 직후엔 수동 `run_forward_settlement(limit 충분히 크게)`로 즉시 정산 가능.

## 6. 한계
- `re_notice`(원공고 낙찰 없음)·취소·PQ미개찰 공고는 수집 개선과 무관하게 영구/일시 미정산.
- 개찰일시 미저장이라 deadline 프록시 넓은 창 필요 → 호출량↑. 추후 개찰일시 컬럼 추가 시 정밀화 가능(후속).
