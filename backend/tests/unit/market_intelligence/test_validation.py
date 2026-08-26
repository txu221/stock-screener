from __future__ import annotations

import math
from dataclasses import replace
from datetime import date, datetime, timezone

import pytest

from app.domain.market_intelligence.constants import (
    NORMALIZATION_VERSION,
    PRICE_BASIS,
)
from app.domain.market_intelligence.models import RawBar, RejectionCode
from app.domain.market_intelligence.validation import validate_provider_rows

INGESTED_AT = datetime(2026, 5, 15, 21, 10, tzinfo=timezone.utc)
SOURCE_AT = datetime(2026, 5, 15, 21, 5, tzinfo=timezone.utc)
SESSION = date(2026, 5, 15)
EXPECTED_SESSIONS = (date(2026, 5, 14), SESSION)


@pytest.fixture
def valid_raw_bar() -> RawBar:
    return RawBar(
        provider="yahoo",
        provider_symbol="XLK",
        symbol="XLK",
        raw_trading_date="2026-05-15T00:00:00-04:00",
        trading_date=SESSION,
        open=100.0,
        high=110.0,
        low=90.0,
        close=105.0,
        adjusted_close=103.95,
        volume=12_000_000.0,
        source_timestamp=SOURCE_AT,
    )


def test_valid_row_preserves_raw_evidence_and_adjusts_all_ohlc(
    valid_raw_bar: RawBar,
) -> None:
    result = validate_provider_rows(
        (valid_raw_bar,), EXPECTED_SESSIONS, INGESTED_AT
    )

    assert result.rejections == ()
    assert result.received_symbols == ("XLK",)
    assert len(result.canonical_bars) == 1
    bar = result.canonical_bars[0]
    assert bar.provider == "yahoo"
    assert bar.provider_symbol == "XLK"
    assert bar.raw_trading_date == valid_raw_bar.raw_trading_date
    assert bar.raw_open == 100.0
    assert bar.raw_high == 110.0
    assert bar.raw_low == 90.0
    assert bar.raw_close == 105.0
    assert bar.provider_adjusted_close == 103.95
    assert bar.adjustment_factor == pytest.approx(0.99)
    assert bar.adjusted_open == pytest.approx(99.0)
    assert bar.adjusted_high == pytest.approx(108.9)
    assert bar.adjusted_low == pytest.approx(89.1)
    assert bar.adjusted_close == pytest.approx(103.95)
    assert bar.provider_volume == 12_000_000.0
    assert bar.source_timestamp == SOURCE_AT
    assert bar.ingestion_timestamp == INGESTED_AT
    assert bar.price_basis == PRICE_BASIS
    assert bar.normalization_version == NORMALIZATION_VERSION


@pytest.mark.parametrize(
    ("changes", "expected_code"),
    [
        ({"volume": -1.0}, RejectionCode.NEGATIVE_VOLUME),
        ({"high": 89.0}, RejectionCode.INVALID_OHLC_RELATION),
        ({"high": 99.0}, RejectionCode.INVALID_OHLC_RELATION),
        ({"high": 104.0}, RejectionCode.INVALID_OHLC_RELATION),
        ({"low": 101.0}, RejectionCode.INVALID_OHLC_RELATION),
        ({"low": 106.0}, RejectionCode.INVALID_OHLC_RELATION),
        ({"open": 0.0}, RejectionCode.NON_POSITIVE_PRICE),
        ({"close": -1.0}, RejectionCode.NON_POSITIVE_PRICE),
        ({"adjusted_close": 0.0}, RejectionCode.INVALID_ADJUSTED_CLOSE),
        ({"open": float("nan")}, RejectionCode.NON_FINITE_VALUE),
        ({"high": float("inf")}, RejectionCode.NON_FINITE_VALUE),
        ({"low": float("-inf")}, RejectionCode.NON_FINITE_VALUE),
        ({"volume": float("nan")}, RejectionCode.NON_FINITE_VALUE),
    ],
)
def test_invalid_row_is_rejected_without_coercion(
    valid_raw_bar: RawBar,
    changes: dict[str, object],
    expected_code: RejectionCode,
) -> None:
    raw = replace(valid_raw_bar, **changes)

    result = validate_provider_rows((raw,), EXPECTED_SESSIONS, INGESTED_AT)

    assert result.canonical_bars == ()
    assert len(result.rejections) == 1
    assert result.rejections[0].code is expected_code
    assert result.rejections[0].provider == "yahoo"
    assert result.rejections[0].symbol == "XLK"
    assert result.rejections[0].trading_date == SESSION
    assert result.rejections[0].ingestion_timestamp == INGESTED_AT
    if "volume" in changes and math.isfinite(float(changes["volume"])):
        assert result.rejections[0].raw_evidence["volume"] == changes["volume"]


