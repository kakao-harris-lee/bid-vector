"""DB read / resolve / write helpers for the KONEPS collector.

These functions were extracted verbatim from ``KonepsCollectorService``
(``collector.py``) as part of the incremental God-module decomposition
(Phase C1 DB read/resolve layer, Phase C2 DB write/persist layer). They take an
explicit ``db: Session`` and carry no instance state, so they live here as
module-level functions rather than methods.

The read/resolve helpers perform DB reads and in-memory ORM mutations
(including the pre-existing ``db.add`` / ``db.flush`` staging in the upsert
helpers) but never ``db.commit``. The write functions
(``persist_crawl_results`` / ``create_crawl_job`` / ``mark_crawl_job_failed``)
own the transaction boundary: each issues exactly one ``db.commit`` at the same
point it did in the original method (``persist_crawl_results`` stages every item
in the loop and commits once at the end).

Behavior is intentionally identical to the original methods; this module is a
pure relocation, not a rewrite. To avoid an import cycle, this module must
never import ``collector``: the collector imports ``persistence`` (and the
sibling ``matching`` / ``parsing`` / ``scsbid`` modules), not the other way
around.
"""

import json
from datetime import timedelta
from typing import Any

from sqlalchemy.orm import Session

from app.core.time import utc_now
from app.models.models import CrawlJob, HistoricalData, Project, TenderResult
from app.schemas.schemas import CrawlRequest
from app.services.base_amount_basis import (
    classify_base_basis,
    normalize_winning_rate,
)
from app.services.koneps import matching, parsing, scsbid
from app.services.project_similarity import ProjectSimilarityService
from app.services.realtime import realtime_event_manager


def notice_numbers_with_persisted_reserve(db: Session) -> set[str]:
    """Notice numbers that already carry a non-empty persisted reserve price.

    One indexed scan over ``historical_data.notice_number`` (no per-notice
    round trip): a reserve price is JSON-encoded into ``reserve_prices`` as
    ``"[]"`` when absent, so we drop NULL/empty/``"[]"`` rows. Used to skip
    the per-notice reserve-detail HTTP fetch for already-settled awards.
    """
    rows = (
        db.query(HistoricalData.notice_number)
        .filter(
            HistoricalData.notice_number.isnot(None),
            HistoricalData.reserve_prices.isnot(None),
            HistoricalData.reserve_prices != "",
            HistoricalData.reserve_prices != "[]",
        )
        .all()
    )
    return {str(notice_number) for (notice_number,) in rows if notice_number}


def notice_numbers_checked_recently(db: Session, within_hours: int) -> set[str]:
    """Notice numbers whose reserve detail was checked within ``within_hours``.

    The deferred reserve-detail backfill stamps
    ``HistoricalData.reserve_detail_checked_at`` whenever a fetch succeeded but
    returned no settled reserve ("not_settled"). The collector uses this set to
    back off permanently-empty notices: one that was checked recently is skipped
    from the deferred set so it is re-checked at most once per recheck window
    instead of every 6h sweep (which would burn the ScsbidInfoService rate
    limit). ``within_hours <= 0`` disables the gate (returns an empty set).

    One indexed scan; the cutoff is an instant (``utc_now`` minus the window)
    and the stored timestamps are UTC-aware, so the comparison is frame-exact.
    """
    if within_hours <= 0:
        return set()
    cutoff = utc_now() - timedelta(hours=within_hours)
    rows = (
        db.query(HistoricalData.notice_number)
        .filter(
            HistoricalData.notice_number.isnot(None),
            HistoricalData.reserve_detail_checked_at.isnot(None),
            HistoricalData.reserve_detail_checked_at >= cutoff,
        )
        .all()
    )
    return {str(notice_number) for (notice_number,) in rows if notice_number}


