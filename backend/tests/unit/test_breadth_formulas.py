import pandas as pd
import pytest

from app.services.breadth.formulas import prepare_feature_frame, signal_flags_at
from app.services.breadth.types import BreadthFormulaPolicy

CALCULATION_DATE = pd.Timestamp("2026-08-21")


def _feature_row(**overrides):
    values = {
        "adjusted_close": 104.0,
        "prior_adjusted_close": 100.0,
        "daily_return": 0.04,
        "volume": 100_000.0,
        "prior_volume": 99_999.0,
        "adtv20_usd": 250_000.0,
        "adjusted_close_20": 100.0,
        "raw_close_usd_20": 5.0,
        "month_return": 0.04,
        "low_34": 92.0,
        "high_34": 104.0,
        "low_65": 83.0,
        "high_65": 104.0,
        "sma40": 100.0,
        "sma50": 100.0,
        "atr14": 1.0,
        "adjusted_high": 105.0,
        "adjusted_low": 103.0,
        "previous_251_high": 104.0,
        "previous_251_low": 90.0,
    }
    values.update(overrides)
    return pd.DataFrame([values], index=[CALCULATION_DATE])


def test_prepare_feature_frame_separates_adjusted_signals_from_raw_liquidity():
    index = pd.to_datetime(["2026-08-20", "2026-08-21"])
    prices = pd.DataFrame(
        {
            "Open": [99.0, 49.5],
            "High": [102.0, 51.0],
            "Low": [98.0, 49.0],
            "Close": [100.0, 50.0],
            "Adj Close": [50.0, 50.0],
            "Volume": [200_000, 220_000],
        },
        index=index,
    )
    fx = pd.Series([0.8, 0.8], index=prices.index)

    result = prepare_feature_frame(prices, fx)

    assert result.iloc[0].adjusted_high == pytest.approx(51.0)
    assert result.iloc[0].raw_close_usd == pytest.approx(80.0)
    assert result.iloc[0].dollar_volume_usd == pytest.approx(16_000_000.0)


def test_prepare_feature_frame_normalizes_timezone_aware_daily_sessions():
    index = pd.bdate_range("2026-07-01", periods=40, tz="America/New_York")
    prices = pd.DataFrame(
        {
            "Open": 100.0,
            "High": 101.0,
            "Low": 99.0,
            "Close": 100.0,
            "Adj Close": 100.0,
            "Volume": 1_000_000,
        },
        index=index,
    )
    fx = pd.Series(1.0, index=index.tz_localize(None))

    features = prepare_feature_frame(prices, fx)
    signals = signal_flags_at(
        features,
        index[-1].date(),
        BreadthFormulaPolicy(),
    )

    assert features.index.tz is None
    assert signals.eligibility.advance_decline is True


def test_prepare_feature_frame_rejects_a_missing_session():
    index = pd.DatetimeIndex([pd.Timestamp("2026-03-19"), pd.NaT])
    prices = pd.DataFrame(
        {
            "Open": [100.0, 101.0],
            "High": [101.0, 102.0],
            "Low": [99.0, 100.0],
            "Close": [100.0, 101.0],
            "Adj Close": [100.0, 101.0],
            "Volume": [1_000_000, 1_000_000],
        },
        index=index,
    )

    with pytest.raises(ValueError, match="valid sessions"):
        prepare_feature_frame(prices, pd.Series(1.0, index=index))


def test_prepare_feature_frame_initializes_and_smooths_wilder_atr():
    index = pd.bdate_range("2026-07-01", periods=15)
    prices = pd.DataFrame(
        {
            "Open": 100.0,
            "High": 101.0,
            "Low": 99.0,
            "Close": 100.0,
            "Adj Close": 100.0,
            "Volume": 100_000,
        },
        index=index,
    )

    result = prepare_feature_frame(prices, pd.Series(1.0, index=index))

    assert result.atr14.iloc[:13].isna().all()
    assert result.atr14.iloc[13] == pytest.approx(2.0)
    assert result.atr14.iloc[14] == pytest.approx(2.0)


def test_prepare_feature_frame_restarts_wilder_atr_after_initial_gap():
    index = pd.bdate_range("2026-07-01", periods=18)
    prices = pd.DataFrame(
        {
            "Open": 100.0,
            "High": 101.0,
            "Low": 99.0,
            "Close": 100.0,
            "Adj Close": 100.0,
            "Volume": 100_000,
        },
        index=index,
    )
    prices.loc[index[0], "High"] = float("nan")

    result = prepare_feature_frame(
        prices,
        pd.Series(1.0, index=index),
        atr_period=14,
    )

    assert result.atr14.iloc[:14].isna().all()
    assert result.atr14.iloc[14] == pytest.approx(2.0)
    assert result.atr14.iloc[15] == pytest.approx(2.0)


