# G-2 Exit Review Template

이 문서는 G-2 exit review를 작성하기 위한 템플릿과 evidence manifest contract다. 현재 문서 자체는 G-2 완료 선언이 아니다. 실제 review는 `docs/operations/g2-evidence-runbook.md`로 N일 증적을 모은 뒤, 이 템플릿의 manifest와 checklist를 채워서 작성한다.

기본 판정은 `pending`이다. `approve` 또는 `hold`는 manifest에 실제 파일 경로와 날짜별 상태가 채워진 뒤 review에서만 선택한다.

## 1. Review 산출물

권장 저장 위치:

- Review 문서: `reports/g2-evidence/<review_id>/exit-review.md`
- Evidence manifest: `reports/g2-evidence/<review_id>/manifest.json`
- 일일 원본 증적: `reports/g2-evidence/YYYY-MM-DD/...`

`review_id`는 `g2-exit-YYYYMMDD`처럼 검토일을 포함한다. 증적 파일은 raw secret, raw Telegram chat id, app device token을 포함하지 않아야 하며, 알림 대상은 masked label 또는 route metadata로만 남긴다.

## 2. Evidence Manifest 구조

Manifest는 사람이 읽을 수 있는 JSON으로 유지한다. 모든 `path`는 repository root 기준 상대 경로를 사용한다. 실제 파일이 없는 path는 `status=missing`으로 표시하고, `approve` 근거로 쓰지 않는다.

```json
{
  "review_id": "g2-exit-YYYYMMDD",
  "manifest_version": 1,
  "status": "draft|ready_for_review|reviewed",
  "basis": {
    "roadmap": "docs/roadmap.md",
    "runbook": "docs/operations/g2-evidence-runbook.md",
    "review_template": "docs/operations/g2-exit-review-template.md",
    "basis_commit": "<git-sha-used-for-review>"
  },
  "evidence_window": {
    "start_date": "YYYY-MM-DD",
    "end_date": "YYYY-MM-DD",
    "required_days": 0,
    "observed_days": 0,
    "counted_days": 0,
    "timezone": "Asia/Seoul"
  },
  "operators": [
    {
      "operator_id": "<operator-id>",
      "username": "synthetic-...",
      "company": "<company-name>",
      "is_synthetic": true,
      "operator_scope_status": "pass|fail|missing|mixed_scope",
      "profile": {
        "status": "pass|fail|missing|mixed_scope",
        "path": "reports/g2-evidence/YYYY-MM-DD/<operator_id>/profile.json",
        "required_fields_present": true
      },
      "strategy": {
        "status": "pass|fail|missing|mixed_scope",
        "path": "reports/g2-evidence/YYYY-MM-DD/<operator_id>/strategy.json",
        "thresholds_valid": true
      },
      "notification_channel": {
        "status": "pass|fail|missing|mixed_scope",
        "mode": "active|dry_run_only|skipped|missing",
        "path": "reports/g2-evidence/YYYY-MM-DD/<operator_id>/notification-channels.json",
        "masked_target_present": true,
        "raw_secret_absent": true
      },
      "evidence_paths": {
        "g2_evidence": [
          "reports/g2-evidence/YYYY-MM-DD/<operator_id>/g2-evidence.json"
        ],
        "candidate_preview": [
          "reports/g2-evidence/YYYY-MM-DD/<operator_id>/strategy-candidates.json"
        ],
        "strategy_monitor": [
          "reports/g2-evidence/YYYY-MM-DD/<operator_id>/strategy-monitor.json"
        ],
        "decision_experiments": [
          "reports/g2-evidence/YYYY-MM-DD/<operator_id>/decision-experiments.json"
        ],
        "decision_apply_dry_run": [
          "reports/g2-evidence/YYYY-MM-DD/<operator_id>/decision-experiment-apply-strategy-dry-run.json"
        ],
        "operations_dashboard": [
          "reports/g2-evidence/YYYY-MM-DD/<operator_id>/operations-dashboard.json"
        ]
      }
    }
  ],
  "daily_status": [
    {
      "date": "YYYY-MM-DD",
      "status": "pass|partial|fail|excluded",
      "summary": "short human-readable status",
      "operators": {
        "<operator-id>": {
          "profile": "pass|fail|missing|mixed_scope",
          "strategy": "pass|fail|missing|mixed_scope",
          "notification_channel": "pass|fail|missing|mixed_scope",
          "candidate_preview": "pass|fail|missing|mixed_scope",
          "strategy_monitor": "pass|fail|skipped|missing|mixed_scope",
          "decision_experiment": "pass|fail|skipped|missing|mixed_scope",
          "g2_evidence_status": "ready|insufficient|missing|mixed_scope",
          "blocking_gap_ids": []
        }
      },
      "dry_run_item_ids": [],
      "approved_execution_item_ids": [],
      "excluded_evidence": [
        {
          "path": "reports/g2-evidence/YYYY-MM-DD/...",
          "reason": "canonical_only|mixed_scope|operator_mismatch|raw_secret|incomplete"
        }
      ]
    }
  ],
  "blocking_gaps": [
    {
      "gap_id": "GAP-001",
      "date": "YYYY-MM-DD",
      "operator_id": "<operator-id-or-null>",
      "source": "g2-evidence.blocking_gaps|reviewer|runbook-checklist",
      "category": "credential|KONEPS response|no candidates|Telegram/app notification|task/broker|mixed data|missing evidence",
      "description": "gap text copied or summarized from evidence",
      "status": "open|triaged|resolved|excluded|accepted_hold",
      "treatment": "rerun|documented_not_counted|mapping_fixed|operator_removed|hold",
      "owner": "<person-or-role>",
      "resolution_path": "reports/g2-evidence/YYYY-MM-DD/...",
      "resolved_date": "YYYY-MM-DD"
    }
  ],
  "action_register": {
    "dry_run_items": [
      {
        "item_id": "DRY-001",
        "date": "YYYY-MM-DD",
        "scope": "global|operator",
        "operator_id": "<operator-id-or-null>",
        "source": "read-only API|dry-run script|dashboard inspection",
        "output_path": "reports/g2-evidence/YYYY-MM-DD/...",
        "result": "pass|fail|blocked",
        "notes": ""
      }
    ],
    "approved_execution_items": [
      {
        "item_id": "APP-001",
        "date": "YYYY-MM-DD",
        "approval_ref": "<ticket/comment/manual-approval-ref>",
        "approved_by": "<person>",
        "approved_at": "YYYY-MM-DDTHH:MM:SSZ",
        "execution_window": "YYYY-MM-DD HH:MM-HH:MM TZ",
        "scope": "global|operator",
        "operator_id": "<operator-id-or-null>",
        "operation": "strategy_monitor|synthetic_experiment_run|smoke_write|decision_apply|other",
        "output_path": "reports/g2-evidence/YYYY-MM-DD/...",
        "stop_or_rollback_condition": "condition checked before execution",
        "result": "pass|fail|rolled_back|blocked"
      }
    ]
  }
}
```

