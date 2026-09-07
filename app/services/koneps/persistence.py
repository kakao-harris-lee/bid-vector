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

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.time import utc_now
from app.models.models import (
    CrawlJob,
    HistoricalData,
    Project,
)
from app.schemas.koneps_items import CrawlItemMetadataFacts, KonepsCollectedItem
from app.schemas.schemas import CrawlRequest
from app.services.koneps import (
    base_provenance,
    budget_fields,
    matching,
    parsing,
    scsbid,
)
from app.services.inference_outbox import InferenceOutboxService
from app.services.realtime import realtime_event_manager
from app.services.task_singleton import singleton_lock_id
from app.services.tender_result_events import (
    stage_tender_result_event as stage_tender_result_event,
)
from app.services.tender_result_persistence import (
    resolve_tender_result as resolve_tender_result,
)


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
    item: KonepsCollectedItem,
    request: CrawlRequest,
    historical_record: HistoricalData,
) -> tuple[Project | None, bool]:
    """Find or create a project and report whether its semantic input changed.

    This persistence boundary never performs embedding inference. The caller
    stages a durable semantic-input event for changed rows in the same unit of
    work, and the inference task path builds the embedding after commit.
    """
    project: Project | None = None
    if historical_record.project_id is not None:
        project = (
            db.query(Project).filter(Project.id == historical_record.project_id).first()
        )

    if project is None:
        project = find_matching_project(db, item=item, request=request)

    if project is None:
        project = Project(
            title=item.title or item.notice_number or "KONEPS notice",
            description="",
            requirements="",
            budget_estimate=0.0,
            category=matching.resolve_project_category(item, request),
        )
        db.add(project)
        db.flush()

    semantic_input_changed = update_project_from_item(
        project, item=item, request=request
    )
    db.flush()
    return project, semantic_input_changed


def find_matching_project(
    db: Session,
    *,
    item: KonepsCollectedItem,
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
    target_title = parsing.normalize_title(item.title)
    target_notice_number = parsing.normalize_notice_number(item.notice_number)
    target_source_url = matching.normalize_source_url(item.source_url)
    target_agencies = matching.extract_item_agency_keys(item)
    target_category = matching.resolve_project_category(item, request)
    target_budget = matching.resolve_budget_estimate(item)
    target_deadline = parsing.coerce_datetime(item.closing_at)

    if target_notice_number:
        # 1. Index fast path: match on the indexed notice_number column.
        raw_notice = str(item.notice_number or "").strip()
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
    project: Project, *, item: KonepsCollectedItem, request: CrawlRequest
) -> bool:
    """Apply crawled notice details onto a project without discarding user-entered context."""
    from app.services.similarity_read_model import (
        invalidate_project_embedding,
        project_embedding_input_state,
    )

    previous_semantic_state = project_embedding_input_state(project)
    facts = item.opening_facts()
    resolved_category = matching.resolve_project_category(item, request)
    description_lines = _project_description_lines(item, facts)
    requirement_lines = _project_requirement_lines(item, facts)

    if item.title and parsing.should_replace_project_title(project.title, item.title):
        project.title = str(item.title).strip()
    _update_project_notice_identity(project, item=item)
    _update_project_agencies(project, facts=facts)
    project.description = parsing.merge_text_lines(
        project.description, description_lines
    )
    project.requirements = parsing.merge_text_lines(
        project.requirements, requirement_lines
    )
    project.category = resolved_category or project.category
    # 추정가격은 출처 인지 가드를 거친다: 파생 예정가·예산/기초금액 폴백이 저장된 공고
    # 추정가격을 덮으면 provenance 분모(#358)와 budget_cap(#356)이 함께 흔들린다.
    # 결합 주의: 신고(``item.estimated_amount_source``)는 item 의 **추정가격 자리**를 서술하고
    # 가드가 판정하는 값은 ``resolve_budget_estimate``(base 폴백 포함) 산출이다. 둘이 어긋나지
    # 않는 근거는 DTO 게이트다 — 그 자리가 비면 출처도 ``None`` 으로 접혀, 폴백으로 도착한
    # base 가 게시값 권위를 얻지 못한다(``app/schemas/koneps_items.py``).
    budget_fields.apply_budget_amounts(project, item=item)

    closing_at = parsing.coerce_datetime(item.closing_at)
    if closing_at is not None:
        project.deadline = closing_at

    resolved_status = matching.resolve_project_status(item)
    if resolved_status:
        project.status = resolved_status

    if item.business_type_code is not None:
        project.business_type_code = item.business_type_code
    if item.business_type_label is not None:
        project.business_type_label = item.business_type_label

    semantic_input_changed = (
        project_embedding_input_state(project) != previous_semantic_state
    )
    if semantic_input_changed:
        invalidate_project_embedding(project)

    # 공고 낙찰하한율은 값이 있을 때만 갱신한다. scsbid/재수집 아이템이 이 필드를
    # 실어오지 않는 경우(None) 기존 값을 지우지 않도록 덮어쓰지 않는다.
    if item.award_floor_rate is not None:
        project.award_floor_rate = item.award_floor_rate

    # 공고 참가자격 raw 필드도 비어있지 않은 dict일 때만 저장한다. scsbid/재수집
    # 아이템이 자격 원문을 싣지 않으면(None/빈 dict) 기존 값을 지우지 않는다
    # (award_floor_rate와 동일 가드). 이 컬럼은 PR-B 라벨 추출의 원천이며 현재
    # 소비자가 없다.
    if item.eligibility_raw:
        project.eligibility_raw = item.eligibility_raw

    db_title = project.title or item.notice_number or "KONEPS notice"
    project.title = db_title.strip()
    return semantic_input_changed


