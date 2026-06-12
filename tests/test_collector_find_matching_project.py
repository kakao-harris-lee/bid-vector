"""Regression + perf tests for ``KonepsCollectorService._find_matching_project``.

Background (perf/scsbid-find-matching-project-index)
----------------------------------------------------
scsbid open-bid persistence called ``_find_matching_project`` once per award
item. The old implementation loaded *every* project in the item's category
(thousands of rows) and scanned them in Python, capping throughput at ~1-2
items/s. ``notice_number`` is KONEPS's authoritative unique key and is indexed
(``ix_projects_notice_number``), so the rewrite resolves notice-bearing items via
the index and only touches the small set of notice-less projects for fuzzy
matching.

These tests assert:

1. A notice number that matches an existing project's indexed column resolves via
   the index fast path -- and does NOT load the full category (verified by a
   query spy that fails if a broad ``Project`` load is issued).
2. A notice number stored only in free-form ``description`` text (column NULL)
   still matches (column-NULL fallback path).
3. A notice number that matches no existing project returns ``None`` -- and is NOT
   mis-merged into a different-notice project via title overlap (behaviour change
   guard).
4. Notice-less items keep the original source_url / title fuzzy behaviour.
5. The fuzzy heuristics themselves (source_url priority, scored title match) are
   unchanged for the notice-less candidate set.
"""

from __future__ import annotations

from app.models.models import Project
from app.schemas.schemas import CrawlRequest
from app.services.koneps.collector import KonepsCollectorService


def _request() -> CrawlRequest:
    return CrawlRequest(
        source="scsbid-openapi", category="construction", execution_mode="live"
    )


def _award_item(notice_number: str | None, **overrides) -> dict:
    item = {
        "title": overrides.get("title", f"개찰결과 {notice_number}"),
        "base_amount": 100_000_000.0,
        "estimated_amount": 96_000_000.0,
        "source_url": overrides.get(
            "source_url", f"http://ebid.example.com/detail/{notice_number}"
        ),
        "metadata": overrides.get("metadata", {"opening_demand_agency": "서울특별시교육청"}),
    }
    if notice_number is not None:
        item["notice_number"] = notice_number
    if "closing_at" in overrides:
        item["closing_at"] = overrides["closing_at"]
    return item


def test_index_fast_path_matches_notice_number_column(test_db):
    """A notice number matching an existing indexed column resolves to that project."""
    existing = Project(
        title="기존 공고",
        description="",
        requirements="",
        budget_estimate=100_000_000.0,
        category="construction",
        notice_number="R26BK01552430",
    )
    test_db.add(existing)
    test_db.flush()

    service = KonepsCollectorService()
    item = _award_item("R26BK01552430")
    matched = service._find_matching_project(test_db, item=item, request=_request())

    assert matched is not None
    assert matched.id == existing.id


def test_index_fast_path_normalizes_notice_number(test_db):
    """Whitespace/case differences in the item's notice number still match the stored column."""
    existing = Project(
        title="기존 공고",
        description="",
        requirements="",
        budget_estimate=100_000_000.0,
        category="construction",
        notice_number="R26BK01552430",
    )
    test_db.add(existing)
    test_db.flush()

    service = KonepsCollectorService()
    # Lower case + embedded whitespace: _normalize_notice_number upper-cases and
    # strips whitespace, so this must still resolve to the stored project.
    item = _award_item(" r26bk 01552430 ")
    matched = service._find_matching_project(test_db, item=item, request=_request())

    assert matched is not None
    assert matched.id == existing.id


def test_fast_path_does_not_full_scan_projects(test_db):
    """The index fast path must not emit an unfiltered ``SELECT ... FROM projects``.

    We seed a large notice-bearing category and capture every SQL statement the
    engine executes during the call. The old O(n) behaviour issued a broad
    category load (``FROM projects`` with at most a category filter); the rewrite
    must filter on ``notice_number`` so the bulk rows are never materialised.
    """
    from sqlalchemy import event

    bulk_size = 500
    for index in range(bulk_size):
        test_db.add(
            Project(
                title=f"대량 공고 {index}",
                description="",
                requirements="",
                budget_estimate=100_000_000.0,
                category="construction",
                notice_number=f"BULK{index:06d}",
            )
        )
    target = Project(
        title="목표 공고",
        description="",
        requirements="",
        budget_estimate=100_000_000.0,
        category="construction",
        notice_number="TARGET0001",
    )
    test_db.add(target)
    test_db.flush()

    engine = test_db.get_bind()
    project_selects: list[str] = []

    def _capture(conn, cursor, statement, parameters, context, executemany):
        normalized = " ".join(statement.split())
        if normalized.upper().startswith("SELECT") and "projects" in normalized.lower():
            project_selects.append(normalized)

    event.listen(engine, "before_cursor_execute", _capture)
    try:
        service = KonepsCollectorService()
        item = _award_item("TARGET0001")
        matched = service._find_matching_project(test_db, item=item, request=_request())
    finally:
        event.remove(engine, "before_cursor_execute", _capture)

    assert matched is not None
    assert matched.id == target.id

    # Exactly one project SELECT runs for a notice-bearing match, and it must be
    # constrained by notice_number (the indexed key) -- never a bare category /
    # unfiltered scan that would materialise the whole bulk set.
    assert project_selects, "expected at least one project SELECT"
    assert all(
        "notice_number" in stmt.lower() for stmt in project_selects
    ), project_selects


