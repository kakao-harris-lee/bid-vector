"""Database models"""
from sqlalchemy import Boolean, Column, DateTime, Float, ForeignKey, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import relationship

from app.core.database import Base
from app.core.time import utc_now
from app.core.vector import VECTOR


class User(Base):
    """Primary operator account model (legacy table name retained for compatibility)."""
    __tablename__ = "users"

    id = Column(Integer, primary_key=True)
    username = Column(String(50), unique=True, index=True)
    email = Column(String(100), unique=True, index=True)
    hashed_password = Column(String(255))
    full_name = Column(String(100))
    company = Column(String(100))
    is_active = Column(Boolean, default=True)
    is_admin = Column(Boolean, default=False)
    created_at = Column(DateTime(timezone=True), default=utc_now)
    updated_at = Column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)

    # Relationships
    bids = relationship("Bid", back_populates="user")
    predictions = relationship("PricePrediction", back_populates="user")
    company_profile = relationship("CompanyProfile", back_populates="user", uselist=False)
    strategy_profile = relationship("OperatorStrategy", back_populates="user", uselist=False)
    strategy_runs = relationship("OperatorStrategyRun", back_populates="operator")
    bid_decisions = relationship("BidDecisionRecord", back_populates="operator")
    paper_bid_runs = relationship("PaperBidRun", back_populates="operator")
    decision_experiment_runs = relationship("DecisionExperimentRun", back_populates="operator")
    allocations = relationship("Allocation", back_populates="user")
    notifications = relationship("Notification", back_populates="user")
    notification_channels = relationship("OperatorNotificationChannel", back_populates="operator")


class Project(Base):
    """Project/Procurement model"""
    __tablename__ = "projects"

    id = Column(Integer, primary_key=True)
    title = Column(String(255), index=True)
    description = Column(Text)
    requirements = Column(Text)
    budget_estimate = Column(Float)
    budget_min = Column(Float)
    budget_max = Column(Float)
    category = Column(String(100), index=True)
    notice_number = Column(String(100), nullable=True, index=True)
    source_url = Column(Text, nullable=True)
    issuing_agency = Column(String(255), nullable=True, index=True)
    demand_agency = Column(String(255), nullable=True, index=True)
    # 공고 낙찰하한율 (KONEPS sucsfbidLwltRate). fraction 저장 (예: 0.88 = 88%).
    # 운영자가 별도 하한율을 입력하지 않았을 때 낙찰 적격 검증의 폴백으로 쓴다.
    award_floor_rate = Column(Float, nullable=True)
    # 수집 시 KONEPS 자격 관련 원문(면허제한·업종·참가제한지역 등)을 dict로 보존한다
    # (PR-B 라벨 추출의 원천). 현재 소비자 없음 — classifier/requirements/게이트는
    # 이 컬럼을 읽지 않으며 추천 동작에 영향이 없다. none_as_null=True로 미설정/None을
    # JSON null이 아닌 SQL NULL로 저장해 backfill의 ``IS NULL`` 재개 키가 정확히 맞는다.
    eligibility_raw = Column(JSON(none_as_null=True), nullable=True)
    status = Column(String(50), default="open")  # open, re_notice, closed, awarded, failed, cancelled
    created_at = Column(DateTime(timezone=True), default=utc_now)
    deadline = Column(DateTime(timezone=True))
    semantic_text = Column(Text, default="")
    embedding_payload = Column(Text, default="[]")
    embedding_model = Column(String(255), nullable=True)
    embedding_updated_at = Column(DateTime(timezone=True), nullable=True)
    business_type_code = Column(String(8), nullable=True, index=True)
    business_type_label = Column(String(64), nullable=True)
    embedding = Column(VECTOR(384), nullable=True)

    # Relationships
    bids = relationship("Bid", back_populates="project")
    bid_decisions = relationship("BidDecisionRecord", back_populates="project")
    paper_bids = relationship("PaperBid", back_populates="project")
    allocations = relationship("Allocation", back_populates="project")
    historical_records = relationship("HistoricalData", back_populates="project")
    tender_results = relationship("TenderResult", back_populates="project")
    crawl_jobs = relationship("CrawlJob", back_populates="project")


