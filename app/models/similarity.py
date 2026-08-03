"""Persistence models for versioned similarity snapshots and inference events."""

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


class ProjectSimilaritySnapshot(Base):
    """Projection header that records target and corpus freshness, including zero hits."""

    __tablename__ = "project_similarity_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "target_project_id",
            "embedding_model",
            "target_embedding_updated_at",
            "same_category_only",
            "min_similarity_bucket",
            name="uq_project_similarity_snapshot_version",
        ),
        Index(
            "ix_project_similarity_snapshots_lookup",
            "target_project_id",
            "embedding_model",
            "target_embedding_updated_at",
            "same_category_only",
            "min_similarity_bucket",
        ),
    )

    id = Column(Integer, primary_key=True)
    target_project_id = Column(
        Integer,
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    embedding_model = Column(String(255), nullable=False)
    target_embedding_updated_at = Column(DateTime(timezone=True), nullable=False)
    same_category_only = Column(Boolean, default=True, nullable=False)
    min_similarity_bucket = Column(Float, default=0.15, nullable=False)
    corpus_embedding_count = Column(Integer, nullable=False)
    corpus_embedding_updated_at = Column(DateTime(timezone=True), nullable=True)
    edge_count = Column(Integer, default=0, nullable=False)
    source = Column(String(50), nullable=False)
    computed_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)

    edges = relationship(
        "ProjectSimilarityEdge",
        back_populates="snapshot",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class ProjectSimilarityEdge(Base):
    """Ranked candidate row owned by a versioned similarity snapshot."""

    __tablename__ = "project_similarity_edges"
    __table_args__ = (
        UniqueConstraint(
            "snapshot_id",
            "rank",
            name="uq_project_similarity_edges_snapshot_rank",
        ),
        Index("ix_project_similarity_edges_snapshot_rank", "snapshot_id", "rank"),
    )

    id = Column(Integer, primary_key=True)
    snapshot_id = Column(
        Integer,
        ForeignKey("project_similarity_snapshots.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    candidate_project_id = Column(
        Integer,
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    rank = Column(Integer, nullable=False)
    similarity_score = Column(Float, nullable=False)

    snapshot = relationship("ProjectSimilaritySnapshot", back_populates="edges")


class InferenceOutboxEvent(Base):
    """Recoverable and deduplicated event queue for inference side effects."""

    __tablename__ = "inference_outbox_events"
    __table_args__ = (
        UniqueConstraint(
            "event_type",
            "aggregate_type",
            "aggregate_id",
            "dedupe_key",
            name="uq_inference_outbox_event_dedupe",
        ),
        Index("ix_inference_outbox_events_claim", "status", "available_at", "id"),
    )

    id = Column(Integer, primary_key=True)
    event_type = Column(String(100), nullable=False, index=True)
    aggregate_type = Column(String(50), default="project", nullable=False, index=True)
    aggregate_id = Column(Integer, nullable=False, index=True)
    dedupe_key = Column(String(255), nullable=False)
    payload_json = Column(JSON(none_as_null=True), nullable=True)
    status = Column(String(20), default="pending", nullable=False, index=True)
    attempts = Column(Integer, default=0, nullable=False)
    available_at = Column(
        DateTime(timezone=True), default=utc_now, nullable=False, index=True
    )
    locked_at = Column(DateTime(timezone=True), nullable=True)
    processed_at = Column(DateTime(timezone=True), nullable=True)
    last_error = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at = Column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )
