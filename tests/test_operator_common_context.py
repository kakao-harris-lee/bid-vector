"""Contract of the ``current_operator_*`` response envelope helpers.

``_operator_context_fields`` is the single source of the two envelope keys and
``_with_current_operator`` is the payload-injecting form the reporting routes use
(analytics + decision-samples). Both must keep the PR #70 convention: the fields
describe the **target** operator, ids are ``int`` and usernames ``str`` even when
the ORM row carries neither.
"""

from __future__ import annotations

from app.api.operator_common import _operator_context_fields, _with_current_operator
from app.models.models import User


def _user(user_id, username) -> User:
    return User(id=user_id, username=username)


def test_context_fields_keys_and_order():
    fields = _operator_context_fields(_user(7, "operator"))
    assert list(fields) == ["current_operator_id", "current_operator_username"]
    assert fields == {
        "current_operator_id": 7,
        "current_operator_username": "operator",
    }


def test__with_current_operator_injects_same_fields():
    operator = _user(7, "operator")
    assert _with_current_operator({}, operator) == _operator_context_fields(operator)


def test__with_current_operator_mutates_and_returns_same_payload():
    payload = {"operator_id": 7, "rows": []}
    result = _with_current_operator(payload, _user(7, "operator"))
    assert result is payload
    # 기존 키가 앞, 주입 키가 뒤 — 라우터 응답의 키 순서를 고정한다.
    assert list(result) == [
        "operator_id",
        "rows",
        "current_operator_id",
        "current_operator_username",
    ]


def test_casts_id_to_int_and_username_to_str():
    result = _with_current_operator({}, _user("7", None))
    assert result == {"current_operator_id": 7, "current_operator_username": ""}
    assert isinstance(result["current_operator_id"], int)


def test_existing_context_keys_are_overwritten():
    stale = {"current_operator_id": 1, "current_operator_username": "stale"}
    result = _with_current_operator(stale, _user(7, "operator"))
    assert result == {
        "current_operator_id": 7,
        "current_operator_username": "operator",
    }