class Bid(Base):
    """Bid model"""
    __tablename__ = "bids"

    id = Column(Integer, primary_key=True)
    project_id = Column(Integer, ForeignKey("projects.id"), index=True)
    user_id = Column(Integer, ForeignKey("users.id"), index=True)
    bid_amount = Column(Float)
    proposed_timeline = Column(Integer)  # days
    description = Column(Text)
    status = Column(String(50), default="submitted")  # submitted, reviewed, accepted, rejected
    score = Column(Float, nullable=True)  # AI-generated score
    created_at = Column(DateTime(timezone=True), default=utc_now)
    updated_at = Column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)

    # Relationships
    project = relationship("Project", back_populates="bids")
    user = relationship("User", back_populates="bids")


class PricePrediction(Base):
    """Price prediction model"""
    __tablename__ = "price_predictions"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    project_id = Column(Integer, ForeignKey("projects.id"))
    predicted_price = Column(Float)
    price_range_min = Column(Float)
    price_range_max = Column(Float)
    confidence_score = Column(Float)  # 0-1
    model_version = Column(String(50))
    predictor_name = Column(String(100), default="historical_statistical", index=True)
    predictor_family = Column(String(100), default="statistical", index=True)
    fallback_reason = Column(Text, nullable=True)
    selector_name = Column(String(100), default="configured_preference", index=True)
    selection_reason = Column(Text, nullable=True)
    backtest_sample_count = Column(Integer, default=0)
    backtest_average_absolute_error_rate = Column(Float, nullable=True)
    training_window_size = Column(Integer, default=0)
    pricing_mode = Column(String(50), default="heuristic", index=True)
    historical_sample_size = Column(Integer, default=0)
    agency_match_sample_size = Column(Integer, default=0)
    predicted_bid_rate = Column(Float, default=0.0)
    guardrail_applied = Column(Boolean, default=False, index=True)
    guardrail_reason = Column(Text, nullable=True)
    floor_bid_rate = Column(Float, nullable=True)
    floor_price = Column(Float, nullable=True)
    created_at = Column(DateTime(timezone=True), default=utc_now)

    # Relationships
    user = relationship("User", back_populates="predictions")


class CompanyProfile(Base):
    """Single-operator company profile used for bid matching and prioritization."""
    __tablename__ = "company_profiles"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), unique=True, index=True)
    business_type = Column(String(50), default="service")
    license_codes = Column(Text, default="")
    region_codes = Column(Text, default="")
    annual_revenue = Column(Float, default=0.0)
    capacity_score = Column(Float, default=0.0)
    # 시공능력평가액(원). Construction-only manual annual input used as the
    # primary budget-fit signal for 공사 카테고리. 0 means "not provided",
    # in which case the matcher falls back to annual_revenue/capacity_score.
    construction_capacity_amount = Column(
        Float, default=0.0, nullable=False, server_default="0"
    )
    # 도급한도(원). Maximum simultaneously-held award amount. Persisted for
    # future capacity tracking; not yet used by the matcher.
    awarded_contract_limit = Column(
        Float, default=0.0, nullable=False, server_default="0"
    )
    # cohort 정체성(협회 가입/기술부문). ``license_codes``/``region_codes`` 와 동일한
    # 콤마 구분 Text 다중값 패턴(join/split_multi_value_text 로 처리). 첫 실수요 고객
    # (해양엔지니어링협회)의 핵심 cohort 속성이며 이 단계는 캡처만 한다 —
    # eligibility 게이팅/추천 반영은 후속. 빈 문자열은 "미기재"로 중립.
    association_memberships = Column(Text, default="", server_default="")
    tech_fields = Column(Text, default="", server_default="")
    total_awards = Column(Integer, default=0)
    # 국세청 사업자등록번호 검증(상태조회/진위확인) 결과 캡처. 원문 사업자번호는
    # 절대 저장하지 않는다(CLAUDE.md §8, 설계 §비기능) — pepper HMAC-SHA256 해시만
    # 저장한다. 해시는 정규화(숫자만)된 번호로 계산하며 중복/검색 조회용으로 index.
    # ``mask_business_number`` 로 만든 masked 번호만 표시/응답에 쓴다(원문 미노출).
    business_registration_number_hash = Column(String(64), nullable=True, index=True)
    # 검증 상태. 허용값 단일 출처는 services.verification.status.BusinessVerificationStatus
    # (unverified/verified/inactive/mismatch/unknown). onboarding_suggestions.status 와
    # 같은 패턴 — 컬럼은 plain String, enum 은 서비스 계층에 둔다(모델↔서비스 순환 회피).
    business_verification_status = Column(
        String(20), default="unverified", nullable=False, server_default="unverified"
    )
    # masked/normalized 상태 payload(JSON 문자열). 원문 사업자번호(b_no)는 마스킹해
    # 담고 b_stt/b_stt_cd/tax_type/valid 등 상태 필드만 보존한다 — 원문·서비스키 미포함.
    business_verification_payload = Column(Text, nullable=True)
    business_verified_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=utc_now)
    updated_at = Column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)

    user = relationship("User", back_populates="company_profile")