def resolve_project_for_item(
    db: Session,
    *,
    item: dict[str, Any],
    request: CrawlRequest,
    historical_record: HistoricalData,
    project_similarity: ProjectSimilarityService,
    defer_embeddings: bool = False,
) -> tuple[Project | None, bool]:
    """Find or create a project row for a crawled notice and keep it enriched with crawl metadata.

    Returns ``(project, embedding_deferred)``. When ``defer_embeddings`` is
    True the synchronous embedding refresh is skipped (so high-volume scsbid
    award collection does not exceed the Celery time limit); the caller is
    expected to enqueue an async backfill for the touched project ids.
    """
    project: Project | None = None
    if historical_record.project_id is not None:
        project = (
            db.query(Project).filter(Project.id == historical_record.project_id).first()
        )

    if project is None:
        project = find_matching_project(db, item=item, request=request)

    is_new_project = project is None
    if project is None:
        project = Project(
            title=item.get("title") or item.get("notice_number") or "KONEPS notice",
            description="",
            requirements="",
            budget_estimate=0.0,
            category=matching.resolve_project_category(item, request),
        )
        db.add(project)
        db.flush()

    update_project_from_item(project, item=item, request=request)
    if defer_embeddings:
        # Persist a project row now; embedding is rebuilt asynchronously.
        db.flush()
        return project, True

    project_similarity.refresh_project_embedding(db, project, force=is_new_project)
    return project, False


def find_matching_project(
    db: Session,
    *,
    item: dict[str, Any],
    request: CrawlRequest,
) -> Project | None:
    """Heuristically link a crawled notice to an existing project using explicit keys first.

    Performance + correctness note (perf/scsbid-find-matching-project-index)
    ----------------------------------------------------------------------
    ``notice_number`` is KONEPS's authoritative unique key: two notices with
    different numbers are *different* tenders. The previous implementation
    loaded **every** project in the category (thousands of rows) per item and
    scanned them in Python, capping scsbid award persistence at ~1-2 items/s.

    This version exploits ``ix_projects_notice_number``:

    1. **Index fast path** -- when the item carries a notice number, query the
       index directly (``notice_number.in_(...)``) for matching
       ``Project.notice_number`` values. The column is persisted in canonical
       (normalized) form -- ``update_project_from_item`` writes
       ``_normalize_notice_number(...)`` and the ``20260612_*`` data migration
       back-filled legacy rows -- so the indexed equality probe is exact and
       the overwhelming majority of scsbid open-bid items resolve here with a
       handful of indexed rows loaded instead of the whole category.
    2. **Column-NULL notice fallback** -- a small set of legacy projects keep
       their notice number only inside free-form ``description``/``requirements``
       text (``Project.notice_number IS NULL``). Those few rows are loaded and
       compared with the existing extraction logic.
    3. **source_url / title fuzzy** -- restricted to ``notice_number IS NULL``
       candidates. Projects that *do* carry a notice number are resolved
       authoritatively in steps 1-2; we deliberately no longer fuzzy-merge an
       item into a project whose notice number differs, which previously could
       collapse two distinct tenders on title overlap.

    Invariant (why limiting step 3 to notice-less rows loses nothing):
    ``_normalize_source_url`` keeps only the ``bidNtceNo``/``bidNtceOrd``
    (and ``bidPbancNo``/``bidPbancOrd``) query keys, so a KONEPS detail URL
    *encodes the notice number*. Therefore ``source_url`` equality implies
    ``notice_number`` equality, and the step-1 notice probe already subsumes
    any source_url match a notice-bearing project could have offered. Limiting
    the fuzzy step to notice-less candidates never drops a valid notice-bearing
    match -- it only avoids re-introducing a full category scan.

    Behavioural change: an item with a notice number is no longer matched to an
    existing project that has a *different* notice number via title overlap.
    This is intentional -- it prevents merging distinct tenders -- and the
    fuzzy heuristics now apply only to notice-less candidates.

    4. **Notice-less items** (rare; some KONEPS payloads) retain the original
       full category load + source_url/title fuzzy matching to avoid any
       regression for that path.
    """
    target_title = parsing.normalize_title(item.get("title"))
    target_notice_number = parsing.normalize_notice_number(item.get("notice_number"))
    target_source_url = matching.normalize_source_url(item.get("source_url"))
    target_agencies = matching.extract_item_agency_keys(item)
    target_category = matching.resolve_project_category(item, request)
    target_budget = matching.resolve_budget_estimate(item)
    target_deadline = parsing.coerce_datetime(item.get("closing_at"))

    if target_notice_number:
        # 1. Index fast path: match on the indexed notice_number column.
        raw_notice = str(item.get("notice_number") or "").strip()
        notice_variants = {
            variant for variant in (raw_notice, target_notice_number) if variant
        }
        for candidate in (
            db.query(Project).filter(Project.notice_number.in_(notice_variants)).all()
        ):
            if (
                parsing.normalize_notice_number(candidate.notice_number)
                == target_notice_number
            ):
                return candidate

        # 2. Notice number stored only in free-form text (column is NULL).
        #    These are a small minority, so scanning them is cheap.
        null_notice_query = db.query(Project).filter(Project.notice_number.is_(None))
        if target_category:
            null_notice_query = null_notice_query.filter(
                Project.category == target_category
            )
        null_notice_candidates = null_notice_query.all()
        for candidate in null_notice_candidates:
            candidate_notice_number = parsing.normalize_notice_number(
                matching.extract_project_notice_number(candidate)
            )
            if (
                candidate_notice_number
                and candidate_notice_number == target_notice_number
            ):
                return candidate

        # 3. source_url / title fuzzy, restricted to notice-less candidates.
        #    Projects carrying a (different) notice number are authoritatively
        #    distinct tenders and must not be fuzzy-merged here.
        return matching.match_by_url_or_title(
            null_notice_candidates,
            target_source_url=target_source_url,
            target_title=target_title,
            target_agencies=target_agencies,
            target_budget=target_budget,
            target_deadline=target_deadline,
        )

    # 4. Item has no notice number (rare). Preserve the original behaviour:
    #    full category load + source_url/title fuzzy matching.
    query = db.query(Project)
    if target_category:
        query = query.filter(Project.category == target_category)
    candidates = query.all()
    return matching.match_by_url_or_title(
        candidates,
        target_source_url=target_source_url,
        target_title=target_title,
        target_agencies=target_agencies,
        target_budget=target_budget,
        target_deadline=target_deadline,
    )


