"""유사공고 투영 스냅샷이 아직 쓸 만한가 — 순수 판정 커널.

왜 필요한가
-----------
스냅샷은 코퍼스 N 건 중 상위 20건을 담은 요약이다. "이 요약이 아직 코퍼스를
기술하는가"를 **정확한 워터마크 일치**로 물으면, 그 스코프에 임베딩이 1건만
들어와도 그 스코프의 모든 스냅샷이 동시에 무효가 된다. 한 회전에 걸리는 시간보다
코퍼스가 자주 움직이는 순간 후보 집합은 구조적으로 드레인되지 않고, 백필은 같은
지점을 영원히 다시 밟는다(2026-08-13 러닝머신 사고).

그래서 판정을 **물질성**으로 바꾼다: 코퍼스가 의미 있게 달라졌을 때만 stale 로
보고, 그렇지 않으면 **최대 수명**까지 그대로 쓴다. 임계와 그 근거는
:mod:`app.core.constants` 에 선언되어 있다.

두 소비자가 이 커널 하나를 공유한다.

- :mod:`app.services.similarity_projection_backfill` — 어떤 대상을 다시 계산할지
  고르는 SQL 술어(같은 규칙을 SQL 로 렌더링한다)
- :class:`app.services.similarity_read_model.ProjectSimilarityReadModelService` —
  저장된 스냅샷을 읽기에 내줄지 판정

두 판정이 갈리면 시스템이 진동한다: 백필은 "신선하다"며 다시 계산하지 않는데
읽기는 "낡았다"며 결과를 비우면, 어떤 경로로도 고쳐지지 않는 빈 화면이 남는다.
그래서 규칙은 여기 한 벌만 두고, SQL 렌더링과 Python 판정이 같은 값표에서
일치함을 테스트로 고정한다(tests/test_projection_freshness.py).

순수 함수(I/O 0)다. 워터마크 조회와 SQL 조립은 호출부의 책임이다(§4.7).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from app.core.constants import (
    SIMILARITY_PROJECTION_CORPUS_DRIFT_MATERIALITY_RATIO,
    SIMILARITY_PROJECTION_MAX_SNAPSHOT_AGE_SECONDS,
)


@dataclass(frozen=True)
class ProjectionFreshnessPolicy:
    """신선도 판정에 쓰이는 두 임계. 호출부가 주입할 수 있는 seam 이다."""

    corpus_drift_materiality_ratio: float
    max_snapshot_age_seconds: int


DEFAULT_PROJECTION_FRESHNESS_POLICY: Final = ProjectionFreshnessPolicy(
    corpus_drift_materiality_ratio=(
        SIMILARITY_PROJECTION_CORPUS_DRIFT_MATERIALITY_RATIO
    ),
    max_snapshot_age_seconds=SIMILARITY_PROJECTION_MAX_SNAPSHOT_AGE_SECONDS,
)


def corpus_drift_threshold_rows(
    snapshot_corpus_count: int,
    policy: ProjectionFreshnessPolicy = DEFAULT_PROJECTION_FRESHNESS_POLICY,
) -> float:
    """스냅샷이 요약한 코퍼스 크기에서 "의미 있는 변화"가 되는 행 수.

    비율이 1행 미만으로 떨어지는 작은 코퍼스에서는 한 건의 추가도 상위 20을 흔들기
    때문에 바닥을 1행으로 둔다.
    """
    return max(1.0, policy.corpus_drift_materiality_ratio * float(snapshot_corpus_count))


def corpus_drift_is_material(
    *,
    snapshot_corpus_count: int,
    corpus_count: int,
    policy: ProjectionFreshnessPolicy = DEFAULT_PROJECTION_FRESHNESS_POLICY,
) -> bool:
    """코퍼스가 스냅샷 시점 대비 임계 이상 달라졌는가.

    증가·감소를 모두 센다. 행이 빠지는 것도 상위 20을 바꾸기 때문이다.
    """
    delta = abs(int(corpus_count) - int(snapshot_corpus_count))
    return delta >= corpus_drift_threshold_rows(snapshot_corpus_count, policy)


def snapshot_age_exceeds_max(
    age_seconds: float,
    policy: ProjectionFreshnessPolicy = DEFAULT_PROJECTION_FRESHNESS_POLICY,
) -> bool:
    """건수가 그대로여도(=재임베딩) 최대 수명을 넘기면 다시 계산한다."""
    return float(age_seconds) > float(policy.max_snapshot_age_seconds)


def projection_is_fresh(
    *,
    snapshot_corpus_count: int,
    corpus_count: int,
    snapshot_age_seconds: float,
    policy: ProjectionFreshnessPolicy = DEFAULT_PROJECTION_FRESHNESS_POLICY,
) -> bool:
    """스냅샷을 그대로 써도 되는가 — 물질성과 최대 수명을 모두 통과할 때만 참.

    스냅샷 **존재 여부**와 edge 정합(스냅샷이 주장하는 edge 수와 실제 행 수의 일치)은
    신선도가 아니라 정합성 문제라 여기서 묻지 않는다. 호출부가 따로 판정한다.
    """
    if corpus_drift_is_material(
        snapshot_corpus_count=snapshot_corpus_count,
        corpus_count=corpus_count,
        policy=policy,
    ):
        return False
    return not snapshot_age_exceeds_max(snapshot_age_seconds, policy)
