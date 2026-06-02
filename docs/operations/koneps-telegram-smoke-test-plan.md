# KONEPS + Telegram End-to-End Smoke Test 계획

> **목적**: 실제 KONEPS OpenAPI + 실제 Telegram Bot으로 수집 → 분류 → 가격예측 → 결정 → 알림 전 경로가 운영 상태에서 작동하는지 단일 시점에 확인.
>
> **대상 환경**: 현재 호스트 (`ENVIRONMENT=production`, 실 토큰, 실 service key).

## 1. 현재 상태 (이미 검증된 부분)

| 컴포넌트 | 가동 상태 | 비고 |
|---|---|---|
| KONEPS OpenAPI 수집 | ✅ 매시간 자동 (run_id 421+, 평일 ~49/run) | `koneps-openapi` source, service key 정상 |
| SBERT 분류기 | ✅ 신규 행 임베딩 자동 생성 | `paraphrase-multilingual-MiniLM-L12-v2` |
| business_type_code 보강 | ✅ title-rule fallback로 매 3분 | detail HTML 경로는 KONEPS SPA라 비활성 |
| category 재할당 | ✅ keyword + SBERT prototype | |
| paper_bid forward | ✅ 매일 08:05 UTC | 가드레일 max=1.0 유지 |
| paper_bid historical (settlement) | ✅ 매 24h, 100건/run | within_0_1pct 16%, would_have_won 36% |
| Telegram 설정 | ⚠ 설정값 존재, 송신 미검증 | `TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID` 모두 채워짐 |

→ **smoke 핵심 미검증 부분 = Telegram 실 송신 + 운영자 chat 도착**.

## 2. 검증 목표

다음 5가지가 한 번의 trigger로 모두 동작하는지 확인:

1. KONEPS OpenAPI 1회 동기 호출 → 신규 공고 1건 이상 수신
2. 신규 행이 SBERT 임베딩으로 저장 (`embedding_model != 'fallback-hash-v1'`)
3. 해당 행에 `predict_price` 호출 → 정상 응답 + 가드레일 적용
4. 운영자 결정(`BidDecisionRecord`)이 `priority_score >= TELEGRAM_DECISION_PRIORITY_THRESHOLD (0.78)`으로 생성
5. `OperatorNotificationService.dispatch_bid_decision`이 `TelegramNotificationService.send_message`를 호출 → Telegram API `{"ok": true}` 응답 → 운영자가 메시지 수신

## 3. 사전 조건

- [ ] `.env`에 5종 시크릿 정상값 확인:
  - `KONEPS_OPENAPI_SERVICE_KEY` (현재 길이 64)
  - `TELEGRAM_BOT_TOKEN`
  - `TELEGRAM_CHAT_ID`
  - `TELEGRAM_DECISION_PRIORITY_THRESHOLD` (현재 0.78)
  - `TELEGRAM_DECISION_PROBABILITY_THRESHOLD` (현재 0.80)
- [ ] `ENVIRONMENT=production` (test이면 Telegram 자동 skip)
- [ ] 운영자 (`TELEGRAM_CHAT_ID=5940357912`)가 본 봇을 차단 안 함, `/start` 한 적 있음
- [ ] `BUSINESS_TYPE_ENRICHMENT_SCHEDULE_ENABLED=true` (옵션, 백그라운드만)
- [ ] 컨테이너 `bid_vector_api`/`worker`/`beat` 모두 `healthy`
- [ ] 호스트가 `main` 브랜치에 있음 (`scripts/sync-after-merge.sh` 한 줄)

## 4. 절차

### Phase 1 — KONEPS 1회 호출

```bash
docker exec bid_vector_api python <<'PY'
import warnings; warnings.filterwarnings('ignore')
from app.schemas.schemas import CrawlRequest
from app.services.koneps.collector import KonepsCollectorService

req = CrawlRequest(source="koneps-openapi", execution_mode="live", max_items=20)
result = KonepsCollectorService().collect_notices(req)
print(f"collected={result['collected_count']} source={result['source']}")
for item in result["items"][:3]:
    print(f"  notice={item['notice_number']} base={item['base_amount']} title={item['title'][:60]}")
PY
```

**합격 기준**:
- `collected_count >= 1`
- 각 item에 `notice_number`, `title`, `base_amount`, `source_url` 존재
- HTTP error/timeout 없음

**실패 신호 + 대응**:
- `KONEPS_OPENAPI_SERVICE_KEY` 비어있음 → `.env` 확인
- 응답 키 누락 (`bsnsDivNm`/`prcmBsneSeCd`) → KONEPS API 응답 변경; 별도 트랙
- 4xx/5xx → 서비스 키 만료 또는 rate limit; 1~2시간 후 재시도