def update_project_from_item(
    project: Project, *, item: dict[str, Any], request: CrawlRequest
) -> None:
    """Apply crawled notice details onto a project without discarding user-entered context."""
    item_metadata = item.get("metadata", {})
    resolved_category = matching.resolve_project_category(item, request)
    budget_estimate = matching.resolve_budget_estimate(item)
    budget_values = [
        float(amount)
        for amount in (
            item.get("base_amount"),
            item.get("estimated_amount"),
            budget_estimate,
        )
        if amount not in (None, "", 0, 0.0)
    ]
    description_lines = _project_description_lines(item, item_metadata)
    requirement_lines = _project_requirement_lines(item, item_metadata)

    if item.get("title") and parsing.should_replace_project_title(
        project.title, item.get("title")
    ):
        project.title = str(item.get("title")).strip()
    _update_project_notice_identity(project, item=item)
    _update_project_agencies(project, item_metadata=item_metadata)
    project.description = parsing.merge_text_lines(
        project.description, description_lines
    )
    project.requirements = parsing.merge_text_lines(
        project.requirements, requirement_lines
    )
    project.category = resolved_category or project.category
    project.budget_estimate = budget_estimate or float(project.budget_estimate or 0.0)
    project.budget_min = min(budget_values) if budget_values else project.budget_min
    project.budget_max = max(budget_values) if budget_values else project.budget_max

    closing_at = parsing.coerce_datetime(item.get("closing_at"))
    if closing_at is not None:
        project.deadline = closing_at

    resolved_status = matching.resolve_project_status(item)
    if resolved_status:
        project.status = resolved_status

    if item.get("business_type_code") is not None:
        project.business_type_code = item.get("business_type_code")
    if item.get("business_type_label") is not None:
        project.business_type_label = item.get("business_type_label")

    # 공고 낙찰하한율은 값이 있을 때만 갱신한다. scsbid/재수집 아이템이 이 필드를
    # 실어오지 않는 경우(None) 기존 값을 지우지 않도록 덮어쓰지 않는다.
    if item.get("award_floor_rate") is not None:
        project.award_floor_rate = item.get("award_floor_rate")

    # 공고 참가자격 raw 필드도 비어있지 않은 dict일 때만 저장한다. scsbid/재수집
    # 아이템이 자격 원문을 싣지 않으면(None/빈 dict) 기존 값을 지우지 않는다
    # (award_floor_rate와 동일 가드). 이 컬럼은 PR-B 라벨 추출의 원천이며 현재
    # 소비자가 없다.
    if item.get("eligibility_raw"):
        project.eligibility_raw = item.get("eligibility_raw")

    db_title = project.title or item.get("notice_number") or "KONEPS notice"
    project.title = db_title.strip()


