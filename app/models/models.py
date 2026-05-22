"""Database models"""
from sqlalchemy import Boolean, Column, DateTime, Float, ForeignKey, Integer, String, Text
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
    status = Column(String(50), default="open")  # open, re_notice, closed, awarded, failed, cancelled
    created_at = Column(DateTime(timezone=True), default=utc_now)
    deadline = Column(DateTime(timezone=True))
    semantic_text = Column(Text, default="")
    embedding_payload = Column(Text, default="[]")
    embedding_model = Column(String(255), nullable=True)
    embedding_updated_at = Column(DateTime(timezone=True), nullable=True)
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
    total_awards = Column(Integer, default=0)
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
    recommended_amount = Column(Float, default=0.0)
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
    created_at = Column(DateTime(timezone=True), default=utc_now)
    completed_at = Column(DateTime(timezone=True), nullable=True)

    project = relationship("Project", back_populates="crawl_jobs")


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
