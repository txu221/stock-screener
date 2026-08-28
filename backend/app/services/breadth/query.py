"""Revision-aware read helpers for canonical market breadth rows."""

from __future__ import annotations

from datetime import date

from sqlalchemy.orm import Query, Session

from app.models.market_breadth import MarketBreadth

from .types import CURRENT_BREADTH_CALCULATION_REVISION


def breadth_query(db: Session, *, market: str) -> Query:
    """Return the market partition containing only canonical revision rows."""
    return db.query(MarketBreadth).filter(
        MarketBreadth.market == market.upper(),
        MarketBreadth.calculation_revision
        == CURRENT_BREADTH_CALCULATION_REVISION,
    )


def current_breadth_query(
    db: Session,
    *,
    market: str,
    as_of_date: date | None = None,
) -> Query:
    query = breadth_query(db, market=market)
    if as_of_date is not None:
        query = query.filter(MarketBreadth.date <= as_of_date)
    return query.order_by(MarketBreadth.date.desc())


def latest_breadth(
    db: Session,
    *,
    market: str,
    as_of_date: date | None = None,
) -> MarketBreadth | None:
    return current_breadth_query(
        db,
        market=market,
        as_of_date=as_of_date,
    ).first()
