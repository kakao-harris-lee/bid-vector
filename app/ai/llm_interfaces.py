"""Abstract contracts for token-conscious LLM request pipelines.

This module intentionally contains interface and payload definitions only. It is
meant to let future implementations slim prompt context, wrap request execution
with token guards, and centralize executor construction without wiring those
implementations into the current application flow.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence


@dataclass(frozen=True)
class TokenBudget:
    """Token limits applied before and after an LLM request."""

    context_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None


@dataclass(frozen=True)
class TokenUsage:
    """Observed token usage for one request/response cycle."""

    context_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0


@dataclass(frozen=True)
class ContextFragment:
    """A candidate context slice that can be selected, compacted, or dropped."""

    key: str
    content: str
    priority: int = 0
    estimated_tokens: int | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ContextRequest:
    """Input for building a minimal context bundle."""

    purpose: str
    inputs: Mapping[str, Any] = field(default_factory=dict)
    budget: TokenBudget | None = None
    required_keys: tuple[str, ...] = ()


@dataclass(frozen=True)
class ContextBundle:
    """Selected context returned by a context provider."""

    fragments: tuple[ContextFragment, ...]
    rendered: str
    estimated_tokens: int | None = None
    dropped_keys: tuple[str, ...] = ()


class TokenEstimator(ABC):
    """Estimate token cost without binding callers to a tokenizer vendor."""

    @abstractmethod
    def estimate(self, text: str) -> int:
        """Return an estimated token count for the given text."""


class ContextProvider(ABC):
    """Build a slim prompt context for a specific request purpose."""

    @abstractmethod
    def build_context(self, request: ContextRequest) -> ContextBundle:
        """Return the smallest sufficient context bundle for the request."""


class ContextSelector(ABC):
    """Choose which context fragments survive a token budget."""

    @abstractmethod
    def select(
        self,
        request: ContextRequest,
        fragments: Sequence[ContextFragment],
    ) -> ContextBundle:
        """Select and render fragments for the current request."""


@dataclass(frozen=True)
class LLMRequest:
    """Normalized request payload passed through decorators and executors."""

    operation: str
    prompt: str
    context: ContextBundle | None = None
    model: str | None = None
    budget: TokenBudget | None = None
    idempotency_key: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class LLMResponse:
    """Normalized response payload returned by an executor."""

    text: str
    usage: TokenUsage = field(default_factory=TokenUsage)
    model: str | None = None
    raw: Mapping[str, Any] = field(default_factory=dict)


class RequestExecutor(ABC):
    """Execute one normalized LLM request."""

    @abstractmethod
    def execute(self, request: LLMRequest) -> LLMResponse:
        """Return the model response for a normalized request."""


class RequestExecutorDecorator(RequestExecutor, ABC):
    """Base contract for decorators that wrap request execution."""

    @property
    @abstractmethod
    def wrapped(self) -> RequestExecutor:
        """Return the executor being decorated."""


class DuplicateRequestGuard(ABC):
    """Detect and record duplicate requests before execution."""

    @abstractmethod
    def fingerprint(self, request: LLMRequest) -> str:
        """Return a stable fingerprint for duplicate detection."""

    @abstractmethod
    def has_seen(self, fingerprint: str) -> bool:
        """Return whether the fingerprint has already been executed."""

    @abstractmethod
    def remember(self, fingerprint: str, response: LLMResponse | None = None) -> None:
        """Record a request fingerprint and optional response."""


class OutputTokenGuard(ABC):
    """Validate or trim responses that exceed the allowed output budget."""

    @abstractmethod
    def enforce(self, request: LLMRequest, response: LLMResponse) -> LLMResponse:
        """Return a response that satisfies the request output budget."""


@dataclass(frozen=True)
class ExecutorFactoryRequest:
    """Factory input for selecting provider, model, and decorators."""

    provider: str
    model: str | None = None
    decorators: tuple[str, ...] = ()
    budget: TokenBudget | None = None
    options: Mapping[str, Any] = field(default_factory=dict)


class RequestExecutorFactory(ABC):
    """Create request executors without exposing provider branching to callers."""

    @abstractmethod
    def create_executor(self, request: ExecutorFactoryRequest) -> RequestExecutor:
        """Return a configured executor for the requested provider and guards."""
