"""개찰 1위(잠정) 수집 패스 — 실투찰 등록 공고 한정.

운영자가 실제로 제출한 실투찰(``BidDecisionRecord.submitted_bid_amount`` 존재)
공고 중 마감이 지난 것을 대상으로, ScsbidInfoService ``getOpengResultListInfo*``
날짜 윈도 조회로 **개찰 직후 1위 업체 정보 + 참가자 수**를 채워
``TenderResult.opening_*`` 에 영속화한다.

원칙(§2 정직 명세 · §4.5 파이프라인):

- 이 신호는 **잠정(개찰 1위)** 이다. 수의계약이면 1위=사실상 확정이나 적격심사는
  1위부터 캐스케이드라 1위≠낙찰 가능 — 낙찰 확정(``winning_*`` / ``award_outcome``)
  과 구분한다. 이 패스는 ``opening_*`` 만 쓰고 ``winning_*`` 는 건드리지 않는다.
- 공고번호 표적조회는 불가하다(실측). 마감일 기준 날짜 윈도를 조회한 뒤
  클라이언트에서 ``bidNtceNo`` 로 매칭한다(기존 낙찰피드와 동일 제약).
- 외부 호출은 rate limit 을 존중해 직렬 + throttle 하고, 사이클당 상한을 둔다.
  같은 (카테고리, 마감일) 그룹은 **1콜로 묶어** 중복 호출을 막는다.
- fetch 가 성공한 그룹의 후보에 한해(매칭 여부 무관) ``opening_checked_at`` 을
  스탬프해 backoff 안에서는 재조회하지 않는다(미매칭=개찰 미공개/윈도 밖이면
  backoff 후 재시도). fetch 예외 그룹은 미스탬프로 남겨 다음 사이클에 재시도한다.
- 외부 호출이므로 task 경로(6h ``notify_award_results``)에서만, 알림 흐름을 막지
  않게 예외 격리로 호출된다. 시크릿(service_key)은 로그/출력에 남기지 않는다.

개찰 1위 수집 뒤 낙찰이 확정되면, 낙찰피드의 ``resolve_tender_result`` 가 이 opening
전용 shell 행(낙찰자 미확정: winning_company 비어 있고 announced_at NULL)을 재사용해
winning_* 를 **같은 행에 병합**한다. 따라서 serializer 는 opening_rank1_* 와
winning_* 가 공존하는 한 행을 읽고, 화면은 "개찰 1위(잠정) → 낙찰 확정" 으로
전환되되 참가자수·개찰시각(winning_* 에 등가물 없음)은 보존된다.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta
from typing import Any, Callable

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.single_user import ensure_operator_account
from app.core.time import ensure_utc, to_kst, utc_now
from app.models.models import BidDecisionRecord, Project, TenderResult, User
from app.services.award_verification import strip_notice_suffix
from app.services.koneps import http_client, openapi, parsing

# A ``FetchOpeningResults`` takes ``(operation, inqry_bgn_dt, inqry_end_dt)`` —
# YYYYMMDDHHMM date-window tokens — and returns the raw 개찰결과 목록 rows.
# Injected in tests (§4.7) so the collection pass runs without live KONEPS IO.
FetchOpeningResults = Callable[[str, str, str], list[dict[str, Any]]]


def _live_fetch_opening_results(
    operation: str, inqry_bgn_dt: str, inqry_end_dt: str
) -> list[dict[str, Any]]:
    """Live ScsbidInfoService 개찰결과 목록 fetch (paginated). Never logs the key.

    Mirrors ``KonepsCollectorService._fetch_scsbid_reserve_detail`` (key-variant
    client + resultCode 가드) but sweeps a date window (``inqryDiv="1"``) with
    ``numOfRows`` pagination — 공고번호 표적조회는 불가(실측). ``totalCount`` 결손
    시 short-page(마지막 페이지가 page_size 미만)로 종료하고, ``MAX_PAGES`` 러너웨이
    가드로 상한을 둔다. 페이지 사이에는 전용 딜레이를 둔다(rate limit).
    """
    service_key = str(settings.KONEPS_OPENAPI_SERVICE_KEY or "").strip()
    url = f"{settings.KONEPS_OPENAPI_SCSBID_INFO_URL.rstrip('/')}/{operation}"
    page_size = max(1, min(int(settings.KONEPS_OPENING_RESULT_PAGE_SIZE or 999), 999))
    max_pages = max(1, int(settings.KONEPS_OPENING_RESULT_MAX_PAGES or 1))
    delay = max(0.0, float(settings.KONEPS_OPENING_RESULT_REQUEST_DELAY_SECONDS or 0.0))

    rows: list[dict[str, Any]] = []
    total_count: int | None = None
    for page_no in range(1, max_pages + 1):
        if page_no > 1 and delay:
            time.sleep(delay)
        params = {
            "type": "json",
            "numOfRows": page_size,
            "pageNo": page_no,
            "inqryDiv": "1",
            "inqryBgnDt": inqry_bgn_dt,
            "inqryEndDt": inqry_end_dt,
        }
        response, key_variant = http_client.request_openapi_with_key_variants(
            url, params=params, service_key=service_key, operation=operation
        )
        if response.status_code >= 400:
            raise ValueError(
                f"KONEPS ScsbidInfoService HTTP {response.status_code} for {operation}: "
                f"{response.text[:300]} Tried service key variants: {key_variant}."
            )
        payload = http_client.load_openapi_json(response)
        header = openapi.openapi_header(payload)
        result_code = str(header.get("resultCode") or "").strip()
        if result_code and result_code not in {"00", "03"}:
            raise ValueError(
                f"KONEPS ScsbidInfoService returned resultCode={result_code}: "
                f"{header.get('resultMsg') or 'unknown error'}"
            )
        body = openapi.openapi_body(payload)
        page_rows = openapi.openapi_item_list(body)
        rows.extend(page_rows)
        # totalCount 는 결손 시 None 유지(0 으로 강제하면 1페이지에서 조용히 절단됨).
        if total_count is None:
            total_count = parsing.safe_int(body.get("totalCount"))
        # 빈/짧은 페이지는 자연 종료(totalCount 유무와 무관). totalCount 를 알면 그
        # 카운트 도달로도 종료한다.
        if not page_rows or len(page_rows) < page_size:
            break
        if total_count is not None and (
            len(rows) >= total_count or page_no * page_size >= total_count
        ):
            break
    return rows


class OpeningResultCollectionService:
    """Collect the 개찰 1위(잠정) snapshot for the operator's real bids."""

    def collect(
        self,
        db: Session,
        *,
        operator: User | None = None,
        limit: int | None = None,
        fetch: FetchOpeningResults | None = None,
        now: datetime | None = None,
        sleep: Callable[[float], None] | None = None,
    ) -> dict[str, Any]:
        """Fill opening rank-1 info for tracked real bids past their deadline.

        Returns a summary dict (counts). Groups candidates by (operation, KST
        마감일) so each date window is fetched exactly once, matches feed rows by
        ``bidNtceNo``, and stamps ``opening_checked_at`` on the candidates of any
        group **whose fetch succeeded** (matched or not) so a backoff throttles
        re-queries. A group whose fetch raised is left un-stamped (retried next
        cycle). Consecutive fetch errors trip a circuit breaker that aborts the
        remaining groups. Every external call after the first is throttled by a
        dedicated delay(rate limit).
        """
        operator = operator or ensure_operator_account(db)
        fetch = fetch or _live_fetch_opening_results
        sleep = sleep or time.sleep
        stamp = now or utc_now()
        cap = max(
            1,
            int(
                limit
                if limit is not None
                else settings.KONEPS_OPENING_RESULT_COLLECTION_MAX_ITEMS
            ),
        )
        delay = max(
            0.0, float(settings.KONEPS_OPENING_RESULT_REQUEST_DELAY_SECONDS or 0.0)
        )
        max_consecutive = max(
            1, int(settings.KONEPS_OPENING_RESULT_MAX_CONSECUTIVE_ERRORS or 1)
        )

        candidates = self._candidate_projects(
            db, operator=operator, now=stamp, limit=cap
        )
        if not candidates:
            return {
                "status": "skipped",
                "candidate_count": 0,
                "matched_count": 0,
                "checked_count": 0,
                "group_count": 0,
                "error_count": 0,
                "aborted": False,
            }

        groups = self._group_by_window(candidates)
        matched = 0
        checked = 0
        error_count = 0
        consecutive_errors = 0
        aborted = False
        for index, ((operation, begin, end), projects) in enumerate(groups.items()):
            # 첫 외부 호출 전에는 sleep 없음; 이후 모든 연속 호출(성공/실패 무관, 특히
            # 429 뒤) 사이에 throttle 을 둔다.
            if index > 0 and delay:
                sleep(delay)
            try:
                rows = fetch(operation, begin, end)
            except Exception:  # noqa: BLE001 - a bad window must not abort the pass
                # Leave these candidates un-stamped so the next cycle retries the
                # window; a transient fetch error should not consume the backoff.
                error_count += 1
                consecutive_errors += 1
                if consecutive_errors >= max_consecutive:
                    # 연속 오류가 임계에 도달하면 남은 그룹을 버리고 중단한다(쿼터
                    # 소진 방어). 남은 후보는 미스탬프라 다음 사이클에 재시도된다.
                    aborted = True
                    break
                continue
            consecutive_errors = 0
            by_base = self._index_rows_by_base_notice(rows)
            for project in projects:
                summary = by_base.get(strip_notice_suffix(project.notice_number or ""))
                self._apply(db, project, summary, stamp=stamp)
                checked += 1
                if summary is not None and summary.get("rank1") is not None:
                    matched += 1
        db.commit()
        return {
            "status": "aborted" if aborted else "ok",
            "candidate_count": len(candidates),
            "matched_count": matched,
            "checked_count": checked,
            "group_count": len(groups),
            "error_count": error_count,
            "aborted": aborted,
        }

    def _candidate_projects(
        self, db: Session, *, operator: User, now: datetime, limit: int
    ) -> list[Project]:
        """Projects with a real bid, past deadline, not yet opening-collected.

        A project is skipped once *any* of its ``TenderResult`` rows carries an
        ``opening_rank1_company`` (collected) or was ``opening_checked_at`` within
        the recheck backoff (recently tried, still unmatched). Real bids are few,
        so the per-project TenderResult scan is cheap.
        """
        recheck_hours = max(0, int(settings.KONEPS_OPENING_RESULT_RECHECK_HOURS or 0))
        backoff_cutoff = now - timedelta(hours=recheck_hours) if recheck_hours else None

        # NOTE: 엔티티 전체 SELECT DISTINCT 는 Postgres 에서 json 컬럼
        # (Project.eligibility_raw)에 동등 연산자가 없어 UndefinedFunction 으로
        # 실패한다(라이브 실증 2026-07-19; SQLite 테스트는 통과하는 dialect 갭).
        # 중복 제거(실투찰 레코드 다건 join)는 파이썬 측 id dedupe 로 수행한다.
        projects = (
            db.query(Project)
            .join(BidDecisionRecord, BidDecisionRecord.project_id == Project.id)
            .filter(
                BidDecisionRecord.operator_id == operator.id,
                BidDecisionRecord.submitted_bid_amount.isnot(None),
                Project.deadline.isnot(None),
                Project.deadline < now,
            )
            .order_by(Project.deadline.desc())
            .all()
        )

        candidates: list[Project] = []
        seen_project_ids: set[int] = set()
        for project in projects:
            if project.id in seen_project_ids:
                continue
            seen_project_ids.add(project.id)
            if self._already_handled(db, project, backoff_cutoff=backoff_cutoff):
                continue
            candidates.append(project)
            if len(candidates) >= limit:
                break
        return candidates

    def _already_handled(
        self, db: Session, project: Project, *, backoff_cutoff: datetime | None
    ) -> bool:
        """True if opening was already collected or checked within the backoff."""
        results = (
            db.query(TenderResult).filter(TenderResult.project_id == project.id).all()
        )
        for result in results:
            if result.opening_rank1_company:
                return True
            if backoff_cutoff is not None and result.opening_checked_at is not None:
                if ensure_utc(result.opening_checked_at) >= backoff_cutoff:
                    return True
        return False

    def _group_by_window(
        self, projects: list[Project]
    ) -> dict[tuple[str, str, str], list[Project]]:
        """Group candidates by (operation, window-begin, window-end) tokens.

        The 개찰 falls on the KST 마감일 ~ 익일, so the window spans that day 00:00
        through the next day 23:59 (YYYYMMDDHHMM). Same-category same-day projects
        share one fetch.
        """
        groups: dict[tuple[str, str, str], list[Project]] = {}
        for project in projects:
            operation = openapi.opening_result_operation_for_category(project.category)
            kst_day = to_kst(ensure_utc(project.deadline)).date()
            begin = f"{kst_day.strftime('%Y%m%d')}0000"
            end = f"{(kst_day + timedelta(days=1)).strftime('%Y%m%d')}2359"
            groups.setdefault((operation, begin, end), []).append(project)
        return groups

    def _index_rows_by_base_notice(
        self, rows: list[dict[str, Any]]
    ) -> dict[str, dict[str, Any]]:
        """Project rows into summaries keyed by suffix-stripped 공고번호."""
        indexed: dict[str, dict[str, Any]] = {}
        for raw in rows:
            summary = openapi.build_opening_result_summary(raw)
            if summary is None:
                continue
            base = strip_notice_suffix(summary["notice_number"])
            indexed.setdefault(base, summary)
        return indexed

    def _apply(
        self,
        db: Session,
        project: Project,
        summary: dict[str, Any] | None,
        *,
        stamp: datetime,
    ) -> None:
        """Stamp opening_checked_at, and write opening_* when a row matched.

        Writes onto the latest ``TenderResult`` (get-or-create) and never touches
        ``winning_*``. An unmatched candidate is only stamped so the backoff
        applies; a matched one also records the 1위 snapshot.
        """
        tender = (
            db.query(TenderResult)
            .filter(TenderResult.project_id == project.id)
            .order_by(TenderResult.id.desc())
            .first()
        )
        if tender is None:
            tender = TenderResult(project_id=project.id)
            db.add(tender)

        tender.opening_checked_at = stamp
        if summary is None:
            return
        rank1 = summary.get("rank1")
        if rank1 is not None:
            tender.opening_rank1_company = rank1.get("company")
            tender.opening_rank1_business_no = rank1.get("business_no")
            tender.opening_rank1_amount = rank1.get("amount")
            tender.opening_rank1_rate = rank1.get("rate")
        tender.opening_participant_count = summary.get("participant_count")
        tender.opened_at = summary.get("opened_at")
