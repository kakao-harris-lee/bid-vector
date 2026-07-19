"""개찰 1위(잠정) 수집 패스 + 파서 + 잠정 신호 노출 테스트.

Covers (§4.7 — fetch 주입 seam 으로 라이브 KONEPS IO 없이):

- ``parse_openg_corp_info`` 값 테이블: 실측 2건 + 캐럿 결손/빈 문자열 + 사업자번호
  제로패딩 보존.
- ``build_opening_result_summary`` 투영 + bidNtceNo 부재 시 None.
- 수집 패스: 매칭 upsert / opening_checked_at 스탬프 / backoff 재조회 억제 /
  미매칭 재시도 / winning_* 불변 / 사이클 상한 / 같은 윈도 1콜 묶음 / fetch 예외 격리.
- serializer ``opening_rank1_is_ours``: won / lost / None(상호·1위 부재).
"""

from __future__ import annotations

from datetime import timedelta

from app.core.single_user import ensure_operator_account
from app.core.time import utc_now
from app.models.models import Project, TenderResult
from app.services.koneps import openapi
from app.services.opening_result_collection import OpeningResultCollectionService
from app.services.real_bid_track import RealBidTrackService

# 실측 개찰 1위 캐럿 문자열 (2026-07-19).
RANK1_HAERIM = "주식회사해림^2268151610^최정현^77308840^88.001"
RANK1_HAEDAM = "주식회사 해담^3818103701^한경남^48094470^88.019"


# --------------------------------------------------------------------------- #
# 순수 파서 — 값 테이블
# --------------------------------------------------------------------------- #
def test_parse_openg_corp_info_measured_rows():
    a = openapi.parse_openg_corp_info(RANK1_HAERIM)
    assert a == {
        "company": "주식회사해림",
        "business_no": "2268151610",
        "representative": "최정현",
        "amount": 77308840.0,
        "rate": 0.88001,
    }
    b = openapi.parse_openg_corp_info(RANK1_HAEDAM)
    assert b["company"] == "주식회사 해담"
    assert b["business_no"] == "3818103701"
    assert b["rate"] == 0.88019


def test_parse_openg_corp_info_preserves_business_no_zero_padding():
    """사업자번호는 제로패딩 문자열 그대로 — int 변환 금지(#210)."""
    parsed = openapi.parse_openg_corp_info("주식회사영^0012345678^대표^1000^80.0")
    assert parsed["business_no"] == "0012345678"
    assert isinstance(parsed["business_no"], str)


def test_parse_openg_corp_info_missing_and_empty():
    assert openapi.parse_openg_corp_info(None) is None
    assert openapi.parse_openg_corp_info("") is None
    assert openapi.parse_openg_corp_info("^2268151610^최정현") is None  # 상호 부재
    # 짧은 분할: 있는 필드만, 나머지는 None.
    partial = openapi.parse_openg_corp_info("주식회사해림^2268151610")
    assert partial == {
        "company": "주식회사해림",
        "business_no": "2268151610",
        "representative": None,
        "amount": None,
        "rate": None,
    }


def test_build_opening_result_summary_projection():
    summary = openapi.build_opening_result_summary(
        {
            "bidNtceNo": "R26BK01627948",
            "bidNtceOrd": "000",
            "opengCorpInfo": RANK1_HAERIM,
            "prtcptCnum": "7",
            "opengDt": "2026-07-16 14:55:39",
            "progrsDivCdNm": "개찰완료",
        }
    )
    assert summary["notice_number"] == "R26BK01627948"
    assert summary["bid_notice_order"] == "000"  # 제로패딩 문자열 보존
    assert summary["rank1"]["company"] == "주식회사해림"
    assert summary["participant_count"] == 7
    assert summary["opened_at"] is not None
    assert summary["progress"] == "개찰완료"


def test_build_opening_result_summary_requires_notice_number():
    assert openapi.build_opening_result_summary({"opengCorpInfo": RANK1_HAERIM}) is None


def test_opening_result_operation_for_category():
    assert (
        openapi.opening_result_operation_for_category("construction")
        == "getOpengResultListInfoCnstwk"
    )
    assert (
        openapi.opening_result_operation_for_category("service")
        == "getOpengResultListInfoServc"
    )
    assert (
        openapi.opening_result_operation_for_category("goods")
        == "getOpengResultListInfoThng"
    )
    # 미지의 카테고리는 용역으로 폴백.
    assert (
        openapi.opening_result_operation_for_category("unknown")
        == "getOpengResultListInfoServc"
    )


# --------------------------------------------------------------------------- #
# 수집 패스 — fetch 주입
# --------------------------------------------------------------------------- #
class _FakeFetch:
    """개찰결과 목록 fetch 스텁: bidNtceNo→row 맵을 반환하고 호출을 기록한다."""

    def __init__(self, rows: list[dict] | None = None, *, raises: bool = False):
        self.rows = rows or []
        self.raises = raises
        self.calls: list[tuple[str, str, str]] = []

    def __call__(self, operation, begin, end):
        self.calls.append((operation, begin, end))
        if self.raises:
            raise ValueError("boom")
        return list(self.rows)


