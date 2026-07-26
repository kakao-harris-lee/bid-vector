#!/usr/bin/env python3
"""운영자 면허 ↔ 공고 면허요건 매칭 영향 리포트 (읽기 전용, 외부 호출 0).

열린 공고(``status='open'`` 이고 마감이 지나지 않은 것)를 순회하며
``app.services.license_eligibility.assess_license_eligibility`` 로 참가 자격을
판정하고, ① 자격 데이터 커버리지 ② verdict 분포 ③ **후속 wiring 영향 추정**
(eligible 공고 샘플 / ineligible 상위 요구 면허 빈도) ④ 한계 고지(eligible 정밀도
미검증 · 그룹 의미론 가정 · unknown≠ineligible)를 stdout 으로 요약한다.

④ 는 장식이 아니다. 이 리포트가 wiring 여부를 결정하는 산출물이라 **출력만 보는
사람**이 eligible 을 검증된 자격 신호로 오독하면 안 되므로, 한계는 코드 주석이
아니라 stdout 에 함께 실린다(§2 정직).

이 리포트의 목적은 "면허 축을 추천에 연결하면 무엇이 걸러지는가"를 **먼저
계측**하는 것이다. 판정은 아직 분류/추천/수집 어디에서도 소비되지 않으며,
wiring 여부는 여기 수치를 보고 결정한다.

DB read-only 다(write/commit 없음). KONEPS 등 외부 API 는 호출하지 않는다.
보유 면허는 canonical operator 프로필(``get_operator_profile``)에서 읽는다.
시각은 KST.

사용 예:
    docker exec bid_vector_api python scripts/report_license_eligibility.py
    docker exec bid_vector_api python scripts/report_license_eligibility.py \
        --limit 2000 --samples 20
"""
# ruff: noqa: E402 - imports follow the sys.path bootstrap below.
from __future__ import annotations

import argparse
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.core.database import SessionLocal  # noqa: E402
from app.core.single_user import get_operator_profile  # noqa: E402
from app.core.time import kst_now, utc_now  # noqa: E402
from app.models.models import Project  # noqa: E402
from app.services.license_eligibility import (  # noqa: E402
    ELIGIBLE_PRECISION_CAVEAT,
    GROUP_SEMANTICS_ASSUMPTION,
    VERDICT_ELIGIBLE,
    VERDICT_INELIGIBLE,
    VERDICT_VALUES,
    assess_license_eligibility,
)
from app.services.query_predicates import open_projects  # noqa: E402

# 리포트 기본값 (매직값은 여기 선언, 함수 안 리터럴 금지 — §4.5.1).
DEFAULT_LIMIT: int | None = None
DEFAULT_SAMPLES = 15
DEFAULT_TOP_REQUIRED = 15

# 대상 공고 상태 — 열린(미마감) 입찰 가능 공고. open_projects() 는
# ACTIVE_PROJECT_STATUSES({open, re_notice}) 를 소비한다(재공고 포함).

# 샘플 출력에서 공고명을 자르는 길이(가독성).
TITLE_CLIP_CHARS = 40

_LOG_PREFIX = "[license-eligibility]"


@dataclass(frozen=True)
class ReportRow:
    """리포트가 필요로 하는 공고 최소 투영 (DB 접근과 집계 로직 분리용)."""

    notice_number: str
    title: str | None
    eligibility_raw: dict | None


@dataclass(frozen=True)
class EligibleSample:
    """eligible 판정 공고 하나의 샘플 — 어떤 그룹 면허로 통과했는지 남긴다."""

    notice_number: str
    title: str
    matched_licenses: tuple[str, ...]


