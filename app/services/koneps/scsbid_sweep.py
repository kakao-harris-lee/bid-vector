"""Per-run data holders for the ScsbidInfoService award sweep.

The sweep's immutable configuration and its mutable accumulators are plain data
with no IO, so they live next to the pure helpers that read them (``scsbid``)
and the projection that renders them (``collection_accounting``) instead of in
the collector, whose file owns HTTP/DB orchestration. Keeping them here also
stops the orchestrator file from growing every time the sweep gains a counter.

To avoid an import cycle this module must never import ``collector``: the
collector imports these shapes, not the other way around.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app.schemas.koneps_items import KonepsCollectedItem


@dataclass(frozen=True)
class ScsbidSweepConfig:
    """Immutable per-run configuration for a ScsbidInfoService award sweep.

    Bundles the values resolved once in ``_collect_scsbid_openapi_items``'s setup
    so the extracted sweep/page/item helpers receive an explicit, read-only
    config instead of closing over a dozen locals. Pure data; no behaviour.
    """

    service_key: str
    page_size: int
    max_pages: int
    max_items: int
    delay_seconds: float
    begin_token: str
    end_token: str
    collect_reserve_detail: bool
    defer_reserve_detail: bool
    inline_reserve_detail_max_fetches: int
    already_have_reserve: frozenset[str]
    reserve_detail_age_cutoff: datetime | None
    checked_recently: frozenset[str]


@dataclass
class ScsbidSweepState:
    """Mutable accumulators shared across the categories of one award sweep.

    Holds exactly the running collections, counters, and last-seen header fields
    that ``_collect_scsbid_openapi_items`` previously kept as method locals. The
    extracted helpers mutate this in place so dedup sets, counters, and ordering
    are preserved bit-for-bit (no copying, no re-ordering).
    """

    parsed_items: list[KonepsCollectedItem] = field(default_factory=list)
    seen_notice_numbers: set[str] = field(default_factory=set)
    deferred_reserve_detail: list[dict[str, str]] = field(default_factory=list)
    deferred_reserve_seen: set[tuple[str, str]] = field(default_factory=set)
    reserve_detail_count: int = 0
    reserve_detail_error_count: int = 0
    reserve_detail_reused_count: int = 0
    reserve_detail_deferred_count: int = 0
    reserve_detail_backoff_skipped_count: int = 0
    reserve_detail_recheck_skipped_count: int = 0
    reserve_detail_inline_fetch_count: int = 0
    reserve_detail_inline_cap_skipped_count: int = 0
    api_call_count: int = 0
    key_variant: str = ""
    last_result_code: str = ""
    last_result_message: str = ""
    category_metadata: list[dict[str, Any]] = field(default_factory=list)
    received_count: int = 0
    missing_notice_count: int = 0
    parse_drop_count: int = 0
    duplicate_count: int = 0
    cap_skipped_count: int = 0
    truncated_by_max_items: bool = False
