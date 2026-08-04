"""Source-neutral collection accounting projections."""

from collections.abc import Sequence

from app.schemas.koneps_items import KonepsCollectedItem


def ensure_collection_accounting(
    metadata,
    items: Sequence[KonepsCollectedItem],
) -> None:
    """Fill standard counters when a source did not provide richer accounting."""
    metadata.setdefault("received_count", len(items))
    metadata.setdefault("normalized_count", len(items))
    metadata.setdefault("duplicate_count", 0)
    metadata.setdefault("dropped_count", 0)
    metadata.setdefault("drop_reasons", {})
    metadata.setdefault("source_total_count", metadata.get("openapi_total_count"))
    metadata.setdefault("pages_fetched", metadata.get("openapi_pages_fetched"))
    source_total = metadata.get("source_total_count")
    metadata.setdefault(
        "truncated",
        bool(
            source_total is not None
            and int(source_total or 0) > int(metadata["received_count"])
        ),
    )


def scsbid_accounting(state, config) -> dict[
    str, int | bool | dict[str, int]
]:
    categories = state.category_metadata
    source_total = sum(int(row["total_count"] or 0) for row in categories)
    return {
        "received_count": state.received_count,
        "normalized_count": len(state.parsed_items),
        "duplicate_count": state.duplicate_count,
        "dropped_count": (
            state.missing_notice_count
            + state.parse_drop_count
            + state.duplicate_count
            + state.cap_skipped_count
        ),
        "drop_reasons": {
            "missing_notice_number": state.missing_notice_count,
            "parse_rejected": state.parse_drop_count,
            "duplicate_notice": state.duplicate_count,
            "max_items_cap": state.cap_skipped_count,
        },
        "source_total_count": source_total,
        "pages_fetched": sum(
            int(row["pages_fetched"] or 0) for row in categories
        ),
        "truncated": state.truncated_by_max_items
        or any(
            int(row["total_count"] or 0)
            > int(row["pages_fetched"] or 0) * config.page_size
            for row in categories
        ),
    }


def scsbid_cap_reached(
    state, config, *, skipped_count: int = 0, source_has_more: bool = False
) -> bool:
    """Record max-items truncation once the normalized item cap is reached."""
    if len(state.parsed_items) < config.max_items:
        return False
    state.cap_skipped_count += max(0, int(skipped_count))
    state.truncated_by_max_items = bool(
        state.truncated_by_max_items or skipped_count or source_has_more
    )
    return True