def _project_description_lines(
    item: KonepsCollectedItem,
    facts: CrawlItemMetadataFacts,
) -> list[str | None]:
    demand_agency = facts.resolved_demand_agency()
    return [
        (f"공고번호: {item.notice_number}" if item.notice_number else None),
        (f"공고기관: {facts.issuing_agency}" if facts.issuing_agency else None),
        f"수요기관: {demand_agency}" if demand_agency else None,
        f"공고원문: {item.source_url}" if item.source_url else None,
        (f"업무구분: {item.business_type}" if item.business_type else None),
        (f"개찰상태: {facts.opening_status}" if facts.opening_status else None),
    ]


def _project_requirement_lines(
    item: KonepsCollectedItem,
    facts: CrawlItemMetadataFacts,
) -> list[str | None]:
    return [
        f"지역요건: {item.region}" if item.region else None,
        (
            f"면허요건: {' '.join(item.license_codes or [])}"
            if item.license_codes
            else None
        ),
        (f"기초금액: {float(item.base_amount):.0f}" if item.base_amount else None),
        (
            f"추정금액: {float(item.estimated_amount):.0f}"
            if item.estimated_amount
            else None
        ),
        (f"계약방법: {facts.contract_method}" if facts.contract_method else None),
    ]


def _update_project_notice_identity(
    project: Project,
    *,
    item: KonepsCollectedItem,
) -> None:
    notice_number = item.notice_number
    normalized_notice_number = parsing.normalize_notice_number(notice_number)
    if normalized_notice_number and (
        not project.notice_number
        or parsing.normalize_notice_number(project.notice_number)
        == normalized_notice_number
    ):
        project.notice_number = normalized_notice_number
    source_url = item.source_url
    if source_url and (
        not project.source_url
        or matching.normalize_source_url(project.source_url)
        == matching.normalize_source_url(source_url)
    ):
        project.source_url = str(source_url).strip()


def _update_project_agencies(
    project: Project,
    *,
    facts: CrawlItemMetadataFacts,
) -> None:
    issuing_agency = facts.issuing_agency
    if issuing_agency and (
        not project.issuing_agency
        or parsing.normalize_agency_name(project.issuing_agency)
        == parsing.normalize_agency_name(issuing_agency)
    ):
        project.issuing_agency = str(issuing_agency).strip()
    demand_agency = facts.resolved_demand_agency()
    if demand_agency and (
        not project.demand_agency
        or parsing.normalize_agency_name(project.demand_agency)
        == parsing.normalize_agency_name(demand_agency)
    ):
        project.demand_agency = str(demand_agency).strip()


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
        category=request.category,
        execution_mode=request.execution_mode,
        max_items=request.max_items,
        status="running",
        result_count=0,
        celery_task_id=str(celery_task_id) if celery_task_id else None,
        release_sha=str(settings.APP_RELEASE_SHA or "").strip() or None,
        release_tag=str(settings.APP_RELEASE_TAG or "").strip() or None,
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
) -> CrawlJob:
    """Persist crawl history and any usable opening-result data.

    Project facts and ``semantic_input.changed`` outbox rows are committed
    atomically. No source embeds inline: the declared inference task consumes
    the durable event and later emits the existing ``embedding.ready`` event
    that drives the similarity projection.

    ``response`` 봉투는 celery/HTTP payload 형태를 유지하지만, 그 안의 ``items`` 는
    여기서 ``KonepsCollectedItem`` 으로 **승격**된다(방어적 DTO Phase 3의 검증 지점):
    수집 생산자는 이미 모델을 넘기므로 무비용 통과이고, 손으로 만든 dict payload
    (백필 스크립트/외부 호출부)는 이 지점에서 필수 필드가 검증된다.
    """
    items = _promote_items(response.get("items", []))
    metadata = response.get("metadata", {})
    metadata.pop("semantic_input_outbox_event_ids", None)

    _apply_crawl_response_summary(
        crawl_job,
        job_status=response.get("job_status", "completed"),
        collected_count=response.get("collected_count", len(items)),
        metadata=metadata,
    )

    inference_outbox = InferenceOutboxService()
    linked_project_ids: set[int] = set()
    semantic_input_outbox_event_ids: set[int] = set()
    persisted_count = 0

    for item in items:
        project, outbox_event_id = _persist_crawl_item(
            db,
            item=item,
            request=request,
            inference_outbox=inference_outbox,
            crawl_job_status=crawl_job.status,
        )
        if project is not None:
            linked_project_ids.add(int(project.id))
            persisted_count += 1
        if outbox_event_id is not None:
            semantic_input_outbox_event_ids.add(outbox_event_id)

    if len(linked_project_ids) == 1:
        crawl_job.project_id = next(iter(linked_project_ids))
    crawl_job.persisted_count = persisted_count
    response.setdefault("metadata", {})["persisted_count"] = persisted_count

    # Surface committed outbox ids so task callers can fast-dispatch the
    # inference processor after this method returns. The periodic sweep remains
    # the delivery guarantee if that best-effort enqueue fails.
    if semantic_input_outbox_event_ids:
        response.setdefault("metadata", {})[
            "semantic_input_outbox_event_ids"
        ] = sorted(semantic_input_outbox_event_ids)

    return _commit_and_publish_crawl_job(
        db, crawl_job, event_name=_crawl_event_name(crawl_job.status)
    )