### Manifest 작성 규칙

- `operators[]`에는 G-2 판정에 포함할 operator만 넣는다. 제외한 operator는 `excluded_evidence` 또는 review note에 사유를 남긴다.
- `profile.path`, `strategy.path`, `notification_channel.path`는 operator별 최신 상태 파일을 가리킨다. 날짜별로 값이 바뀌면 `daily_status`와 `evidence_paths`에 해당 날짜 파일을 모두 남긴다.
- `daily_status[].status=pass`는 해당 날짜가 G-2 판단에 계산 가능하다는 뜻이다. `partial`은 재실행 또는 gap 처리가 필요하고, `excluded`는 `approve`의 N일 카운트에 넣지 않는다.
- `blocking_gaps[].status=resolved`는 resolution path가 실제로 존재하고 reviewer가 원 gap 해소를 확인했을 때만 사용한다.
- `excluded` gap은 G-2 성공 증거로 쓰지 않는다는 뜻이지 성공 처리가 아니다. `accepted_hold`가 하나라도 남아 있으면 최종 판정은 `hold`다.
- `dry_run_items`와 `approved_execution_items`는 반드시 분리한다. DB write, 실제 KONEPS 호출, 실제 Telegram/app 송신, `dry_run=false` 전략 적용은 `approved_execution_items`에 approval reference가 있어야 한다.

## 3. Exit Review 문서 양식

```markdown
# G-2 Exit Review

- review_id:
- 검토일:
- 검토 기간: YYYY-MM-DD ~ YYYY-MM-DD (N일)
- 기준 commit:
- 검토자:
- manifest: reports/g2-evidence/<review_id>/manifest.json
- 판정: pending

## 1. 대상 사업자

| operator_id | username | company | profile | strategy | notification mode | counted evidence days |
|---|---|---|---|---|---|---|
| | | | pass/fail/missing | pass/fail/missing | active/dry_run_only/skipped/missing | 0/N |

## 2. 날짜별 상태

| 날짜 | overall | counted | 주요 dry-run item | 승인 후 실행 item | open blocking gaps |
|---|---|---|---|---|---|
| YYYY-MM-DD | pass/partial/fail/excluded | yes/no | DRY-001 | APP-001 | GAP-001 |

## 3. Exit Gate 판정

| Gate | 판정 | 근거 파일 |
|---|---|---|
| 3개 이상 가상 사업자가 독립 ID/사업자 정보/전략으로 운영됨 | pass/fail | manifest.operators, profile/strategy paths |
| 각 사업자의 공고 추천과 알림이 서로 섞이지 않음 | pass/fail | g2-evidence, notification channel, operations dashboard paths |
| 관리자 화면에서 사업자별 백테스트, smoke, 통계, 수집 상태를 구분해 볼 수 있음 | pass/fail | admin/operations or operations dashboard evidence paths |
| 사용자 화면은 관리 기능 없이 투찰 판단에 집중함 | pass/fail | dashboard inspection evidence path |

## 4. Evidence 요약

- operator roster:
- G-2 evidence ledger:
- strategy monitor:
- decision experiment:
- synthetic experiment or backtest:
- notification routing:
- scheduled smoke:
- admin/user surface:

## 5. Blocking Gaps 처리

| gap_id | date | operator_id | category | status | treatment | resolution path |
|---|---|---|---|---|---|---|
| GAP-001 | | | | open/triaged/resolved/excluded/accepted_hold | | |

## 6. Dry-run 항목

| item_id | date | scope | source | output path | result |
|---|---|---|---|---|---|
| DRY-001 | | | | | pass/fail/blocked |

## 7. 승인 후 실행 항목

| item_id | date | approval ref | operation | operator_id | output path | result |
|---|---|---|---|---|---|---|
| APP-001 | | | | | | pass/fail/rolled_back/blocked |

## 8. 제외한 증적

| path | reason | reviewer note |
|---|---|---|
| | canonical_only/mixed_scope/operator_mismatch/raw_secret/incomplete | |

## 9. 최종 판정

- G-2 exit: approve / hold
- approve 근거:
- hold 사유:
- 다음 조치:
```

