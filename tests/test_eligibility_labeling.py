"""Tests for eligibility_labeling + report_eligibility_labels.

규칙 데이터 케이스 단위 값 테이블(§4.5.3). 실 API/DB 접속 없이 순수 해석기와
리포트 집계 로직을 검증한다.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

from app.services.eligibility_labeling import (
    FLAG_ASSOCIATION,
    FLAG_ENGINEERING_BUSINESS,
    TITLE_SOURCE,
    ASSOCIATION_QUALIFICATION_TERMS,
    TECH_FIELD_TERMS,
    extract_eligibility_labels,
)

# Load the report script by path (scripts/ is not an importable package).
_SPEC = importlib.util.spec_from_file_location(
    "report_eligibility_labels",
    Path(__file__).resolve().parents[1] / "scripts" / "report_eligibility_labels.py",
)
report = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = report
_SPEC.loader.exec_module(report)


def _row(eligibility_raw: dict | None, title: str | None = None) -> Any:
    """Build a report ReportRow (notice_number irrelevant to aggregation)."""
    return report.ReportRow(
        notice_number="R0001", title=title, eligibility_raw=eligibility_raw
    )


# --- 순수 해석기: 규칙 데이터 케이스 -----------------------------------------


def test_engineering_business_and_tech_field_from_license_field():
    labels = extract_eligibility_labels(
        {"license_limits": [{"lcnsLmtNm": "엔지니어링사업자(항만및해안)"}]}
    )

    assert labels.has_eligibility_data is True
    assert labels.engineering_business_required is True
    assert labels.association_required is False
    assert labels.tech_fields == ("항만및해안",)
    # provenance: 어느 필드/원문이 어떤 용어로 잡혔는지 남는다.
    terms = {(m.term, m.source_field) for m in labels.matches}
    assert ("엔지니어링사업자", "license_limits.lcnsLmtNm") in terms
    assert ("항만및해안", "license_limits.lcnsLmtNm") in terms
    assert all(m.evidence == "엔지니어링사업자(항만및해안)" for m in labels.matches)


def test_association_required_from_membership_text():
    labels = extract_eligibility_labels(
        {"license_limits": [{"lcnsLmtNm": "한국엔지니어링협회 가입 업체"}]}
    )

    assert labels.association_required is True
    assert labels.engineering_business_required is False
    assert any(
        m.term == "엔지니어링협회" and m.source_field == "license_limits.lcnsLmtNm"
        for m in labels.matches
    )


def test_association_matches_from_permitted_industry_whitespace_variant():
    # permsnIndstrytyList 원문 + 공백 변형("엔지니어링 협회")도 정규화가 흡수해 잡힌다.
    labels = extract_eligibility_labels(
        {
            "license_limits": [
                {"lcnsLmtNm": "종합공사업", "permsnIndstrytyList": "엔지니어링 협회 회원"}
            ]
        }
    )

    assert labels.association_required is True
    assert any(
        m.source_field == "license_limits.permsnIndstrytyList" for m in labels.matches
    )


def test_unrelated_license_yields_no_labels():
    labels = extract_eligibility_labels({"license_limits": [{"lcnsLmtNm": "정보통신공사업"}]})

    assert labels.has_eligibility_data is True
    assert labels.association_required is False
    assert labels.engineering_business_required is False
    assert labels.tech_fields == ()
    assert labels.matches == ()


def test_tech_field_alias_maps_to_standard_name():
    # 별칭 "수로측량"·"해양조사" → 표준명 "수로조사" 로 정규화되고 중복 제거된다.
    labels = extract_eligibility_labels(
        {"license_limits": [{"lcnsLmtNm": "수로측량"}, {"lcnsLmtNm": "해양조사"}]}
    )

    assert labels.tech_fields == ("수로조사",)


def test_title_match_is_reference_only_not_a_requirement():
    # title 에만 자격 용어가 있으면 bool 판정에 넣지 않고 참고 증거로만 남긴다.
    labels = extract_eligibility_labels(
        {"license_limits": [{"lcnsLmtNm": "정보통신공사업"}]},
        title="○○ 엔지니어링사업자 대상 항만및해안 용역",
    )

    assert labels.association_required is False
    assert labels.engineering_business_required is False
    assert labels.tech_fields == ()
    assert labels.matches  # title 증거는 존재
    assert all(m.source_field == TITLE_SOURCE for m in labels.matches)


def test_none_eligibility_returns_defaults():
    labels = extract_eligibility_labels(None)

    assert labels.has_eligibility_data is False
    assert labels.association_required is False
    assert labels.engineering_business_required is False
    assert labels.tech_fields == ()
    assert labels.matches == ()


def test_empty_dict_returns_defaults():
    labels = extract_eligibility_labels({})

    assert labels.has_eligibility_data is False
    assert labels.matches == ()


def test_flags_only_eligibility_has_data_but_no_labels():
    # 플래그만 있어도 참가자격 데이터는 존재하나 라벨은 안 붙는다.
    labels = extract_eligibility_labels({"flags": {"indstrytyLmtYn": "N"}})

    assert labels.has_eligibility_data is True
    assert labels.association_required is False
    assert labels.tech_fields == ()
    assert labels.matches == ()


def test_to_dict_shape():
    payload = extract_eligibility_labels(
        {"license_limits": [{"lcnsLmtNm": "엔지니어링사업자(항만및해안)"}]}
    ).to_dict()

    assert set(payload) == {
        "association_required",
        "engineering_business_required",
        "tech_fields",
        "matches",
        "has_eligibility_data",
    }
    assert payload["tech_fields"] == ["항만및해안"]
    assert set(payload["matches"][0]) == {"term", "source_field", "evidence"}


# --- 규칙 테이블 무결성 -------------------------------------------------------


def test_qualification_flags_are_known():
    known = {FLAG_ASSOCIATION, FLAG_ENGINEERING_BUSINESS}
    assert all(term.flag in known for term in ASSOCIATION_QUALIFICATION_TERMS)


def test_tech_field_codes_present():
    assert {tf.code for tf in TECH_FIELD_TERMS} == {"PORT001", "MAR001", "HYDRO001"}


# --- 리포트 집계 (fake rows — DB 실접속 없음) --------------------------------


def test_aggregate_distribution_counts_labels():
    rows = [
        _row({"license_limits": [{"lcnsLmtNm": "한국엔지니어링협회 가입"}]}),
        _row({"license_limits": [{"lcnsLmtNm": "엔지니어링사업자(항만및해안)"}]}),
        _row({"license_limits": [{"lcnsLmtNm": "해양엔지니어링"}]}),
        _row(None),  # eligibility 데이터 없음 → 집계 제외
    ]

    summary = report.aggregate_labels(rows)

    assert summary.total == 3
    assert summary.association_required == 1
    assert summary.engineering_business_required == 1
    assert summary.tech_field_counts["항만및해안"] == 1
    assert summary.tech_field_counts["해양엔지니어링"] == 1


def test_aggregate_collects_unmatched_raw_samples():
    rows = [
        _row(
            {
                "license_limits": [
                    {"lcnsLmtNm": "정보통신공사업", "permsnIndstrytyList": "정보통신공사업"}
                ]
            }
        ),
        _row(
            {
                "license_limits": [
                    {"lcnsLmtNm": "정보통신공사업", "permsnIndstrytyList": "전기공사업"}
                ]
            }
        ),
        _row({"license_limits": [{"lcnsLmtNm": "엔지니어링사업자"}]}),  # 매칭됨 → 미매칭 아님
    ]

    summary = report.aggregate_labels(rows)

    assert summary.unmatched_total == 2
    # 규칙이 못 붙인 원문이 빈도순으로 캘리브레이션 대상에 올라온다.
    assert summary.unmatched_license.most_common(1)[0] == ("정보통신공사업", 2)
    assert summary.unmatched_industry["전기공사업"] == 1


def test_aggregate_title_only_match_is_unmatched():
    # title 참고 매칭만 있는 공고는 라벨 근거가 없으므로 미매칭으로 집계된다.
    rows = [_row({"license_limits": [{"lcnsLmtNm": "정보통신공사업"}]}, title="엔지니어링사업자 용역")]

    summary = report.aggregate_labels(rows)

    assert summary.total == 1
    assert summary.engineering_business_required == 0
    assert summary.unmatched_total == 1


def test_render_summary_is_kst_and_lists_samples():
    summary = report.aggregate_labels(
        [_row({"license_limits": [{"lcnsLmtNm": "정보통신공사업"}]})]
    )
    text = report.render_summary(summary, samples=5, top_tech_fields=5)

    assert "KST" in text
    assert "정보통신공사업" in text
    assert "unmatched=1" in text


# --- load_rows: eligibility_raw NOT NULL 필터 (test_db SQLite 픽스처) --------


def test_load_rows_filters_null_eligibility(test_db):
    from app.models.models import Project

    test_db.add_all(
        [
            Project(
                id=1,
                notice_number="R0001",
                title="항만 용역",
                eligibility_raw={"license_limits": [{"lcnsLmtNm": "엔지니어링사업자"}]},
            ),
            Project(
                id=2,
                notice_number="R0002",
                title="일반 용역",
                eligibility_raw=None,
            ),
        ]
    )
    test_db.commit()

    rows = report.load_rows(test_db)

    assert [r.notice_number for r in rows] == ["R0001"]
    assert rows[0].eligibility_raw == {"license_limits": [{"lcnsLmtNm": "엔지니어링사업자"}]}
