"""Regression guard for the shared read-scoped operator resolver.

``app.core.single_user.resolve_read_operator`` consolidates three previously
byte-identical router helpers (``_resolve_analytics_operator``,
``_resolve_operator_for_read``, ``_resolve_samples_operator``). It is a
security boundary, so these tests pin the exact fallback / 403 / 404 policy:

  - **No bearer token** -> canonical operator fallback; an explicit, mismatched
    ``operator_id`` is rejected with ``403`` (an unauthenticated caller may
    never read another operator's data).
  - **Bearer token** -> standard privileged/non-privileged policy delegated to
    ``resolve_target_operator`` (``403`` cross-account, ``404`` unknown id).

It also asserts the three router-level helpers remain thin aliases of the
shared function, so behavior cannot silently diverge again.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException, status

from app.api.analytics import _resolve_analytics_operator
from app.api.decision_samples import _resolve_samples_operator
from app.api.operator import _resolve_operator_for_read
from app.core.security import get_password_hash
from app.core.single_user import (
    READ_OPERATOR_FORBIDDEN_DETAIL,
    ensure_operator_account,
    resolve_read_operator,
)
from app.models.models import User


def _make_operator(test_db, *, username: str) -> User:
    user = User(
        username=username,
        email=f"{username}@example.test",
        full_name=username,
        company="",
        hashed_password=get_password_hash("pw"),
        is_active=True,
    )
    test_db.add(user)
    test_db.commit()
    test_db.refresh(user)
    return user


def test_helpers_are_thin_aliases_of_shared_resolver():
    # Pinning the consolidation: the three routers must share one implementation.
    assert _resolve_analytics_operator is resolve_read_operator
    assert _resolve_operator_for_read is resolve_read_operator
    assert _resolve_samples_operator is resolve_read_operator


def test_unauthenticated_no_id_falls_back_to_canonical(test_db):
    canonical = ensure_operator_account(test_db)

    resolved = resolve_read_operator(test_db, None, None)

    assert resolved.id == canonical.id
    assert resolved.username == "operator"


def test_unauthenticated_matching_id_returns_canonical(test_db):
    canonical = ensure_operator_account(test_db)

    resolved = resolve_read_operator(test_db, None, int(canonical.id))

    assert resolved.id == canonical.id


def test_unauthenticated_mismatched_id_is_forbidden(test_db):
    canonical = ensure_operator_account(test_db)
    other = _make_operator(test_db, username="synthetic-other")
    assert int(other.id) != int(canonical.id)

    with pytest.raises(HTTPException) as exc_info:
        resolve_read_operator(test_db, None, int(other.id))

    assert exc_info.value.status_code == status.HTTP_403_FORBIDDEN
    assert exc_info.value.detail == READ_OPERATOR_FORBIDDEN_DETAIL


def test_authenticated_self_with_none_id_returns_self(test_db):
    actor = _make_operator(test_db, username="self-operator")

    resolved = resolve_read_operator(test_db, actor, None)

    assert resolved.id == actor.id


def test_authenticated_non_privileged_cross_account_is_forbidden(test_db):
    actor = _make_operator(test_db, username="non-privileged")
    target = _make_operator(test_db, username="synthetic-target")

    with pytest.raises(HTTPException) as exc_info:
        resolve_read_operator(test_db, actor, int(target.id))

    assert exc_info.value.status_code == status.HTTP_403_FORBIDDEN
    assert exc_info.value.detail == READ_OPERATOR_FORBIDDEN_DETAIL


def test_authenticated_canonical_can_target_another_operator(test_db):
    canonical = ensure_operator_account(test_db)  # username == "operator" -> privileged
    target = _make_operator(test_db, username="synthetic-target")

    resolved = resolve_read_operator(test_db, canonical, int(target.id))

    assert resolved.id == target.id


def test_authenticated_privileged_unknown_id_is_not_found(test_db):
    canonical = ensure_operator_account(test_db)

    with pytest.raises(HTTPException) as exc_info:
        resolve_read_operator(test_db, canonical, 999_999)

    assert exc_info.value.status_code == status.HTTP_404_NOT_FOUND
