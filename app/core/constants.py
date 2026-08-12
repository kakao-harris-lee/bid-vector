"""Tier 0 shared domain constants.

Single source of truth for small, cross-module domain vocabularies that were
previously duplicated as literals across services, routers and scripts. A
vocabulary may be exported in two shapes: a ``Literal`` **type alias** for pydantic
field annotations, and a **value set** (tuple/frozenset) for runtime membership
tests, defaults and CLI ``choices``. Keeping the exact same string values here
preserves runtime behavior; this module exists only to remove the duplication, not
to change any semantics.
"""

from __future__ import annotations

from typing import Literal, get_args

# The full action vocabulary the decision-gate ladder
# (``app/services/allocation_core.py``) can produce for one notice: bid now,
# review manually, or skip. The same three strings also form the ``settle_actions``
# filter of the paper-bidding / synthetic backtests (which actions get settled).
#
# Declared ONCE as a ``Literal`` so it can be used directly as a pydantic field
# annotation, plus a value tuple (declaration order preserved) for runtime
# membership tests and CLI choices.
#
# Shared by (previously each re-declared the same inline Literal):
#   - app/schemas/paper_bidding_items.py   (PaperBidAction re-export)
#   - app/schemas/task_payloads.py         (settle_actions task payloads)
#   - app/schemas/paper_bidding.py         (PaperBiddingRunRequest.settle_actions)
#   - app/schemas/synthetic.py             (SyntheticExperimentParams.settle_actions)
#   - app/schemas/{accuracy,dashboard,decision,operator,operator_strategy,
#                  opportunity,telegram}.py  (action / initial_action fields)
#   - app/api/synthetic.py                 (SyntheticBacktestRunRequest.settle_actions)
#   - app/services/synthetic_experiment/sample_gap.py (legacy value normalization)
#   - app/services/dashboard_summary/normalizers.py   (_normalize_action)
#
# NOTE: this is the *recommendation* vocabulary. Three neighbours look similar but
# are NOT the same set — do not merge them:
#   - the operator's submit-time vocabulary ``{"submit", "review", "skip"}``
#     (``BidDecisionActionRequest.action`` in app/schemas/opportunity.py);
#   - the 4-value ``decision_status`` workflow vocabulary (:data:`DecisionStatus`
#     below) — a *lifecycle* state, not a recommendation;
#   - ``PaperBidDecisionStatus`` in app/schemas/paper_bidding_items.py, which is the
#     3-value set ``{"planned", "reviewing", "skipped"}`` that
#     ``_decision_status_for_action`` can produce and therefore has NO "submitted".
PaperBidAction = Literal["bid_now", "review", "skip"]
PAPER_BID_ACTIONS: tuple[PaperBidAction, ...] = get_args(PaperBidAction)

# SQLAlchemy drivername of the only database target this deployment supports.
# The composer that builds ``DATABASE_URL`` from split ``DATABASE_*`` settings
# (app/core/config.py) and the check that rejects a ``DATABASE_URL`` disagreeing
# with those settings (app/services/database_target.py) MUST use one value: if
# they drift, the deployment gate compares against a driver nobody composes.
POSTGRES_DRIVERNAME = "postgresql+psycopg"

# ``BidDecisionRecord.decision_status`` 워크플로 어휘 — 한 결정 레코드가 지나는 상(相)
# 전체다. 운영자가 계획(``planned``)에서 검토(``reviewing``)를 거쳐 투찰(``submitted``)
# 하거나 포기(``skipped``)한다. 액션(:data:`PaperBidAction`) 은 "무엇을 권고했는가",
# 이 어휘는 "그 뒤 운영자가 어디까지 진행했는가"라 서로 다른 축이다.
#
# 종전에는 같은 4값 ``Literal`` 을 스키마 10곳 · 라우터 2곳 · 서비스 상수 1곳이 각각 다시
# 선언했다(§4.5-1 위반). 값 하나를 늘리려면 13곳을 동시에 고쳐야 했고, 한 곳만 빠지면
# 그 경계에서만 422 가 나는 조용한 어긋남이 됐다. 여기서 한 번 선언하고 pydantic 필드
# 주석(별칭) · 런타임 멤버십/쿼리 패턴(값 튜플)으로 나눠 쓴다.
#
# 공유 소비처(종전 각자 인라인 재선언):
#   - app/schemas/{accuracy,bid,dashboard,decision,operator,operator_strategy,
#                  opportunity,telegram}.py  (decision_status / initial_ / current_)
#   - app/api/operations.py                  (BidDecisionStatusUpdateRequest)
#   - app/api/dashboard.py                   (status 쿼리 pattern — 값 튜플로 조립)
#   - app/services/dashboard_summary/constants.py (_OPPORTUNITY_STATUSES)
#
# NOTE: 부분집합 두 개와 혼동하지 않는다 — :data:`ACTIVE_DECISION_STATUSES`
# (``{"planned", "reviewing"}``: 아직 열려 있는 건)와 ``PaperBidDecisionStatus``
# (``submitted`` 가 없는 3값: 자동 생성기가 낼 수 있는 상태).
DecisionStatus = Literal["planned", "reviewing", "submitted", "skipped"]
DECISION_STATUSES: tuple[DecisionStatus, ...] = get_args(DecisionStatus)