class OperatorStrategy(Base):
    """Single-operator watch rules used for opportunity monitoring."""
    __tablename__ = "operator_strategies"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), unique=True, index=True)
    focus_categories = Column(Text, default="")
    focus_regions = Column(Text, default="")
    exclude_regions = Column(Text, default="")
    required_keywords = Column(Text, default="")
    exclude_keywords = Column(Text, default="")
    min_budget_estimate = Column(Float, default=0.0)
    max_budget_estimate = Column(Float, default=0.0)
    minimum_match_score = Column(Float, default=0.6)
    minimum_probability_score = Column(Float, default=0.55)
    bid_now_threshold = Column(Float, default=0.7)
    review_threshold = Column(Float, default=0.45)
    auto_workload_penalty_multiplier = Column(Float, default=1.0)
    category_priority_overrides = Column(Text, default="{}")
    notify_only_high_priority = Column(Boolean, default=True)
    max_recommended_candidates = Column(Integer, default=10)
    created_at = Column(DateTime(timezone=True), default=utc_now)
    updated_at = Column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)

    user = relationship("User", back_populates="strategy_profile")


class OperatorNotificationChannel(Base):
    """Per-operator notification route metadata without raw secret targets."""
    __tablename__ = "operator_notification_channels"
    __table_args__ = (
        UniqueConstraint(
            "operator_id",
            "channel_type",
            "route_key",
            name="uq_operator_notification_channels_operator_channel_route",
        ),
    )

    id = Column(Integer, primary_key=True)
    operator_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    channel_type = Column(String(50), nullable=False, index=True)
    route_key = Column(String(120), nullable=False, index=True)
    target_label = Column(String(120), nullable=True)
    is_active = Column(Boolean, default=False, nullable=False, server_default="0")
    dry_run_only = Column(Boolean, default=True, nullable=False, server_default="1")
    verified_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=utc_now)
    updated_at = Column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)

    operator = relationship("User", back_populates="notification_channels")


class OperatorStrategyRun(Base):
    """Execution history for manual or scheduled operator strategy monitoring runs."""
    __tablename__ = "operator_strategy_runs"

    id = Column(Integer, primary_key=True)
    operator_id = Column(Integer, ForeignKey("users.id"), index=True)
    task_id = Column(String(100), nullable=True, index=True)
    trigger_source = Column(String(50), default="manual_sync", index=True)
    status = Column(String(50), default="queued", index=True)
    high_priority_only = Column(Boolean, default=True)
    limit_applied = Column(Integer, default=10)
    request_payload = Column(Text, default="{}")
    result_payload = Column(Text, default="{}")
    evaluated_project_count = Column(Integer, default=0)
    selected_candidate_count = Column(Integer, default=0)
    persisted_candidate_count = Column(Integer, default=0)
    notification_count = Column(Integer, default=0)
    error_message = Column(Text, nullable=True)
    started_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=utc_now)
    updated_at = Column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)

    operator = relationship("User", back_populates="strategy_runs")


