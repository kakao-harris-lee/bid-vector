"""Rolling backtest helpers for price predictor selection."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from app.ai.predictors.base import (
    BasePricePredictor,
    PredictionResult,
    PricePredictionContext,
)
from app.ai.predictors.historical import read_record_value
from app.core.config import settings
from app.core.constants import AUTO_PROMOTION_EXCLUDED_PREDICTOR_KEYS
from app.domain.aggregates import average


def build_predictor_backtest_report(
    context: PricePredictionContext,
    registry: dict[str, BasePricePredictor],
    *,
    use_record_context: bool = False,
) -> dict[str, Any]:
    """Compare runnable predictors against recent historical bid-rate holdouts.

    ``use_record_context=False`` (기본) 는 종전과 동일하게 **호출 공고의**
    agency/category 를 전 홀드아웃 행에 전파한다 — 라이브 auto 선택("이 공고에서
    어느 predictor 가 나은가")의 의미다. ``True`` 는 홀드아웃 행 **자신의**
    agency/category 로 평가한다 — 오프라인 캘리브레이션("각 공고를 그 공고로서
    예측했는가")의 의미로, 계층(agency/category) 의존 predictor 가 전역 평균으로
    붕괴하지 않는다. 라이브 경로는 이 플래그를 켜지 않는다.
    """
    chronological_records = _sort_historical_records(context.historical_records)
    holdout_size = min(
        max(1, int(settings.PRICE_PREDICTION_BACKTEST_HOLDOUT_SIZE or 1)),
        max(0, len(chronological_records) - 1),
    )
    min_training_size = max(1, int(settings.PRICE_PREDICTION_BACKTEST_MIN_TRAINING_SAMPLES or 1))
    methodology = _report_methodology(registry, use_record_context=use_record_context)
    if len(chronological_records) <= min_training_size:
        return {
            "status": "insufficient_data",
            **methodology,
            "holdout_size": 0,
            "min_training_size": min_training_size,
            "sample_count": 0,
            "guardrail_rate": None,
            "fallback_rate": None,
            "results": [],
            "best_predictor_key": None,
            "best_predictor_name": None,
            "best_average_absolute_error_rate": None,
        }

    holdout_records = chronological_records[-holdout_size:]
    training_prefix = chronological_records[:-holdout_size]
    results: list[dict[str, Any]] = []
    for predictor_key, predictor in registry.items():
        result = _backtest_one_predictor(
            predictor_key=predictor_key,
            predictor=predictor,
            base_context=context,
            training_prefix=training_prefix,
            holdout_records=holdout_records,
            min_training_size=min_training_size,
            use_record_context=use_record_context,
        )
        results.append(result)

    # 자동 승격 제외 키는 results 에는 그대로 보고되지만 best 후보가 될 수 없다 —
    # best_predictor_key 는 auto 선택과 manifest recommended_env 로 흘러가는 승격
    # 경로다(선언·사유는 AUTO_PROMOTION_EXCLUDED_PREDICTOR_KEYS 참조).
    eligible_results = [
        result
        for result in results
        if result["status"] == "completed"
        and result["sample_count"] > 0
        and result["average_absolute_error_rate"] is not None
        and result["predictor_key"] not in AUTO_PROMOTION_EXCLUDED_PREDICTOR_KEYS
    ]
    best_result = min(
        eligible_results,
        key=lambda result: (
            float(result["average_absolute_error_rate"]),
            -int(result["sample_count"]),
            str(result["predictor_key"]),
        ),
        default=None,
    )

    by_group = _compute_by_group(
        holdout_records=holdout_records,
        training_prefix=training_prefix,
        registry=registry,
        best_result=best_result,
        base_context=context,
        min_training_size=min_training_size,
        use_record_context=use_record_context,
    )

    return {
        "status": "completed" if best_result is not None else "no_eligible_predictor",
        **methodology,
        "holdout_size": holdout_size,
        "min_training_size": min_training_size,
        "sample_count": int(best_result["sample_count"]) if best_result else 0,
        "guardrail_rate": best_result.get("guardrail_rate") if best_result else None,
        "fallback_rate": best_result.get("fallback_rate") if best_result else None,
        "results": results,
        "best_predictor_key": best_result["predictor_key"] if best_result else None,
        "best_predictor_name": best_result["predictor_name"] if best_result else None,
        "best_average_absolute_error_rate": best_result["average_absolute_error_rate"] if best_result else None,
        "by_group": by_group,
    }


def _report_methodology(
    registry: dict[str, BasePricePredictor],
    *,
    use_record_context: bool,
) -> dict[str, str | list[str] | bool]:
    """리포트가 스스로 싣는 방법론 메타(§4.5 선언 데이터).

    report_version "2": best_* 의 의미가 "평가된 전체 중 최선"에서 "자동 승격 제외
    (excluded_predictor_arms) 를 뺀 최선"으로 바뀌었고, ``use_record_context`` 에
    따라 홀드아웃 컨텍스트 방법론이 달라진다 — 리포트 자신이 그 사실을 실어야
    형태만으로 두 실행을 구별할 수 있다(ml_training/comparison.py 의 #360 대응과
    같은 패턴). 버전 키 부재 = v1(제외 이전 의미)이다.
    """
    return {
        "report_version": "2",
        "predictor_arms": sorted(registry),
        "excluded_predictor_arms": sorted(
            set(registry) & AUTO_PROMOTION_EXCLUDED_PREDICTOR_KEYS
        ),
        "use_record_context": bool(use_record_context),
    }


def _backtest_one_predictor(
    *,
    predictor_key: str,
    predictor: BasePricePredictor,
    base_context: PricePredictionContext,
    training_prefix: list[object],
    holdout_records: list[object],
    min_training_size: int,
    use_record_context: bool = False,
) -> dict[str, Any]:
    """Evaluate one predictor across a rolling historical holdout."""
    absolute_errors: list[float] = []
    skipped_reasons: list[str] = []
    fallback_count = 0
    guardrail_count = 0
    rolling_training_records = list(training_prefix)

    for holdout_record in holdout_records:
        actual_bid_rate = _resolve_bid_rate(holdout_record)
        budget = _resolve_budget(holdout_record, fallback=base_context.budget)
        if actual_bid_rate is None or budget <= 0:
            skipped_reasons.append("holdout record has no usable bid rate or budget")
            rolling_training_records.append(holdout_record)
            continue
        if len(rolling_training_records) < min_training_size:
            skipped_reasons.append("not enough training samples before holdout")
            rolling_training_records.append(holdout_record)
            continue

        category = base_context.category
        agency_name = base_context.agency_name
        if use_record_context:
            record_category = read_record_value(holdout_record, "category")
            record_agency = read_record_value(holdout_record, "agency_name")
            category = str(record_category) if record_category else category
            agency_name = str(record_agency) if record_agency else agency_name
        evaluation_context = PricePredictionContext(
            budget=budget,
            category=category,
            description=base_context.description,
            historical_records=tuple(rolling_training_records),
            agency_name=agency_name,
        )
        availability = predictor.check_availability(evaluation_context)
        if not availability.available:
            skipped_reasons.append(str(availability.reason or "predictor unavailable"))
            rolling_training_records.append(holdout_record)
            continue

        try:
            prediction = predictor.predict(evaluation_context)
        except Exception as exc:
            skipped_reasons.append(f"prediction failed: {exc}")
            rolling_training_records.append(holdout_record)
            continue

        predicted_bid_rate = _resolve_prediction_bid_rate(prediction, budget=budget)
        if predicted_bid_rate is None:
            skipped_reasons.append("prediction returned no usable bid rate")
            rolling_training_records.append(holdout_record)
            continue
        absolute_errors.append(abs(predicted_bid_rate - actual_bid_rate))
        fallback_count += 1 if prediction.fallback_reason else 0
        guardrail_count += 1 if prediction.guardrail_applied else 0
        rolling_training_records.append(holdout_record)

    return {
        "predictor_key": predictor_key,
        "predictor_name": predictor.name,
        "predictor_family": predictor.family,
        "status": "completed" if absolute_errors else "skipped",
        "sample_count": len(absolute_errors),
        "average_absolute_error_rate": _average(absolute_errors),
        "max_absolute_error_rate": round(max(absolute_errors), 6) if absolute_errors else None,
        "fallback_rate": _rate(fallback_count, len(absolute_errors)),
        "guardrail_rate": _rate(guardrail_count, len(absolute_errors)),
        "skipped_count": len(skipped_reasons),
        "skipped_reasons": _top_reasons(skipped_reasons),
    }


def _sort_historical_records(records: tuple[object, ...]) -> list[object]:
    """Return records in oldest-first order when timestamps are available."""
    indexed_records = list(enumerate(records))

    def sort_key(indexed_record: tuple[int, object]) -> tuple[str, int]:
        index, record = indexed_record
        opened_at = read_record_value(record, "opened_at") or read_record_value(record, "created_at")
        if opened_at is None:
            return "", index
        return opened_at.isoformat() if hasattr(opened_at, "isoformat") else str(opened_at), index

    if any(read_record_value(record, "opened_at") or read_record_value(record, "created_at") for _, record in indexed_records):
        return [record for _, record in sorted(indexed_records, key=sort_key)]
    return list(records)


def _resolve_bid_rate(record: object) -> float | None:
    """Resolve a usable actual bid rate from a historical record."""
    bid_rate = float(read_record_value(record, "bid_rate") or 0.0)
    if bid_rate <= 0:
        predicted_price = float(read_record_value(record, "predicted_price") or 0.0)
        base_amount = float(read_record_value(record, "base_amount") or 0.0)
        if predicted_price > 0 and base_amount > 0:
            bid_rate = predicted_price / base_amount
    if 0.5 <= bid_rate <= 1.5:
        return bid_rate
    return None


def _resolve_budget(record: object, *, fallback: float) -> float:
    """Resolve the base amount used for a holdout prediction."""
    budget = float(read_record_value(record, "base_amount") or 0.0)
    return budget if budget > 0 else float(fallback or 0.0)


def _resolve_prediction_bid_rate(prediction: PredictionResult, *, budget: float) -> float | None:
    """Resolve the predicted bid rate from a validated prediction result."""
    bid_rate = float(prediction.predicted_bid_rate or 0.0)
    if bid_rate <= 0 and budget > 0:
        bid_rate = float(prediction.predicted_price or 0.0) / budget
    if 0.0 < bid_rate < 2.0:
        return bid_rate
    return None


def _average(values: list[float]) -> float | None:
    """Return a rounded average while preserving empty sets."""
    return average(values, digits=6)


def _rate(count: int, total: int) -> float | None:
    return round(count / total, 6) if total else None


def _top_reasons(reasons: list[str], *, limit: int = 3) -> list[str]:
    """Return the most common skip reasons in stable order."""
    counts: dict[str, int] = {}
    for reason in reasons:
        counts[reason] = counts.get(reason, 0) + 1
    return [
        reason
        for reason, _ in sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:limit]
    ]


def _group_records_by_business_group(records: list[object]) -> dict[str, list[object]]:
    """Partition records by their business_group value, skipping None."""
    groups: dict[str, list[object]] = defaultdict(list)
    for record in records:
        group = read_record_value(record, "business_group")
        if group is not None:
            groups[str(group)].append(record)
    return dict(groups)


def _compute_by_group(
    *,
    holdout_records: list[object],
    training_prefix: list[object],
    registry: dict[str, BasePricePredictor],
    best_result: dict[str, Any] | None,
    base_context: PricePredictionContext,
    min_training_size: int,
    use_record_context: bool = False,
) -> dict[str, dict[str, Any]]:
    """Compute per-business-group backtest metrics using the best available
    predictor (falling back to any registry entry). Each group evaluates only its
    own holdout records over the intact training prefix; records without a
    business_group are skipped.
    """
    groups = _group_records_by_business_group(holdout_records)
    if not groups:
        return {}

    predictor_key: str | None = best_result["predictor_key"] if best_result else None
    predictor: BasePricePredictor | None = registry.get(predictor_key) if predictor_key else None
    if predictor is None and registry:
        predictor_key, predictor = next(iter(registry.items()))

    by_group: dict[str, dict[str, Any]] = {}
    for group_name, group_holdout_records in sorted(groups.items()):
        if predictor is None:
            by_group[group_name] = {
                "sample_count": 0,
                "average_absolute_error_rate": None,
                "skipped_count": len(group_holdout_records),
            }
            continue

        result = _backtest_one_predictor(
            predictor_key=predictor_key,
            predictor=predictor,
            base_context=base_context,
            training_prefix=training_prefix,
            holdout_records=group_holdout_records,
            min_training_size=min_training_size,
            use_record_context=use_record_context,
        )
        by_group[group_name] = {
            "sample_count": result["sample_count"],
            "average_absolute_error_rate": result["average_absolute_error_rate"],
            "skipped_count": result["skipped_count"],
        }

    return by_group
