"""Persisted application-operation models."""

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text

from app.core.database import Base
from app.core.time import utc_now


class SimilarProjectsRefreshOperation(Base):
    """Opaque, operator-scoped handle for one similar-project refresh."""

    __tablename__ = "similar_projects_refresh_operations"

    operation_id = Column(String(64), primary_key=True)
    project_id = Column(
        Integer,
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    operator_id = Column(
        Integer,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    task_id = Column(String(155), nullable=True, index=True)
    status = Column(String(32), nullable=False, default="accepted", index=True)
    error_message = Column(Text, nullable=True)
    expires_at = Column(DateTime(timezone=True), nullable=False, index=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        onupdate=utc_now,
    )