class HistoricalData(Base):
    """Historical tender/opening data for prediction"""
    __tablename__ = "historical_data"

    id = Column(Integer, primary_key=True)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=True, index=True)
    notice_number = Column(String(100), index=True)
    agency_name = Column(String(255), index=True)
    category = Column(String(100), index=True)
    base_amount = Column(Float, default=0.0)
    predicted_price = Column(Float, default=0.0)
    bid_rate = Column(Float, default=0.0)
    reserve_prices = Column(Text, default="[]")
    selected_numbers = Column(Text, default="[]")
    opened_at = Column(DateTime(timezone=True), nullable=True)
    # Last time a deferred reserve-detail backfill fetched this notice but found
    # no settled reserve yet ("not_settled"). Lets the collector back off
    # permanently-empty notices (open >24h but reserve never published) so they
    # are re-checked at most once per RECHECK window instead of every 6h sweep,
    # which otherwise burns the ScsbidInfoService rate limit (HTTP 429).
    reserve_detail_checked_at = Column(DateTime(timezone=True), nullable=True)
    # --- base_amount provenance classification (backfilled, NEVER overwrites
    # base_amount) ---------------------------------------------------------
    # 66% of stored ``base_amount`` values are NOT the real 기초금액 (integer
    # 원화); they are derived/polluted (예: win÷winning_rate 역산 = 예정가-basis,
    # or a base×1.1 VAT division). These columns tag that provenance so the
    # feedback/calibration loop can filter them out. ``classify_base_basis`` is
    # the single source of truth (app/services/base_amount_basis.py).
    #
    # WARNING: any 밴드 캘리브레이션 / 백테스트 / 피드백 재추정 that consumes
    # ``base_amount`` MUST restrict to rows where ``base_amount_basis == 'clean'``.
    # Calibrating on non-clean rows re-derives 예정가-basis pollution and reintroduces
    # the +0.5%p 밴드 bias (see PREDICTION_AGENCY_BAND_ASSESSMENT_RATES notes).
    base_amount_basis = Column(String(30), nullable=True, index=True)
    # Estimated real 기초금액 for non-clean rows ONLY (추정치 — NOT the ground
    # truth base_amount). Derived from 복수예비가격 midpoint when 15 reserves
    # exist; NULL when unrecoverable or when the row is already 'clean'.
    base_amount_estimated = Column(Float, nullable=True)
    # Idempotency marker: set once the basis classifier has processed this row.
    basis_checked_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=utc_now)

    project = relationship("Project", back_populates="historical_records")


class BidDecisionRecord(Base):
    """Persistent record of a single operator's bid-pursuit decision."""
    __tablename__ = "bid_decision_records"

    id = Column(Integer, primary_key=True)
    project_id = Column(Integer, ForeignKey("projects.id"), index=True)
    operator_id = Column(Integer, ForeignKey("users.id"), index=True)
    pursue_bid = Column(Boolean, default=False)
    action = Column(String(50), default="skip")
    decision_status = Column(String(50), default="planned")
    initial_action = Column(String(50), default="skip")
    initial_decision_status = Column(String(50), default="planned")
    first_decided_at = Column(DateTime(timezone=True), default=utc_now)
    # Nullable with no Python default: a real-bid-only record (no prior system
    # recommendation) must stay NULL so the actual submitted bid is never read
    # as a "system recommendation" (§2 정직 명세). Decision paths that do produce
    # a recommendation set this explicitly; all readers are null-safe.
    recommended_amount = Column(Float, nullable=True)
    probability_score = Column(Float, default=0.0)
    matched_score = Column(Float, default=0.0)
    priority_score = Column(Float, default=0.0)
    urgency_score = Column(Float, default=0.0)
    competitiveness_score = Column(Float, default=0.0)
    budget_capture_score = Column(Float, default=0.0)
    expected_margin_score = Column(Float, default=0.0)
    execution_complexity_score = Column(Float, default=0.0)
    deadline_hours_remaining = Column(Integer, nullable=True)
    current_active_bids = Column(Integer, default=0)
    max_active_bids = Column(Integer, default=3)
    current_workload_score = Column(Float, default=0.0)
    workload_source = Column(String(20), default="provided")
    score_breakdown = Column(Text, default="{}")
    reasoning = Column(Text, default="")
    # Real-bid tracking (distinct from recommended_amount, which stays the
    # system recommendation). Populated when the operator registers an actual
    # KONEPS submission so the post-개찰 낙찰결과 알림 파이프라인can verify it.
    submitted_bid_amount = Column(Float, nullable=True)
    submitted_floor_rate = Column(Float, nullable=True)
    submitted_at = Column(DateTime(timezone=True), nullable=True)
    # Idempotency marker for the award-result Telegram (set once, after send).
    award_notified_at = Column(DateTime(timezone=True), nullable=True)
    # Win/loss outcome of the real bid, persisted once the winner is public
    # (operator company-name vs TenderResult.winning_company). "won"/"lost";
    # NULL = 미확정. §2 정직 명세: never pre-filled by estimate — set only after
    # the winner is known, and by name-match (never by amount alone, since the
    # same 개찰 can carry identical 투찰가 across distinct companies).
    award_outcome = Column(String(20), nullable=True, index=True)
    award_outcome_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=utc_now)
    updated_at = Column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)

    project = relationship("Project", back_populates="bid_decisions")
    operator = relationship("User", back_populates="bid_decisions")


class DecisionExperimentRun(Base):
    """Persistent execution history for analytics-backed decision tuning experiments."""
    __tablename__ = "decision_experiment_runs"

    id = Column(Integer, primary_key=True)
    operator_id = Column(Integer, ForeignKey("users.id"), index=True)
    experiment_key = Column(String(100), index=True)
    recommendation_key = Column(String(100), index=True)
    status = Column(String(50), default="planned", index=True)
    outcome = Column(String(50), nullable=True, index=True)
    priority_rank = Column(Integer, default=1)
    title = Column(String(255))
    hypothesis = Column(Text, default="")
    suggested_change = Column(Text, default="")
    target_metric = Column(String(100), default="")
    expected_direction = Column(String(20), default="increase")
    success_criteria = Column(Text, default="")
    guardrail_metric = Column(String(100), default="")
    minimum_decision_sample = Column(Integer, default=1)
    duration_days = Column(Integer, default=14)
    baseline_days = Column(Integer, default=14)
    rollback_trigger = Column(Text, default="")
    notes = Column(Text, default="")
    baseline_summary = Column(Text, default="{}")
    latest_evaluation = Column(Text, default="{}")
    started_at = Column(DateTime(timezone=True), default=utc_now)
    ended_at = Column(DateTime(timezone=True), nullable=True)
    last_evaluated_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=utc_now)
    updated_at = Column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)

    operator = relationship("User", back_populates="decision_experiment_runs")


class Allocation(Base):
    """Legacy bid-decision record table retained during the domain migration."""
    __tablename__ = "allocations"

    id = Column(Integer, primary_key=True)
    project_id = Column(Integer, ForeignKey("projects.id"), index=True)
    user_id = Column(Integer, ForeignKey("users.id"), index=True)
    recommended_amount = Column(Float, default=0.0)
    probability_score = Column(Float, default=0.0)
    allocation_rank = Column(Integer, default=1)
    status = Column(String(50), default="proposed")
    created_at = Column(DateTime(timezone=True), default=utc_now)
    updated_at = Column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)

    project = relationship("Project", back_populates="allocations")
    user = relationship("User", back_populates="allocations")


class TenderResult(Base):
    """Actual tender result snapshot"""
    __tablename__ = "tender_results"

    id = Column(Integer, primary_key=True)
    project_id = Column(Integer, ForeignKey("projects.id"), index=True)
    winning_company = Column(String(255))
    winning_amount = Column(Float, default=0.0)
    winning_rate = Column(Float, default=0.0)
    result_status = Column(String(50), default="pending")
    announced_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=utc_now)

    # 개찰 1위(잠정) 스냅샷 — 낙찰 확정(winning_*)과 구분되는 개찰 직후 신호.
    # 수의계약이면 1위=사실상 확정이나 적격심사는 1위부터 캐스케이드라 1위≠낙찰
    # 가능(§2 정직 명세). 사업자번호는 제로패딩 보존 문자열(#210 교훈 준용 — KONEPS
    # 식별자 int 변환 금지). opening_checked_at 은 수집 패스의 recheck backoff
    # 마커(NULL=미조회).
    opening_rank1_company = Column(String(255), nullable=True)
    opening_rank1_business_no = Column(String(20), nullable=True)
    opening_rank1_amount = Column(Float, nullable=True)
    opening_rank1_rate = Column(Float, nullable=True)
    opening_participant_count = Column(Integer, nullable=True)
    opened_at = Column(DateTime(timezone=True), nullable=True)
    opening_checked_at = Column(DateTime(timezone=True), nullable=True)

    project = relationship("Project", back_populates="tender_results")


class PaperBidRun(Base):
    """Backtest or forward paper-bidding execution run."""
    __tablename__ = "paper_bid_runs"

    id = Column(Integer, primary_key=True)
    operator_id = Column(Integer, ForeignKey("users.id"), index=True)
    strategy_version = Column(String(100), default="local")
    model_version = Column(String(100), default="current")
    status = Column(String(50), default="running", index=True)
    mode = Column(String(50), default="historical_backtest", index=True)
    scenario = Column(String(50), default="base")
    category_filter = Column(String(100), nullable=True, index=True)
    target_start_at = Column(DateTime(timezone=True), nullable=True)
    target_end_at = Column(DateTime(timezone=True), nullable=True)
    data_cutoff_policy = Column(String(100), default="deadline_minus_2h")
    started_at = Column(DateTime(timezone=True), default=utc_now)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    candidate_count = Column(Integer, default=0)
    paper_bid_count = Column(Integer, default=0)
    settled_count = Column(Integer, default=0)
    request_payload = Column(Text, default="{}")
    result_payload = Column(Text, default="{}")
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), default=utc_now)
    updated_at = Column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)

    operator = relationship("User", back_populates="paper_bid_runs")
    paper_bids = relationship("PaperBid", back_populates="run", cascade="all, delete-orphan")