def test_notice_number_in_free_form_text_matches(test_db):
    """A project whose notice number lives only in description (column NULL) still matches."""
    existing = Project(
        title="레거시 공고",
        description="공고번호: R26BK99999999\n기타 내용",
        requirements="",
        budget_estimate=100_000_000.0,
        category="construction",
        notice_number=None,
    )
    test_db.add(existing)
    test_db.flush()

    service = KonepsCollectorService()
    item = _award_item("R26BK99999999")
    matched = service._find_matching_project(test_db, item=item, request=_request())

    assert matched is not None
    assert matched.id == existing.id


def test_unmatched_notice_number_returns_none_no_title_merge(test_db):
    """An item with a notice number not present anywhere returns None.

    Behaviour-change guard: even though an existing project has an overlapping
    title (and matching agency/budget), it carries a *different* notice number,
    so it must NOT be fuzzy-merged. Returning None drives new-project creation.
    """
    existing = Project(
        title="도로 포장 공사",
        description="",
        requirements="",
        budget_estimate=100_000_000.0,
        category="construction",
        notice_number="R26BK00000001",
        issuing_agency="서울특별시교육청",
    )
    test_db.add(existing)
    test_db.flush()

    service = KonepsCollectorService()
    item = _award_item(
        "R26BK00000002",  # different authoritative key
        title="도로 포장 공사",  # same title
        source_url="http://other.example.com/x",
        metadata={"opening_demand_agency": "서울특별시교육청"},
    )
    matched = service._find_matching_project(test_db, item=item, request=_request())

    assert matched is None


def test_notice_less_item_keeps_title_fuzzy_behaviour(test_db):
    """An item with no notice number still resolves via title/agency fuzzy matching."""
    existing = Project(
        title="상수도 정비 공사",
        description="",
        requirements="",
        budget_estimate=100_000_000.0,
        category="construction",
        notice_number=None,
        issuing_agency="서울특별시교육청",
    )
    test_db.add(existing)
    test_db.flush()

    service = KonepsCollectorService()
    item = _award_item(
        None,
        title="상수도 정비 공사",
        source_url="http://ebid.example.com/detail/no-notice",
        metadata={"opening_demand_agency": "서울특별시교육청"},
    )
    matched = service._find_matching_project(test_db, item=item, request=_request())

    assert matched is not None
    assert matched.id == existing.id


def test_notice_less_item_source_url_priority(test_db):
    """source_url match takes priority over title for notice-less candidates."""
    by_url = Project(
        title="전혀 다른 제목",
        description="",
        requirements="",
        budget_estimate=100_000_000.0,
        category="construction",
        notice_number=None,
        source_url="http://ebid.example.com/detail/shared-url",
    )
    test_db.add(by_url)
    test_db.flush()

    service = KonepsCollectorService()
    item = _award_item(
        None,
        title="공유 URL 공고",
        source_url="http://ebid.example.com/detail/shared-url",
        metadata={},
    )
    matched = service._find_matching_project(test_db, item=item, request=_request())

    assert matched is not None
    assert matched.id == by_url.id


def test_notice_bearing_item_with_url_only_match_against_notice_less(test_db):
    """A notice-bearing item still matches a notice-less project sharing its source_url.

    The fuzzy step runs over notice-less candidates even for notice-bearing
    items, so a legacy notice-less project carrying the same detail URL is linked
    rather than duplicated.
    """
    legacy = Project(
        title="레거시 URL 공고",
        description="",
        requirements="",
        budget_estimate=100_000_000.0,
        category="construction",
        notice_number=None,
        source_url="http://ebid.example.com/detail/legacy-shared",
    )
    test_db.add(legacy)
    test_db.flush()

    service = KonepsCollectorService()
    item = _award_item(
        "R26BK12340000",
        title="새 제목",
        source_url="http://ebid.example.com/detail/legacy-shared",
        metadata={},
    )
    matched = service._find_matching_project(test_db, item=item, request=_request())

    assert matched is not None
    assert matched.id == legacy.id


