from __future__ import annotations

from datetime import date, datetime
from unittest.mock import MagicMock

import pytest
from app.services.breadth.types import (
    BreadthDailyResult,
    BreadthEligibilityCounts,
    BreadthIndicatorValues,
)
from app.services.breadth_coverage import (
    BreadthCalculationResult,
    BreadthCoverageReport,
    BreadthOutcomeReport,
    BreadthPriceCoverage,
)
from app.services.daily_breadth_runner import (
    DailyBreadthDependencies,
    DailyBreadthRequest,
    IncompleteDailyBreadth,
    run_daily_breadth,
)
from app.services.derived_data_execution_policy import (
    resolve_derived_data_execution_policy,
)

CALCULATION_DATE = date(2026, 3, 20)


def _policy(
    mode: str,
    *,
    target_date: date = CALCULATION_DATE,
    current_date: date = CALCULATION_DATE,
):
    return resolve_derived_data_execution_policy(
        execution_policy=mode,
        target_date=target_date,
        current_date=current_date,
    )


def _calculation(
    *,
    scanned: int = 90,
    skipped: int = 10,
    misses: int = 10,
    errors: int = 0,
) -> BreadthCalculationResult:
    candidates = scanned + skipped
    insufficient = max(skipped - misses - errors, 0)
    indicators = {
            "stocks_up_4pct": 10,
            "stocks_down_4pct": 4,
            "ratio_5day": 1.5,
            "ratio_10day": 1.2,
            "stocks_up_25pct_quarter": 2,
            "stocks_down_25pct_quarter": 1,
            "stocks_up_25pct_month": 3,
            "stocks_down_25pct_month": 1,
            "stocks_up_50pct_month": 1,
            "stocks_down_50pct_month": 0,
            "stocks_up_13pct_34days": 5,
            "stocks_down_13pct_34days": 2,
            "total_stocks_scanned": candidates,
            "broad_universe_count": candidates,
            "calculation_revision": 2,
        }
    daily_result = BreadthDailyResult(
        market="US",
        calculation_date=CALCULATION_DATE,
        values=BreadthIndicatorValues(
            **{
                key: value
                for key, value in indicators.items()
                if key in BreadthIndicatorValues.__dataclass_fields__
            }
        ),
        eligibility=BreadthEligibilityCounts(),
        broad_universe_count=candidates,
        eligibility_signature="a" * 64,
        stockbee_eligibility_signature="b" * 64,
    )
    return BreadthCalculationResult(
        indicators=indicators,
        coverage=BreadthCoverageReport.from_parts(
            BreadthPriceCoverage(
                candidate_stocks=candidates,
                symbols_with_cached_history=candidates - misses,
                cache_miss_stocks=misses,
                cache_miss_symbols_sample=("MISS",) if misses else (),
                cache_coverage_ratio=(
                    (candidates - misses) / candidates
                    if candidates
                    else 0.0
                ),
            ),
            BreadthOutcomeReport(
                scanned=scanned,
                cache_misses=misses,
                insufficient=insufficient,
                errors=errors,
            ),
        ),
        daily_result=daily_result,
    )


def _dependencies(calculator: MagicMock):
    return DailyBreadthDependencies(
        calculator=calculator,
        publish_snapshot=MagicMock(),
    )


def test_runner_persists_and_serializes_compatible_success():
    calculator = MagicMock()
    calculator.calculate_daily_breadth.return_value = _calculation()
    dependencies = _dependencies(calculator)
    policy = _policy("refresh_guarded")

    outcome = run_daily_breadth(
        MagicMock(),
        DailyBreadthRequest(
            calculation_date=CALCULATION_DATE,
            market="US",
            policy=policy,
        ),
        dependencies,
    )

    calculator.calculate_daily_breadth.assert_called_once_with(
        calculation_date=CALCULATION_DATE,
        policy=policy,
    )
    calculator.store_daily_result.assert_called_once()
    calculator.store_daily_breadth.assert_not_called()
    dependencies.publish_snapshot.assert_called_once_with("US")
    result = outcome.to_task_result(policy)
    assert result["date"] == "2026-03-20"
    assert result["indicators"]["stocks_up_4pct"] == 10
    assert result["total_stocks_scanned"] == 100
    assert result["cache_only"] is True
    assert result["cache_policy"] == "refresh_guarded"
    assert result["cache_diagnostics"]["cache_miss_stocks"] == 10


