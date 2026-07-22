"""운영자 식별 피드백 → operator 소스 라벨 upsert (얇은 DB 경계).

``eligibility_labeling`` 이 rule/llm/operator 라벨 어휘의 단일 출처(순수 해석기,
IO 없음)이고, 이 모듈은 그 어휘를 ``NoticeEligibilityLabel`` 에 영속화하는 얇은
DB 경계다. rule 라벨 생성 스크립트(``scripts/generate_eligibility_labels``)와
operator 식별 피드백 엔드포인트가 **같은 upsert 핵심**을 공유해 ``(project_id,
source)`` 유니크·멱등을 한 곳에서 지킨다(복붙 금지 §4.5.6).

operator 라벨은 rule 라벨과 별개 축(``source`` 로 분리)이라 같은 공고에 rule 과
operator 라벨이 유니크 제약 안에서 공존한다. precision/recall 리포트
(``scripts/report_eligibility_precision``)는 rule∩operator 교집합에서 이 라벨을
정답으로 읽어 지표를 산출한다(리포트 무수정).
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.models.models import NoticeEligibilityLabel, Project, User
from app.services.eligibility_labeling import (
    OPERATOR_LABELER_VERSION,
    OPERATOR_VERDICT_TO_LABEL,
    SOURCE_OPERATOR,
)

# 운영자 라벨 rationale 감사 접두(§2 정직) — 운영자 직접 판정임을 남긴다.
_OPERATOR_RATIONALE_PREFIX = "운영자 식별 피드백"


def upsert_eligibility_label(
    db: Session,
    *,
    project_id: int,
    label: str,
    source: str,
    rationale: str,
    labeler_version: str,
) -> tuple[NoticeEligibilityLabel, bool]:
    """``(project_id, source)`` 라벨을 insert/update 한다. ``(라벨, created)`` 반환.

    소스별 1라벨(유니크 제약)을 코드에서 upsert 로 지켜, 재저장 시 중복 없이 최신
    판정·근거·버전으로 갱신한다. ``created`` 는 신규 insert 면 True, 기존 갱신이면
    False. commit 은 호출부가 관리한다(스크립트=배치 commit, 엔드포인트=요청 트랜잭션).
    """
    existing = (
        db.query(NoticeEligibilityLabel)
        .filter(
            NoticeEligibilityLabel.project_id == project_id,
            NoticeEligibilityLabel.source == source,
        )
        .one_or_none()
    )
    if existing is None:
        created = NoticeEligibilityLabel(
            project_id=project_id,
            label=label,
            source=source,
            rationale=rationale,
            labeler_version=labeler_version,
        )
        db.add(created)
        return created, True
    existing.label = label
    existing.rationale = rationale
    existing.labeler_version = labeler_version
    return existing, False


def _operator_rationale(verdict: str) -> str:
    """운영자 피드백 라벨의 감사 근거 문구(verdict 원문 포함)."""
    return f"{_OPERATOR_RATIONALE_PREFIX}: {verdict}"


def record_operator_label(
    db: Session,
    *,
    project: Project,
    verdict: str,
    operator: User | None = None,
) -> NoticeEligibilityLabel:
    """운영자 식별 verdict(적합/부적합/보류)를 operator 소스 라벨로 upsert 한다.

    verdict→label 매핑은 ``eligibility_labeling.OPERATOR_VERDICT_TO_LABEL`` 단일
    출처를 쓴다. 미지원 verdict 는 ``KeyError`` 로 조기 실패하며, 정상 경로에선
    스키마가 앞단에서 422 로 거른다. project 존재 검증은 라우터 책임이고 여기선
    매핑·upsert 만 한다. ``operator`` 는 write-context(단일 운영자 모델에선 canonical
    operator) 로 라벨에 별도 차원이 없어 감사 문맥으로만 받는다. commit 은
    호출부(엔드포인트 트랜잭션)가 관리하며, 여기선 flush 로 id/타임스탬프만 확정한다.
    """
    label = OPERATOR_VERDICT_TO_LABEL[verdict]
    record, _created = upsert_eligibility_label(
        db,
        project_id=int(project.id),
        label=label,
        source=SOURCE_OPERATOR,
        rationale=_operator_rationale(verdict),
        labeler_version=OPERATOR_LABELER_VERSION,
    )
    db.flush()
    return record