class PaperBid(Base):
    """Immutable virtual bid generated before settlement."""
    __tablename__ = "paper_bids"

    id = Column(Integer, primary_key=True)
    run_id = Column(Integer, ForeignKey("paper_bid_runs.id"), index=True)
    project_id = Column(Integer, ForeignKey("projects.id"), index=True)
    operator_id = Column(Integer, ForeignKey("users.id"), index=True)
    notice_number = Column(String(100), nullable=True, index=True)
    action = Column(String(50), default="skip", index=True)
    decision_status = Column(String(50), default="skipped", index=True)
    data_cutoff_at = Column(DateTime(timezone=True), nullable=True, index=True)
    paper_bid_amount = Column(Float, default=0.0)
    paper_bid_rate = Column(Float, default=0.0)
    scenario = Column(String(50), default="base")
    priority_score = Column(Float, default=0.0)
    probability_score = Column(Float, default=0.0)
    matched_score = Column(Float, default=0.0)
    predicted_price = Column(Float, default=0.0)
    predicted_bid_rate = Column(Float, default=0.0)
    price_range_min = Column(Float, default=0.0)
    price_range_max = Column(Float, default=0.0)
    confidence_score = Column(Float, default=0.0)
    predictor_name = Column(String(100), default="historical_statistical")
    predictor_family = Column(String(100), default="statistical")
    model_version = Column(String(100), default="current")
    strategy_version = Column(String(100), default="local")
    input_snapshot_hash = Column(String(64), nullable=True, index=True)
    reasoning = Column(Text, default="")
    created_at = Column(DateTime(timezone=True), default=utc_now)

    run = relationship("PaperBidRun", back_populates="paper_bids")
    project = relationship("Project", back_populates="paper_bids")
    settlement = relationship("PaperBidSettlement", back_populates="paper_bid", uselist=False, cascade="all, delete-orphan")


class PaperBidSettlement(Base):
    """Settlement comparing a paper bid with the final tender result."""
    __tablename__ = "paper_bid_settlements"

    id = Column(Integer, primary_key=True)
    paper_bid_id = Column(Integer, ForeignKey("paper_bids.id"), unique=True, index=True)
    tender_result_id = Column(Integer, ForeignKey("tender_results.id"), nullable=True, index=True)
    result_status = Column(String(50), default="pending")
    winning_company = Column(String(255), nullable=True)
    winning_amount = Column(Float, default=0.0)
    winning_rate = Column(Float, default=0.0)
    amount_delta = Column(Float, default=0.0)
    absolute_error_rate = Column(Float, default=0.0)
    bid_rate_delta = Column(Float, default=0.0)
    absolute_bid_rate_error = Column(Float, default=0.0)
    price_close = Column(Boolean, default=False)
    price_competitive = Column(Boolean, default=False)
    would_have_won_price_only = Column(String(50), default="unknown")
    would_have_won_final = Column(String(50), default="unknown")
    # 예정가격 / 낙찰하한가 derived at settlement (scoring) time. Nullable: a
    # missing value is an honest "not enough data" signal (gate => "unknown"),
    # distinct from 0.
    estimated_price = Column(Float, nullable=True)
    minimum_bid_price = Column(Float, nullable=True)
    settlement_reason = Column(Text, default="")
    settled_at = Column(DateTime(timezone=True), default=utc_now)
    created_at = Column(DateTime(timezone=True), default=utc_now)

    paper_bid = relationship("PaperBid", back_populates="settlement")
    tender_result = relationship("TenderResult")


class CrawlJob(Base):
    """Crawler execution history"""
    __tablename__ = "crawl_jobs"

    id = Column(Integer, primary_key=True)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=True, index=True)
    source = Column(String(100), default="koneps")
    target_date = Column(String(20), nullable=True)
    status = Column(String(50), default="queued")
    result_count = Column(Integer, default=0)
    error_message = Column(Text, nullable=True)
    # Celery task id that owns this row. Used as an idempotency key so a
    # redelivered task (acks_late + time-limit SIGKILL) reuses its existing
    # running row instead of spawning an orphan ``running`` crawl job.
    celery_task_id = Column(String(155), nullable=True, index=True)
    created_at = Column(DateTime(timezone=True), default=utc_now)
    completed_at = Column(DateTime(timezone=True), nullable=True)

    project = relationship("Project", back_populates="crawl_jobs")


