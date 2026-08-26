from __future__ import annotations

from datetime import date, datetime, timezone

from app.domain.market_intelligence.constants import (
    BENCHMARK_SYMBOL,
    LATEST_POINTER_KEY,
    MARKET_INTELLIGENCE_UNIVERSE,
    METRIC_SEMANTICS,
    METRIC_VERSION,
    NORMALIZATION_VERSION,
    PIPELINE_NAME,
    PRICE_BASIS,
    SECTOR_NAMES,
    SECTOR_SYMBOLS,
    UNIVERSE_HASH,
)
from app.domain.market_intelligence.models import (
    BarRejection,
    CanonicalBar,
    IngestionStatus,
    ProviderBatchResult,
    ProviderSymbolFailure,
    RankDirection,
    RankRecord,
    RawBar,
    RejectionCode,
    RequestFailure,
    RunAudit,
    SectorMetrics,
    SectorSnapshot,
    ValidationResult,
)


def test_phase1_universe_is_exact_and_spy_is_not_ranked() -> None:
    assert MARKET_INTELLIGENCE_UNIVERSE == (
        "SPY",
        "XLC",
        "XLY",
        "XLP",
        "XLE",
        "XLF",
        "XLV",
        "XLI",
        "XLB",
        "XLRE",
        "XLK",
        "XLU",
    )
    assert BENCHMARK_SYMBOL == "SPY"
    assert SECTOR_SYMBOLS == MARKET_INTELLIGENCE_UNIVERSE[1:]
    assert set(SECTOR_NAMES) == set(SECTOR_SYMBOLS)
    assert len(set(MARKET_INTELLIGENCE_UNIVERSE)) == 12
    assert BENCHMARK_SYMBOL not in SECTOR_SYMBOLS


def test_phase1_versions_pointer_and_semantics_are_frozen() -> None:
    assert PIPELINE_NAME == "market_intelligence_sectors_us"
    assert METRIC_VERSION == "market_intelligence_v1"
    assert NORMALIZATION_VERSION == "market_intelligence_adjusted_ohlcv_v1"
    assert PRICE_BASIS == "yahoo_adjusted_ohlc_provider_volume"
    assert METRIC_SEMANTICS == "ohlcv_derived_proxy"
    assert LATEST_POINTER_KEY == "latest_market_intelligence_sectors_us"
    assert len(UNIVERSE_HASH) == 64


def test_status_rank_and_rejection_enums_have_stable_values() -> None:
    assert {status.value for status in IngestionStatus} == {
        "SUCCEEDED",
        "PARTIAL",
        "FAILED",
    }
    assert {direction.value for direction in RankDirection} == {
        "IMPROVED",
        "DECLINED",
        "UNCHANGED",
        "NOT_AVAILABLE",
    }
    assert {code.value for code in RejectionCode} == {
        "UNEXPECTED_SYMBOL",
        "MISSING_REQUIRED_FIELD",
        "INVALID_TRADING_DATE",
        "NON_FINITE_VALUE",
        "NON_POSITIVE_PRICE",
        "INVALID_ADJUSTED_CLOSE",
        "INVALID_ADJUSTMENT_FACTOR",
        "INVALID_OHLC_RELATION",
        "NEGATIVE_VOLUME",
        "DUPLICATE_BAR",
    }


def test_raw_and_canonical_contracts_preserve_adjustment_evidence() -> None:
    source_timestamp = datetime(2026, 5, 15, 21, 5, tzinfo=timezone.utc)
    raw = RawBar(
        provider="yahoo",
        provider_symbol="XLK",
        symbol="XLK",
        raw_trading_date="2026-05-15T00:00:00-04:00",
        trading_date=date(2026, 5, 15),
        open=100.0,
        high=110.0,
        low=90.0,
        close=105.0,
        adjusted_close=103.95,
        volume=12_000_000.0,
        source_timestamp=source_timestamp,
    )
    canonical = CanonicalBar(
        provider=raw.provider,
        provider_symbol=raw.provider_symbol,
        symbol=raw.symbol,
        raw_trading_date=raw.raw_trading_date,
        trading_date=raw.trading_date,
        raw_open=raw.open,
        raw_high=raw.high,
        raw_low=raw.low,
        raw_close=raw.close,
        provider_adjusted_close=raw.adjusted_close,
        adjustment_factor=0.99,
        adjusted_open=99.0,
        adjusted_high=108.9,
        adjusted_low=89.1,
        adjusted_close=103.95,
        provider_volume=raw.volume,
        source_timestamp=source_timestamp,
        ingestion_timestamp=source_timestamp,
        price_basis=PRICE_BASIS,
        normalization_version=NORMALIZATION_VERSION,
    )

    assert canonical.raw_close == 105.0
    assert canonical.provider_adjusted_close == 103.95
    assert canonical.adjustment_factor == 0.99
    assert canonical.adjusted_high == 108.9
    assert canonical.provider_volume == 12_000_000.0
    assert canonical.raw_trading_date == raw.raw_trading_date