def _project_description_lines(
    item: dict[str, Any],
    item_metadata: dict[str, Any],
) -> list[str | None]:
    demand_agency = item_metadata.get("opening_demand_agency") or item_metadata.get(
        "demand_agency"
    )
    return [
        (f"공고번호: {item.get('notice_number')}" if item.get("notice_number") else None),
        (
            f"공고기관: {item_metadata.get('issuing_agency')}"
            if item_metadata.get("issuing_agency")
            else None
        ),
        f"수요기관: {demand_agency}" if demand_agency else None,
        f"공고원문: {item.get('source_url')}" if item.get("source_url") else None,
        (f"업무구분: {item.get('business_type')}" if item.get("business_type") else None),
        (
            f"개찰상태: {item_metadata.get('opening_status')}"
            if item_metadata.get("opening_status")
            else None
        ),
    ]


def _project_requirement_lines(
    item: dict[str, Any],
    item_metadata: dict[str, Any],
) -> list[str | None]:
    return [
        f"지역요건: {item.get('region')}" if item.get("region") else None,
        (
            f"면허요건: {' '.join(item.get('license_codes') or [])}"
            if item.get("license_codes")
            else None
        ),
        (
            f"기초금액: {float(item.get('base_amount')):.0f}"
            if item.get("base_amount")
            else None
        ),
        (
            f"추정금액: {float(item.get('estimated_amount')):.0f}"
            if item.get("estimated_amount")
            else None
        ),
        (
            f"계약방법: {item_metadata.get('contract_method')}"
            if item_metadata.get("contract_method")
            else None
        ),
    ]


def _update_project_notice_identity(
    project: Project,
    *,
    item: dict[str, Any],
) -> None:
    notice_number = item.get("notice_number")
    normalized_notice_number = parsing.normalize_notice_number(notice_number)
    if normalized_notice_number and (
        not project.notice_number
        or parsing.normalize_notice_number(project.notice_number)
        == normalized_notice_number
    ):
        project.notice_number = normalized_notice_number
    source_url = item.get("source_url")
    if source_url and (
        not project.source_url
        or matching.normalize_source_url(project.source_url)
        == matching.normalize_source_url(source_url)
    ):
        project.source_url = str(source_url).strip()


def _update_project_agencies(
    project: Project,
    *,
    item_metadata: dict[str, Any],
) -> None:
    issuing_agency = item_metadata.get("issuing_agency")
    if issuing_agency and (
        not project.issuing_agency
        or parsing.normalize_agency_name(project.issuing_agency)
        == parsing.normalize_agency_name(issuing_agency)
    ):
        project.issuing_agency = str(issuing_agency).strip()
    demand_agency = item_metadata.get("opening_demand_agency") or item_metadata.get(
        "demand_agency"
    )
    if demand_agency and (
        not project.demand_agency
        or parsing.normalize_agency_name(project.demand_agency)
        == parsing.normalize_agency_name(demand_agency)
    ):
        project.demand_agency = str(demand_agency).strip()


def resolve_tender_result(
    db: Session,
    *,
    project_id: int | None,
    item_metadata: dict[str, Any],
    crawl_job_status: str,
) -> TenderResult:
    """Upsert a tender result snapshot so repeated crawls do not duplicate the same award record."""
    announced_at = parsing.coerce_datetime(item_metadata.get("opening_announced_at"))
    winning_company = item_metadata.get("winning_company") or ""
    winning_amount = item_metadata.get("winning_amount") or 0.0
    winning_rate = item_metadata.get("winning_rate") or 0.0
    result_status = item_metadata.get("opening_status") or crawl_job_status

    tender_result: TenderResult | None = None
    if project_id is not None:
        candidates = (
            db.query(TenderResult)
            .filter(TenderResult.project_id == project_id)
            .order_by(TenderResult.id.desc())
            .all()
        )
        for candidate in candidates:
            if announced_at is not None and candidate.announced_at == announced_at:
                tender_result = candidate
                break
            if (
                candidate.winning_company == winning_company
                and float(candidate.winning_amount or 0.0)
                == float(winning_amount or 0.0)
                and float(candidate.winning_rate or 0.0) == float(winning_rate or 0.0)
            ):
                tender_result = candidate
                break

        if tender_result is None:
            # 현실 순서(개찰 1위 수집이 먼저 → 낙찰 피드가 나중)에서 남는 opening
            # 전용 shell(낙찰자 미확정: winning_company 비어 있고 announced_at NULL)을
            # 재사용해 winning_* 를 같은 행에 병합한다. 그러지 않으면 새 행이 생겨
            # opening_rank1_*/참가자수/opened_at 이 최신행만 읽는 serializer 에서
            # 소실된다(참가자수·개찰시각은 winning_* 에 등가물이 없어 영구 소실).
            # 기존 매칭(announced_at/winning 동등)이 모두 실패했을 때만 발동하므로
            # 기존 케이스 동작은 불변이다.
            for candidate in candidates:
                if (
                    not str(candidate.winning_company or "").strip()
                    and candidate.announced_at is None
                ):
                    tender_result = candidate
                    break

    if tender_result is None:
        tender_result = TenderResult(project_id=project_id)
        db.add(tender_result)

    tender_result.project_id = project_id
    tender_result.winning_company = winning_company
    tender_result.winning_amount = winning_amount
    tender_result.winning_rate = winning_rate
    tender_result.result_status = result_status
    tender_result.announced_at = announced_at
    return tender_result


