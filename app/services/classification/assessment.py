"""Shared result contract for a single rule-based classification axis."""

from dataclasses import dataclass


@dataclass
class RuleAssessment:
    """Result of a single rule-based classification check."""

    score: float
    passed: bool
    reasons: list[str]
    penalty: float = 0.0
