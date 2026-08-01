"""G-2 증적 sweep task 의 요약 payload 계약 — per-operator 정상/에러 2모양.

방어적 DTO 규율 Phase 5. ``run_g2_candidate_recheck`` / ``collect_g2_evidence`` 는
운영자별 sweep 결과를 **자유 형식 dict** 로 조립해 두 곳에 동시에 내보냈다:

1. ``Analytics.event_data`` (``json.dumps``) — 나중에
   ``scripts/backfill_g2_daily_drafts.py`` 가 ``json.loads`` 후 ``.get()`` 으로 되읽어
   **counted_days 판정**에 쓴다. 즉 텔레메트리가 아니라 G-2 exit 게이트의 입력이다.
2. celery task 반환값 — 로그/결과 백엔드로 나간다.

쓰는 쪽과 되읽는 쪽이 서로의 키 집합을 모르는 상태였고, 특히 sweep 의 per-operator
항목은 **성공/실패에 따라 키 집합이 다른 2모양**(정상 = 측정치, 에러 = ``error``)인데
그 배타성이 코드 어디에도 선언되어 있지 않았다. 여기서 두 모양을 각각 모델로 선언하고
union 으로 묶는다(P1 의 historical/forward 요청 스냅샷 분리, P4.2 의 pending-edit
적재/해제 분리와 같은 이유 — 한 모델로 합치면 에러 행에 ``sections: {}`` 같은 없던 키가
생겨 저장 산출이 바뀐다).

두 갈래 비대칭(P1 ``Persisted*`` / P4.2 선례와 동일):

* **생산 모델**(:class:`~app.schemas._base.StrictModel`, ``extra="forbid"``) — 오타 키를
  즉시 거부한다. **필드 선언 순서가 저장 문자열의 키 순서**이므로 종전 dict 리터럴과
  같은 순서로 선언한다(공백만 다르고 파싱 동치).
* **복원 모델**(``Persisted*``, ``extra="ignore"`` + 모든 필드 ``| None``) — 과거 행에는
  지금 있는 키가 없다. forbid 로 읽으면 오래된 한 행이 backfill 전체를 죽이고, 생산
  기본값(``0``)을 쓰면 **기록되지 않은 gap 수가 0 으로 날조**되어 통과하지 않았던 날이
  counted_days 로 굳는다. 미기록은 ``None`` 으로 보존하고 판정은 소비처가 한다.

복원 쪽 per-operator 는 **합집합 한 모양**이다(P4.2 pending-edit 복원과 같은 선택):
되읽는 쪽이 필요한 것은 "이 항목이 에러인가, 아니면 어떤 상태였나"뿐이고, 두 모양 중
하나로 판별하는 union 복원은 소비처에 isinstance 분기를 강요한다.
"""

from __future__ import annotations

from pydantic import ConfigDict, Field

from app.schemas._base import StrictModel

__all__ = [
    "G2CandidateRecheckOperatorError",
    "G2CandidateRecheckOperatorEntry",
    "G2CandidateRecheckOperatorResult",
    "G2CandidateRecheckSummary",
    "G2CollectEvidenceSummary",
    "G2EvidenceOperatorSnapshot",
    "G2EvidenceOperatorSnapshotEntry",
    "G2EvidenceOperatorSnapshotError",
    "G2LedgerOperatorSummary",
    "G2LedgerOperatorSummaryEntry",
    "G2LedgerOperatorSummaryError",
    "PersistedG2CandidateRecheckOperator",
    "PersistedG2CandidateRecheckSummary",
    "PersistedG2CollectEvidenceSummary",
    "PersistedG2EvidenceOperatorSnapshot",
]


# --- g2_candidate_recheck ---------------------------------------------------
class G2CandidateRecheckOperatorResult(StrictModel):
    """후보 재점검이 성공한 운영자 1명의 측정치.

    ``evaluated_project_count`` / ``returned_candidate_count`` 는 "니치 물량이 언제
    회복되는지"를 재는 관측값이라 없는 값을 ``0`` 으로 채우면 관측 자체가 거짓이 된다
    (그래서 복원 모델에서 ``None`` 을 허용한다).
    """

    operator_id: int
    username: str
    evaluated_project_count: int
    returned_candidate_count: int


class G2CandidateRecheckOperatorError(StrictModel):
    """후보 재점검이 실패한 운영자 1명 — 측정치 대신 예외 타입만 남는다.

    성공 모양과 **키 집합이 배타적**이다. 실패한 운영자에 ``returned_candidate_count:
    0`` 을 남기면 "평가했지만 후보가 없었다"와 구분되지 않는다.
    """

    operator_id: int
    username: str
    error: str


# 생산 union: sweep 이 조립하는 per-operator 항목의 타입.
G2CandidateRecheckOperatorEntry = (
    G2CandidateRecheckOperatorResult | G2CandidateRecheckOperatorError
)


class G2CandidateRecheckSummary(StrictModel):
    """``g2_candidate_recheck`` sweep 1회의 집계 + per-operator 명세."""

    operator_count: int
    total_candidates: int
    operators_with_candidates: int
    error_count: int
    per_operator: list[G2CandidateRecheckOperatorEntry] = Field(default_factory=list)


class PersistedG2CandidateRecheckOperator(StrictModel):
    """저장된 per-operator 항목 복원용 — 성공/실패 두 모양을 합집합으로 읽는다."""

    model_config = ConfigDict(extra="ignore")

    operator_id: int | None = None
    username: str | None = None
    evaluated_project_count: int | None = None
    returned_candidate_count: int | None = None
    error: str | None = None