def _crawl_event_name(status: str) -> str:
    """Realtime event name for a crawl job's current status.

    Frontend consumers key on the ``crawl.`` prefix (see frontend
    useRealtimeEvents), but the suffix must still reflect actual status for
    telemetry and any suffix-sensitive consumer.
    """
    return "crawl.completed" if status == "completed" else "crawl.fallback"


def create_crawl_job(
    db: Session,
    request: CrawlRequest,
    *,
    celery_task_id: str | None = None,
) -> CrawlJob:
    """Create a crawl job record before execution starts.

    ``celery_task_id`` is stamped at INSERT time so the row is immediately
    recoverable by a redelivered task (closes the orphan window where a
    SIGKILL between create and stamp would leave an unrecoverable row).
    """
    crawl_job = CrawlJob(
        source=request.source,
        target_date=request.target_date,
        status="running",
        result_count=0,
        celery_task_id=str(celery_task_id) if celery_task_id else None,
    )
    db.add(crawl_job)
    db.commit()
    db.refresh(crawl_job)
    realtime_event_manager.publish_event(
        _crawl_event_name(crawl_job.status),
        {
            "crawl_job_id": int(crawl_job.id),
            "project_id": (
                int(crawl_job.project_id) if crawl_job.project_id is not None else None
            ),
            "status": crawl_job.status,
            "source": crawl_job.source,
            "target_date": crawl_job.target_date,
            "result_count": int(crawl_job.result_count or 0),
            "error_message": crawl_job.error_message,
        },
    )
    return crawl_job


def persist_crawl_results(
    db: Session,
    crawl_job: CrawlJob,
    request: CrawlRequest,
    response: dict[str, Any],
    *,
    defer_embeddings: bool = False,
) -> CrawlJob:
    """Persist crawl history and any usable opening-result data.

    ``defer_embeddings`` is decided by the *caller*, not this method: only the
    Celery collection task (the single path with a hard time limit) defers
    embeddings to an async backfill. Synchronous callers (``POST
    /operations/crawl``, the scsbid backfill script) leave it at the default
    ``False`` so projects are embedded inline -- otherwise their newly created
    projects would never be embedded (no inline, no enqueued backfill) and
    silently drop out of pgvector search/recommendation.
    """
    items = response.get("items", [])
    metadata = response.get("metadata", {})

    _apply_crawl_response_summary(
        crawl_job,
        job_status=response.get("job_status", "completed"),
        collected_count=response.get("collected_count", len(items)),
        metadata=metadata,
    )

    project_similarity = ProjectSimilarityService()
    linked_project_ids: set[int] = set()
    # When deferred (Celery collection task for high-volume scsbid sweeps),
    # per-item embedding is skipped and the touched project ids are surfaced
    # for a single async backfill; CPU model inference per item would
    # otherwise exceed the Celery hard time limit.
    deferred_embedding_project_ids: set[int] = set()

    for item in items:
        project, embedding_deferred = _persist_crawl_item(
            db,
            item=item,
            request=request,
            project_similarity=project_similarity,
            crawl_job_status=crawl_job.status,
            defer_embeddings=defer_embeddings,
        )
        if project is not None:
            linked_project_ids.add(int(project.id))
            if embedding_deferred:
                deferred_embedding_project_ids.add(int(project.id))

    if len(linked_project_ids) == 1:
        crawl_job.project_id = next(iter(linked_project_ids))

    # Surface deferred-embedding project ids so the task layer can enqueue a
    # single async backfill. The service layer never imports the task module
    # (avoids a circular import); the orchestration lives in the task.
    if deferred_embedding_project_ids:
        response.setdefault("metadata", {})["deferred_embedding_project_ids"] = sorted(
            deferred_embedding_project_ids
        )

    return _commit_and_publish_crawl_job(
        db, crawl_job, event_name=_crawl_event_name(crawl_job.status)
    )