def _promote_items(raw_items: Any) -> list[KonepsCollectedItem]:
    """수집 payload 의 items 를 타입 있는 DTO 리스트로 승격한다.

    이미 DTO 면 그대로 통과(재검증 없음)하고, dict 면 ``model_validate`` 로 구조 계약을
    강제한다. 필수 필드(공고번호/제목/기초금액) 결손은 여기서 ``ValidationError`` 로
    거부한다 — 영속화는 수집 루프와 달리 best-effort 가 아니고, 결손 item 을 통과시키면
    ORM 대입 단계에서 조용히 기본값이 저장되기 때문이다.
    """
    return [
        item
        if isinstance(item, KonepsCollectedItem)
        else KonepsCollectedItem.model_validate(item)
        for item in raw_items or []
    ]


def _apply_crawl_response_summary(
    crawl_job: CrawlJob,
    *,
    job_status: str,
    collected_count: int,
    metadata: dict[str, Any],
) -> None:
    crawl_job.status = job_status
    crawl_job.result_count = collected_count
    crawl_job.received_count = int(metadata.get("received_count") or 0)
    crawl_job.normalized_count = int(
        metadata.get("normalized_count") or collected_count or 0
    )
    crawl_job.duplicate_count = int(metadata.get("duplicate_count") or 0)
    crawl_job.dropped_count = int(metadata.get("dropped_count") or 0)
    crawl_job.source_total_count = (
        int(metadata["source_total_count"])
        if metadata.get("source_total_count") is not None
        else None
    )
    crawl_job.pages_fetched = (
        int(metadata["pages_fetched"])
        if metadata.get("pages_fetched") is not None
        else None
    )
    crawl_job.truncated = bool(metadata.get("truncated"))
    # 수집원이 실효 상한을 신고하면(scsbid sweep 은 요청이 아니라 설정·예산에서 얻는다)
    # 요청값 대신 그 값을 기록한다. 신고 없는 수집원은 생성 시점 요청값을 유지한다.
    if metadata.get("item_cap") is not None:
        crawl_job.max_items = int(metadata["item_cap"])
    crawl_job.drop_reasons = dict(metadata.get("drop_reasons") or {})
    crawl_job.error_message = parsing.format_crawl_error_message(metadata)
    crawl_job.completed_at = utc_now()


def _persist_crawl_item(
    db: Session,
    *,
    item: KonepsCollectedItem,
    request: CrawlRequest,
    inference_outbox: InferenceOutboxService,
    crawl_job_status: str,
) -> tuple[Project | None, int | None]:
    _lock_notice_identity(db, item.notice_number)
    facts = item.opening_facts()
    historical_record = _resolve_historical_record(
        db, notice_number=item.notice_number
    )
    project, semantic_input_changed = resolve_project_for_item(
        db,
        item=item,
        request=request,
        historical_record=historical_record,
    )
    if project is not None:
        historical_record.project_id = project.id
    _update_historical_record_from_item(
        historical_record,
        item=item,
        request=request,
        facts=facts,
        # 공고 추정가격의 출처(백필과 같은 입력) — base_provenance 참조.
        project=project,
    )
    _persist_tender_result_for_item(
        db,
        project=project,
        historical_record=historical_record,
        facts=facts,
        crawl_job_status=crawl_job_status,
    )
    if project is None:
        return project, None
    event = inference_outbox.ensure_semantic_input_changed_event(
        db,
        project,
        semantic_input_changed=semantic_input_changed,
    )
    if event is None:
        return project, None
    return project, int(event.id)


