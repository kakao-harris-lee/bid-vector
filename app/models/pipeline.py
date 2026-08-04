"""Persistence models for pipeline lineage, events, and post-commit delivery."""

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship

from app.core.database import Base
from app.core.time import utc_now


class OperatorStrategyRun(Base):
    """Execution history for one manual or scheduled monitoring run."""

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
    projection_not_ready_count = Column(Integer, default=0)
    release_sha = Column(String(64), nullable=True, index=True)
    release_tag = Column(String(255), nullable=True)
    error_message = Column(Text, nullable=True)
    started_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=utc_now)
    updated_at = Column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)

    operator = relationship("User", back_populates="strategy_runs")
    items = relationship(
        "OperatorStrategyRunItem", back_populates="run", cascade="all, delete-orphan"
    )


class OperatorStrategyRunItem(Base):
    """Durable per-project lineage and checkpoint for one monitor run."""

    __tablename__ = "operator_strategy_run_items"
    __table_args__ = (
        UniqueConstraint(
            "run_id", "project_id", name="uq_operator_strategy_run_items_run_project"
        ),
        Index("ix_operator_strategy_run_items_run_status", "run_id", "status"),
    )

    id = Column(Integer, primary_key=True)
    run_id = Column(
        Integer,
        ForeignKey("operator_strategy_runs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=False, index=True)
    status = Column(String(30), nullable=False, default="processing", index=True)
    stage = Column(String(50), nullable=False, default="selected")
    decision_record_id = Column(
        Integer, ForeignKey("bid_decision_records.id"), nullable=True, index=True
    )
    notification_id = Column(
        Integer, ForeignKey("notifications.id"), nullable=True, index=True
    )
    is_new_candidate = Column(Boolean, default=False, nullable=False)
    result_payload = Column(Text, default="{}", nullable=False)
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)
    completed_at = Column(DateTime(timezone=True), nullable=True)

    run = relationship("OperatorStrategyRun", back_populates="items")


class NotificationDeliveryOutbox(Base):
    """Post-commit delivery work linked to the exact monitor notification."""

    __tablename__ = "notification_delivery_outbox"
    __table_args__ = (
        UniqueConstraint(
            "notification_id", "channel", name="uq_notification_delivery_channel"
        ),
        Index(
            "ix_notification_delivery_outbox_claim", "status", "available_at", "id"
        ),
    )

    id = Column(Integer, primary_key=True)
    notification_id = Column(
        Integer, ForeignKey("notifications.id", ondelete="CASCADE"), nullable=False
    )
    monitor_run_id = Column(
        Integer, ForeignKey("operator_strategy_runs.id"), nullable=True, index=True
    )
    operator_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=True, index=True)
    decision_record_id = Column(
        Integer, ForeignKey("bid_decision_records.id"), nullable=True, index=True
    )
    channel = Column(String(30), nullable=False, default="telegram")
    payload_json = Column(JSON(none_as_null=True), nullable=False)
    status = Column(String(20), nullable=False, default="pending", index=True)
    attempts = Column(Integer, nullable=False, default=0)
    available_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)
    locked_at = Column(DateTime(timezone=True), nullable=True)
    processed_at = Column(DateTime(timezone=True), nullable=True)
    last_error = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at = Column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )


class TenderResultEvent(Base):
    """Append-only source observation backing the mutable current snapshot."""

    __tablename__ = "tender_result_events"

    id = Column(Integer, primary_key=True)
    tender_result_id = Column(
        Integer, ForeignKey("tender_results.id", ondelete="SET NULL"), nullable=True
    )
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=True, index=True)
    event_key = Column(String(64), nullable=False, unique=True, index=True)
    event_type = Column(String(50), nullable=False, default="koneps_observation")
    payload_json = Column(JSON(none_as_null=True), nullable=False)
    observed_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)


class TenderResult(Base):
    """Current tender result snapshot backed by append-only observations."""

    __tablename__ = "tender_results"

    id = Column(Integer, primary_key=True)
    project_id = Column(Integer, ForeignKey("projects.id"), index=True)
    is_current = Column(Boolean, default=True, nullable=False, index=True)
    winning_company = Column(String(255))
    winning_amount = Column(Float, default=0.0)
    winning_rate = Column(Float, default=0.0)
    result_status = Column(String(50), default="pending")
    announced_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=utc_now)
    opening_rank1_company = Column(String(255), nullable=True)
    opening_rank1_business_no = Column(String(20), nullable=True)
    opening_rank1_amount = Column(Float, nullable=True)
    opening_rank1_rate = Column(Float, nullable=True)
    opening_participant_count = Column(Integer, nullable=True)
    opened_at = Column(DateTime(timezone=True), nullable=True)
    opening_checked_at = Column(DateTime(timezone=True), nullable=True)

    project = relationship("Project", back_populates="tender_results")


Index(
    "uq_tender_results_current_project",
    TenderResult.project_id,
    unique=True,
    postgresql_where=TenderResult.is_current.is_(True),
).ddl_if(dialect="postgresql")


class CrawlJob(Base):
    """Crawler execution history and source accounting."""

    __tablename__ = "crawl_jobs"

    id = Column(Integer, primary_key=True)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=True, index=True)
    source = Column(String(100), default="koneps")
    target_date = Column(String(20), nullable=True)
    category = Column(String(100), nullable=True, index=True)
    execution_mode = Column(String(30), nullable=True)
    max_items = Column(Integer, nullable=True)
    status = Column(String(50), default="queued")
    result_count = Column(Integer, default=0)
    received_count = Column(Integer, default=0, nullable=False)
    normalized_count = Column(Integer, default=0, nullable=False)
    duplicate_count = Column(Integer, default=0, nullable=False)
    dropped_count = Column(Integer, default=0, nullable=False)
    persisted_count = Column(Integer, default=0, nullable=False)
    source_total_count = Column(Integer, nullable=True)
    pages_fetched = Column(Integer, nullable=True)
    truncated = Column(Boolean, default=False, nullable=False)
    drop_reasons = Column(JSON(none_as_null=True), nullable=True)
    release_sha = Column(String(64), nullable=True, index=True)
    release_tag = Column(String(255), nullable=True)
    error_message = Column(Text, nullable=True)
    celery_task_id = Column(String(155), nullable=True, index=True)
    created_at = Column(DateTime(timezone=True), default=utc_now)
    completed_at = Column(DateTime(timezone=True), nullable=True)

    project = relationship("Project", back_populates="crawl_jobs")


class Notification(Base):
    """Operator notification linked to a monitor run and decision."""

    __tablename__ = "notifications"
    __table_args__ = (
        UniqueConstraint(
            "monitor_run_id",
            "project_id",
            "type",
            name="uq_notifications_monitor_run_project_type",
        ),
    )

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    monitor_run_id = Column(
        Integer, ForeignKey("operator_strategy_runs.id"), nullable=True, index=True
    )
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=True, index=True)
    decision_record_id = Column(
        Integer, ForeignKey("bid_decision_records.id"), nullable=True, index=True
    )
    title = Column(String(255))
    message = Column(Text)
    type = Column(String(50))
    is_read = Column(Boolean, default=False)
    created_at = Column(DateTime(timezone=True), default=utc_now)
    read_at = Column(DateTime(timezone=True), nullable=True)

    user = relationship("User", back_populates="notifications")