def test_negative_volume_is_preserved_and_never_abs_clamped_or_zeroed(
    valid_raw_bar: RawBar,
) -> None:
    raw = replace(valid_raw_bar, volume=-125.0)

    result = validate_provider_rows((raw,), EXPECTED_SESSIONS, INGESTED_AT)

    assert result.canonical_bars == ()
    rejection = result.rejections[0]
    assert rejection.code is RejectionCode.NEGATIVE_VOLUME
    assert rejection.raw_evidence["volume"] == -125.0
    assert rejection.raw_evidence["volume"] not in (0.0, 125.0)


def test_missing_required_value_is_recorded(valid_raw_bar: RawBar) -> None:
    raw = replace(valid_raw_bar, close=None)

    result = validate_provider_rows((raw,), EXPECTED_SESSIONS, INGESTED_AT)

    assert result.canonical_bars == ()
    assert result.rejections[0].code is RejectionCode.MISSING_REQUIRED_FIELD
    assert result.rejections[0].raw_evidence["close"] is None


def test_unexpected_symbol_is_rejected(valid_raw_bar: RawBar) -> None:
    raw = replace(valid_raw_bar, symbol="QQQ", provider_symbol="QQQ")

    result = validate_provider_rows((raw,), EXPECTED_SESSIONS, INGESTED_AT)

    assert result.canonical_bars == ()
    assert result.received_symbols == ("QQQ",)
    assert result.rejections[0].code is RejectionCode.UNEXPECTED_SYMBOL


@pytest.mark.parametrize("trading_date", [None, date(2026, 5, 13)])
def test_invalid_or_non_session_date_is_rejected(
    valid_raw_bar: RawBar,
    trading_date: date | None,
) -> None:
    raw = replace(valid_raw_bar, trading_date=trading_date)

    result = validate_provider_rows((raw,), EXPECTED_SESSIONS, INGESTED_AT)

    assert result.canonical_bars == ()
    assert result.rejections[0].code is RejectionCode.INVALID_TRADING_DATE


def test_duplicate_group_rejects_every_row_without_selecting_a_winner(
    valid_raw_bar: RawBar,
) -> None:
    duplicate = replace(valid_raw_bar, volume=13_000_000.0)

    result = validate_provider_rows(
        (valid_raw_bar, duplicate), EXPECTED_SESSIONS, INGESTED_AT
    )

    assert result.canonical_bars == ()
    assert len(result.rejections) == 2
    assert {item.code for item in result.rejections} == {
        RejectionCode.DUPLICATE_BAR
    }
    assert [item.raw_evidence["volume"] for item in result.rejections] == [
        12_000_000.0,
        13_000_000.0,
    ]


def test_invalid_adjustment_factor_is_rejected(valid_raw_bar: RawBar) -> None:
    raw = replace(
        valid_raw_bar,
        close=1e308,
        open=1e308,
        high=1e308,
        low=1e308,
        adjusted_close=5e-324,
    )

    result = validate_provider_rows((raw,), EXPECTED_SESSIONS, INGESTED_AT)

    assert result.canonical_bars == ()
    assert result.rejections[0].code is RejectionCode.INVALID_ADJUSTMENT_FACTOR


def test_zero_range_bar_is_valid_when_all_ohlc_are_equal(
    valid_raw_bar: RawBar,
) -> None:
    raw = replace(
        valid_raw_bar,
        open=100.0,
        high=100.0,
        low=100.0,
        close=100.0,
        adjusted_close=99.0,
    )

    result = validate_provider_rows((raw,), EXPECTED_SESSIONS, INGESTED_AT)

    assert result.rejections == ()
    assert result.canonical_bars[0].adjusted_high == 99.0
    assert result.canonical_bars[0].adjusted_low == 99.0


def test_non_finite_rejection_evidence_is_json_safe(valid_raw_bar: RawBar) -> None:
    raw = replace(valid_raw_bar, close=float("nan"))

    result = validate_provider_rows((raw,), EXPECTED_SESSIONS, INGESTED_AT)

    assert result.rejections[0].raw_evidence["close"] == "NaN"


@pytest.mark.parametrize("adjusted_close", [float("nan"), float("inf")])
def test_non_finite_adjusted_close_has_adjusted_close_rejection_code(
    valid_raw_bar: RawBar,
    adjusted_close: float,
) -> None:
    raw = replace(valid_raw_bar, adjusted_close=adjusted_close)

    result = validate_provider_rows((raw,), EXPECTED_SESSIONS, INGESTED_AT)

    assert result.canonical_bars == ()
    assert result.rejections[0].code is RejectionCode.INVALID_ADJUSTED_CLOSE


@pytest.mark.parametrize(
    "sessions",
    [
        (SESSION, date(2026, 5, 14)),
        (date(2026, 5, 14), date(2026, 5, 14), SESSION),
    ],
)
def test_reference_sessions_must_be_strictly_increasing_and_unique(
    valid_raw_bar: RawBar,
    sessions: tuple[date, ...],
) -> None:
    with pytest.raises(
        ValueError,
        match="expected_sessions must be strictly increasing and unique",
    ):
        validate_provider_rows((valid_raw_bar,), sessions, INGESTED_AT)
