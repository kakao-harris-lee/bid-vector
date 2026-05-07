"""Tests for operations skeleton endpoints."""


def test_crawl_skeleton(client):
    """Crawl endpoint should return a valid skeleton response."""
    response = client.post(
        "/api/v1/operations/crawl",
        json={"source": "koneps", "category": "software"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["job_status"] == "queued"
    assert data["source"] == "koneps"
    assert data["items"] == []


def test_allocate_skeleton(client):
    """Allocation endpoint should pick the fairest candidate."""
    response = client.post(
        "/api/v1/operations/allocate",
        json={
            "project_id": 1,
            "recommended_amount": 12345.67,
            "probability_score": 0.88,
            "candidates": [
                {"user_id": 10, "company_name": "A", "total_awards": 3, "weight": 0.9},
                {"user_id": 11, "company_name": "B", "total_awards": 1, "weight": 0.7},
            ],
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["assigned_user_id"] == 11
    assert data["project_id"] == 1


def test_notify_telegram_skeleton(client):
    """Telegram skeleton endpoint should return configuration-aware response."""
    response = client.post(
        "/api/v1/operations/notify/telegram",
        json={
            "title": "신규 공고",
            "message": "AI 추천가가 준비되었습니다.",
            "url": "https://example.com/notices/1",
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["task_name"] == "send_telegram_notification"
    assert data["status"] in {"ready", "pending_configuration"}