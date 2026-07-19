"""개찰 1위(잠정) 수집 패스 + 파서 + 잠정 신호 노출 테스트.

Covers (§4.7 — fetch 주입 seam 으로 라이브 KONEPS IO 없이):

- ``parse_openg_corp_info`` 값 테이블: 실측 2건 + 캐럿 결손/빈 문자열 + 사업자번호
  제로패딩 보존.
- ``build_opening_result_summary`` 투영 + bidNtceNo 부재 시 None.
- 수집 패스: 매칭 upsert / opening_checked_at 스탬프 / backoff 재조회 억제 /
  미매칭 재시도 / winning_* 불변 / 사이클 상한 / 같은 윈도 1콜 묶음 / fetch 예외 격리.
- 방어: 그룹 간 throttle / 연속오류 서킷브레이커 / 페이지네이션 totalCount 결손
  방어 + max_pages 가드.
- 병합: resolve_tender_result 가 opening shell 을 재사용해 winning_* 를 같은 행에.
- serializer ``opening_rank1_is_ours``: won / lost / None(상호·1위 부재).
"""

from __future__ import annotations

from datetime import timedelta

import app.services.opening_result_collection as oc
from app.core.single_user import ensure_operator_account
from app.core.time import utc_now
from app.models.models import BidDecisionRecord, Project, TenderResult
from app.services.koneps import openapi
from app.services.koneps.persistence import resolve_tender_result
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
    """사업자번호는 제로패딩 문자열 그대로 — int 변환 금지(#210 교훈 준용)."""
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