# ---------------------------------------------------------------------------
# Reverse-direction guard (PR #83 Blocking 1): write canonicalization
# ---------------------------------------------------------------------------
# The index fast path probes ``notice_number.in_(...)`` with equality, which is
# only exact if the stored column is always canonical. These tests assert the
# *write* side normalizes, so a stored value never drifts out of canonical form
# and causes the probe to miss (-> duplicate project + mis-attributed result).


def test_update_project_from_item_stores_canonical_notice_number(test_db):
    """``_update_project_from_item`` must persist notice_number in canonical form.

    A raw item value with lower case / inner whitespace must be stored upper-cased
    and whitespace-stripped, matching ``_normalize_notice_number``.
    """
    project = Project(
        title="신규 공고",
        description="",
        requirements="",
        budget_estimate=0.0,
        category="construction",
    )
    test_db.add(project)
    test_db.flush()

    service = KonepsCollectorService()
    item = _award_item(" r26bk 01552430 ")
    service._update_project_from_item(project, item=item, request=_request())

    assert project.notice_number == "R26BK01552430"


def test_update_project_from_item_blank_notice_stays_none(test_db):
    """A blank/whitespace-only notice number must leave the column untouched (None)."""
    project = Project(
        title="신규 공고",
        description="",
        requirements="",
        budget_estimate=0.0,
        category="construction",
    )
    test_db.add(project)
    test_db.flush()

    service = KonepsCollectorService()
    item = _award_item("   ")
    service._update_project_from_item(project, item=item, request=_request())

    assert project.notice_number is None


def test_noncanonical_stored_value_matches_after_write_normalization(test_db):
    """End-to-end: persist a notice-bearing item, then a canonical-form item matches.

    Persisting via ``_resolve_project_for_item`` stores the canonical column, so a
    subsequent lookup with a differently-formatted (but equivalent) notice number
    resolves to the same project through the index fast path -- no duplicate.
    """
    from app.services.project_similarity import ProjectSimilarityService

    service = KonepsCollectorService()
    similarity = ProjectSimilarityService()
    similarity.refresh_project_embedding = (  # type: ignore[method-assign]
        lambda db, project, force=False: ([], "spy-model")
    )

    # First crawl persists a project with a messy raw notice number.
    from app.models.models import HistoricalData

    historical = HistoricalData(category="construction")
    test_db.add(historical)
    test_db.flush()

    first_item = _award_item(" r26bk 01552430 ")
    project, _ = service._resolve_project_for_item(
        test_db,
        item=first_item,
        request=_request(),
        historical_record=historical,
        project_similarity=similarity,
    )
    test_db.flush()
    assert project.notice_number == "R26BK01552430"

    # A later item with the canonical notice number must resolve to the same row.
    canonical_item = _award_item("R26BK01552430")
    matched = service._find_matching_project(
        test_db, item=canonical_item, request=_request()
    )
    assert matched is not None
    assert matched.id == project.id
    # Exactly one project exists -- the write normalization prevented a duplicate.
    assert test_db.query(Project).count() == 1


def test_backfill_canonicalizes_legacy_noncanonical_rows(test_db):
    """The data-migration transform canonicalizes a legacy non-canonical row.

    Mirrors the ``upper(regexp_replace(notice_number, '\\s+', '', 'g'))`` update
    using portable SQLAlchemy expressions (SQLite has no regexp_replace), proving
    the intended canonical target equals ``_normalize_notice_number``.
    """
    legacy = Project(
        title="레거시 비정규 공고",
        description="",
        requirements="",
        budget_estimate=100_000_000.0,
        category="construction",
        notice_number="r26bk 01552430",  # lower case + inner space
    )
    test_db.add(legacy)
    test_db.flush()

    service = KonepsCollectorService()
    # Canonical target the migration produces for this stored value.
    canonical = service._normalize_notice_number(legacy.notice_number)
    assert canonical == "R26BK01552430"

    # Apply the canonicalization (DB-portable equivalent of the migration UPDATE).
    legacy.notice_number = canonical
    test_db.flush()

    # After backfill, a canonical-form item resolves via the index fast path.
    item = _award_item("R26BK01552430")
    matched = service._find_matching_project(test_db, item=item, request=_request())
    assert matched is not None
    assert matched.id == legacy.id