def _seed_real_bid(
    test_db,
    *,
    notice_number: str,
    category: str = "service",
    deadline_days_ago: int = 1,
) -> Project:
    project = Project(
        title=f"실투찰 {notice_number}",
        description="opening rank1",
        budget_estimate=100_000_000.0,
        category=category,
        notice_number=notice_number,
        status="open",
        deadline=utc_now() - timedelta(days=deadline_days_ago),
    )
    test_db.add(project)
    test_db.commit()
    test_db.refresh(project)

    operator = ensure_operator_account(test_db)
    RealBidTrackService().record_real_bid(
        test_db, operator=operator, project=project, bid_amount=77_308_840
    )
    return project


def _row_for(
    notice_number: str, *, corp: str = RANK1_HAERIM, participants: str = "7"
) -> dict:
    return {
        "bidNtceNo": notice_number,
        "bidNtceOrd": "000",
        "opengCorpInfo": corp,
        "prtcptCnum": participants,
        "opengDt": "2026-07-16 14:55:39",
        "progrsDivCdNm": "개찰완료",
    }


def test_collect_matches_and_upserts_opening_fields(test_db):
    project = _seed_real_bid(test_db, notice_number="R26BK01627948")
    fetch = _FakeFetch([_row_for("R26BK01627948")])

    result = OpeningResultCollectionService().collect(test_db, fetch=fetch)
    assert result["matched_count"] == 1
    assert result["checked_count"] == 1

    tender = test_db.query(TenderResult).filter_by(project_id=project.id).one()
    assert tender.opening_rank1_company == "주식회사해림"
    assert tender.opening_rank1_business_no == "2268151610"  # 제로패딩/문자열 보존
    assert tender.opening_rank1_amount == 77308840.0
    assert tender.opening_rank1_rate == 0.88001
    assert tender.opening_participant_count == 7
    assert tender.opened_at is not None
    assert tender.opening_checked_at is not None


def test_collect_stamps_checked_at_when_unmatched(test_db):
    """미매칭(개찰 미공개)도 opening_checked_at 스탬프 — 다음 backoff 후 재시도."""
    project = _seed_real_bid(test_db, notice_number="R26BK00000404")
    fetch = _FakeFetch([_row_for("R26BK09999999")])  # 다른 공고만 응답

    result = OpeningResultCollectionService().collect(test_db, fetch=fetch)
    assert result["matched_count"] == 0
    assert result["checked_count"] == 1

    tender = test_db.query(TenderResult).filter_by(project_id=project.id).one()
    assert tender.opening_rank1_company is None
    assert tender.opening_checked_at is not None


def test_collect_skips_already_collected(test_db):
    """1위가 이미 채워진 공고는 재수집하지 않는다(fetch 미호출)."""
    _seed_real_bid(test_db, notice_number="R26BK01627948")
    fetch = _FakeFetch([_row_for("R26BK01627948")])
    OpeningResultCollectionService().collect(test_db, fetch=fetch)

    fetch2 = _FakeFetch([_row_for("R26BK01627948")])
    result = OpeningResultCollectionService().collect(test_db, fetch=fetch2)
    assert result["candidate_count"] == 0
    assert fetch2.calls == []


def test_collect_backoff_suppresses_then_retries_unmatched(test_db):
    """미매칭은 backoff 안에서 억제되고, backoff 경과 후 재조회된다."""
    _seed_real_bid(test_db, notice_number="R26BK00000404")
    now = utc_now()

    fetch1 = _FakeFetch([])  # 매칭 없음
    OpeningResultCollectionService().collect(test_db, fetch=fetch1, now=now)
    assert len(fetch1.calls) == 1

    # backoff 안(같은 now): 후보에서 제외.
    fetch2 = _FakeFetch([])
    r2 = OpeningResultCollectionService().collect(test_db, fetch=fetch2, now=now)
    assert r2["candidate_count"] == 0
    assert fetch2.calls == []

    # backoff 경과(RECHECK_HOURS 초과): 재조회.
    later = now + timedelta(hours=7)
    fetch3 = _FakeFetch([_row_for("R26BK00000404")])
    r3 = OpeningResultCollectionService().collect(test_db, fetch=fetch3, now=later)
    assert r3["candidate_count"] == 1
    assert len(fetch3.calls) == 1


def test_collect_preserves_winning_fields(test_db):
    """opening_* 만 쓰고 낙찰 확정(winning_*)은 건드리지 않는다."""
    project = _seed_real_bid(test_db, notice_number="R26BK01627948")
    test_db.add(
        TenderResult(
            project_id=project.id,
            winning_company="낙찰건설",
            winning_amount=78_000_000,
            winning_rate=0.78,
            result_status="opened",
        )
    )
    test_db.commit()

    fetch = _FakeFetch([_row_for("R26BK01627948")])
    OpeningResultCollectionService().collect(test_db, fetch=fetch)

    tender = test_db.query(TenderResult).filter_by(project_id=project.id).one()
    assert tender.winning_company == "낙찰건설"
    assert tender.winning_amount == 78_000_000
    assert tender.winning_rate == 0.78
    assert tender.opening_rank1_company == "주식회사해림"