@pytest.mark.parametrize(
    ("overrides", "expected_up", "expected_down"),
    [
        ({"daily_return": 0.04}, True, False),
        ({"daily_return": 0.039999}, False, False),
        ({"daily_return": -0.04}, False, True),
        ({"daily_return": -0.039999}, False, False),
        ({"daily_return": 0.04, "volume": 99_999.0}, False, False),
        (
            {"daily_return": 0.04, "volume": 100_000.0, "prior_volume": 100_000.0},
            False,
            False,
        ),
        ({"daily_return": 0.04, "adtv20_usd": 249_999.99}, False, False),
    ],
)
def test_daily_stockbee_boundaries(overrides, expected_up, expected_down):
    signals = signal_flags_at(
        _feature_row(**overrides), CALCULATION_DATE.date(), BreadthFormulaPolicy()
    )

    assert signals.up_4pct is expected_up
    assert signals.down_4pct is expected_down


@pytest.mark.parametrize(
    ("month_return", "up_25", "down_25", "up_50", "down_50"),
    [
        (0.25, True, False, False, False),
        (0.249999, False, False, False, False),
        (-0.25, False, True, False, False),
        (0.50, True, False, True, False),
        (-0.50, False, True, False, True),
    ],
)
def test_month_stockbee_boundaries(month_return, up_25, down_25, up_50, down_50):
    signals = signal_flags_at(
        _feature_row(month_return=month_return),
        CALCULATION_DATE.date(),
        BreadthFormulaPolicy(),
    )

    assert signals.up_25pct_month is up_25
    assert signals.down_25pct_month is down_25
    assert signals.up_50pct_month is up_50
    assert signals.down_50pct_month is down_50


def test_month_reference_price_is_usd_five_inclusive():
    eligible = signal_flags_at(
        _feature_row(raw_close_usd_20=5.0, month_return=0.25),
        CALCULATION_DATE.date(),
        BreadthFormulaPolicy(),
    )
    ineligible = signal_flags_at(
        _feature_row(raw_close_usd_20=4.999, month_return=0.25),
        CALCULATION_DATE.date(),
        BreadthFormulaPolicy(),
    )

    assert eligible.eligibility.stockbee_month is True
    assert eligible.up_25pct_month is True
    assert ineligible.eligibility.stockbee_month is False
    assert ineligible.up_25pct_month is False


@pytest.mark.parametrize(
    ("overrides", "signal_name", "expected"),
    [
        ({"adjusted_close": 113.0, "low_34": 100.0}, "up_13pct_34days", True),
        ({"adjusted_close": 112.999, "low_34": 100.0}, "up_13pct_34days", False),
        ({"adjusted_close": 87.0, "high_34": 100.0}, "down_13pct_34days", True),
        ({"adjusted_close": 125.0, "low_65": 100.0}, "up_25pct_quarter", True),
        ({"adjusted_close": 75.0, "high_65": 100.0}, "down_25pct_quarter", True),
    ],
)
def test_range_signal_boundaries(overrides, signal_name, expected):
    signals = signal_flags_at(
        _feature_row(**overrides), CALCULATION_DATE.date(), BreadthFormulaPolicy()
    )

    assert getattr(signals, signal_name) is expected


def test_t2108_requires_strictly_above_sma40():
    equal = signal_flags_at(
        _feature_row(adjusted_close=100.0, sma40=100.0),
        CALCULATION_DATE.date(),
        BreadthFormulaPolicy(),
    )
    above = signal_flags_at(
        _feature_row(adjusted_close=100.001, sma40=100.0),
        CALCULATION_DATE.date(),
        BreadthFormulaPolicy(),
    )

    assert equal.eligibility.t2108 is True
    assert equal.t2108_above is False
    assert above.t2108_above is True


def test_52week_record_comparison_is_strict():
    equal = signal_flags_at(
        _feature_row(adjusted_high=104.0, previous_251_high=104.0),
        CALCULATION_DATE.date(),
        BreadthFormulaPolicy(),
    )
    record = signal_flags_at(
        _feature_row(adjusted_high=104.001, previous_251_high=104.0),
        CALCULATION_DATE.date(),
        BreadthFormulaPolicy(),
    )

    assert equal.new_high_52week is False
    assert record.new_high_52week is True


@pytest.mark.parametrize(
    ("atr", "eligible", "extended"),
    [
        (1.1, True, True),
        (1.1001, True, False),
        (0.0, False, False),
        (float("inf"), False, False),
    ],
)
def test_atr_extension_boundaries(atr, eligible, extended):
    signals = signal_flags_at(
        _feature_row(adjusted_close=110.0, sma50=100.0, atr14=atr),
        CALCULATION_DATE.date(),
        BreadthFormulaPolicy(),
    )

    assert signals.eligibility.atr_extension is eligible
    assert signals.atr_10x_extension is extended


def test_signal_lookup_requires_exact_session_and_invalid_prices_are_ineligible():
    missing = signal_flags_at(
        _feature_row(), pd.Timestamp("2026-08-22").date(), BreadthFormulaPolicy()
    )
    invalid = signal_flags_at(
        _feature_row(adjusted_close=float("nan")),
        CALCULATION_DATE.date(),
        BreadthFormulaPolicy(),
    )

    assert missing.eligibility.advance_decline is False
    assert invalid.eligibility.advance_decline is False
    assert invalid.eligibility.stockbee_daily is False