# Price-stance scenario vocabulary — how conservatively one bid amount is derived.
# The predictors emit one entry per label (``PricePredictionScenario.label``) and a
# paper-bidding / backtest run selects ONE of those labels by string match
# (``app/services/paper_bidding_backtest/scoring.py::_select_scenario``), so the
# request field and the predictor label MUST share one vocabulary — they were
# previously declared independently in five places.
#
# Shared by:
#   - app/schemas/prediction.py        (PricePredictionScenario.label)
#   - app/schemas/paper_bidding.py     ({Forward,}PaperBiddingRunRequest.scenario)
#   - app/schemas/task_payloads.py     (HistoricalBacktestTaskRequest.scenario)
#   - app/tasks/celery_app.py          (beat schedule scenario validation)
#   - app/services/paper_bidding_scheduler.py (in-process scheduler validation)
#   - scripts/backtest_paper_bidding.py · scripts/backtest_synthetic_operators.py
#     (argparse ``--scenario`` choices)
#
# NOTE: persisted run records (``PaperBidRun.scenario``) and stored experiment
# params keep a plain ``str`` on purpose — they must be able to read back runs
# recorded before this vocabulary was fixed.
PriceScenario = Literal["conservative", "base", "aggressive"]
PRICE_SCENARIOS: tuple[PriceScenario, ...] = get_args(PriceScenario)

# Decision statuses that represent an *open / actionable* bid opportunity for
# the single operator. A record in one of these statuses has been planned or is
# under review but has not yet been submitted or skipped.
#
# This set is shared by:
#   - app/services/allocation.py           (BidDecisionService.ACTIVE_DECISION_STATUSES)
#   - app/services/decision_analytics.py   (DecisionAnalyticsService.ACTIVE_DECISION_STATUSES)
#   - app/services/opportunity_analysis.py (OpportunityAnalysisService.ACTIVE_DECISION_STATUSES)
#   - app/api/dashboard.py                 (_ACTIVE_OPPORTUNITY_STATUSES)
#   - app/api/operator.py                  (active_decision_count .in_() filter)
#
# NOTE: This is a strict subset of :data:`DECISION_STATUSES` (the full workflow
# vocabulary) and is intentionally distinct from experiment lifecycle statuses
# such as {"planned", "running"} used in app/services/decision_experiments.py —
# do not merge those two; "running" is not a bid-decision status.
ACTIVE_DECISION_STATUSES: frozenset[str] = frozenset({"planned", "reviewing"})

# The base *open* Project status ONLY — deliberately NARROWER than
# ACTIVE_PROJECT_STATUSES below (it EXCLUDES "re_notice").
#
# Sole remaining consumer:
#   - scripts/measure_reliable_base_impact.py   (Python-level bucketing)
#
# That diagnostic buckets base-amount changes by the literal ``"open"`` status to
# verify the P2 hypothesis ("open-notice base contamination is ~0"). Its bucket
# encodes that specific status question, NOT a generic "biddable now" scope, so
# it must stay on the singular literal — widening it to include re_notice would
# fold in prior-round settled contamination and repurpose the measurement. The
# reporting / backfill scripts that DO mean "biddable now" use
# ACTIVE_PROJECT_STATUSES / open_projects() instead.
OPEN_PROJECT_STATUS: str = "open"