def test_rejection_and_rank_records_are_explicit() -> None:
    now = datetime(2026, 5, 15, 21, 5, tzinfo=timezone.utc)
    rejection = BarRejection(
        provider="yahoo",
        provider_symbol="XLK",
        symbol="XLK",
        trading_date=date(2026, 5, 15),
        code=RejectionCode.NEGATIVE_VOLUME,
        reason="volume must be non-negative",
        raw_evidence={"Volume": -100},
        ingestion_timestamp=now,
    )
    rank = RankRecord(
        current_rank=2,
        previous_rank=7,
        rank_change=5,
        rank_direction=RankDirection.IMPROVED,
    )

    assert rejection.raw_evidence["Volume"] == -100
    assert rank.rank_change == 5
    assert rank.rank_direction is RankDirection.IMPROVED


def test_provider_batch_distinguishes_request_and_symbol_failures() -> None:
    now = datetime(2026, 5, 15, 21, 5, tzinfo=timezone.utc)
    symbol_failure = ProviderSymbolFailure(
        symbol="XLU",
        code="NO_DATA",
        message="symbol absent from successful batch response",
    )
    partial_batch = ProviderBatchResult(
        provider="yahoo",
        response_timestamp=now,
        rows=(),
        symbol_failures=(symbol_failure,),
        request_failure=None,
    )
    request_failure = RequestFailure(code="PROVIDER_TIMEOUT", message="timeout")
    failed_batch = ProviderBatchResult(
        provider="yahoo",
        response_timestamp=now,
        rows=(),
        symbol_failures=(),
        request_failure=request_failure,
    )

    assert partial_batch.request_failure is None
    assert partial_batch.symbol_failures == (symbol_failure,)
    assert failed_batch.request_failure == request_failure
    assert failed_batch.symbol_failures == ()


def test_snapshot_and_run_audit_contracts_keep_versions_and_health_counts() -> None:
    now = datetime(2026, 5, 15, 21, 5, tzinfo=timezone.utc)
    metrics = SectorMetrics(
        return_1d=0.01,
        return_5d=0.02,
        return_20d=0.03,
        return_60d=0.04,
        relative_return_vs_spy_1d=0.005,
        relative_return_vs_spy_5d=0.006,
        relative_return_vs_spy_20d=0.007,
        relative_return_vs_spy_60d=0.008,
        rvol20=1.5,
        flow_pressure_1d_proxy=0.25,
        cmf_5d_proxy=0.2,
        cmf_20d_proxy=0.15,
        cmf_60d_proxy=0.1,
    )
    snapshot = SectorSnapshot(
        trading_date=date(2026, 5, 15),
        symbol="XLK",
        asset_type="sector_etf",
        sector_name="Technology",
        metrics=metrics,
        ranks={
            "return_1d": RankRecord(
                current_rank=2,
                previous_rank=7,
                rank_change=5,
                rank_direction=RankDirection.IMPROVED,
            )
        },
        provider="yahoo",
        source_freshness={"status": "FRESH", "as_of": "2026-05-15"},
        price_basis=PRICE_BASIS,
        metric_version=METRIC_VERSION,
        calculation_timestamp=now,
        data_quality_status="COMPLETE",
    )
    audit = RunAudit(
        idempotency_key="a" * 64,
        input_hash="b" * 64,
        ingestion_status=IngestionStatus.SUCCEEDED,
        provider="yahoo",
        provider_status="SUCCEEDED",
        request_failure=None,
        metric_version=METRIC_VERSION,
        normalization_version=NORMALIZATION_VERSION,
        price_basis=PRICE_BASIS,
        counters={"expected_symbols": 12, "valid_symbols": 12},
        missing_symbols=(),
        provider_failures=(),
        target_session=date(2026, 5, 15),
        provider_response_at=now,
        source_freshness={"status": "FRESH"},
        calculation_timestamp=now,
        ingestion_timestamp=now,
    )

    assert snapshot.metrics.cmf_20d_proxy == 0.15
    assert snapshot.ranks["return_1d"].rank_change == 5
    assert audit.counters["expected_symbols"] == 12
    assert audit.metric_version == METRIC_VERSION


def test_validation_result_tracks_received_symbols_without_silent_drop() -> None:
    result = ValidationResult(
        canonical_bars=(),
        rejections=(),
        received_symbols=("SPY", "XLK"),
    )

    assert result.received_symbols == ("SPY", "XLK")


def test_golden_scenario_expands_to_raw_like_rows_for_every_symbol_and_session(
    golden_sessions: tuple[date, ...],
    golden_raw_bars: tuple[RawBar, ...],
) -> None:
    assert len(golden_sessions) == 91
    assert golden_sessions[-1] == date(2026, 5, 15)
    assert len(golden_raw_bars) == 91 * 12
    assert {bar.symbol for bar in golden_raw_bars} == set(
        MARKET_INTELLIGENCE_UNIVERSE
    )
    assert all(bar.trading_date in golden_sessions for bar in golden_raw_bars)
    assert all(bar.provider == "fixture_yahoo" for bar in golden_raw_bars)
    assert all(bar.adjusted_close == bar.close * 0.99 for bar in golden_raw_bars)
    assert not any(hasattr(bar, "return_1d") for bar in golden_raw_bars)