class SmokeTestRun(Base):
    """Persisted KONEPS + Telegram end-to-end smoke test cycle.

    One row per daily smoke cycle so the operations dashboard can surface a
    consolidated PASS/FAIL history + consecutive green streak alongside crawl
    and notification health. Per-phase ``data`` payloads are intentionally
    dropped before persistence to keep rows small; the JSON retains compact
    evidence plus actionability fields such as ``failure_category`` and
    ``retry_method``.
    """
    __tablename__ = "smoke_test_runs"

    id = Column(Integer, primary_key=True)
    started_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    overall_passed = Column(Boolean, default=False, index=True)
    phases = Column(Text, default="[]")  # JSON: compact per-phase evidence
    telegram_message_id = Column(Integer, nullable=True)
    telegram_status = Column(String(50), nullable=True)
    created_at = Column(DateTime(timezone=True), default=utc_now, index=True)


class DocumentAnalysis(Base):
    """Document analysis results"""
    __tablename__ = "document_analyses"

    id = Column(Integer, primary_key=True)
    project_id = Column(Integer, ForeignKey("projects.id"))
    document_type = Column(String(100))  # requirement, specification, etc.
    key_requirements = Column(Text)  # JSON
    complexity_score = Column(Float)
    estimated_effort = Column(Float)
    risks = Column(Text)  # JSON
    created_at = Column(DateTime(timezone=True), default=utc_now)


class Notification(Base):
    """Notification model"""
    __tablename__ = "notifications"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    title = Column(String(255))
    message = Column(Text)
    type = Column(String(50))  # bid_update, recommendation, alert
    is_read = Column(Boolean, default=False)
    created_at = Column(DateTime(timezone=True), default=utc_now)
    read_at = Column(DateTime(timezone=True), nullable=True)

    user = relationship("User", back_populates="notifications")


class Analytics(Base):
    """Analytics data"""
    __tablename__ = "analytics"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    event_type = Column(String(100))  # bid_submitted, prediction_made, etc.
    event_data = Column(Text)  # JSON
    timestamp = Column(DateTime(timezone=True), default=utc_now)


class SyntheticExperiment(Base):
    """Saved definition of a synthetic-operator backtest experiment (Lab)."""
    __tablename__ = "synthetic_experiments"

    id = Column(Integer, primary_key=True)
    name = Column(String(200), nullable=False)
    description = Column(String(2000), nullable=True)
    # JSON serialized as Text (mirrors PaperBidRun.request_json/result_payload):
    # start_at/end_at/category/limit/scenario/cutoff_hours/history_limit/
    # settle_actions.
    params_json = Column(Text, default="{}")
    # JSON serialized list[str] of target synthetic operator slugs. Empty list
    # means "all seeded synthetic operators".
    operator_slugs_json = Column(Text, default="[]")
    created_at = Column(DateTime(timezone=True), default=utc_now)
    updated_at = Column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)

    runs = relationship(
        "SyntheticExperimentRun",
        back_populates="experiment",
        cascade="all, delete-orphan",
        order_by="SyntheticExperimentRun.created_at.desc()",
    )


class SyntheticExperimentRun(Base):
    """A single asynchronous execution of a synthetic experiment."""
    __tablename__ = "synthetic_experiment_runs"

    id = Column(Integer, primary_key=True)
    experiment_id = Column(
        Integer, ForeignKey("synthetic_experiments.id"), nullable=False, index=True
    )
    status = Column(String(50), default="queued", index=True)  # queued/running/completed/failed
    task_id = Column(String(100), nullable=True, index=True)
    started_at = Column(DateTime(timezone=True), nullable=True)
    finished_at = Column(DateTime(timezone=True), nullable=True)
    error = Column(Text, nullable=True)
    summary_json = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), default=utc_now)

    experiment = relationship("SyntheticExperiment", back_populates="runs")
    results = relationship(
        "SyntheticExperimentResult",
        back_populates="run",
        cascade="all, delete-orphan",
        order_by="SyntheticExperimentResult.operator_slug.asc()",
    )