# Project statuses that represent a currently *open / biddable* procurement
# notice: the base open notice PLUS a re-noticed one. A ``re_notice`` is a
# re-issued opportunity the operator can still bid on, so every "notices I can
# bid on now" query must include it — the KONEPS pipeline treats {open,
# re_notice} as ONE biddable set. Filtering on the singular ``"open"`` literal
# silently drops re-noticed opportunities (a latent bug, not a narrower scope),
# UNLESS the call site is deliberately asking a literal-status question (see
# OPEN_PROJECT_STATUS above).
#
# This set is the single source for the biddable scope, shared by:
#   - app/services/query_predicates.open_projects()
#     (backtest_data_audit.py, paper_bidding_backtest.py, and the read-only
#      reporting / backfill scripts below)
#   - app/services/opportunity_monitoring.StrategyMonitoringService
#     (ACTIVE_PROJECT_STATUSES class attribute)
#   - scripts/report_license_eligibility.py
#   - scripts/report_license_gate_impact.py
#   - scripts/report_no_candidate_cause.py
#   - scripts/report_eligibility_segment_backtest.py   (open-coverage denominator)
#   - scripts/backfill_award_floor_rate.py              (quota-consuming target set)
#
# NOTE: this is a different concept from ACTIVE_DECISION_STATUSES (which tracks
# the bid-decision lifecycle, not the procurement-notice status). Do not conflate
# the two.
ACTIVE_PROJECT_STATUSES: frozenset[str] = frozenset({"open", "re_notice"})

# 기초금액(``HistoricalData.base_amount``)을 "부가세로 설명되는 값"으로 신뢰할 상한 비
# (기초금액 ÷ 추정가격). 부가세는 10% 이므로 정상 과세 공고는 1.10, 면세는 1.00 이고,
# 나머지 0.05 는 수집 시점 차이·반올림을 흡수하는 측정 마진이다. 이 비를 넘는 base 는
# 세금이 아니라 오염(다른 금액 필드 혼입)이며, 그 base 로 만든 하한 임계는 실낙찰
# 데이터가 반증한다(#356: 그 코호트에서 실낙찰자 전원이 임계 미만으로 낙찰).
#
# 판정 알고리즘은 ``app/services/base_amount_basis.py::exceeds_base_trust_ratio`` 한 벌을
# 공유한다(§4.5-8 — 상수만 나누고 비교식을 두 벌로 두면 경계에서 갈린다). 소비처:
#   - app/services/opportunity_analysis/market.py (#356 budget_cap 초과 허용 게이트 —
#     오염된 base 위에서 계산된 하한에 예산 상한을 넘을 권한을 주지 않는다)
#   - app/services/base_amount_basis.py           (provenance 분류의 suspect-ratio 규칙 —
#     같은 임계를 넘는 저장 base 를 clean 버킷에서 뺀다)
#
# 두 소비처는 같은 임계를 **다른 시점**에 적용한다: 게이트는 라이브 요청의 두 금액에,
# 분류는 저장된 행에 라벨로. 다만 둘은 **독립이 아니다**(코드리뷰 C2 정정): 게이트가 라벨을
# 직접 읽지는 않지만 게이트 입력 ``bid_base`` 가 ``get_reliable_base`` 를 거쳐 저장 라벨에서
# 파생되므로, 어떤 행이 non-clean 으로 재태깅되고 복구 추정치까지 가지면 게이트 입력이
# 오염 base → 복구 기초금액으로 바뀌어 판정이 CLOSED→OPEN 으로 열릴 수 있다. 그 방향은
# 의도된 것이며(더 개연적인 입력), 근거·영향 코호트는 ``enforceable_floor_price`` docstring.
BID_BASE_TRUST_RATIO_MAX: float = 1.15

