"""Tests for the eligibility labelset (persist + precision/recall report).

- classify_eligibility: 규칙 데이터 케이스 값 테이블(positive 3경로/negative/ambiguous).
- generate script: upsert 멱등 + dry-run 무기록(주입된 test_db 세션).
- report script: compute_precision_recall 순수 + rule-only / rule+operator 집계.
- 모델 (project_id, source) 유니크 제약.

실 API/외부 호출 없음. 스크립트는 패키지가 아니라 경로 로드한다.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest
from sqlalchemy.exc import IntegrityError

from app.models.models import NoticeEligibilityLabel, Project
from app.services.eligibility_labeling import (
    LABEL_AMBIGUOUS,
    LABEL_NEGATIVE,
    LABEL_POSITIVE,
    LABELER_VERSION,
    SOURCE_OPERATOR,
    SOURCE_RULE,
    classify_eligibility,
    extract_eligibility_labels,
)

_SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


def _load(module_name: str):
    spec = importlib.util.spec_from_file_location(
        module_name, _SCRIPTS / f"{module_name}.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


generate = _load("generate_eligibility_labels")
precision = _load("report_eligibility_precision")


def _verdict(eligibility_raw: dict | None, title: str | None = None):
    return classify_eligibility(
        extract_eligibility_labels(eligibility_raw, title=title)
    )


# --- classify_eligibility: 규칙 데이터 케이스 값 테이블 -----------------------


def test_positive_from_association_membership():
    verdict = _verdict({"license_limits": [{"lcnsLmtNm": "한국엔지니어링협회 가입"}]})

    assert verdict.label == LABEL_POSITIVE
    # 근거에 매칭 term/필드가 남는다(§2 감사).
    assert "엔지니어링협회" in verdict.rationale
    assert "license_limits.lcnsLmtNm" in verdict.rationale


def test_positive_from_engineering_business():
    verdict = _verdict({"license_limits": [{"lcnsLmtNm": "엔지니어링사업자"}]})

    assert verdict.label == LABEL_POSITIVE
    assert "엔지니어링사업자" in verdict.rationale


def test_positive_from_tech_field_only():
    # 협회/엔지니어링 없이 기술부문(항만및해안)만 있어도 positive.
    verdict = _verdict({"license_limits": [{"lcnsLmtNm": "항만및해안 설계"}]})

    assert verdict.label == LABEL_POSITIVE
    assert "항만및해안" in verdict.rationale


def test_negative_when_data_present_but_no_match():
    verdict = _verdict({"license_limits": [{"lcnsLmtNm": "정보통신공사업"}]})

    assert verdict.label == LABEL_NEGATIVE
    assert "매칭 0" in verdict.rationale


def test_ambiguous_when_no_eligibility_data():
    assert _verdict(None).label == LABEL_AMBIGUOUS
    assert _verdict({}).label == LABEL_AMBIGUOUS
    assert "판정 불가" in _verdict(None).rationale


def test_title_only_match_is_not_positive():
    # title 참고 매칭만 있으면 라벨 근거가 아니므로 negative(자격 데이터는 존재).
    verdict = _verdict(
        {"license_limits": [{"lcnsLmtNm": "정보통신공사업"}]},
        title="엔지니어링사업자 대상 항만 용역",
    )

    assert verdict.label == LABEL_NEGATIVE


# --- compute_precision_recall: 순수 값 테이블 --------------------------------


def test_precision_recall_perfect_agreement():
    pr = precision.compute_precision_recall(
        [(LABEL_POSITIVE, LABEL_POSITIVE), (LABEL_NEGATIVE, LABEL_NEGATIVE)]
    )

    assert (pr.tp, pr.fp, pr.fn, pr.support) == (1, 0, 0, 2)
    assert pr.precision == 1.0
    assert pr.recall == 1.0


def test_precision_recall_mixed_counts():
    # rule=pos/op=neg → fp, rule=neg/op=pos → fn.
    pr = precision.compute_precision_recall(
        [
            (LABEL_POSITIVE, LABEL_POSITIVE),  # tp
            (LABEL_POSITIVE, LABEL_NEGATIVE),  # fp
            (LABEL_NEGATIVE, LABEL_POSITIVE),  # fn
            (LABEL_AMBIGUOUS, LABEL_NEGATIVE),  # true-neg-ish (no contribution)
        ]
    )

    assert (pr.tp, pr.fp, pr.fn, pr.support) == (1, 1, 1, 4)
    assert pr.precision == pytest.approx(0.5)
    assert pr.recall == pytest.approx(0.5)


def test_precision_recall_empty_is_none():
    pr = precision.compute_precision_recall([])

    assert pr.support == 0
    assert pr.precision is None
    assert pr.recall is None


# --- aggregate_report: rule-only vs rule+operator ----------------------------


def _label_row(project_id, label, source, *, notice="R0001", category="service", rationale=""):
    return precision.LabelRow(
        project_id=project_id,
        notice_number=notice,
        category=category,
        label=label,
        source=source,
        rationale=rationale,
    )


def test_aggregate_rule_only_waits_for_operator():
    rows = [
        _label_row(1, LABEL_POSITIVE, SOURCE_RULE, notice="R1", rationale="엔지니어링사업자"),
        _label_row(2, LABEL_NEGATIVE, SOURCE_RULE, notice="R2"),
        _label_row(3, LABEL_AMBIGUOUS, SOURCE_RULE, notice="R3"),
    ]

    data = precision.aggregate_report(rows)

    assert data.rule_total == 3
    assert data.distribution[LABEL_POSITIVE] == 1
    assert data.positive_samples == [("R1", "엔지니어링사업자")]
    assert data.precision_recall.support == 0  # operator 라벨 대기
    text = precision.render_report(data)
    assert "operator 라벨 대기" in text
    assert "positive=1" in text


def test_aggregate_intersection_precision_recall_with_operator():
    rows = [
        # project 1: rule positive, operator positive → tp
        _label_row(1, LABEL_POSITIVE, SOURCE_RULE),
        _label_row(1, LABEL_POSITIVE, SOURCE_OPERATOR),
        # project 2: rule positive, operator negative → fp
        _label_row(2, LABEL_POSITIVE, SOURCE_RULE),
        _label_row(2, LABEL_NEGATIVE, SOURCE_OPERATOR),
        # project 3: rule negative, operator positive → fn
        _label_row(3, LABEL_NEGATIVE, SOURCE_RULE),
        _label_row(3, LABEL_POSITIVE, SOURCE_OPERATOR),
        # project 4: rule only (no operator) → excluded from intersection
        _label_row(4, LABEL_POSITIVE, SOURCE_RULE),
    ]

    data = precision.aggregate_report(rows)

    pr = data.precision_recall
    assert pr.support == 3  # only projects 1-3 have both sources
    assert (pr.tp, pr.fp, pr.fn) == (1, 1, 1)
    assert pr.precision == pytest.approx(0.5)
    assert pr.recall == pytest.approx(0.5)
    # operator rows do not inflate rule distribution.
    assert data.distribution[LABEL_POSITIVE] == 3


def test_aggregate_category_breakdown():
    rows = [
        _label_row(1, LABEL_POSITIVE, SOURCE_RULE, category="construction"),
        _label_row(2, LABEL_NEGATIVE, SOURCE_RULE, category="construction"),
        _label_row(3, LABEL_POSITIVE, SOURCE_RULE, category="service"),
    ]

    data = precision.aggregate_report(rows)

    assert data.category_breakdown["construction"][LABEL_POSITIVE] == 1
    assert data.category_breakdown["construction"][LABEL_NEGATIVE] == 1
    assert data.category_breakdown["service"][LABEL_POSITIVE] == 1


# --- generate script: upsert 멱등 + dry-run (test_db 주입) --------------------


def _seed_projects(db):
    db.add_all(
        [
            Project(
                id=1,
                notice_number="R0001",
                title="항만 용역",
                category="service",
                eligibility_raw={"license_limits": [{"lcnsLmtNm": "엔지니어링사업자"}]},
            ),
            Project(
                id=2,
                notice_number="R0002",
                title="일반 용역",
                category="service",
                eligibility_raw={"license_limits": [{"lcnsLmtNm": "정보통신공사업"}]},
            ),
            Project(
                id=3,
                notice_number="R0003",
                title="자격 없음",
                category="service",
                eligibility_raw=None,  # eligibility 없음 → 라벨 대상 아님
            ),
        ]
    )
    db.commit()


def test_generate_labels_idempotent_upsert(test_db):
    _seed_projects(test_db)

    first = generate.run_generation(test_db)

    assert first.candidates == 2  # project 3 has NULL eligibility_raw → excluded
    assert first.by_outcome["inserted"] == 2
    labels = test_db.query(NoticeEligibilityLabel).all()
    assert len(labels) == 2
    by_project = {row.project_id: row for row in labels}
    assert by_project[1].label == LABEL_POSITIVE
    assert by_project[2].label == LABEL_NEGATIVE
    assert all(row.source == SOURCE_RULE for row in labels)
    assert all(row.labeler_version == LABELER_VERSION for row in labels)

    # Re-run: no duplicates, rows updated in place.
    second = generate.run_generation(test_db)

    assert second.by_outcome["updated"] == 2
    assert second.by_outcome["inserted"] == 0
    assert test_db.query(NoticeEligibilityLabel).count() == 2


def test_generate_dry_run_writes_nothing(test_db):
    _seed_projects(test_db)

    stats = generate.run_generation(test_db, dry_run=True)

    assert stats.candidates == 2  # computed
    assert test_db.query(NoticeEligibilityLabel).count() == 0  # but not persisted


def test_load_label_rows_joins_projects(test_db):
    _seed_projects(test_db)
    generate.run_generation(test_db)
    # inject an operator ground-truth label for project 1.
    test_db.add(
        NoticeEligibilityLabel(
            project_id=1, label=LABEL_POSITIVE, source=SOURCE_OPERATOR
        )
    )
    test_db.commit()

    rows = precision.load_label_rows(test_db)
    data = precision.aggregate_report(rows)

    assert data.rule_total == 2
    assert data.precision_recall.support == 1  # project 1 rule ∩ operator
    assert data.precision_recall.tp == 1


# --- 모델 유니크 제약 --------------------------------------------------------


def test_unique_project_source_constraint(test_db):
    test_db.add(
        NoticeEligibilityLabel(project_id=1, label=LABEL_POSITIVE, source=SOURCE_RULE)
    )
    test_db.commit()

    test_db.add(
        NoticeEligibilityLabel(project_id=1, label=LABEL_NEGATIVE, source=SOURCE_RULE)
    )
    with pytest.raises(IntegrityError):
        test_db.commit()
    test_db.rollback()

    # 다른 source 는 허용된다(소스별 1라벨).
    test_db.add(
        NoticeEligibilityLabel(project_id=1, label=LABEL_POSITIVE, source=SOURCE_OPERATOR)
    )
    test_db.commit()
    assert test_db.query(NoticeEligibilityLabel).count() == 2