### Phase 2 — SBERT 임베딩 검증

```bash
docker exec bid_vector_api python <<'PY'
import warnings; warnings.filterwarnings('ignore')
from sqlalchemy import text
from app.core.database import SessionLocal

db = SessionLocal()
r = db.execute(text("""
SELECT id, title, embedding_model
FROM projects WHERE created_at > now() - interval '10 minute'
ORDER BY id DESC LIMIT 5
""")).fetchall()
for row in r:
    assert "fallback-hash" not in (row[2] or ""), f"id={row[0]} still fallback hash"
    print(f"OK id={row[0]} model={row[2][-30:]}  title={row[1][:50]}")
db.close()
PY
```

**합격 기준**: 모든 신규 행이 `paraphrase-multilingual-MiniLM-L12-v2` 사용.

### Phase 3 — predict_price 검증

신규 수집한 project_id 하나로:

```bash
docker exec bid_vector_api python <<'PY'
import warnings; warnings.filterwarnings('ignore')
from app.core.database import SessionLocal
from app.models.models import Project
from app.ai.business_group import resolve_business_group
from app.ai.price_prediction import predict_price
from app.services.backtest_cutoff import BacktestCutoffService

db = SessionLocal()
project = (
    db.query(Project)
    .filter(Project.budget_estimate > 0)
    .filter(Project.created_at > __import__("datetime").datetime.now(__import__("datetime").timezone.utc)
            .replace(microsecond=0).astimezone(__import__("datetime").timezone.utc)
            - __import__("datetime").timedelta(minutes=30))
    .order_by(Project.id.desc())
    .first()
)
assert project is not None, "no fresh project"

desc = " ".join(p for p in [project.title, project.description or "", project.requirements or ""] if p)
bg = resolve_business_group(project.business_type_code)
cs = BacktestCutoffService()
history = cs.load_price_history_at_cutoff(
    db, category=project.category, agency_name=project.issuing_agency or project.demand_agency,
    cutoff_at=cs.resolve_data_cutoff_at(project, tender_result=None, hours_before_deadline=0),
    exclude_project_id=int(project.id), limit=80, explicit_bid_rate_only=True,
)
pred = predict_price(
    budget=float(project.budget_estimate),
    category=project.category or "other",
    description=desc,
    historical_records=history,
    agency_name=project.issuing_agency or project.demand_agency,
    feedback_calibration=None,
    business_type_code=project.business_type_code,
    business_group=bg,
)
print(f"project={project.id} cat={project.category} bg={bg}")
print(f"  predicted_bid_rate={pred.get('predicted_bid_rate')}")
print(f"  guardrail_applied={pred.get('guardrail_applied')}")
print(f"  predictor_name={pred.get('predictor_name')}")
assert 0.7 <= float(pred.get('predicted_bid_rate') or 0) <= 1.1, "bid_rate out of band"
db.close()
PY
```

**합격 기준**:
- `predictor_name` ∈ {`ensemble_blend`, `historical_statistical`}
- `predicted_bid_rate` ∈ [0.7, 1.1]
- `guardrail_applied=True` (history 빈약 시 정상)

### Phase 4 — Telegram 송신 (직접 API 호출)

가장 단순한 path — `TelegramNotificationService.send_message` 직접:

```bash
docker exec bid_vector_api python <<'PY'
import warnings; warnings.filterwarnings('ignore')
from app.services.notifications.telegram import TelegramNotificationService
from datetime import datetime

svc = TelegramNotificationService()
print("is_configured:", svc.is_configured())

result = svc.send_message(
    f"[smoke-test] bid-vector e2e ping — {datetime.utcnow().isoformat()}Z\n"
    f"수집/분류/예측 경로 정상, Telegram 송신 확인용.\n"
    f"이 메시지에 답장하지 않아도 됩니다."
)
print(result)
assert result.get("sent") is True, result
assert result.get("telegram_message_id"), "no message_id"
PY
```

**합격 기준**:
- `is_configured=True`
- `result.sent=True`, `status="sent"`, `telegram_message_id` 정수
- 운영자 (`TELEGRAM_CHAT_ID=5940357912`) 휴대폰/데스크탑에서 메시지 도착 확인 (사람 검증)

**실패 신호 + 대응**:
- `status="pending_configuration"` → 시크릿 빈 값 또는 placeholder
- `status="skipped_test_environment"` → `ENVIRONMENT=test`로 잘못 설정됨
- `RuntimeError("Telegram API rejected ...")` 400 chat not found → 봇과 `/start` 안 함; 401 → 토큰 만료
- 운영자가 메시지 못 받음 + ok=True → chat_id 잘못된 채로 다른 chat에 송신됨