# 수집 item 이 싣는 추정가격(``KonepsCollectedItem.estimated_amount``)의 **출처** 어휘.
# 같은 자리에 서로 다른 성격의 금액이 실려 오는데, 저장 시점에는 값만 봐서는 구분할 수
# 없다. 그래서 생산자가 무엇을 실었는지 신고하고, write 가드가 그 신고를 해석한다
# (판정 규칙 = ``app/domain/estimate_provenance.py``,
#  해석 = ``app/services/koneps/budget_fields.py``).
#
#   - ``notice``                   공고가 **추정가격으로 게시한** 값(``presmptPrce`` /
#                                  ``presmptAmt``) — 정정공고로 바뀌는 유일한 권위값
#   - ``derived``                  개찰 피드의 예정가(planned_price 또는 낙찰가÷사정률)
#   - ``estimate-budget-fallback`` 추정가격 키가 없어 예산 키(``asignBdgtAmt`` /
#                                  ``bdgtAmt``)에서 채운 값 — 게시값이지만 개념이 다르다
#                                  (배정예산 ≥ 추정가격이라 분모로 쓰면 위로 뜬다)
#   - ``estimate-base-fallback``   추정가격 축이 통째로 비어 기초금액을 복사한 폴백
#
# 권위는 ``notice`` 하나뿐이고 나머지 셋은 **빈 자리만** 채운다(fill-only). 왜 필요한가:
# ``Project.budget_estimate`` 는 base provenance 분류(#358 suspect-ratio)의 **분모**이자
# #356 V3 게이트의 ``budget_cap`` 이다. 권위 없는 값이 이 자리를 덮으면 분모가 예정가(+10%)·
# 예산액(≥추정가격)·base 자기사본(비 1.0)으로 바뀌어 오염 판정이 조용히 뒤집힌다.
#
# 두 폴백의 ``estimate-`` 접두는 **추정가격 축 전용 · 수집 내부 전용**임을 드러낸다. 접두 없는
# ``base-fallback`` 은 아래 이웃 어휘와 문자열이 정확히 겹쳐 혼동을 만든다.
#
# 이웃 어휘와 **다른 집합**이다 — 병합 금지:
#   - ``app/domain/reliable_base.ReliableBaseSource`` — 표시 계층의 *기초금액* 축 출처.
#     그쪽 ``base-fallback`` 은 저장 base 를 뜻하고 사용자 노출 문구로도 쓰인다.
#   - ``app/services/base_amount_basis`` 의 ``base_amount_basis`` 라벨 — 저장된 base 의
#     사후 분류이지 수집 item 의 신고가 아니다.
EstimatedAmountSource = Literal[
    "notice", "derived", "estimate-budget-fallback", "estimate-base-fallback"
]
ESTIMATED_AMOUNT_SOURCES: tuple[EstimatedAmountSource, ...] = get_args(
    EstimatedAmountSource
)
ESTIMATE_SOURCE_NOTICE: EstimatedAmountSource = "notice"
ESTIMATE_SOURCE_DERIVED: EstimatedAmountSource = "derived"
ESTIMATE_SOURCE_BUDGET_FALLBACK: EstimatedAmountSource = "estimate-budget-fallback"
ESTIMATE_SOURCE_BASE_FALLBACK: EstimatedAmountSource = "estimate-base-fallback"
# 신뢰 **정책**을 코드 분기가 아니라 데이터로 선언한다(§4.5-3): 저장된 추정가격을 덮을 수
# 있는 출처 집합. write 가드(``app/services/koneps/budget_fields.py``)는 이 집합의 멤버십만
# 본다. 어휘가 늘 때 "덮을 수 있는가"를 여기 한 줄로 결정하게 하려는 것이고, 빠뜨리면 기본이
# 보수(fill-only)라 새 출처가 조용히 권위를 얻는 일이 없다.
ESTIMATE_SOURCES_AUTHORITATIVE: frozenset[EstimatedAmountSource] = frozenset(
    {ESTIMATE_SOURCE_NOTICE}
)

# ``PaperBidRun.mode`` 값 — 하나의 run 이 과거 개찰 재현(historical replay)인지
# 진행 중 공고의 forward paper 생성인지 구분한다. 두 모드는 요청 스냅샷 키 집합과
# 데이터 컷오프 정책이 다르므로, 저장된 payload 를 되읽는 쪽이 모드로 분기한다.
#
# 공유 소비처:
#   - app/services/paper_bidding_backtest/orchestration.py  (_create_run mode 인자)
#   - app/services/paper_bidding_backtest/persistence.py    (data_cutoff_policy 분기)
#   - app/services/paper_bidding_run_payload.py             (복원 모델 룩업)
#   - app/services/paper_bidding_backtest/forward_settlement.py (미정산 대상 스캔)
HISTORICAL_BACKTEST_RUN_MODE: str = "historical_backtest"
FORWARD_PAPER_RUN_MODE: str = "forward_paper"
PAPER_BID_RUN_MODES: frozenset[str] = frozenset(
    {HISTORICAL_BACKTEST_RUN_MODE, FORWARD_PAPER_RUN_MODE}
)

