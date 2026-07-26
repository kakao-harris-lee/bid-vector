"""Shared helpers for the single-operator API surface.

Pure move (#295 pattern): these helpers were factored out of
``app/api/operator.py`` so the router file stays thin. They carry no behaviour
change — the router delegates to them and to the per-domain impl modules.
"""

from app.models.models import User

# synthetic-* accounts are validation profiles that must never pollute the
# canonical operator's answer set (CLAUDE.md §8). Single source reused by the
# account switcher serializer and the eligibility-feedback labeler guard.
_SYNTHETIC_USERNAME_PREFIX = "synthetic-"


def _append_operator_query(path: str, operator_id: int) -> str:
    separator = "&" if "?" in path else "?"
    return f"{path}{separator}operator_id={int(operator_id)}"


def _operator_context_fields(target: User) -> dict:
    """Return the standard ``current_operator_*`` envelope fields.

    Convention (from PR #70): ``current_operator_*`` reflects the **target**
    operator the response is scoped to — i.e. the company the frontend
    switcher should highlight as active — not the bearer-token owner.
    """
    return {
        "current_operator_id": int(target.id),
        "current_operator_username": str(target.username or ""),
    }


def _is_profile_configured(
    license_codes: list[str],
    region_codes: list[str],
    annual_revenue: float,
    capacity_score: float,
    total_awards: int,
    construction_capacity_amount: float = 0.0,
    awarded_contract_limit: float = 0.0,
) -> bool:
    return any([
        bool(license_codes),
        bool(region_codes),
        annual_revenue > 0,
        capacity_score > 0,
        total_awards > 0,
        construction_capacity_amount > 0,
        awarded_contract_limit > 0,
    ])


def _feedback_status(error_rate: float | None) -> str:
    """Map feedback error rate to a dashboard card status."""
    if error_rate is None:
        return "info"
    if error_rate <= 0.03:
        return "healthy"
    if error_rate <= 0.08:
        return "watch"
    return "critical"