class PersistedG2CandidateRecheckSummary(StrictModel):
    """저장된 ``g2_candidate_recheck`` 행 복원용 (읽기 계약 선언 + 레지스트리 등록).

    현재 되읽는 소비처는 없지만, 이 이벤트가 어떤 키를 기대하는지가 주석이 아니라
    타입으로 남아야 새 소비처가 ``.get()`` 짐작으로 시작하지 않는다.
    """

    model_config = ConfigDict(extra="ignore")

    operator_count: int | None = None
    total_candidates: int | None = None
    operators_with_candidates: int | None = None
    error_count: int | None = None
    # 빈 리스트 기본값을 쓰지 않는다: 미기록(``None``)과 "0명을 훑었다"(``[]``)는 다르고,
    # 복원 모델의 불변식은 "인자 없이 만들면 모든 값이 부재"다(레지스트리 등록 가드).
    per_operator: list[PersistedG2CandidateRecheckOperator] | None = None


# --- collect_g2_evidence ----------------------------------------------------
class G2EvidenceOperatorSnapshot(StrictModel):
    """증적 원장 스냅샷의 per-operator **compact** 셀(저장 크기를 위해 축약된 모양).

    ``blocking_gaps`` 목록 대신 개수만 담는다 — 전체 목록은 같은 sweep 이 만드는
    daily draft(:class:`G2LedgerOperatorSummary`) 쪽에만 들어간다. backfill 이 개수만
    보고 placeholder gap 을 합성하는 이유가 이 축약이다.
    """

    operator_id: int
    username: str
    evidence_status: str
    blocking_gaps_count: int
    sections: dict[str, str] = Field(default_factory=dict)


class G2EvidenceOperatorSnapshotError(StrictModel):
    """원장 요약 수집이 실패한 운영자 1명 — 상태 대신 예외 타입만 남는다.

    ``evidence_status`` 를 빈 문자열로 채우지 않는다: 수집 실패와 "상태 미기록"은
    daily 판정에서 서로 다르게 취급돼야 한다(실패는 fail, 미기록은 partial).
    """

    operator_id: int
    username: str
    error: str


# 생산 union: sweep 이 조립하는 compact 셀의 타입.
G2EvidenceOperatorSnapshotEntry = (
    G2EvidenceOperatorSnapshot | G2EvidenceOperatorSnapshotError
)


class G2CollectEvidenceSummary(StrictModel):
    """``collect_g2_evidence`` sweep 1회의 집계 + per-operator compact 명세."""

    generated_window_days: int
    recent_limit: int
    operator_count: int
    ready_count: int
    error_count: int
    per_operator: list[G2EvidenceOperatorSnapshotEntry] = Field(default_factory=list)


class PersistedG2EvidenceOperatorSnapshot(StrictModel):
    """저장된 compact 셀 복원용 — 성공/실패 두 모양을 합집합으로 읽는다.

    ``blocking_gaps_count`` 는 ``int | None`` 이다. 이 키가 없던 과거 행을 ``0`` 으로
    읽으면 "차단 gap 이 없던 날"로 승격되어 통과하지 않은 날이 counted_days 에 들어간다
    (backfill 은 ``None`` 을 만나면 그 날을 그냥 통과시키지 않는다).
    """

    model_config = ConfigDict(extra="ignore")

    operator_id: int | None = None
    username: str | None = None
    evidence_status: str | None = None
    blocking_gaps_count: int | None = None
    sections: dict[str, str] | None = None
    error: str | None = None


class PersistedG2CollectEvidenceSummary(StrictModel):
    """저장된 ``collect_g2_evidence`` 행 복원용 — backfill 의 판정 입력.

    ``per_operator`` 만 있으면 backfill 이 돌아가지만 집계 키도 함께 선언한다: 어떤
    키가 기록되는지가 한 곳에 모여 있어야 새 소비처가 계약을 짐작하지 않는다.
    """

    model_config = ConfigDict(extra="ignore")

    generated_window_days: int | None = None
    recent_limit: int | None = None
    operator_count: int | None = None
    ready_count: int | None = None
    error_count: int | None = None
    # 미기록(``None``)과 "0명을 훑었다"(``[]``)를 구분한다 — 소비처는 ``or []`` 로 자기
    # degrade 정책을 명시한다(둘 다 "통과한 target 없음"이라 counted_day 는 나지 않는다).
    per_operator: list[PersistedG2EvidenceOperatorSnapshot] | None = None


# --- daily manifest draft 입력 (원장 전체 요약) ------------------------------
class G2LedgerOperatorSummary(StrictModel):
    """daily manifest draft 로 넘기는 target 운영자 1명의 **전체** 원장 요약.

    compact 셀과 달리 ``blocking_gaps`` 원문 목록을 담는다 — draft 의 gap 행
    (``detail`` / ``description``)이 이 문자열을 그대로 쓴다.
    """

    operator_id: int
    username: str
    evidence_status: str
    sections: dict[str, str] = Field(default_factory=dict)
    blocking_gaps: list[str] = Field(default_factory=list)


class G2LedgerOperatorSummaryError(StrictModel):
    """원장 요약 수집이 실패한 target 운영자 1명 (draft 입력의 에러 모양)."""

    operator_id: int
    username: str
    error: str


# 생산 union: build_daily_evidence_draft 로 넘기는 target 요약의 타입.
G2LedgerOperatorSummaryEntry = G2LedgerOperatorSummary | G2LedgerOperatorSummaryError