# 실제 외부 송신을 하지 않는 환경(선언적 데이터 — 환경 추가는 이 집합에 한 줄).
#
# ``ENVIRONMENT=test`` 에서 Telegram/메일 실호출이 0 이어야 한다는 불변 요구
# (CLAUDE.md §7)를 도메인 코드의 ``settings.ENVIRONMENT == "test"`` 분기가 아니라 **데이터**
# 로 선언한다. 분기로 흩어지면 새 채널이 그 판정을 빠뜨려도 아무 테스트가 실패하지 않고
# 조용히 실제 송신이 새어 나간다.
#
# 공유 소비처:
#   - app/services/notifications/telegram_transport.py (transport 선택 — 실호출 0 구현)
#   - app/services/notifications/manager.py            (Telegram 실송신 가능 판정)
#   - app/services/notifications/email.py              (라이브 SMTP 송신 판정)
NON_DELIVERING_ENVIRONMENTS: frozenset[str] = frozenset({"test"})

# Telegram delivery telemetry event types written to the ``analytics`` table.
#
# ``TELEGRAM_DELIVERY_EVENT_TYPE`` records every delivery attempt that reached
# the send boundary (sent / blocked / dry-run). ``TELEGRAM_DELIVERY_SUPPRESSED_
# EVENT_TYPE`` is deliberately a SEPARATE type: a fatigue suppression is not a
# failed delivery, so it must not enter the delivery success-rate denominator in
# app/services/analytics_reporting.py.
TELEGRAM_DELIVERY_EVENT_TYPE: str = "telegram.delivery"
TELEGRAM_DELIVERY_SUPPRESSED_EVENT_TYPE: str = "telegram.delivery.suppressed"

# 나머지 ``analytics.event_data`` 생산자의 event_type. 각 타입의 payload 계약은
# app/schemas/analytics_events.py 가 선언하고, 그 레지스트리가 event_type → 복원 모델을
# 매핑한다. 같은 문자열을 생산자/소비자에 따로 적어 두면 한쪽만 바뀌어도 조용히 어긋나
# 집계가 0건이 되므로 여기서 단일 출처를 유지한다.
TELEGRAM_STRATEGY_PENDING_EDIT_EVENT_TYPE: str = "telegram.strategy.pending_edit"
BID_REPORT_EMAIL_DELIVERY_EVENT_TYPE: str = "email.bid_report.delivery"
# 프론트가 텔레메트리 엔드포인트(``POST /analytics/event``)로 올리는 이벤트.
PROJECT_VIEW_EVENT_TYPE: str = "project_view"
RECOMMENDATION_FEEDBACK_EVENT_TYPE: str = "recommendation_feedback"
# G-2 증적 sweep beat 가 남기는 관측 이벤트. ``collect_g2_evidence`` 행은
# scripts/backfill_g2_daily_drafts.py 가 되읽어 counted_days 판정에 쓰이는 **게이트
# 입력**이다 — 생산(app/tasks/evidence_jobs.py)과 소비(backfill 스크립트)가 같은 문자열을
# 따로 적어 두면 조용히 0건이 되어 통과한 날이 사라진다. 동시에 운영자 활동이 아니라
# beat 가 남긴 내부 관측이므로 아래 INTERNAL_TELEMETRY_EVENT_TYPES 에도 든다.
G2_CANDIDATE_RECHECK_EVENT_TYPE: str = "g2_candidate_recheck"
COLLECT_G2_EVIDENCE_EVENT_TYPE: str = "collect_g2_evidence"