def test_collect_honors_cycle_cap(test_db):
    for suffix in ("01", "02", "03"):
        _seed_real_bid(test_db, notice_number=f"R26BK000000{suffix}")
    fetch = _FakeFetch([])
    result = OpeningResultCollectionService().collect(test_db, fetch=fetch, limit=2)
    assert result["candidate_count"] == 2


def test_collect_batches_same_window_into_one_call(test_db):
    """같은 카테고리·마감일 공고는 1콜로 묶어 중복 호출을 막는다."""
    same_day = utc_now() - timedelta(days=1)
    for notice in ("R26BK00000011", "R26BK00000012"):
        project = Project(
            title=notice,
            budget_estimate=100_000_000.0,
            category="service",
            notice_number=notice,
            status="open",
            deadline=same_day,
        )
        test_db.add(project)
        test_db.commit()
        test_db.refresh(project)
        RealBidTrackService().record_real_bid(
            test_db,
            operator=ensure_operator_account(test_db),
            project=project,
            bid_amount=50_000_000,
        )

    fetch = _FakeFetch(
        [_row_for("R26BK00000011"), _row_for("R26BK00000012", corp=RANK1_HAEDAM)]
    )
    result = OpeningResultCollectionService().collect(test_db, fetch=fetch)
    assert len(fetch.calls) == 1  # 한 번만 호출
    assert result["matched_count"] == 2


def test_collect_isolates_fetch_error_without_stamping(test_db):
    """fetch 예외는 그 윈도를 건너뛰고 stamp 하지 않아 다음 사이클에 재시도된다."""
    project = _seed_real_bid(test_db, notice_number="R26BK01627948")
    fetch = _FakeFetch(raises=True)
    OpeningResultCollectionService().collect(test_db, fetch=fetch)

    tender = test_db.query(TenderResult).filter_by(project_id=project.id).one_or_none()
    # 예외 윈도는 stamp 하지 않는다 — 후보로 남아 재시도.
    assert tender is None or tender.opening_checked_at is None


# --------------------------------------------------------------------------- #
# serializer — opening_rank1_is_ours
# --------------------------------------------------------------------------- #
def _seed_bid_with_opening(
    test_db, *, operator_company: str, rank1_company: str | None
):
    project = Project(
        title="개찰 노출",
        budget_estimate=100_000_000.0,
        category="service",
        notice_number="R26BK01627948",
        status="open",
        deadline=utc_now() - timedelta(days=1),
    )
    test_db.add(project)
    test_db.commit()
    test_db.refresh(project)

    operator = ensure_operator_account(test_db)
    operator.company = operator_company
    test_db.commit()

    record = RealBidTrackService().record_real_bid(
        test_db, operator=operator, project=project, bid_amount=77_308_840
    )
    test_db.add(
        TenderResult(
            project_id=project.id,
            opening_rank1_company=rank1_company,
            opening_rank1_amount=77_308_840.0,
            opening_rank1_rate=0.88001,
            opening_participant_count=7,
            opened_at=utc_now(),
            opening_checked_at=utc_now(),
        )
    )
    test_db.commit()
    return record


def test_serialize_opening_rank1_is_ours_won(test_db):
    record = _seed_bid_with_opening(
        test_db, operator_company="(주)해림", rank1_company="주식회사해림"
    )
    payload = RealBidTrackService().serialize_record(test_db, record)
    # 표기 차이(주식회사/(주)) 를 정규화로 흡수해 매칭.
    assert payload["opening_rank1_is_ours"] is True
    assert payload["opening_rank1_company"] == "주식회사해림"
    assert payload["opening_participant_count"] == 7
    assert payload["opening_rank1_rate"] == 0.88001


def test_serialize_opening_rank1_is_ours_lost(test_db):
    record = _seed_bid_with_opening(
        test_db, operator_company="주식회사해림", rank1_company="주식회사경쟁"
    )
    payload = RealBidTrackService().serialize_record(test_db, record)
    assert payload["opening_rank1_is_ours"] is False


def test_serialize_opening_rank1_is_ours_none_without_operator_company(test_db):
    record = _seed_bid_with_opening(
        test_db, operator_company="", rank1_company="주식회사해림"
    )
    payload = RealBidTrackService().serialize_record(test_db, record)
    # 운영자 상호 부재 → 판정하지 않음(§2 정직 명세).
    assert payload["opening_rank1_is_ours"] is None


def test_serialize_opening_rank1_is_ours_none_without_rank1(test_db):
    record = _seed_bid_with_opening(
        test_db, operator_company="주식회사해림", rank1_company=None
    )
    payload = RealBidTrackService().serialize_record(test_db, record)
    assert payload["opening_rank1_is_ours"] is None
    assert payload["opening_rank1_company"] is None