## 4. Approve/Hold 기준

### Approve 가능 조건

`approve`는 아래 조건을 모두 만족할 때만 선택한다.

1. Manifest가 `ready_for_review` 또는 `reviewed` 상태이며, review 문서와 manifest가 같은 `review_id`를 참조한다.
2. `evidence_window.counted_days >= required_days`이고, counted day가 모두 `daily_status.status=pass`다.
3. `operators[]`에 synthetic 또는 G-2 검증 대상으로 지정된 operator가 3개 이상 있으며, 각 operator의 `operator_id`, `username`, `profile`, `strategy`, `notification_channel`이 실제 파일 path와 함께 확인된다.
4. 각 counted day에서 모든 included operator의 `g2_evidence_status`가 `ready`이거나, `insufficient` 항목이 G-2 gate 밖의 canonical smoke처럼 명확히 제외되어 `blocking_gaps`에 `excluded`로 남아 있다.
5. `blocking_gaps`에 `open`, `triaged`, `accepted_hold` 상태가 없다. `excluded` gap은 성공 근거로 쓰지 않았다는 note가 있어야 한다.
6. `mixed_scope`, `operator_mismatch`, canonical-only 증적을 G-2 ready 근거로 사용하지 않았다.
7. 알림 증적은 operator별 route가 분리되었거나, synthetic/non-canonical operator에 대해 `dry_run_only` 또는 `skipped` 정책이 명확하다. canonical Telegram/app target으로 synthetic/non-canonical 알림이 섞인 흔적이 없어야 한다.
8. 승인 후 실행 항목은 모두 `approved_execution_items`에 approval reference, 실행 창, output path, result가 남아 있다.
9. dry-run 또는 read-only 항목은 `dry_run_items`에만 기록되어 있고, DB write 또는 실제 외부 송신 성공처럼 해석하지 않았다.
10. 관리자 surface와 사용자 surface의 역할 분리 증적이 있고, 사용자 화면에 cross-operator 관리 기능이 노출되지 않았다는 확인이 남아 있다.

G-0 scheduled smoke는 운영 안정성의 선행 신호다. canonical-only smoke를 G-2 per-operator 성공 증거로 계산하면 안 된다. 다만 canonical scheduled smoke가 별도 운영 안정성 전제로 green이고, G-2 operator별 증적이 독립적으로 충분하면 canonical-only smoke 자체만으로 `hold`를 선언할 필요는 없다.

### Hold 조건

아래 중 하나라도 있으면 `hold`를 선택한다.

- counted operator가 3개 미만이다.
- operator의 profile, strategy, notification channel 또는 G-2 ledger path가 없거나 target operator와 맞지 않는다.
- `blocking_gaps`에 `open`, `triaged`, `accepted_hold`가 남아 있다.
- `mixed_scope`, `operator_mismatch`, canonical-only evidence를 pass 근거로 사용했다.
- synthetic/non-canonical 알림이 canonical Telegram/app target으로 송신되었거나, 송신 여부를 구분할 수 없다.
- 승인 없이 DB write, 실제 KONEPS 호출, 실제 Telegram/app 송신, `dry_run=false` 전략 적용이 수행되었다.
- raw Telegram chat id, app device token, secret target이 review artifact에 노출되었다.
- 관리자/사용자 surface 분리 확인이 없거나 사용자 surface에 cross-operator 관리 기능이 노출된다.
- 실패가 `credential`, `KONEPS response`, `no candidates`, `Telegram/app notification`, `task/broker`, `mixed data`, `missing evidence` 중 하나로 분류되지 않아 재현 가능한 조치가 없다.

`hold`는 실패가 아니라 다음 실행 조건을 명확히 하는 상태다. Hold review에는 최소한 hold 사유, gap owner, 재실행 또는 제외 조건, 다음 review에 필요한 파일 path를 남긴다.