# 클라이언트가 ``POST /analytics/event`` 로 **올릴 수 있는** event_type 전체.
#
# 종전 이 엔드포인트는 임의 문자열을 받았다: 인증도 상한도 없어서 아무 클라이언트가
# 아무 타입으로 ``analytics`` 테이블을 채울 수 있었고, 그렇게 들어온 타입은 어떤 소비처도
# 읽지 않으면서 운영자 활동 카운트(``total_events``)만 부풀렸다. 프론트가 실제로 올리는
# 타입은 두 개뿐이므로(``frontend/src/shared/api/operations.ts`` 의 ``trackProjectView`` /
# ``submitRecommendationFeedback``) 어휘를 그 둘로 좁혀 미지 타입을 422 로 거부한다.
#
# 값은 위의 두 상수와 같은 문자열이며 그 동치는
# ``tests/test_analytics_event_contracts.py`` 가 고정한다(``Literal`` 은 리터럴만 받으므로
# 상수 참조로 조립할 수 없어 값을 다시 적는다 — 그 대신 동치를 테스트로 못박는다).
#
# 새 클라이언트 이벤트를 추가할 때: 여기 한 값 + app/schemas/analytics_events.py 의
# 복원 모델/레지스트리 한 줄. 그 둘을 함께 하지 않으면 이벤트는 저장되지만 아무도
# 읽지 못한다(레지스트리 미등록 = 명시적 부재).
ClientAnalyticsEventType = Literal["project_view", "recommendation_feedback"]
CLIENT_ANALYTICS_EVENT_TYPES: tuple[ClientAnalyticsEventType, ...] = get_args(
    ClientAnalyticsEventType
)

# Internal telemetry event types that operator-facing event *counts* exclude.
#
# This set is shared by:
#   - app/api/analytics.py           (INTERNAL_TELEMETRY_EVENT_TYPES)
#   - app/api/operator_dashboard.py  (INTERNAL_OPERATOR_EVENT_TYPES)
#
# Both used to declare the same literal set independently; new internal event
# types are added here so neither counter silently starts reporting telemetry as
# operator activity.
# G-2 sweep beat 이벤트도 여기 든다: beat 가 매일 2건을 남기므로, 제외하지 않으면
# **운영자가 아무 것도 하지 않은 날에도** 활동 카운트가 올라가 위 주석이 선언한 정책
# ("텔레메트리를 운영자 활동으로 보고하지 않는다")과 정면으로 어긋난다.
INTERNAL_TELEMETRY_EVENT_TYPES: frozenset[str] = frozenset(
    {
        TELEGRAM_DELIVERY_EVENT_TYPE,
        TELEGRAM_DELIVERY_SUPPRESSED_EVENT_TYPE,
        TELEGRAM_STRATEGY_PENDING_EDIT_EVENT_TYPE,
        G2_CANDIDATE_RECHECK_EVENT_TYPE,
        COLLECT_G2_EVIDENCE_EVENT_TYPE,
    }
)

# 실험 predictor 두 축 — Phase 1 예정가 분포 엔진(레지스트리 키 "distribution")과
# Phase 2 낙찰률 GBM("award_rate_gbm") — 은 아직 **자동 승격 후보가 아니다**.
# 운영 .env 실측(2026-08-11)상 PRICE_PREDICTION_ENABLE_EXPERIMENTAL_PREDICTORS
# 가 이미 true 라 실험 플래그는 게이트가 아니고, 선호 설정이 자동으로 바뀌는 승격
# 경로들이 있다: (a) auto 선택·리포트의 best 후보 선정 — 동점 시 키 문자열
# 오름차순이라 "award_rate_gbm"/"distribution" 이 "ensemble"/"historical" 보다 먼저
# 뽑힌다, (b) release manifest 가 best_predictor_key 를
# recommended_env["PRICE_PREDICTION_PREFERRED_PREDICTOR"] 로 기록, (c) promotion
# gate 가 리포트 best arm 의 성적으로 pass/fail 을 판정 — 키 없는 리포트에서는
# completed 최소 오차로 **폴백 선정**하므로 그 폴백에서도 걸러야 한다(리뷰 K4).
# 여기 든 키는 아래 소비자 전부에서 제외된다(평가·results 리포트에는 그대로 나타난다 —
# 비교 수치는 계속 쌓되 승격만 막는다). 각 축이 자기 승격 게이트를 통과하면 이 집합에서
# 그 키를 빼는 것이 승격 절차다(축마다 따로 — 한 축의 통과가 다른 축을 풀지 않는다).
# 새 승격 경로를 만들면 아래 목록에 소비자를 추가하라 — 이 목록의 전수성은 주석이
# 아니라 리뷰가 지킨다.
#
# Shared by (grep 전수, 2026-08-12):
#   - app/ai/predictor_backtest.py        (best 후보 eligibility 필터 + arm 메타 기재
#                                          + _resolve_group_predictor 의 by_group 폴백 필터)
#   - app/services/ml_release/manifest.py (recommended_env 기록 가드)
#   - app/services/ml_release/gate.py     (게이트 metrics best arm 선정·폴백 필터)
AUTO_PROMOTION_EXCLUDED_PREDICTOR_KEYS: frozenset[str] = frozenset(
    {"distribution", "award_rate_gbm"}
)

