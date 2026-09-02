from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.domain.market_intelligence.mvp import (
    ETF_CATEGORIES,
    ETF_UNIVERSE,
    FLOW_PRESSURE_DISCLOSURE,
    MVP_METRIC_VERSION,
    PULSE_SYMBOLS,
    EtfStrengthItem,
    calculate_price_metrics,
    classify_price_history_quality,
    score_and_rank_etfs,
)
from app.domain.market_intelligence.price_provenance import price_row_content_hash


def test_mvp_fixed_universes_and_version_are_explicit():
    assert PULSE_SYMBOLS == ("SPY", "QQQ", "DIA", "IWM")
    assert MVP_METRIC_VERSION == "market_intelligence_mvp_v1"
    assert len(ETF_UNIVERSE) == 28
    assert len(set(ETF_UNIVERSE)) == len(ETF_UNIVERSE)


def test_etf_categories_match_the_approved_unleveraged_scope():
    assert ETF_CATEGORIES == {
        "broad_market": ("SPY", "QQQ", "IWM", "DIA"),
        "sector": (
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
        ),
        "semiconductor": ("SMH", "SOXX", "XSD"),
        "software": ("IGV",),
        "biotech": ("XBI", "IBB"),
        "defense": ("ITA", "PPA"),
        "energy": ("XLE", "XOP"),
        "metals": ("GDX", "GDXJ", "COPX"),
        "uranium": ("URA",),
    }
    assert "XLE" in ETF_CATEGORIES["sector"]
    assert "XLE" in ETF_CATEGORIES["energy"]

    disallowed_tokens = ("2X", "3X", "ULTRA", "INVERSE", "BEAR")
    assert not any(
        token in symbol
        for symbol in ETF_UNIVERSE
        for token in disallowed_tokens
    )


def test_flow_pressure_copy_discloses_derived_proxy_semantics():
    assert FLOW_PRESSURE_DISCLOSURE == (
        "OHLCV-derived pressure proxy. "
        "Not measured institutional or exchange net flow."
    )


def test_price_provenance_verification_cache_is_bound_to_complete_row_evidence(
    monkeypatch,
):
    import app.domain.market_intelligence.mvp as module

    timestamp = datetime(2026, 8, 31, 21, 0, tzinfo=timezone.utc)
    evidence = {
        "symbol": "SPY",
        "date": date(2026, 8, 31),
        "open": 100.0,
        "high": 101.0,
        "low": 99.0,
        "close": 100.0,
        "volume": 1_000_000,
        "adj_close": 100.0,
        "adjustment_factor": 1.0,
        "dividend_cash": 0.0,
        "split_ratio": 0.0,
        "provider": "yahoo",
        "source_timestamp": timestamp,
        "normalization_version": "canonical_price_adjustment_v2",
    }
    row = SimpleNamespace(
        **evidence,
        price_basis="yahoo_adjusted_close_provider_volume",
        content_hash=price_row_content_hash(evidence),
        revision_number=0,
        reconciled_at=timestamp,
    )
    original = module.price_row_content_hash
    calls = []

    def counted(values):
        calls.append(values)
        return original(values)

    module._verified_price_content_hash.cache_clear()
    monkeypatch.setattr(module, "price_row_content_hash", counted)

    assert classify_price_history_quality([row]) == "corporate_action_adjusted"
    assert classify_price_history_quality([row]) == "corporate_action_adjusted"
    assert len(calls) == 1

    row.high = 102.0
    assert (
        classify_price_history_quality([row])
        == "partial_corporate_action_adjustment"
    )
    assert len(calls) == 2


def test_price_metrics_use_rows_as_sessions_and_exclude_today_from_rvol():
    as_of = date(2026, 8, 26)
    rows = [
        SimpleNamespace(
            date=as_of - timedelta(days=40 - index * 2),
            adj_close=100.0,
            volume=1_000_000,
        )
        for index in range(20)
    ]
    rows.append(
        SimpleNamespace(
            date=as_of,
            adj_close=110.0,
            volume=5_000_000,
        )
    )

    result = calculate_price_metrics(rows, as_of=as_of)

    assert result.return_1d == pytest.approx(0.10)
    assert result.return_20d == pytest.approx(0.10)
    assert result.rvol20 == pytest.approx(5.0)


def test_price_metrics_keep_insufficient_and_zero_denominator_null():
    as_of = date(2026, 8, 26)
    rows = [
        SimpleNamespace(
            date=as_of - timedelta(days=20 - index),
            adj_close=100.0,
            volume=0,
        )
        for index in range(20)
    ]
    rows.append(SimpleNamespace(date=as_of, adj_close=101.0, volume=1_000_000))

    result = calculate_price_metrics(rows, as_of=as_of)

    assert result.return_20d == pytest.approx(0.01)
    assert result.return_60d is None
    assert result.rvol20 is None
    assert result.drawdown_60d is None


def test_etf_strength_score_is_versioned_deterministic_and_null_when_incomplete():
    items = (
        EtfStrengthItem(
            symbol="QQQ",
            categories=("broad_market",),
            available=True,
            return_20d=0.25,
            relative_strength_20d=0.20,
            relative_strength_60d=0.30,
            rvol20=2.0,
            drawdown_60d=0.0,
        ),
        EtfStrengthItem(
            symbol="SPY",
            categories=("broad_market",),
            available=True,
            return_20d=0.10,
            relative_strength_20d=0.0,
            relative_strength_60d=0.0,
            rvol20=1.0,
            drawdown_60d=-0.10,
        ),
        EtfStrengthItem(
            symbol="IWM",
            categories=("broad_market",),
            available=True,
            return_20d=None,
        ),
    )

    first = score_and_rank_etfs(items)
    second = score_and_rank_etfs(items)
    by_symbol = {item.symbol: item for item in first}

    assert first == second
    assert by_symbol["QQQ"].strength_score == pytest.approx(100.0)
    assert by_symbol["SPY"].strength_score == pytest.approx(50.0)
    assert by_symbol["QQQ"].overall_rank == 1
    assert by_symbol["SPY"].overall_rank == 2
    assert by_symbol["IWM"].strength_score is None
    assert by_symbol["IWM"].overall_rank is None


def test_etf_strength_equal_scores_use_symbol_as_ordinal_tiebreaker():
    template = {
        "categories": ("semiconductor",),
        "available": True,
        "return_20d": 0.10,
        "relative_strength_20d": 0.05,
        "relative_strength_60d": 0.05,
        "rvol20": 1.5,
        "drawdown_60d": -0.05,
    }

    ranked = score_and_rank_etfs(
        (
            EtfStrengthItem(symbol="XSD", **template),
            EtfStrengthItem(symbol="SMH", **template),
        )
    )
    by_symbol = {item.symbol: item for item in ranked}

    assert by_symbol["SMH"].strength_score == by_symbol["XSD"].strength_score
    assert by_symbol["SMH"].overall_rank == 1
    assert by_symbol["XSD"].overall_rank == 2
    assert by_symbol["SMH"].category_ranks == {"semiconductor": 1}
    assert by_symbol["XSD"].category_ranks == {"semiconductor": 2}