def test_refresh_guarded_allows_partial_cache_coverage_with_usable_stocks():
    calculator = MagicMock()
    calculator.calculate_daily_breadth.return_value = _calculation(
        scanned=60,
        skipped=40,
        misses=35,
    )

    outcome = run_daily_breadth(
        MagicMock(),
        DailyBreadthRequest(
            calculation_date=CALCULATION_DATE,
            market="US",
            policy=_policy("refresh_guarded"),
        ),
        _dependencies(calculator),
    )

    assert outcome.coverage.total_stocks_scanned == 60
    calculator.store_daily_result.assert_called_once()


def test_refresh_guarded_rejects_zero_usable_stocks_before_persistence():
    calculator = MagicMock()
    calculator.calculate_daily_breadth.return_value = _calculation(
        scanned=0,
        skipped=100,
        misses=100,
    )
    dependencies = _dependencies(calculator)

    with pytest.raises(
        IncompleteDailyBreadth,
        match="processed no usable stocks",
    ) as caught:
        run_daily_breadth(
            MagicMock(),
            DailyBreadthRequest(
                calculation_date=CALCULATION_DATE,
                market="US",
                policy=_policy("refresh_guarded"),
            ),
            dependencies,
        )

    assert caught.value.coverage.total_stocks_scanned == 0
    calculator.store_daily_result.assert_not_called()
    calculator.store_daily_breadth.assert_not_called()
    dependencies.publish_snapshot.assert_not_called()


def test_strict_cache_only_rejects_miss_ratio_above_tolerance():
    calculator = MagicMock()
    calculator.calculate_daily_breadth.return_value = _calculation(
        scanned=60,
        skipped=40,
        misses=35,
    )

    with pytest.raises(IncompleteDailyBreadth, match="exceeds miss tolerance"):
        run_daily_breadth(
            MagicMock(),
            DailyBreadthRequest(
                calculation_date=CALCULATION_DATE,
                market="US",
                policy=_policy("strict_cache_only"),
            ),
            _dependencies(calculator),
        )

    calculator.store_daily_result.assert_not_called()
    calculator.store_daily_breadth.assert_not_called()


def test_strict_cache_only_rejects_zero_usable_stocks():
    calculator = MagicMock()
    calculator.calculate_daily_breadth.return_value = _calculation(
        scanned=0,
        skipped=100,
        misses=0,
    )
    dependencies = _dependencies(calculator)

    with pytest.raises(
        IncompleteDailyBreadth,
        match="processed no usable stocks",
    ):
        run_daily_breadth(
            MagicMock(),
            DailyBreadthRequest(
                calculation_date=CALCULATION_DATE,
                market="US",
                policy=_policy("strict_cache_only"),
            ),
            dependencies,
        )

    calculator.store_daily_result.assert_not_called()
    calculator.store_daily_breadth.assert_not_called()
    dependencies.publish_snapshot.assert_not_called()


def test_same_day_auto_requires_complete_warmup_metadata():
    calculator = MagicMock()
    calculator.price_cache.get_warmup_metadata.return_value = {
        "status": "partial",
        "count": 90,
        "total": 100,
        "completed_at": datetime.now().isoformat(),
        "error": None,
    }
    calculator.calculate_daily_breadth.return_value = _calculation()

    with pytest.raises(IncompleteDailyBreadth, match="warmup not complete"):
        run_daily_breadth(
            MagicMock(),
            DailyBreadthRequest(
                calculation_date=CALCULATION_DATE,
                market="US",
                policy=_policy("auto"),
            ),
            _dependencies(calculator),
        )

    calculator.store_daily_result.assert_not_called()
    calculator.store_daily_breadth.assert_not_called()


def test_snapshot_failure_is_best_effort():
    calculator = MagicMock()
    calculator.calculate_daily_breadth.return_value = _calculation(
        scanned=100,
        skipped=0,
        misses=0,
    )
    dependencies = DailyBreadthDependencies(
        calculator=calculator,
        publish_snapshot=MagicMock(side_effect=RuntimeError("snapshot down")),
    )

    outcome = run_daily_breadth(
        MagicMock(),
        DailyBreadthRequest(
            calculation_date=CALCULATION_DATE,
            market="US",
            policy=_policy(
                "auto",
                target_date=CALCULATION_DATE,
                current_date=date(2026, 3, 21),
            ),
        ),
        dependencies,
    )

    assert outcome.calculation_date == CALCULATION_DATE
    calculator.store_daily_result.assert_called_once()