def _apply_crawl_response_summary(
    crawl_job: CrawlJob,
    *,
    job_status: str,
    collected_count: int,
    metadata: dict[str, Any],
) -> None:
    crawl_job.status = job_status
    crawl_job.result_count = collected_count
    crawl_job.error_message = parsing.format_crawl_error_message(metadata)
    crawl_job.completed_at = utc_now()


def _persist_crawl_item(
    db: Session,
    *,
    item: dict[str, Any],
    request: CrawlRequest,
    project_similarity: ProjectSimilarityService,
    crawl_job_status: str,
    defer_embeddings: bool,
) -> tuple[Project | None, bool]:
    item_metadata = item.get("metadata", {})
    historical_record = _resolve_historical_record(
        db, notice_number=item.get("notice_number")
    )
    project, embedding_deferred = resolve_project_for_item(
        db,
        item=item,
        request=request,
        historical_record=historical_record,
        project_similarity=project_similarity,
        defer_embeddings=defer_embeddings,
    )
    if project is not None:
        historical_record.project_id = project.id
    _update_historical_record_from_item(
        historical_record,
        item=item,
        item_metadata=item_metadata,
        request=request,
    )
    _persist_tender_result_for_item(
        db,
        project=project,
        historical_record=historical_record,
        item_metadata=item_metadata,
        crawl_job_status=crawl_job_status,
    )
    return project, embedding_deferred


def _resolve_historical_record(
    db: Session,
    *,
    notice_number: Any,
) -> HistoricalData:
    historical_record = (
        db.query(HistoricalData)
        .filter(HistoricalData.notice_number == notice_number)
        .first()
    )
    if historical_record is None:
        historical_record = HistoricalData(notice_number=notice_number)
        db.add(historical_record)
    return historical_record


def _update_historical_record_from_item(
    historical_record: HistoricalData,
    *,
    item: dict[str, Any],
    item_metadata: dict[str, Any],
    request: CrawlRequest,
) -> None:
    historical_record.agency_name = (
        item_metadata.get("opening_demand_agency")
        or item_metadata.get("demand_agency")
        or item_metadata.get("issuing_agency")
        or ""
    )
    historical_record.category = matching.resolve_project_category(item, request)
    _update_historical_base_fields(
        historical_record, item=item, item_metadata=item_metadata
    )
    historical_record.bid_rate = (
        parsing.normalize_bid_rate_value(
            item_metadata.get("bid_rate") or item_metadata.get("winning_rate")
        )
        or 0.0
    )
    _update_historical_reserve_fields(historical_record, item_metadata=item_metadata)
    historical_record.opened_at = parsing.coerce_datetime(
        item_metadata.get("opening_announced_at")
        or item_metadata.get("opening_scheduled_at")
    )