def _lock_notice_identity(db: Session, notice_number: str | None) -> None:
    """Serialize one canonical notice upsert inside the crawl transaction."""
    canonical = parsing.normalize_notice_number(notice_number)
    if not canonical or db.get_bind().dialect.name != "postgresql":
        return
    db.execute(
        text("SELECT pg_advisory_xact_lock(:lock_id)"),
        {"lock_id": singleton_lock_id(f"project_notice:{canonical}")},
    )


def _resolve_historical_record(
    db: Session,
    *,
    notice_number: str | None,
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
    item: KonepsCollectedItem,
    request: CrawlRequest,
    facts: CrawlItemMetadataFacts | None = None,
    project: Project | None = None,
) -> None:
    """Apply one collected item onto its ``HistoricalData`` row.

    ``facts`` 는 재사용을 위한 주입 지점이다(persist 루프는 item 당 한 번만 투영해
    넘긴다). 생략하면 ``item`` 에서 직접 투영한다.

    ``project`` 는 provenance 분류에 필요한 공고 추정가격의 출처다. 생략하면 비율 규칙이
    적용되지 않아 판정이 종전과 동일하다.
    """
    resolved_facts = facts if facts is not None else item.opening_facts()
    historical_record.agency_name = resolved_facts.resolved_agency_name()
    historical_record.category = matching.resolve_project_category(item, request)
    _update_historical_base_fields(
        historical_record, item=item, facts=resolved_facts, project=project
    )
    historical_record.bid_rate = (
        parsing.normalize_bid_rate_value(
            resolved_facts.bid_rate or resolved_facts.winning_rate
        )
        or 0.0
    )
    _update_historical_reserve_fields(historical_record, facts=resolved_facts)
    historical_record.opened_at = parsing.coerce_datetime(
        resolved_facts.opening_announced_at or resolved_facts.opening_scheduled_at
    )


def _update_historical_base_fields(
    historical_record: HistoricalData,
    *,
    item: KonepsCollectedItem,
    facts: CrawlItemMetadataFacts,
    project: Project | None = None,
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

    ``project`` supplies the notice 추정가격 for the provenance ratio rule; the time
    source stays here so one seam freezes it (see ``base_provenance``).
    """
    incoming_base = parsing.coerce_amount(item.base_amount)
    if incoming_base is not None and incoming_base > 0:
        historical_record.base_amount = float(incoming_base)

    incoming_estimated = parsing.coerce_amount(item.estimated_amount)
    if incoming_estimated is not None and incoming_estimated > 0:
        historical_record.predicted_price = float(incoming_estimated)
    elif incoming_base is not None and incoming_base > 0:
        historical_record.predicted_price = float(incoming_base)

    recovered = parsing.coerce_amount(facts.base_amount_estimated)
    if recovered is not None and recovered > 0:
        historical_record.base_amount_estimated = float(recovered)

    base_provenance.tag_base_provenance(
        historical_record, facts=facts, project=project, stamp=utc_now()
    )


def _update_historical_reserve_fields(
    historical_record: HistoricalData,
    *,
    facts: CrawlItemMetadataFacts,
) -> None:
    incoming_reserve_prices = facts.reserve_prices or []
    if incoming_reserve_prices or not scsbid.has_persisted_reserve_prices(
        historical_record
    ):
        historical_record.reserve_prices = json.dumps(
            incoming_reserve_prices,
            ensure_ascii=False,
        )
    incoming_selected_numbers = facts.selected_numbers or []
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
    facts: CrawlItemMetadataFacts,
    crawl_job_status: str,
) -> None:
    if not facts.has_award_signal():
        return
    # ``resolve_tender_result`` 는 반환 직전 ``tender_result.project_id = project_id`` 를
    # **무조건** 대입한다. 그래서 반환값의 project_id 가 None 인 경우는 여기서 넘긴
    # project_id 가 None 인 경우뿐이고, 그건 project 도 None 이고
    # ``historical_record.project_id`` 도 None 일 때만 성립한다(아래 인자식 참조). 즉
    # "반환 project_id 는 None 인데 historical 에는 값이 있다"는 상태는 상호 배타라
    # 도달 불가다 — 과거 여기 있던 백필 분기는 죽은 코드였다(동등 뮤턴트로 증명).
    # 링크 보정이 필요한 지점은 이 뒤가 아니라 project_id 인자 그 자체다.
    resolve_tender_result(
        db,
        project_id=project.id if project is not None else historical_record.project_id,
        facts=facts,
        crawl_job_status=crawl_job_status,
    )


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
