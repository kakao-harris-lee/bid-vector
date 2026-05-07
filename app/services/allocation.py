"""Fair allocation service skeleton."""
from app.schemas.schemas import AllocationCandidate


class AllocationService:
    """Assign bid opportunities fairly among candidate companies."""

    def select_candidate(self, candidates: list[AllocationCandidate]) -> AllocationCandidate:
        """Choose the lowest-award, highest-weight candidate."""
        if not candidates:
            raise ValueError("At least one allocation candidate is required")

        return sorted(
            candidates,
            key=lambda item: (item.total_awards, -item.weight, item.user_id),
        )[0]