# --------------------------------------------------------------------------- #
# 방어 — throttle / 서킷브레이커
# --------------------------------------------------------------------------- #
def _seed_bid_category(test_db, *, notice_number: str, category: str, days_ago: int):
    project = Project(
        title=notice_number,
        budget_estimate=100_000_000.0,
        category=category,
        notice_number=notice_number,
        status="open",
        deadline=utc_now() - timedelta(days=days_ago),
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
    return project


class _SequencedFetch:
    """앞 ``fail_first`` 호출은 예외, 이후는 빈 결과를 반환하며 호출 수를 센다."""

    def __init__(self, *, fail_first: int = 0):
        self.fail_first = fail_first
        self.calls = 0

    def __call__(self, operation, begin, end):
        self.calls += 1
        if self.calls <= self.fail_first:
            raise ValueError("boom")
        return []


def test_collect_throttles_between_groups(test_db):
    """첫 외부 호출 전에는 sleep 없음, 그룹 간 연속 호출 사이에만 throttle."""
    _seed_bid_category(
        test_db, notice_number="R26TA0001", category="service", days_ago=1
    )
    _seed_bid_category(
        test_db, notice_number="R26TA0002", category="construction", days_ago=1
    )
    sleeps: list[float] = []
    fetch = _FakeFetch([])
    result = OpeningResultCollectionService().collect(
        test_db, fetch=fetch, sleep=sleeps.append
    )
    assert result["group_count"] == 2
    # 2그룹 → 첫 호출 전엔 없음 + 그룹2 앞에 1회.
    assert sleeps == [oc.settings.KONEPS_OPENING_RESULT_REQUEST_DELAY_SECONDS]


def test_collect_circuit_breaker_aborts_on_consecutive_errors(test_db, monkeypatch):
    monkeypatch.setattr(oc.settings, "KONEPS_OPENING_RESULT_MAX_CONSECUTIVE_ERRORS", 3)
    for i, cat in enumerate(("service", "construction", "goods", "foreign")):
        _seed_bid_category(
            test_db, notice_number=f"R26CB000{i}", category=cat, days_ago=i + 1
        )
    fetch = _SequencedFetch(fail_first=99)  # 항상 실패
    result = OpeningResultCollectionService().collect(
        test_db, fetch=fetch, sleep=lambda _s: None
    )
    assert result["aborted"] is True
    assert result["status"] == "aborted"
    assert result["error_count"] == 3  # 3연속에서 중단
    assert result["checked_count"] == 0
    assert fetch.calls == 3  # 4번째 그룹은 호출하지 않음


def test_collect_circuit_breaker_resets_on_success(test_db, monkeypatch):
    monkeypatch.setattr(oc.settings, "KONEPS_OPENING_RESULT_MAX_CONSECUTIVE_ERRORS", 3)
    for i, cat in enumerate(("service", "construction", "goods")):
        _seed_bid_category(
            test_db, notice_number=f"R26RS000{i}", category=cat, days_ago=i + 1
        )
    fetch = _SequencedFetch(fail_first=2)  # 2연속 실패 후 성공 → 리셋
    result = OpeningResultCollectionService().collect(
        test_db, fetch=fetch, sleep=lambda _s: None
    )
    assert result["aborted"] is False
    assert result["error_count"] == 2
    assert fetch.calls == 3


# --------------------------------------------------------------------------- #
# 방어 — 페이지네이션 (live fetch, http 계층 주입)
# --------------------------------------------------------------------------- #
class _FakeResp:
    def __init__(self, payload, status_code: int = 200):
        self._payload = payload
        self.status_code = status_code
        self.text = ""


def _install_fake_http(monkeypatch, page_factory, *, total_count):
    """http_client 를 주입해 ``_live_fetch_opening_results`` 페이지네이션을 구동한다."""
    seen: dict[str, int] = {"max_page": 0}

    def fake_request(url, *, params, service_key, operation):
        page_no = int(params["pageNo"])
        seen["max_page"] = max(seen["max_page"], page_no)
        body: dict = {"items": {"item": page_factory(page_no)}}
        if total_count is not None:
            body["totalCount"] = total_count
        payload = {"response": {"header": {"resultCode": "00"}, "body": body}}
        return _FakeResp(payload), "variant"

    monkeypatch.setattr(
        oc.http_client, "request_openapi_with_key_variants", fake_request
    )
    monkeypatch.setattr(oc.http_client, "load_openapi_json", lambda resp: resp._payload)
    return seen


def test_live_fetch_paginates_when_totalcount_missing(monkeypatch):
    """totalCount 결손 시 short-page 로 종료(1페이지 조기절단 없이 2페이지 수집)."""
    monkeypatch.setattr(oc.settings, "KONEPS_OPENING_RESULT_PAGE_SIZE", 2)
    monkeypatch.setattr(oc.settings, "KONEPS_OPENING_RESULT_REQUEST_DELAY_SECONDS", 0.0)

    def factory(page_no):
        if page_no == 1:
            return [{"bidNtceNo": "N1"}, {"bidNtceNo": "N2"}]  # full page
        if page_no == 2:
            return [{"bidNtceNo": "N3"}]  # short page → stop
        return []

    seen = _install_fake_http(monkeypatch, factory, total_count=None)
    rows = oc._live_fetch_opening_results("getOpengResultListInfoServc", "0", "1")
    assert len(rows) == 3
    assert seen["max_page"] == 2  # 3페이지는 조회하지 않음


def test_live_fetch_max_pages_guard(monkeypatch):
    """totalCount 결손 + 매 페이지 full 이어도 max_pages 로 러너웨이를 막는다."""
    monkeypatch.setattr(oc.settings, "KONEPS_OPENING_RESULT_PAGE_SIZE", 2)
    monkeypatch.setattr(oc.settings, "KONEPS_OPENING_RESULT_MAX_PAGES", 3)
    monkeypatch.setattr(oc.settings, "KONEPS_OPENING_RESULT_REQUEST_DELAY_SECONDS", 0.0)

    seen = _install_fake_http(
        monkeypatch,
        lambda p: [{"bidNtceNo": f"N{p}a"}, {"bidNtceNo": f"N{p}b"}],  # 항상 full
        total_count=None,
    )
    rows = oc._live_fetch_opening_results("getOpengResultListInfoServc", "0", "1")
    assert seen["max_page"] == 3  # max_pages 에서 멈춤
    assert len(rows) == 6


# --------------------------------------------------------------------------- #
# 병합 — resolve_tender_result opening shell 재사용
# --------------------------------------------------------------------------- #
def test_resolve_tender_result_reuses_opening_shell(test_db):
    """현실 순서(개찰 수집 먼저 → 낙찰 피드 나중): 같은 행에 opening_*+winning_* 공존."""
    project = _seed_real_bid(test_db, notice_number="R26MG0001")
    OpeningResultCollectionService().collect(
        test_db, fetch=_FakeFetch([_row_for("R26MG0001")])
    )
    # 개찰 shell 1행 존재(참가자수/개찰시각 포함, winning 미확정).
    shell = test_db.query(TenderResult).filter_by(project_id=project.id).one()
    assert shell.opening_rank1_company == "주식회사해림"
    assert shell.winning_company in (None, "")

    # 낙찰 피드 도착 → shell 재사용(새 행 생성 금지).
    resolve_tender_result(
        test_db,
        project_id=project.id,
        item_metadata={
            "winning_company": "주식회사해림",
            "winning_amount": 77_308_840,
            "winning_rate": 0.88001,
            "opening_announced_at": "2026-07-16T15:00:00",
        },
        crawl_job_status="completed",
    )
    test_db.commit()

    rows = test_db.query(TenderResult).filter_by(project_id=project.id).all()
    assert len(rows) == 1  # 새 행 없음
    merged = rows[0]
    assert merged.winning_company == "주식회사해림"
    assert merged.winning_amount == 77_308_840
    # opening_* 보존(참가자수·개찰시각은 winning_* 에 등가물 없음).
    assert merged.opening_rank1_company == "주식회사해림"
    assert merged.opening_participant_count == 7
    assert merged.opened_at is not None


def test_resolve_tender_result_no_shell_creates_new_row(test_db):
    """opening shell 이 없으면 기존 동작 유지(새 행 생성)."""
    project = _seed_real_bid(test_db, notice_number="R26MG0002")
    resolve_tender_result(
        test_db,
        project_id=project.id,
        item_metadata={
            "winning_company": "낙찰건설",
            "winning_amount": 78_000_000,
            "winning_rate": 0.78,
            "opening_announced_at": "2026-07-16T15:00:00",
        },
        crawl_job_status="completed",
    )
    test_db.commit()
    row = test_db.query(TenderResult).filter_by(project_id=project.id).one()
    assert row.winning_company == "낙찰건설"
    assert row.opening_rank1_company is None


def test_candidate_projects_dedupes_multiple_real_bid_records(test_db):
    """실투찰 레코드가 다건인 프로젝트도 후보에 1회만 오른다 (파이썬 dedupe).

    회귀 가드: 엔티티 전체 SELECT DISTINCT 는 Postgres json 컬럼
    (Project.eligibility_raw)에 동등 연산자가 없어 UndefinedFunction 으로 죽는다
    (라이브 실증 2026-07-19). dedupe 는 쿼리가 아니라 id 집합으로 수행해야 한다.
    """
    project = _seed_real_bid(test_db, notice_number="R26BK00000021")
    operator = ensure_operator_account(test_db)
    test_db.add(
        BidDecisionRecord(
            project_id=project.id,
            operator_id=operator.id,
            submitted_bid_amount=51_000_000.0,
            submitted_at=utc_now(),
        )
    )
    test_db.commit()

    fetch = _FakeFetch([_row_for("R26BK00000021")])
    result = OpeningResultCollectionService().collect(test_db, fetch=fetch)

    assert result["candidate_count"] == 1
    assert result["checked_count"] == 1
    assert len(fetch.calls) == 1
