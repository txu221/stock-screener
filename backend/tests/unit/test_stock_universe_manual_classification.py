from app.models.stock_universe import StockUniverse
from app.services.stock_universe_service import StockUniverseService


def test_manual_add_can_explicitly_confirm_existing_common_stock(db_session):
    service = StockUniverseService()
    assert service.add_manual_symbol(db_session, "AAA", "Example") is True
    assert db_session.query(StockUniverse).filter_by(symbol="AAA").one().is_common_stock is False

    assert service.add_manual_symbol(
        db_session,
        "AAA",
        "Example",
        is_common_stock=True,
    ) is True

    assert db_session.query(StockUniverse).filter_by(symbol="AAA").one().is_common_stock is True


def test_manual_add_omitted_classification_preserves_existing_value(db_session):
    db_session.add(
        StockUniverse(
            symbol="AAA",
            market="US",
            is_active=True,
            is_common_stock=True,
            source="finviz",
        )
    )
    db_session.commit()

    service = StockUniverseService()
    assert service.add_manual_symbol(db_session, "AAA", "Example") is True
    assert db_session.query(StockUniverse).filter_by(symbol="AAA").one().is_common_stock is True

    assert service.add_manual_symbol(
        db_session,
        "AAA",
        "Example",
        is_common_stock=False,
    ) is True
    assert db_session.query(StockUniverse).filter_by(symbol="AAA").one().is_common_stock is False
