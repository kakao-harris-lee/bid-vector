from __future__ import annotations

import json

from scripts.production_smoke_test import sanitize_evidence, write_evidence


def _raw_evidence() -> dict:
    return {
        "started_at": "2026-08-04T01:00:00+00:00",
        "finished_at": "2026-08-04T01:01:00+00:00",
        "status": "passed",
        "base_url": "https://user:secret@example.invalid/api",
        "write_mode": True,
        "steps": [
            {
                "name": "operator profile",
                "required": True,
                "status": "passed",
                "summary": "operator_id=7 profile_configured=True",
                "payload": {
                    "operator_id": 7,
                    "profile_configured": True,
                    "email": "private@example.com",
                    "company": "Private Company",
                },
            },
            {
                "name": "KONEPS crawl write check",
                "required": True,
                "status": "passed",
                "payload": {
                    "source": "koneps-openapi",
                    "job_status": "completed",
                    "collected_count": 1,
                    "metadata": {
                        "crawl_job_id": 91,
                        "received_count": 2,
                        "persisted_count": 1,
                        "drop_reasons": {"invalid_notice": 1},
                        "semantic_input_outbox_event_ids": [301],
                        "request_url": "https://api.example.invalid?token=secret",
                    },
                    "items": [
                        {
                            "notice_number": "N-001",
                            "title": "Sensitive title",
                            "metadata": {
                                "raw_openapi_item": {"bizno": "123-45-67890"}
                            },
                        }
                    ],
                },
            },
            {
                "name": "strategy monitor write check",
                "required": True,
                "status": "passed",
                "payload": {
                    "result": {
                        "monitor_run_id": 41,
                        "notification_count": 1,
                        "results": [
                            {
                                "project_id": 12,
                                "decision_record_id": 72,
                                "notification_id": 81,
                                "reasoning": "sensitive model rationale",
                            }
                        ],
                    },
                    "detail": {"raw_payload": "secret detail"},
                },
            },
        ],
    }


def test_sanitize_evidence_keeps_lineage_and_drops_sensitive_payloads():
    sanitized = sanitize_evidence(_raw_evidence())
    rendered = json.dumps(sanitized, ensure_ascii=False)

    assert sanitized["evidence_schema_version"] == 2
    assert "base_url" not in sanitized
    assert "private@example.com" not in rendered
    assert "Private Company" not in rendered
    assert "raw_openapi_item" not in rendered
    assert "123-45-67890" not in rendered
    assert "token=secret" not in rendered
    assert "Sensitive title" not in rendered
    assert "sensitive model rationale" not in rendered
    assert "secret detail" not in rendered
    assert "N-001" in rendered
    assert '"crawl_job_id": 91' in rendered
    assert '"monitor_run_id": 41' in rendered
    assert '"decision_record_id": 72' in rendered
    assert '"notification_id": 81' in rendered
    assert '"semantic_input_outbox_event_ids": [301]' in rendered
    assert '"invalid_notice": 1' in rendered


def test_write_evidence_persists_only_sanitized_shape(tmp_path):
    path = tmp_path / "smoke.json"
    write_evidence(str(path), _raw_evidence())

    persisted = json.loads(path.read_text(encoding="utf-8"))

    assert persisted == sanitize_evidence(_raw_evidence())