### Phase 5 — End-to-End 결정 → 알림 (선택, 본 smoke의 최대 보장)

`OperatorNotificationService.dispatch_bid_decision`이 BidDecisionRecord가 threshold를 넘을 때 자동 호출. 합성 record를 만들어 dispatch가 실제 Telegram 송신을 트리거하는지 확인.

```bash
docker exec bid_vector_api python <<'PY'
import warnings; warnings.filterwarnings('ignore')
from app.core.database import SessionLocal
from app.models.models import Project, User, BidDecisionRecord
from app.services.notifications.manager import OperatorNotificationService

db = SessionLocal()
operator = db.query(User).filter(User.username == "operator").first()
project = db.query(Project).filter(Project.budget_estimate > 0).order_by(Project.id.desc()).first()

# 합성 high-priority 결정
record = BidDecisionRecord(
    project_id=project.id,
    operator_id=operator.id,
    pursue_bid=True,
    action="bid_now",
    decision_status="planned",
    recommended_amount=float(project.budget_estimate) * 0.90,
    probability_score=0.85,
    matched_score=0.80,
    priority_score=0.82,  # > TELEGRAM_DECISION_PRIORITY_THRESHOLD (0.78)
    reasoning="smoke-test synthetic — please ignore",
)
db.add(record)
db.commit()
db.refresh(record)
print(f"record_id={record.id}")

svc = OperatorNotificationService()
delivery = svc.dispatch_bid_decision(db, decision_record=record, project=project)
print("dispatch:", delivery)
assert delivery.get("sent") is True, delivery

# 정리 (실제 결정 아니므로 삭제)
db.delete(record)
db.commit()
PY
```

**합격 기준**:
- `dispatch.sent=True`
- 운영자가 "결정/투찰 알림" 카드 메시지 + inline 버튼 수신
- 사후: 합성 record 삭제됨 (`BidDecisionRecord` 정합성)

## 5. 실행 순서

1. 사전 조건 체크리스트 모두 통과
2. Phase 1 → 4 직렬 실행 (각 phase 합격 확인 후 다음)
3. Phase 5는 옵션 — 운영자가 메시지 받을 수 있는 시간대에 진행
4. 모든 phase 통과 시 `docs/operations/smoke-test-log.md`에 실행 일시·결과 기록

## 6. 안전 장치

- **rate limit**: Phase 1 KONEPS 1회만, Phase 5 Telegram 1회만 — 자동 스케줄과 별개 1샷
- **합성 record 삭제**: Phase 5 종료 시 BidDecisionRecord 삭제 (실 결정으로 오인 방지)
- **운영자 사전 공지**: smoke test 시작 전 운영자에게 "잠시 Telegram 1~2건 도착함" 알림
- **`.env` 노출 금지**: 본 문서엔 시크릿 값을 절대 기록 안 함

## 7. 자동화 후보 (별도 트랙)

본 smoke를 매주 1회 자동화하려면:
- `scripts/smoke_test_koneps_telegram.py` 신규 — Phase 1~4를 한 번에 실행, 결과를 stdout + 로그 파일에 기록
- CI 또는 cron으로 트리거 (예: 월요일 09:00 KST)
- 실패 시 운영자에게 Telegram 알림 자동 발송 (재귀 문제 없음 — 본 smoke 자체가 Telegram 검증)

## 8. 부록: 관련 코드 위치

| 컴포넌트 | 파일 |
|---|---|
| KONEPS collector | `app/services/koneps/collector.py::KonepsCollectorService.collect_notices` |
| SBERT 분류 | `app/services/project_similarity.py::_embed_text` |
| predict_price | `app/ai/price_prediction.py::predict_price` |
| Telegram service | `app/services/notifications/telegram.py::TelegramNotificationService.send_message` |
| Notification manager | `app/services/notifications/manager.py::OperatorNotificationService.dispatch_bid_decision` |
| Settings | `app/core/config.py::Settings.TELEGRAM_*` / `KONEPS_OPENAPI_*` |

## 9. 합격 시 후속

- 본 smoke 통과 = production 운영 사이클의 외부 의존성(KONEPS + Telegram) 양쪽 다 정상
- 다음 단계: 운영자 대시보드에서 결정 카드 → "투찰 진행" 버튼이 실제 `BidDecisionRecord.action='submitted'` 갱신 + Telegram inline 콜백 처리까지 — 본 smoke의 Phase 6 (별 트랙).
