from __future__ import annotations

import pickle
from datetime import date
from types import SimpleNamespace

import pandas as pd

from app.models.stock import StockPrice
from app.services.price_cache_service import PriceCacheService


def _price_frame(closes: list[float], days: list[date]) -> pd.DataFrame:
    data = pd.DataFrame(
        {
            "Open": closes,
            "High": closes,
            "Low": closes,
            "Close": closes,
            "Adj Close": closes,
            "Volume": [1_000_000] * len(closes),
        },
        index=pd.to_datetime(days),
    )
    data.index.name = "Date"
    return data


def test_cached_only_fresh_can_return_structurally_valid_short_history():
    rows = [
        SimpleNamespace(
            symbol="NEW",
            date=row_date,
            open=close,
            high=close,
            low=close,
            close=close,
            adj_close=close,
            volume=1_000_000,
        )
        for row_date, close in (
            (date(2026, 8, 25), 100.0),
            (date(2026, 8, 26), 101.0),
        )
    ]

    class FakeQuery:
        def filter(self, *_args):
            return self

        def order_by(self, *_args):
            return self

        def all(self):
            return rows

    class FakeSession:
        def query(self, *_args):
            return FakeQuery()

        def close(self):
            pass

    service = PriceCacheService(redis_client=None, session_factory=FakeSession)

    result = service.get_many_cached_only_fresh(
        ["NEW"],
        required_as_of_date=date(2026, 8, 26),
        minimum_rows=1,
    )

    assert result["NEW"] is not None
    assert result["NEW"]["Adj Close"].tolist() == [100.0, 101.0]


def test_store_batch_in_cache_skips_non_finite_close_rows():
    captured_rows = []

    class FakeQuery:
        def filter(self, *_args):
            return self

        def all(self):
            return []

    class FakeSession:
        def query(self, *_args):
            return FakeQuery()

        def add(self, row):
            if isinstance(row, StockPrice):
                captured_rows.append(row)

        def flush(self):
            pass

        def commit(self):
            pass

        def rollback(self):
            pass

        def close(self):
            pass

    service = PriceCacheService(redis_client=None, session_factory=FakeSession)
    payload = _price_frame(
        [101.0, float("nan")],
        [date(2026, 6, 25), date(2026, 6, 26)],
    )

    service.store_batch_in_cache({"SPY": payload}, also_store_db=True)

    assert [(row.symbol, row.date, row.close) for row in captured_rows] == [
        ("SPY", date(2026, 6, 25), 101.0)
    ]
    assert captured_rows[0].provider is None
    assert captured_rows[0].price_basis == "raw_ohlcv_unreconciled"


def test_direct_cn_and_krx_fallbacks_are_labeled_as_yahoo_only_after_yahoo_fetch():
    service = PriceCacheService(redis_client=None, session_factory=lambda: None)
    data = _price_frame([101.0], [date(2026, 6, 25)])
    service._fetch_kr_historical_data = lambda symbol, *, period: None  # type: ignore[assignment]
    service._fetch_cn_historical_data = lambda symbol, *, period: None  # type: ignore[assignment]
    service._fetch_yahoo_historical_data = lambda symbol, *, period: data  # type: ignore[assignment]

    cn_data, cn_provider = service._fetch_direct_historical_data_with_provider("000001.SS", period="2y")
    krx_data, krx_provider = service._fetch_direct_historical_data_with_provider("005930.KS", period="2y")

    assert cn_data is data
    assert krx_data is data
    assert cn_provider == "yahoo"
    assert krx_provider == "yahoo"


def test_fetch_full_and_cache_uses_cleaned_price_frame_for_redis_db_and_return():
    service = PriceCacheService(redis_client=None, session_factory=lambda: None)
    raw = _price_frame(
        [101.0, float("nan")],
        [date(2026, 6, 25), date(2026, 6, 26)],
    )
    captured = {}

    service._fetch_direct_historical_data_with_provider = lambda symbol, period: (raw, "yahoo")  # type: ignore[assignment]
    service._store_recent_in_redis = lambda symbol, data, market=None: captured.setdefault("redis", data)  # type: ignore[assignment]
    service._store_in_database = lambda symbol, data, **kwargs: captured.setdefault("db", data)  # type: ignore[assignment]

    result = service._fetch_full_and_cache("SPY", "2y")

    assert result is not None
    assert result["Close"].tolist() == [101.0]
    assert captured["redis"]["Close"].tolist() == [101.0]
    assert captured["db"]["Close"].tolist() == [101.0]


def test_incremental_merge_uses_cleaned_price_frame_for_redis_db_and_return():
    service = PriceCacheService(redis_client=None, session_factory=lambda: None)
    cached = _price_frame([100.0], [date(2026, 6, 24)])
    raw_incremental = _price_frame(
        [101.0, float("nan")],
        [date(2026, 6, 25), date(2026, 6, 26)],
    )
    captured = {}

    service._fetch_direct_historical_data_with_provider = lambda symbol, period: (raw_incremental, "yahoo")  # type: ignore[assignment]
    service._store_recent_in_redis = lambda symbol, data, market=None: captured.setdefault("redis", data)  # type: ignore[assignment]
    service._store_in_database = lambda symbol, data, **kwargs: captured.setdefault("db", data)  # type: ignore[assignment]

    result = service._fetch_incremental_and_merge(
        "SPY",
        "2y",
        cached,
        date(2026, 6, 24),
    )

    assert result is not None
    assert result["Close"].tolist() == [100.0, 101.0]
    assert captured["redis"]["Close"].tolist() == [100.0, 101.0]
    assert captured["db"]["Close"].tolist() == [101.0]


def test_get_many_falls_back_to_db_when_redis_payload_normalizes_away():
    class FakePipeline:
        def get(self, _key):
            return self

        def execute(self):
            poisoned = _price_frame([float("nan")], [date(2026, 6, 24)])
            return [pickle.dumps(poisoned), None]

    class FakeRedis:
        def pipeline(self):
            return FakePipeline()

    service = PriceCacheService(redis_client=FakeRedis(), session_factory=lambda: None)
    db_frame = _price_frame([100.0, 101.0], [date(2026, 6, 23), date(2026, 6, 24)])
    fallback_calls = []

    service._get_expected_data_date = lambda: date(2026, 6, 24)  # type: ignore[assignment]
    service._get_many_from_database = lambda symbols, period: {  # type: ignore[assignment]
        symbol: (db_frame, date(2026, 6, 24)) for symbol in symbols
    }
    service._active_market_by_symbol = lambda symbols: {symbol: "US" for symbol in symbols}  # type: ignore[assignment]
    service._store_recent_in_redis = lambda symbol, data, market=None: None  # type: ignore[assignment]

    original_resolve = service._resolve_bulk_fallback

    def record_fallback(symbols, **kwargs):
        fallback_calls.append(list(symbols))
        return original_resolve(symbols, **kwargs)

    service._resolve_bulk_fallback = record_fallback  # type: ignore[assignment]

    result = service.get_many(["SPY"], period="2y")

    assert fallback_calls == [["SPY"]]
    assert result["SPY"] is db_frame