# 사정률(예정가/기초금액) 관측의 개연 밴드(경계 포함). 밖이면 오적재(스케일 혼입·
# 오염 base)로 보고 값을 고치지 않은 채 관측에서만 배제한다(published_floor_rate 와
# 같은 스탠스).
#
# 소유권을 app/core 로 올린 이유(리뷰 M3): 이 밴드는 distribution 엔진의 관측 관문
# 이면서 동시에 **historical(프로덕션 기본 predictor)의 서빙 가격**에도 닿는다 —
# summarize_historical_records 의 실현 사정률 판정 → reserve prior 블렌드 →
# base_rate 전파 경로가 코드로 확인됐다. 승격 게이트(위
# AUTO_PROMOTION_EXCLUDED_PREDICTOR_KEYS)는 상수 공유 경로를 잡지 못하므로,
# distribution 캘리브레이션 목적의 밴드 조정이 historical 가격을 움직인다는 사실이
# 특정 엔진 모듈이 아니라 이 선언 위치에서 보여야 한다. 양끝 값
# (0.79/0.80/1.20/1.21)은 세 값표가 고정한다: summarize 경유 경계 값표
# (tests/test_reserve_base_rate.py) · 술어 단위 값표
# (tests/test_prediction_distribution_predictor.py) · center 관문 경계
# (tests/test_backtest_yega_distribution.py). 비교식은 distribution_extraction 의
# is_observable_assessment_rate 술어 **한 벌**이다(리뷰 N1 — 상수만 나누고 비교식을
# 두 벌로 두면 경계에서 갈린다. floor_shortfall 의 동명 술어와의 충돌로 개명 —
# 리뷰 N-후속).
#
# Shared by (실소비자 전수 — 리뷰 N2 재작성. 교훈 두 갈래(리뷰 O4): ①이동 —
# 소비자 목록을 쓰는 커밋과 소비자를 옮기는 커밋이 같은 라운드면 목록은 이동이
# 끝난 뒤 마지막에 확정한다 ②열거 — 서빙 엔진 distribution.py 누락은 이동과 무관한
# 작성 시점의 전수 실패였다(작성 시점에 이미 import 존재) — 목록은 grep 전수로만
# 쓴다):
#   - app/ai/predictors/distribution_extraction.py (술어·관문 산술의 단일 출처)
#     ↳ app/ai/predictors/historical/summary.py (realized_assessment_ratio 경유 —
#       historical 서빙 가격, L2 통합)
#     ↳ app/ai/predictors/distribution.py (observe_reserve_draw 경유 — 서빙 엔진
#       관측 관문)
#     ↳ scripts/_yega_coverage.py (observe_reserve_draw 경유 — 캘리 커버리지 축,
#       M 라운드 분해로 backtest_yega_distribution 에서 이동)
#
# ⚠ app/domain/floor_shortfall.ASSESSMENT_RATE_MIN/MAX(0.90~1.10)와 이름이 비슷하지만
# **다른 밴드·다른 목적**이다: 이쪽=관측 필터(오적재 배제), 그쪽=하한 미달 판정의
# 물리적 생성 범위. §4.5-8 근거로 통합하면 하한 미달 빈도가 왜곡된다 — 하단 꼬리
# (0.8~0.90)는 분모만 늘려 낙관, 상단 꼬리((1.10,1.20])는 임계(≈1.00125)도 넘어
# 분자에 들어가 비관, 순방향은 꼬리 분포에 달린다(2026-08-12 실측 하단 76·상단 0
# → 현재는 낙관 방향, 리뷰 O2). 결론 불변: 통합 금지(리뷰 N4).
ASSESSMENT_RATE_PLAUSIBLE_MIN: float = 0.8
ASSESSMENT_RATE_PLAUSIBLE_MAX: float = 1.2