class SyntheticExperimentResult(Base):
    """Per-operator metrics persisted from a synthetic experiment run."""
    __tablename__ = "synthetic_experiment_results"

    id = Column(Integer, primary_key=True)
    run_id = Column(
        Integer, ForeignKey("synthetic_experiment_runs.id"), nullable=False, index=True
    )
    operator_slug = Column(String(100), nullable=False, index=True)
    metrics_json = Column(Text, nullable=False)
    settlement_sample_json = Column(Text, nullable=True)
    # JSON-as-Text breakdown of settlements (Phase 2 Experiment Lab): grouped
    # category / budget-band win rates. Shape:
    # {"by_category": [...], "by_budget_band": [...]}. Nullable for pre-Phase-2
    # rows; serialization falls back to an empty breakdown.
    breakdown_json = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), default=utc_now)

    run = relationship("SyntheticExperimentRun", back_populates="results")


class NoticeEligibilityLabel(Base):
    """공고 참가자격 식별 라벨(협회/엔지니어링 조건) — 소스별 1라벨.

    ML 실증 데이터셋(로드맵 Phase 3)·G-3 식별 precision/recall 리포트의 원천.
    라벨은 이 단계에선 데이터로만 존재하며 classifier/수집/추천은 읽지 않는다.
    ``label`` 은 ``eligibility_labeling.LABEL_VALUES``, ``source`` 는
    ``LABEL_SOURCES`` 값이다(매직값 금지 §4.5.1). ``(project_id, source)`` 유니크로
    소스별 1라벨을 강제하고 재생성은 upsert 한다. ``rationale`` 은 감사용 근거
    요약(매칭 term/필드·사유), ``labeler_version`` 은 규칙 버전 추적이다.
    """
    __tablename__ = "notice_eligibility_labels"
    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "source",
            name="uq_notice_eligibility_labels_project_source",
        ),
    )

    id = Column(Integer, primary_key=True)
    project_id = Column(
        Integer, ForeignKey("projects.id"), nullable=False, index=True
    )
    label = Column(String(20), nullable=False)
    source = Column(String(20), nullable=False)
    rationale = Column(Text, nullable=True)
    labeler_version = Column(String(50), nullable=True)
    created_at = Column(DateTime(timezone=True), default=utc_now)
    updated_at = Column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)

    project = relationship("Project")


class OnboardingSuggestion(Base):
    """온보딩 후보에 대한 운영자 결정(수락/거부/수정)의 append-only 감사 로그.

    설계 근거: ``docs/superpowers/specs/
    2026-07-03-business-number-guided-onboarding-design.md`` §3 "수정하거나 거부한
    후보도 저장해 다음 온보딩 규칙 개선에 사용". apply 엔드포인트가 받은 각 결정을
    **1결정=1행**으로 기록한다 — 재적용은 upsert 가 아니라 새 행이므로 멱등을 강제하지
    않고(각 apply=이벤트) 온보딩 규칙 학습의 시계열 원천이 된다. 프로필/전략 반영과
    무관하게 ``rejected``/``pending`` 결정도 여기에는 남는다(정직 명세 §2: 거부/보류는
    프로필에 반영하지 않되 감사에는 남긴다).

    per-operator/synthetic 격리: ``user_id`` 는 apply 를 수행한 operator(자기 스코프,
    ``resolve_write_operator`` 로 해석된 계정)로만 채운다 — synthetic 결정이 canonical
    감사에 섞이지 않는다. 이 테이블은 데이터로만 존재하며 추천/분류/수집 동작을 바꾸지
    않는다.

    컬럼 의미:
    - ``field`` — ``onboarding.apply.APPLYABLE_FIELDS`` 키(대상 프로필/전략 컬럼명).
    - ``value`` — 사용자가 결정한 값의 JSON 직렬 텍스트(str/float/list 원형 복원).
    - ``status`` — ``onboarding.apply.DecisionStatus`` 값(accepted/rejected/modified/
      pending). 허용값은 서비스 enum 단일 출처를 재사용한다(매직값 금지 §4.5.1).
    - ``source``/``confidence``/``reason`` — GET 후보에서 온 provenance(있으면 기록,
      없으면 null). 감사 행은 불변이라 ``updated_at`` 을 두지 않는다.
    """

    __tablename__ = "onboarding_suggestions"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    field = Column(String(50), nullable=False)
    value = Column(Text, nullable=False)
    status = Column(String(20), nullable=False)
    source = Column(String(50), nullable=True)
    confidence = Column(Float, nullable=True)
    reason = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), default=utc_now)

    user = relationship("User")