@dataclass
class ReportSummary:
    """커버리지 · verdict 분포 · 영향 추정 집계 결과."""

    total: int = 0
    with_eligibility: int = 0
    without_eligibility: int = 0
    # license_limits 행은 있으나 면허명을 하나도 읽지 못한 공고(=판정 근거 0).
    # without_eligibility(행 자체 없음)와 구분해야 커버리지 해석이 정확해진다.
    unparsable_only: int = 0
    # 일부 행만 못 읽고 판정은 내려진 공고 — 요건이 줄어든 채 판정된 것이라
    # eligible 쪽으로 관대할 수 있다(신뢰도 저하 신호).
    partial_unparsable: int = 0
    verdict_counts: Counter = field(default_factory=Counter)
    eligible_samples: list[EligibleSample] = field(default_factory=list)
    ineligible_required: Counter = field(default_factory=Counter)


# --- 집계 (순수 — DB 접근 없음) ----------------------------------------------


def aggregate_eligibility(
    rows: Iterable[ReportRow],
    profile_license_codes: str | None,
    *,
    samples: int = DEFAULT_SAMPLES,
) -> ReportSummary:
    """공고 행들을 판정해 커버리지·verdict 분포·영향 추정을 집계한다(순수 함수).

    ``with_eligibility`` 는 면허요건 데이터가 있어 판정 근거가 존재한 공고 수다.
    근거가 없는 공고는 **행 자체가 없는 경우**(``without_eligibility``)와 **행은
    있으나 면허명을 못 읽은 경우**(``unparsable_only``)로 나눠 센다 — 둘을 합치면
    "자격 데이터가 아예 없다"는 잘못된 해석이 되기 때문이다. ineligible 공고의
    요구 면허를 빈도 집계해 "무엇 때문에 걸러지는가"를 노출한다.
    """
    summary = ReportSummary()
    for row in rows:
        summary.total += 1
        result = assess_license_eligibility(row.eligibility_raw, profile_license_codes)
        summary.verdict_counts[result.verdict] += 1

        if result.has_eligibility_data:
            summary.with_eligibility += 1
            if result.has_unparsable_rows:
                summary.partial_unparsable += 1
        elif result.has_unparsable_rows:
            summary.unparsable_only += 1
        else:
            summary.without_eligibility += 1

        if result.verdict == VERDICT_ELIGIBLE:
            if len(summary.eligible_samples) < samples:
                summary.eligible_samples.append(
                    EligibleSample(
                        notice_number=row.notice_number,
                        title=_clip_title(row.title),
                        matched_licenses=_matched_licenses(result),
                    )
                )
        elif result.verdict == VERDICT_INELIGIBLE:
            for name in result.required_any:
                summary.ineligible_required[name] += 1
    return summary


def _matched_licenses(result) -> tuple[str, ...]:
    """eligible 판정에서 충족한 그룹의 요구 면허명(=우리가 보유해 통과한 면허).

    충족 그룹은 ``missing_by_group`` 에 없으므로, 요구 면허 전체에서 어느 그룹에도
    부족으로 잡히지 않은 이름만 남긴다.
    """
    missing = {name for names in result.missing_by_group.values() for name in names}
    return tuple(name for name in result.required_any if name not in missing)


def _clip_title(title: str | None) -> str:
    """샘플 출력용 공고명 클립."""
    text = (title or "").strip()
    if len(text) <= TITLE_CLIP_CHARS:
        return text
    return text[:TITLE_CLIP_CHARS] + "…"