def _update_historical_base_fields(
    historical_record: HistoricalData,
    *,
    item: dict[str, Any],
    item_metadata: dict[str, Any],
) -> None:
    """Persist base_amount / predicted_price with an anti-clobber guard + provenance tag.

    Root cause (P1): a scsbid 개찰 pass now emits ``base_amount == 0.0`` when reserve
    detail carries no real 기초금액 (the 예정가 폴백 was removed at the source —
    ``app/services/koneps/scsbid.py`` — because ``낙찰가 / success_rate`` is 예정가,
    not 기초금액). A blind ``base_amount = item.get(...) or 0.0`` would then overwrite
    a better base captured by an earlier collection with ``0.0`` on the post-개찰 pass —
    the exact regression that let 예정가 오염 reach ``base_amount``. So we overwrite ONLY
    when the incoming value is a positive amount; otherwise the previously-stored base
    is preserved.

    ``base_amount_estimated`` (복수예비가격-복구 기초금액) is recorded when provided, and
    ``base_amount_basis`` is tagged from the FINAL stored base so newly-collected rows
    carry correct provenance without a separate backfill pass. The original
    ``base_amount`` is NEVER overwritten with an estimate/예정가 (정직 명세 §2 — 원본
    불변, 추정은 ``base_amount_estimated`` 로만).
    """
    incoming_base = parsing.coerce_amount(item.get("base_amount"))
    if incoming_base is not None and incoming_base > 0:
        historical_record.base_amount = float(incoming_base)

    incoming_estimated = parsing.coerce_amount(item.get("estimated_amount"))
    if incoming_estimated is not None and incoming_estimated > 0:
        historical_record.predicted_price = float(incoming_estimated)
    elif incoming_base is not None and incoming_base > 0:
        historical_record.predicted_price = float(incoming_base)

    recovered = parsing.coerce_amount(item_metadata.get("base_amount_estimated"))
    if recovered is not None and recovered > 0:
        historical_record.base_amount_estimated = float(recovered)

    # Provenance: classify the FINAL stored base with the row's winning result so a
    # calibration/holdout loop filtering to ``base_amount_basis == 'clean'`` never
    # picks up a 예정가-역산 / VAT-파생 / 미상 base.
    winning_amount = parsing.coerce_amount(item_metadata.get("winning_amount"))
    winning_rate = normalize_winning_rate(item_metadata.get("winning_rate"))
    historical_record.base_amount_basis = classify_base_basis(
        historical_record.base_amount, winning_amount, winning_rate
    )
    historical_record.basis_checked_at = utc_now()


def _update_historical_reserve_fields(
    historical_record: HistoricalData,
    *,
    item_metadata: dict[str, Any],
) -> None:
    incoming_reserve_prices = item_metadata.get("reserve_prices") or []
    if incoming_reserve_prices or not scsbid.has_persisted_reserve_prices(
        historical_record
    ):
        historical_record.reserve_prices = json.dumps(
            incoming_reserve_prices,
            ensure_ascii=False,
        )
    incoming_selected_numbers = item_metadata.get("selected_numbers") or []
    if incoming_selected_numbers or not scsbid.has_persisted_reserve_prices(
        historical_record
    ):
        historical_record.selected_numbers = json.dumps(
            incoming_selected_numbers,
            ensure_ascii=False,
        )


def _persist_tender_result_for_item(
    db: Session,
    *,
    project: Project | None,
    historical_record: HistoricalData,
    item_metadata: dict[str, Any],
    crawl_job_status: str,
) -> None:
    has_tender_result = any(
        item_metadata.get(key)
        for key in (
            "opening_status",
            "winning_company",
            "winning_amount",
            "winning_rate",
            "opening_announced_at",
        )
    )
    if not has_tender_result:
        return
    tender_result = resolve_tender_result(
        db,
        project_id=project.id if project is not None else historical_record.project_id,
        item_metadata=item_metadata,
        crawl_job_status=crawl_job_status,
    )
    if tender_result.project_id is None and historical_record.project_id is not None:
        tender_result.project_id = historical_record.project_id


def _commit_and_publish_crawl_job(
    db: Session,
    crawl_job: CrawlJob,
    *,
    event_name: str,
) -> CrawlJob:
    db.add(crawl_job)
    db.commit()
    db.refresh(crawl_job)
    realtime_event_manager.publish_event(
        event_name,
        {
            "crawl_job_id": int(crawl_job.id),
            "project_id": (
                int(crawl_job.project_id) if crawl_job.project_id is not None else None
            ),
            "status": crawl_job.status,
            "source": crawl_job.source,
            "target_date": crawl_job.target_date,
            "result_count": int(crawl_job.result_count or 0),
            "error_message": crawl_job.error_message,
        },
    )
    return crawl_job


def mark_crawl_job_failed(
    db: Session, crawl_job: CrawlJob, error_message: str
) -> CrawlJob:
    """Update an existing crawl job when execution fails unexpectedly."""
    crawl_job.status = "failed"
    crawl_job.error_message = error_message
    crawl_job.completed_at = utc_now()
    db.add(crawl_job)
    db.commit()
    db.refresh(crawl_job)
    return crawl_job
