"""DB read / resolve helpers for the KONEPS collector.

These functions were extracted verbatim from ``KonepsCollectorService``
(``collector.py``) as part of the incremental God-module decomposition
(Phase C1, DB read/resolve layer). They take an explicit ``db: Session`` and
carry no instance state, so they live here as module-level functions rather
than methods. They perform DB reads and in-memory ORM mutations (including the
pre-existing ``db.add`` / ``db.flush`` staging in the upsert helpers) but never
``db.commit`` -- the transaction boundary stays with the caller
(``persist_crawl_results``, extracted in a later step).

Behavior is intentionally identical to the original methods; this module is a
pure relocation, not a rewrite. To avoid an import cycle, this module must
never import ``collector``: the collector imports ``persistence`` (and the
sibling ``matching`` / ``parsing`` modules), not the other way around.
"""

from typing import Any

from sqlalchemy.orm import Session

from app.models.models import HistoricalData, Project, TenderResult
from app.schemas.schemas import CrawlRequest
from app.services.koneps import matching, parsing
from app.services.project_similarity import ProjectSimilarityService


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
    description_lines = [
        (f"공고번호: {item.get('notice_number')}" if item.get("notice_number") else None),
        (
            f"공고기관: {item_metadata.get('issuing_agency')}"
            if item_metadata.get("issuing_agency")
            else None
        ),
        (
            f"수요기관: {item_metadata.get('opening_demand_agency') or item_metadata.get('demand_agency')}"
            if item_metadata.get("opening_demand_agency")
            or item_metadata.get("demand_agency")
            else None
        ),
        f"공고원문: {item.get('source_url')}" if item.get("source_url") else None,
        (f"업무구분: {item.get('business_type')}" if item.get("business_type") else None),
        (
            f"개찰상태: {item_metadata.get('opening_status')}"
            if item_metadata.get("opening_status")
            else None
        ),
    ]
    requirement_lines = [
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

    if item.get("title") and parsing.should_replace_project_title(
        project.title, item.get("title")
    ):
        project.title = str(item.get("title")).strip()
    notice_number = item.get("notice_number")
    # Persist notice_number in canonical (normalized) form so the indexed
    # ``notice_number.in_(...)`` fast path in ``find_matching_project`` can
    # rely on equality. Storing a non-canonical value (lower case / inner
    # whitespace) would make the index probe miss and create duplicates.
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

    db_title = project.title or item.get("notice_number") or "KONEPS notice"
    project.title = db_title.strip()


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
        tender_result = TenderResult(project_id=project_id)
        db.add(tender_result)

    tender_result.project_id = project_id
    tender_result.winning_company = winning_company
    tender_result.winning_amount = winning_amount
    tender_result.winning_rate = winning_rate
    tender_result.result_status = result_status
    tender_result.announced_at = announced_at
    return tender_result