def render_summary(
    summary: ReportSummary,
    *,
    profile_license_codes: str | None,
    top_required: int,
) -> str:
    """집계 결과를 사람이 읽는 리포트 텍스트로 렌더한다(시각 KST)."""
    held = (profile_license_codes or "").strip() or "(미기재)"
    lines = [
        f"{_LOG_PREFIX} {_kst_stamp()} READ-ONLY · 소비 0 (분류/추천 미반영)",
        f"{_LOG_PREFIX} 보유 면허: {held}",
        (
            f"{_LOG_PREFIX} 커버리지 — open_notices={summary.total} "
            f"with_eligibility={summary.with_eligibility} "
            f"without_eligibility={summary.without_eligibility} "
            f"unparsable_only={summary.unparsable_only} "
            f"partial_unparsable={summary.partial_unparsable}"
        ),
        f"{_LOG_PREFIX} verdict 분포 — {_verdict_line(summary.verdict_counts)}",
        f"{_LOG_PREFIX} eligible 샘플 (후속 wiring 시 통과 대상 — 정밀도 미검증, 아래 주의):",
        *_sample_lines(summary.eligible_samples),
        f"{_LOG_PREFIX} ineligible 상위 요구 면허 (무엇 때문에 걸러지나):",
        *_count_lines(summary.ineligible_required, top_required),
        f"{_LOG_PREFIX} {ELIGIBLE_PRECISION_CAVEAT}",
        f"{_LOG_PREFIX} {GROUP_SEMANTICS_ASSUMPTION}",
        f"{_LOG_PREFIX} unknown 은 자격 데이터/보유 면허 정보 부재이며 ineligible(부적격)이 아니다.",
    ]
    return "\n".join(lines)


def _verdict_line(counts: Counter) -> str:
    """verdict 분포를 고정 순서로 렌더한다(0건도 표시)."""
    return " ".join(f"{verdict}={counts.get(verdict, 0)}" for verdict in VERDICT_VALUES)


def _sample_lines(samples: list[EligibleSample]) -> list[str]:
    """eligible 샘플을 들여쓴 목록 줄로 만든다(비면 ``(none)``)."""
    if not samples:
        return ["    (none)"]
    return [
        f"    {sample.notice_number} | {sample.title} | 충족 면허: "
        f"{', '.join(sample.matched_licenses) or '(없음)'}"
        for sample in samples
    ]


def _count_lines(counter: Counter, top_n: int) -> list[str]:
    """Counter 상위 N 을 들여쓴 목록 줄로 만든다(비면 ``(none)``)."""
    if not counter or top_n <= 0:
        return ["    (none)"]
    return [f"    {value}: {count}" for value, count in counter.most_common(top_n)]


def _kst_stamp() -> str:
    """리포트 헤더용 현재 KST 타임스탬프."""
    return kst_now().strftime("%Y-%m-%d %H:%M:%S KST")


# --- DB read (얇은 경계) ------------------------------------------------------


def load_rows(db, *, limit: int | None = None) -> list[ReportRow]:
    """열린(미마감) 공고를 최신 id 순으로 투영해 돌려준다(read-only)."""
    query = (
        db.query(Project.notice_number, Project.title, Project.eligibility_raw)
        .filter(open_projects())
        .filter(Project.deadline >= utc_now())
        .order_by(Project.id.desc())
    )
    if limit is not None:
        query = query.limit(limit)
    return [
        ReportRow(notice_number=row[0], title=row[1], eligibility_raw=row[2])
        for row in query.all()
    ]


def load_profile_license_codes(db) -> str | None:
    """canonical operator 프로필의 보유 면허 문자열(없으면 None)."""
    profile = get_operator_profile(db)
    return profile.license_codes if profile else None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--limit",
        type=int,
        default=DEFAULT_LIMIT,
        help="Assess at most N open notices (default: all).",
    )
    parser.add_argument(
        "--samples",
        type=int,
        default=DEFAULT_SAMPLES,
        help="How many eligible notice samples to show.",
    )
    parser.add_argument(
        "--top-required",
        type=int,
        default=DEFAULT_TOP_REQUIRED,
        help="How many top required licenses to show for ineligible notices.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    db = SessionLocal()
    try:
        rows = load_rows(db, limit=args.limit)
        profile_license_codes = load_profile_license_codes(db)
    finally:
        db.close()

    summary = aggregate_eligibility(
        rows, profile_license_codes, samples=max(0, args.samples)
    )
    print(
        render_summary(
            summary,
            profile_license_codes=profile_license_codes,
            top_required=max(0, args.top_required),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
