"""Tests for singleton operator password reset."""

from app.core.config import settings


def _bootstrap_operator(
    client,
    username: str = "reset-operator",
    email: str = "reset@example.com",
    password: str = "old-password-123",
):
    return client.post(
        "/api/v1/auth/bootstrap",
        json={
            "username": username,
            "email": email,
            "full_name": "Reset Operator",
            "company": "Reset Bid Corp",
            "password": password,
        },
    )


def test_operator_password_reset_requires_configured_reset_token(client, monkeypatch):
    """Password reset should stay disabled until a server-side reset token is configured."""
    bootstrap = _bootstrap_operator(
        client, username="reset-disabled", email="reset-disabled@example.com"
    )
    assert bootstrap.status_code == 200
    monkeypatch.setattr(settings, "OPERATOR_PASSWORD_RESET_TOKEN", "")

    response = client.post(
        "/api/v1/auth/password-reset",
        json={
            "username": "reset-disabled",
            "reset_token": "token",
            "new_password": "new-password-123",
        },
    )

    assert response.status_code == 503


def test_operator_password_reset_updates_password_and_returns_session(
    client, monkeypatch
):
    """A valid reset token should rotate the operator password and return a fresh login session."""
    bootstrap = _bootstrap_operator(client)
    assert bootstrap.status_code == 200
    monkeypatch.setattr(settings, "OPERATOR_PASSWORD_RESET_TOKEN", "reset-token-123")

    denied = client.post(
        "/api/v1/auth/password-reset",
        json={
            "username": "reset-operator",
            "reset_token": "wrong-token",
            "new_password": "new-password-123",
        },
    )
    assert denied.status_code == 401

    response = client.post(
        "/api/v1/auth/password-reset",
        json={
            "username": "reset-operator",
            "reset_token": "reset-token-123",
            "new_password": "new-password-123",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["token_type"] == "bearer"
    assert payload["access_token"]
    assert payload["username"] == "reset-operator"

    old_session = client.post(
        "/api/v1/auth/session",
        json={"username": "reset-operator", "password": "old-password-123"},
    )
    assert old_session.status_code == 401

    new_session = client.post(
        "/api/v1/auth/session",
        json={"username": "reset-operator", "password": "new-password-123"},
    )
    assert new_session.status_code == 200
