"""Bounded result accumulator for inference outbox sweeps."""

from __future__ import annotations

from dataclasses import dataclass, field

from app.schemas.similarity_runtime import (
    InferenceOutboxFailure,
    InferenceOutboxProcessedEvent,
    InferenceOutboxProcessResult,
    SimilarityProjectionResult,
)


@dataclass
class InferenceOutboxResultAccumulator:
    sample_limit: int
    processed_count: int = 0
    processed_event_id_first: int | None = None
    processed_event_id_last: int | None = None
    samples: list[InferenceOutboxProcessedEvent] = field(default_factory=list)

    def add(self, event_id: int, result: SimilarityProjectionResult) -> None:
        self.processed_count += 1
        if self.processed_event_id_first is None:
            self.processed_event_id_first = event_id
        self.processed_event_id_last = event_id
        if len(self.samples) < max(0, self.sample_limit):
            self.samples.append(
                InferenceOutboxProcessedEvent(event_id=event_id, result=result)
            )

    def build(
        self,
        *,
        failures: list[InferenceOutboxFailure],
        skipped_count: int,
        recovered_count: int,
    ) -> InferenceOutboxProcessResult:
        return InferenceOutboxProcessResult(
            processed_count=self.processed_count,
            failed_count=len(failures),
            skipped_count=skipped_count,
            recovered_count=recovered_count,
            processed_event_id_first=self.processed_event_id_first,
            processed_event_id_last=self.processed_event_id_last,
            result_sample_count=len(self.samples),
            result_sample_truncated=self.processed_count > len(self.samples),
            event_ids=[item.event_id for item in self.samples],
            failed_event_ids=[item.event_id for item in failures],
            results=self.samples,
            failures=failures,
        )
