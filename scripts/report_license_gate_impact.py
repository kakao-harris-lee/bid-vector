#!/usr/bin/env python3
"""면허 자격 게이트 활성화 영향 시뮬레이션 (읽기 전용, 외부 호출 0).

``LICENSE_ELIGIBILITY_GATE_ENABLED`` 를 **켰다고 가정**했을 때 추천 후보에서 무엇이
바뀌는지를 실제 추천을 건드리지 않고 계측한다. 게이트는 추천 스크리닝에서 전략
watch 룰(``_apply_strategy_filters``)을 통과한 공고에 대해서만 무거운 ML 분석 **전에**
동작하므로, 이 시뮬레이션도 동일하게 **전략 게이트를 통과하는 열린 공고**만 모집단으로
삼아 자격을 판정한다. 산출:

  ① 모집단 — 전략 watch 룰을 통과해 게이트에 도달하는 열린 공고 수
  ② verdict 분포 — eligible / ineligible / unknown
  ③ 게이트 ON 시 **제외될 공고**(ineligible) 목록 — 공고번호·제목·요구 면허
  ④ 한계 고지 — unknown≠ineligible(억제 금지) · eligible 정밀도 미검증 · 그룹 의미론 가정

게이트가 실제로 바꾸는 것은 **ineligible 제외**뿐이다(③). unknown/eligible 은 통과
(중립)라 추천이 줄지 않는다. ④ 는 장식이 아니라 §2 정직 고지다 — 출력만 보는 사람이
eligible 을 검증된 신호로, unknown 을 부적격으로 오독하지 않도록 stdout 에 함께 싣는다.

DB read-only 다(write/commit 없음). KONEPS 등 외부 API 는 호출하지 않는다. 보유 면허와
전략은 canonical operator(``get_operator_profile`` / ``get_operator_strategy``)에서 읽는다.
시각은 KST.

사용 예:
    docker exec bid_vector_api python scripts/report_license_gate_impact.py
    docker exec bid_vector_api python scripts/report_license_gate_impact.py \
        --limit 5000 --samples 50
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
from app.core.single_user import (  # noqa: E402
    get_operator_profile,
    get_operator_strategy,
)
from app.core.time import kst_now, utc_now  # noqa: E402
from app.models.models import OperatorStrategy, Project  # noqa: E402
from app.services.license_eligibility import (  # noqa: E402
    ELIGIBLE_PRECISION_CAVEAT,
    GROUP_SEMANTICS_ASSUMPTION,
    VERDICT_ELIGIBLE,
    VERDICT_INELIGIBLE,
    VERDICT_UNKNOWN,
    VERDICT_VALUES,
    assess_license_eligibility,
)
from app.services.opportunity_monitoring import (  # noqa: E402
    has_watch_rules,
    matches_strategy_watch_rules,
)
from app.services.query_predicates import open_projects  # noqa: E402

# 리포트 기본값 (매직값은 여기 선언, 함수 안 리터럴 금지 — §4.5.1).
DEFAULT_LIMIT: int | None = None
DEFAULT_SAMPLES = 30

# 대상 공고 상태 — 열린(미마감) 입찰 가능 공고. open_projects() 는
# ACTIVE_PROJECT_STATUSES({open, re_notice}) 를 소비한다(재공고 포함).

# 샘플 출력에서 공고명을 자르는 길이(가독성).
TITLE_CLIP_CHARS = 40

_LOG_PREFIX = "[license-gate-impact]"


@dataclass(frozen=True)
class ExcludedNotice:
    """게이트 ON 시 제외될 공고 하나 — 공고가 요구하는 면허 목록을 남긴다."""

    notice_number: str
    title: str
    required_licenses: tuple[str, ...]


@dataclass
class ImpactSummary:
    """전략 게이트 통과 모집단에 대한 자격 판정 시뮬레이션 결과."""

    open_total: int = 0
    # 전략 watch 룰을 통과해 실제로 게이트에 도달하는 공고 수(모집단).
    reaches_gate: int = 0
    verdict_counts: Counter = field(default_factory=Counter)
    excluded: list[ExcludedNotice] = field(default_factory=list)
    # 제외 사유 = 운영자가 실제로 **미보유**한 면허의 빈도(요구 면허 전체가 아니라).
    # 보유 면허(예: 엔지니어링사업 항만·해안)가 사유로 뜨면 오도되므로, 판정 결과의
    # missing_by_group(실제 누락)만 센다. 어느 그룹의 누락을 셀지는
    # _representative_missing 이 정의한다.
    ineligible_missing: Counter = field(default_factory=Counter)


# --- 시뮬레이션 (순수 — DB 접근 없음) ----------------------------------------


def simulate_gate_impact(
    projects: Iterable[Project],
    strategy: OperatorStrategy,
    profile_license_codes: str | None,
    *,
    samples: int = DEFAULT_SAMPLES,
) -> ImpactSummary:
    """전략 게이트를 통과하는 공고에 대해 게이트 ON 영향을 집계한다(순수 함수).

    실제 게이트 위치(``_collect_candidate_evaluations``)와 동일하게, 먼저
    ``matches_strategy_watch_rules`` 로 게이트에 도달하는 공고만 모집단으로 좁힌 뒤
    ``assess_license_eligibility`` 로 판정한다. **ineligible 만** 제외 대상(③)이고
    unknown/eligible 은 통과(중립)라 추천이 줄지 않는다. DB/외부 호출 없음.
    """
    summary = ImpactSummary()
    for project in projects:
        summary.open_total += 1
        if not matches_strategy_watch_rules(project, strategy):
            continue

        summary.reaches_gate += 1
        result = assess_license_eligibility(
            getattr(project, "eligibility_raw", None), profile_license_codes
        )
        summary.verdict_counts[result.verdict] += 1

        if result.verdict != VERDICT_INELIGIBLE:
            continue
        for name in _representative_missing(result):
            summary.ineligible_missing[name] += 1
        if len(summary.excluded) < samples:
            summary.excluded.append(
                ExcludedNotice(
                    notice_number=str(getattr(project, "notice_number", "") or ""),
                    title=_clip_title(getattr(project, "title", None)),
                    required_licenses=tuple(result.required_any),
                )
            )
    return summary


def _representative_missing(result) -> tuple[str, ...]:
    """제외 사유로 집계할 '실제 미보유' 면허 — 가장 가까운(누락 최소) 그룹 기준.

    ineligible 은 모든 그룹이 미충족이라 그룹마다 누락 면허 집합이 있다. 어느 그룹의
    누락을 대표로 셀지 정의가 필요한데, **누락 수가 가장 적은 그룹**(=자격에 가장
    근접한 대안)의 누락을 쓴다 — "무엇만 더 갖추면 이 공고에 참가 가능한가"라는 운영자
    액션 관점에서 가장 유용하기 때문이다. 전 그룹 누락의 합집합을 쓰면 이미 보유한
    다른 대안에서의 무관한 누락까지 섞여 사유가 부풀려진다. 동수면 처음 등장한
    그룹(dict 삽입 순서 = assess 의 그룹 등장 순서)을 대표로 한다.

    운영자가 보유한 면허는 어느 그룹의 누락에도 들어가지 않으므로(그룹의 missing 은
    미충족 요건만 담는다) 사유 목록에 결코 나타나지 않는다 — N1 오도 제거의 핵심.
    """
    if not result.missing_by_group:
        return ()
    return tuple(min(result.missing_by_group.values(), key=len))


def _clip_title(title: str | None) -> str:
    """샘플 출력용 공고명 클립."""
    text = (title or "").strip()
    if len(text) <= TITLE_CLIP_CHARS:
        return text
    return text[:TITLE_CLIP_CHARS] + "…"


def render_summary(
    summary: ImpactSummary,
    *,
    profile_license_codes: str | None,
    has_gate: bool,
) -> str:
    """집계 결과를 사람이 읽는 리포트 텍스트로 렌더한다(시각 KST)."""
    held = (profile_license_codes or "").strip() or "(미기재)"
    excluded_count = summary.verdict_counts.get(VERDICT_INELIGIBLE, 0)
    lines = [
        f"{_LOG_PREFIX} {_kst_stamp()} SIMULATION · 추천 미변경 (게이트 ON 가정)",
        f"{_LOG_PREFIX} 보유 면허: {held}",
        (f"{_LOG_PREFIX} 전략 게이트: " f"{'설정됨' if has_gate else '없음(모든 열린 공고가 게이트에 도달)'}"),
        (
            f"{_LOG_PREFIX} 모집단 — open_notices={summary.open_total} "
            f"reaches_gate={summary.reaches_gate}"
        ),
        f"{_LOG_PREFIX} verdict 분포(게이트 도달분) — {_verdict_line(summary.verdict_counts)}",
        (
            f"{_LOG_PREFIX} 게이트 ON 시 제외될 공고(ineligible): {excluded_count}건 "
            f"— eligible={summary.verdict_counts.get(VERDICT_ELIGIBLE, 0)}·"
            f"unknown={summary.verdict_counts.get(VERDICT_UNKNOWN, 0)} 는 통과(중립)"
        ),
        f"{_LOG_PREFIX} 제외 공고 샘플 (공고번호 | 공고명 | 요구 면허):",
        *_excluded_lines(summary.excluded),
        f"{_LOG_PREFIX} 제외 사유(미보유 면허) 상위 — 가장 가까운 그룹 기준, '무엇을 더 갖추면 되나' (보유 면허는 제외):",
        *_count_lines(summary.ineligible_missing, DEFAULT_SAMPLES),
        f"{_LOG_PREFIX} unknown 은 자격 데이터/보유 면허 정보 부재이며 ineligible(부적격)이 아니다 — 게이트는 절대 억제하지 않는다.",
        f"{_LOG_PREFIX} {ELIGIBLE_PRECISION_CAVEAT}",
        f"{_LOG_PREFIX} {GROUP_SEMANTICS_ASSUMPTION}",
    ]
    return "\n".join(lines)


def _verdict_line(counts: Counter) -> str:
    """verdict 분포를 고정 순서로 렌더한다(0건도 표시)."""
    return " ".join(f"{verdict}={counts.get(verdict, 0)}" for verdict in VERDICT_VALUES)


def _excluded_lines(excluded: list[ExcludedNotice]) -> list[str]:
    """제외 공고를 들여쓴 목록 줄로 만든다(비면 ``(none)``)."""
    if not excluded:
        return ["    (none)"]
    return [
        f"    {notice.notice_number or '(번호없음)'} | {notice.title or '(제목없음)'} | "
        f"요구: {', '.join(notice.required_licenses) or '(요건 미상)'}"
        for notice in excluded
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


def load_projects(db, *, limit: int | None = None) -> list[Project]:
    """열린(미마감) 공고를 최신 id 순으로 돌려준다(read-only).

    전략 필터(``_apply_strategy_filters``)가 제목·설명·요건·카테고리·예산을 읽으므로
    투영이 아니라 Project ORM 객체를 로드한다.
    """
    query = (
        db.query(Project)
        .filter(open_projects())
        .filter(Project.deadline >= utc_now())
        .order_by(Project.id.desc())
    )
    if limit is not None:
        query = query.limit(limit)
    return query.all()


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
        help="Consider at most N open notices (default: all).",
    )
    parser.add_argument(
        "--samples",
        type=int,
        default=DEFAULT_SAMPLES,
        help="How many would-be-excluded notice samples to show.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    db = SessionLocal()
    try:
        strategy = get_operator_strategy(db)
        if strategy is None:
            print(
                f"{_LOG_PREFIX} 전략(OperatorStrategy)이 없어 시뮬레이션할 수 없습니다.",
                file=sys.stderr,
            )
            return 1
        projects = load_projects(db, limit=args.limit)
        profile_license_codes = load_profile_license_codes(db)
    finally:
        db.close()

    summary = simulate_gate_impact(
        projects, strategy, profile_license_codes, samples=max(0, args.samples)
    )
    print(
        render_summary(
            summary,
            profile_license_codes=profile_license_codes,
            has_gate=has_watch_rules(strategy),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
