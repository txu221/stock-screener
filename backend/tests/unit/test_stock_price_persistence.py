from __future__ import annotations

from datetime import date, datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models.stock import StockPrice, StockPriceRevision
from app.services.price_row_normalization import stock_price_row_from_ohlcv
from app.services.stock_price_persistence import persist_stock_price_mappings


DAY = date(2026, 6, 24)
SOURCE_TIMESTAMP = datetime(2026, 8, 28, 16, tzinfo=timezone.utc)


def _session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autocommit=False, autoflush=False)()


def _mapping(
    *,
    adjusted_close: float = 50.0,
    source_timestamp: datetime = SOURCE_TIMESTAMP,
):
    result = stock_price_row_from_ohlcv(
        symbol="SPLT",
        row_date=DAY,
        row={
            "Open": 100.0,
            "High": 102.0,
            "Low": 98.0,
            "Close": 100.0,
            "Adj Close": adjusted_close,
            "Volume": 1_000_000,
            "Dividends": 0.0,
            "Stock Splits": 2.0,
        },
        provider="yahoo",
        source_timestamp=source_timestamp,
        normalization_version="canonical_price_adjustment_v2",
        reconciled_at=source_timestamp,
    )
    assert result is not None
    return result


def test_first_reconciliation_captures_legacy_revision_zero_before_current_revision():
    db = _session()
    db.add(
        StockPrice(
            symbol="SPLT", date=DAY, open=100.0, high=102.0, low=98.0,
            close=100.0, adj_close=100.0, volume=1_000_000,
        )
    )
    db.commit()

    result = persist_stock_price_mappings(db, {"SPLT": [_mapping()]})
    db.commit()

    current = db.query(StockPrice).one()
    revisions = db.query(StockPriceRevision).order_by(StockPriceRevision.revision_number).all()
    assert result == {"inserted": 0, "updated": 1}
    assert current.revision_number == 1
    assert current.adj_close == 50.0
    assert [(revision.revision_number, revision.normalization_version) for revision in revisions] == [
        (0, "legacy_unversioned"),
        (1, "canonical_price_adjustment_v2"),
    ]


def test_identical_replay_is_idempotent_and_does_not_append_a_revision():
    db = _session()
    first = persist_stock_price_mappings(db, {"SPLT": [_mapping()]})
    db.commit()
    replay = persist_stock_price_mappings(db, {"SPLT": [_mapping()]})
    db.commit()

    assert first == {"inserted": 1, "updated": 0}
    assert replay == {"inserted": 0, "updated": 0}
    assert db.query(StockPriceRevision).count() == 1
    assert db.query(StockPrice).one().revision_number == 0


def test_changed_provider_history_appends_revision_and_updates_current_materialization():
    db = _session()
    persist_stock_price_mappings(db, {"SPLT": [_mapping(adjusted_close=50.0)]})
    db.commit()

    result = persist_stock_price_mappings(db, {"SPLT": [_mapping(adjusted_close=52.0)]})
    db.commit()

    current = db.query(StockPrice).one()
    revisions = db.query(StockPriceRevision).order_by(StockPriceRevision.revision_number).all()
    assert result == {"inserted": 0, "updated": 1}
    assert current.adj_close == 52.0
    assert current.revision_number == 1
    assert [revision.revision_number for revision in revisions] == [0, 1]
    assert revisions[0].content_hash != revisions[1].content_hash


def test_changed_source_timestamp_appends_revision_and_updates_current_materialization():
    db = _session()
    first = _mapping()
    second = _mapping(source_timestamp=datetime(2026, 8, 29, 16, tzinfo=timezone.utc))
    persist_stock_price_mappings(db, {"SPLT": [first]})
    db.commit()

    result = persist_stock_price_mappings(db, {"SPLT": [second]})
    db.commit()

    current = db.query(StockPrice).one()
    assert result == {"inserted": 0, "updated": 1}
    assert current.revision_number == 1
    assert current.source_timestamp == second["source_timestamp"].replace(tzinfo=None)
    assert db.query(StockPriceRevision).count() == 2
