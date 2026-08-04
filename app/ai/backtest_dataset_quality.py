"""백테스트 홀드아웃 데이터셋 품질 판정 — 선언 규칙 테이블(순수).

배경
----
``scripts/backtest_price_predictors.py`` 가 남기는 리포트의 ``dataset_quality.status``
는 ML release promotion gate 의 한 축이다(:mod:`app.services.ml_release.gate` 의
``_dataset_quality_gate_reason``). 그 값이 **측정 없이 상수** ``"warning"`` 으로
기록되던 동안 이 축은 구조적으로 실패할 수 없었다: standard 프리셋의 최소 통과값이
정확히 ``warning`` 이라, 홀드아웃이 비어 있어도 게이트를 통과했다.

이 모듈은 status 를 백테스트가 **실제로 아는 신호**에서 도출한다:

1. 표본 깊이 — 학습 prefix + 홀드아웃을 채울 행이 있는가(요구치는 호출부가 주입)
2. ``base_amount`` provenance — 오차 분모가 clean 기초금액인가(#199, CLEAN 만 신뢰)
3. 낙찰률 스케일 — percent-scale 미정규화/파싱 사고 행이 섞이지 않았는가
4. 저율 구간 비중 — 가격 백테스트 대상으로 쓰기 어려운 행의 비중
5. 신선도 — 홀드아웃 최신 행이 기준 시각에서 얼마나 떨어져 있는가

판정 규칙은 코드 분기가 아니라 :data:`_QUALITY_CHECKS` 선언 테이블이고 해석기는 그것을
순회만 한다(§4.5.2/§4.5.3). 임계값은 전부 모듈 상수이며, 표본 요구치처럼 런타임 설정에서
오는 값은 호출부가 주입한다(§4.7.3 — 이 모듈은 ``settings`` 를 읽지 않는다).

**측정 불가 = 통과 아님.** 표본이 하나도 없으면 모든 비율이 0 이 되어 blocking 검사가
실패하고 status 는 ``failed`` 가 된다. 신선도는 타임스탬프가 없으면 판정 불가이고, 그
사실을 침묵으로 넘기지 않고 warning 검사 실패로 남긴다.

status 어휘(``passed``/``warning``/``failed``)는 게이트가 순위를 매기는
``_MLReleaseBase.DATASET_QUALITY_ORDER`` 와 같은 문자열이어야 한다. 계층 역전을 만들지
않으려고 값을 여기 다시 선언하고, 그 동치는 테스트가 못박는다
(``tests/test_backtest_price_predictors.py``).

순수 함수(I/O 0).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime

from pydantic import BaseModel

from app.ai.holdout_quality import LOW_ACTUAL_RATE_THRESHOLD
from app.core.time import ensure_utc
from app.domain.aggregates import rate
from app.domain.rate_normalization import PERCENT_SCALE_THRESHOLD
from app.services.base_amount_basis import BASIS_CLEAN

# ── status·severity 어휘 ──────────────────────────────────────────────────────
STATUS_PASSED = "passed"
STATUS_WARNING = "warning"
STATUS_FAILED = "failed"

SEVERITY_BLOCKING = "blocking"
SEVERITY_WARNING = "warning"

# ── 비교 방향(선언) ───────────────────────────────────────────────────────────
AT_LEAST = "at_least"
AT_MOST = "at_most"

_COMPARATORS: dict[str, Callable[[float, float], bool]] = {
    AT_LEAST: lambda value, threshold: value >= threshold,
    AT_MOST: lambda value, threshold: value <= threshold,
}

# ── 임계값(선언) ──────────────────────────────────────────────────────────────
# 비율은 소수 4자리로 반올림해 리포트에 싣고, 그 값을 그대로 임계와 비교한다.
RATIO_DIGITS = 4
# 오염된 ``base_amount`` 는 오차 분모를 무효화한다(HistoricalData 컬럼 주석의 경고).
# 원칙은 전량 clean 이지만, 태깅 backfill 이 아직 닿지 않은 잔여 행이 1% 미만 섞이는
# 것까지 승격을 막지는 않는다 — 그 경우는 아래 warning 검사가 드러낸다.
MIN_CLEAN_BASE_AMOUNT_BASIS_RATIO = 0.99
FULL_CLEAN_BASE_AMOUNT_BASIS_RATIO = 1.0
# 낙찰률은 fraction(0.88)으로 저장돼야 한다. percent-scale(87.5) 잔재는 한 행만 있어도
# 그 행의 오차가 수십 배로 잡히므로 허용치는 0 건이다. 비율이 아니라 **건수**로 재는
# 이유는 큰 홀드아웃에서 한두 건이 반올림에 묻혀 조용히 통과하지 않게 하기 위함이다.
MAX_UNNORMALIZED_BID_RATE_COUNT = 0.0
# 저율 구간(<0.75)은 가격 백테스트 대상으로 쓰기 어렵다는 기존 홀드아웃 판정을
# 그대로 승계한다(app.ai.holdout_quality). 소수 섞이는 것은 정상이라 warning.
MIN_USABLE_BID_RATE_RATIO = 0.95
# 홀드아웃 최신 행이 이보다 오래되면 예측기를 승격하기 전에 재수집을 검토해야 한다.
# 반기(180일)는 KONEPS 낙찰 레짐 개정(#197 하한율 개정) 주기보다 짧게 잡은 값이다.
MAX_DATASET_AGE_DAYS = 180.0

SECONDS_PER_DAY = 86400.0

# 리포트 소비자(운영자·게이트 감사)에게 "무엇을 쟀는지"를 남긴다. 재지 못한 축이
# 생기면 이 문장을 함께 고친다 — 상수 status 시절의 거짓 scope 가 반복되지 않도록.
QUALITY_SCOPE = (
    "sample depth, base-amount provenance, bid-rate scale, usable bid-rate share, "
    "and holdout freshness"
)


@dataclass(frozen=True, slots=True)
class DatasetQualitySample:
    """품질 판정이 읽는 홀드아웃 한 행(호출부가 ORM 행에서 추출해 주입)."""

    base_amount_basis: str
    bid_rate: float
    observed_at: datetime | None


@dataclass(frozen=True, slots=True)
class _QualityContext:
    """규칙 테이블이 읽는 측정값(전부 실측)."""

    record_count: int
    required_sample_count: int
    clean_basis_ratio: float
    normalized_bid_rate_ratio: float
    unnormalized_bid_rate_count: int
    usable_bid_rate_ratio: float
    dataset_age_days: float | None


@dataclass(frozen=True, slots=True)
class _QualityCheckSpec:
    """검사 한 줄: 무엇을 재고(measure), 무엇과(threshold) 어느 방향으로(comparison)."""

    name: str
    severity: str
    comparison: str
    measure: Callable[[_QualityContext], float | None]
    threshold: Callable[[_QualityContext], float]
    detail: str


_QUALITY_CHECKS: tuple[_QualityCheckSpec, ...] = (
    _QualityCheckSpec(
        name="sample_depth",
        severity=SEVERITY_BLOCKING,
        comparison=AT_LEAST,
        measure=lambda ctx: float(ctx.record_count),
        threshold=lambda ctx: float(ctx.required_sample_count),
        detail="Backtest needs enough rows to fill both the training prefix and the holdout.",
    ),
    _QualityCheckSpec(
        name="base_amount_basis_purity",
        severity=SEVERITY_BLOCKING,
        comparison=AT_LEAST,
        measure=lambda ctx: ctx.clean_basis_ratio,
        threshold=lambda _ctx: MIN_CLEAN_BASE_AMOUNT_BASIS_RATIO,
        detail="Error denominators are only trustworthy on clean base-amount rows.",
    ),
    _QualityCheckSpec(
        name="bid_rate_scale",
        severity=SEVERITY_BLOCKING,
        comparison=AT_MOST,
        measure=lambda ctx: float(ctx.unnormalized_bid_rate_count),
        threshold=lambda _ctx: MAX_UNNORMALIZED_BID_RATE_COUNT,
        detail="Every bid rate must be stored as a fraction; percent-scale rows corrupt the metrics.",
    ),
    _QualityCheckSpec(
        name="base_amount_basis_scope",
        severity=SEVERITY_WARNING,
        comparison=AT_LEAST,
        measure=lambda ctx: ctx.clean_basis_ratio,
        threshold=lambda _ctx: FULL_CLEAN_BASE_AMOUNT_BASIS_RATIO,
        detail="Promotion evidence should be measured on a fully clean holdout.",
    ),
    _QualityCheckSpec(
        name="usable_bid_rate_share",
        severity=SEVERITY_WARNING,
        comparison=AT_LEAST,
        measure=lambda ctx: ctx.usable_bid_rate_ratio,
        threshold=lambda _ctx: MIN_USABLE_BID_RATE_RATIO,
        detail="Rows below the low-rate threshold are poor price-backtest targets.",
    ),
    _QualityCheckSpec(
        name="freshness",
        severity=SEVERITY_WARNING,
        comparison=AT_MOST,
        measure=lambda ctx: ctx.dataset_age_days,
        threshold=lambda _ctx: MAX_DATASET_AGE_DAYS,
        detail="Holdout rows must be recent enough to reflect the current award regime; missing timestamps cannot be audited.",
    ),
)


class DatasetQualityCheckReport(BaseModel):
    """검사 한 건의 판정 근거. ``value`` 가 ``None`` 이면 측정 불가(=통과 아님)."""

    name: str
    passed: bool
    severity: str
    value: float | None
    threshold: float
    comparison: str
    detail: str


class DatasetQualityMetrics(BaseModel):
    """status 를 도출한 실측값(리포트에 그대로 실린다)."""

    record_count: int
    required_sample_count: int
    clean_basis_ratio: float
    normalized_bid_rate_ratio: float
    unnormalized_bid_rate_count: int
    usable_bid_rate_ratio: float
    dataset_age_days: float | None
    newest_observed_at: datetime | None
    low_bid_rate_threshold: float
    percent_scale_threshold: float


class BacktestDatasetQualityReport(BaseModel):
    """백테스트 리포트에 실리는 dataset quality 판정(게이트가 읽는 계약)."""

    status: str
    record_count: int
    base_amount_basis: str
    blocking_issue_count: int
    warning_count: int
    scope: str
    metrics: DatasetQualityMetrics
    checks: tuple[DatasetQualityCheckReport, ...]


def _is_normalized_bid_rate(sample: DatasetQualitySample) -> bool:
    """fraction 스케일로 저장된 낙찰률인가(percent-scale 잔재가 아닌가)."""
    return 0.0 < sample.bid_rate <= PERCENT_SCALE_THRESHOLD


def _is_usable_bid_rate(sample: DatasetQualitySample) -> bool:
    """가격 백테스트 대상으로 쓸 수 있는 낙찰률 구간인가."""
    return _is_normalized_bid_rate(sample) and sample.bid_rate >= LOW_ACTUAL_RATE_THRESHOLD


def _dataset_age_days(
    samples: Sequence[DatasetQualitySample], *, reference_time: datetime
) -> tuple[datetime | None, float | None]:
    """홀드아웃 최신 행의 시각과 기준 시각까지의 경과 일수(없으면 판정 불가)."""
    observed = [
        ensure_utc(sample.observed_at)
        for sample in samples
        if sample.observed_at is not None
    ]
    if not observed:
        return None, None
    newest = max(observed)
    elapsed = (ensure_utc(reference_time) - newest).total_seconds() / SECONDS_PER_DAY
    return newest, round(max(0.0, elapsed), 2)


def _measure(
    samples: Sequence[DatasetQualitySample],
    *,
    required_sample_count: int,
    reference_time: datetime,
) -> tuple[_QualityContext, datetime | None]:
    """홀드아웃에서 규칙 테이블이 읽을 값들을 실측한다."""
    record_count = len(samples)
    normalized_count = sum(1 for sample in samples if _is_normalized_bid_rate(sample))
    newest_observed_at, dataset_age_days = _dataset_age_days(
        samples, reference_time=reference_time
    )
    return (
        _QualityContext(
            record_count=record_count,
            required_sample_count=max(1, int(required_sample_count or 1)),
            clean_basis_ratio=rate(
                sum(1 for sample in samples if sample.base_amount_basis == BASIS_CLEAN),
                record_count,
                digits=RATIO_DIGITS,
            ),
            normalized_bid_rate_ratio=rate(
                normalized_count, record_count, digits=RATIO_DIGITS
            ),
            unnormalized_bid_rate_count=record_count - normalized_count,
            usable_bid_rate_ratio=rate(
                sum(1 for sample in samples if _is_usable_bid_rate(sample)),
                record_count,
                digits=RATIO_DIGITS,
            ),
            dataset_age_days=dataset_age_days,
        ),
        newest_observed_at,
    )


def _evaluate(spec: _QualityCheckSpec, ctx: _QualityContext) -> DatasetQualityCheckReport:
    """검사 한 줄을 해석한다. 측정 불가(``None``)는 통과가 아니라 실패다."""
    value = spec.measure(ctx)
    threshold = spec.threshold(ctx)
    passed = value is not None and _COMPARATORS[spec.comparison](value, threshold)
    return DatasetQualityCheckReport(
        name=spec.name,
        passed=passed,
        severity=spec.severity,
        value=value,
        threshold=threshold,
        comparison=spec.comparison,
        detail=spec.detail,
    )


def _build_metrics(
    ctx: _QualityContext, *, newest_observed_at: datetime | None
) -> DatasetQualityMetrics:
    """status 를 도출한 실측값을 리포트 계약으로 옮긴다(비교 임계도 함께 남긴다)."""
    return DatasetQualityMetrics(
        record_count=ctx.record_count,
        required_sample_count=ctx.required_sample_count,
        clean_basis_ratio=ctx.clean_basis_ratio,
        normalized_bid_rate_ratio=ctx.normalized_bid_rate_ratio,
        unnormalized_bid_rate_count=ctx.unnormalized_bid_rate_count,
        usable_bid_rate_ratio=ctx.usable_bid_rate_ratio,
        dataset_age_days=ctx.dataset_age_days,
        newest_observed_at=newest_observed_at,
        low_bid_rate_threshold=LOW_ACTUAL_RATE_THRESHOLD,
        percent_scale_threshold=PERCENT_SCALE_THRESHOLD,
    )


def _roll_up_status(
    checks: tuple[DatasetQualityCheckReport, ...],
) -> tuple[str, int, int]:
    """실패한 검사의 severity 를 status 로 접는다 → (status, blocking 수, warning 수)."""
    failed = [check for check in checks if not check.passed]
    blocking_issue_count = sum(
        1 for check in failed if check.severity == SEVERITY_BLOCKING
    )
    warning_count = sum(1 for check in failed if check.severity == SEVERITY_WARNING)
    status = (
        STATUS_FAILED
        if blocking_issue_count
        else STATUS_WARNING if warning_count else STATUS_PASSED
    )
    return status, blocking_issue_count, warning_count


def assess_backtest_dataset_quality(
    samples: Sequence[DatasetQualitySample],
    *,
    base_amount_basis: str,
    required_sample_count: int,
    reference_time: datetime,
) -> BacktestDatasetQualityReport:
    """홀드아웃 데이터셋의 품질 status 를 실측에서 도출한다(순수).

    Args:
        samples: 백테스트가 실제로 평가한 행들(호출부가 ORM 행에서 추출).
        base_amount_basis: 행을 고른 provenance 필터(``clean`` 또는 ``any``) — 무엇을
            요구하고 뽑았는지를 리포트에 남긴다. 판정은 필터가 아니라 실측 비율로 한다.
        required_sample_count: 학습 prefix + 홀드아웃을 채우는 데 필요한 최소 행 수
            (런타임 설정에서 오므로 호출부가 주입한다).
        reference_time: 신선도 기준 시각(보통 리포트 생성 시각).

    Returns:
        blocking 실패가 하나라도 있으면 ``failed``, warning 만 있으면 ``warning``,
        전부 통과면 ``passed``.
    """
    ctx, newest_observed_at = _measure(
        samples,
        required_sample_count=required_sample_count,
        reference_time=reference_time,
    )
    checks = tuple(_evaluate(spec, ctx) for spec in _QUALITY_CHECKS)
    status, blocking_issue_count, warning_count = _roll_up_status(checks)
    return BacktestDatasetQualityReport(
        status=status,
        record_count=ctx.record_count,
        base_amount_basis=base_amount_basis,
        blocking_issue_count=blocking_issue_count,
        warning_count=warning_count,
        scope=QUALITY_SCOPE,
        metrics=_build_metrics(ctx, newest_observed_at=newest_observed_at),
        checks=checks,
    )
