"""Shared transactional persistence for normalized stock price rows."""

from __future__ import annotations

from datetime import date
from typing import Any, Mapping, Sequence

from sqlalchemy.orm import Session

from app.models.stock import StockPrice, StockPriceRevision
from app.services.price_row_normalization import (
    RECONCILED_PRICE_BASIS,
    price_row_content_hash,
)


def persist_stock_price_mappings(
    db: Session,
    price_rows_by_symbol: Mapping[str, Sequence[Mapping[str, Any]]],
    *,
    chunk_size: int = 100,
) -> dict[str, int]:
    """Persist current price rows while retaining every distinct provider revision."""
    candidates = [
        dict(row)
        for rows in price_rows_by_symbol.values()
        for row in rows
        if isinstance(row.get("date"), date)
    ]
    if not candidates:
        return {"inserted": 0, "updated": 0}
    if chunk_size < 1:
        raise ValueError("chunk_size must be positive")

    pairs = {(row["symbol"], row["date"]) for row in candidates}
    symbols = sorted({symbol for symbol, _ in pairs})
    current_by_pair: dict[tuple[str, date], StockPrice] = {}
    revisions_by_pair: dict[tuple[str, date], list[StockPriceRevision]] = {}
    for chunk_start in range(0, len(symbols), chunk_size):
        chunk_symbols = symbols[chunk_start:chunk_start + chunk_size]
        chunk_pairs = {pair for pair in pairs if pair[0] in chunk_symbols}
        min_date = min(row_date for _, row_date in chunk_pairs)
        max_date = max(row_date for _, row_date in chunk_pairs)
        existing_rows = (
            db.query(StockPrice)
            .filter(
                StockPrice.symbol.in_(chunk_symbols),
                StockPrice.date >= min_date,
                StockPrice.date <= max_date,
            )
            .all()
        )
        for row in existing_rows:
            pair = (row.symbol, row.date)
            if pair in chunk_pairs:
                current_by_pair[pair] = row
        existing_revisions = (
            db.query(StockPriceRevision)
            .filter(
                StockPriceRevision.symbol.in_(chunk_symbols),
                StockPriceRevision.date >= min_date,
                StockPriceRevision.date <= max_date,
            )
            .all()
        )
        for revision in existing_revisions:
            pair = (revision.symbol, revision.date)
            if pair in chunk_pairs:
                revisions_by_pair.setdefault(pair, []).append(revision)

    inserted = 0
    updated = 0
    pending_revision_rows: list[tuple[StockPriceRevision, StockPrice]] = []
    for incoming in candidates:
        pair = (incoming["symbol"], incoming["date"])
        incoming["content_hash"] = incoming.get("content_hash") or price_row_content_hash(incoming)
        current = current_by_pair.get(pair)
        prior_revisions = revisions_by_pair.setdefault(pair, [])
        if (
            current is not None
            and current.price_basis == RECONCILED_PRICE_BASIS
            and incoming.get("price_basis") != RECONCILED_PRICE_BASIS
        ):
            # A provider-less/native refresh is not enough evidence to replace a
            # reconciled analytical row. Preserve the stable materialization
            # until an equally proven revision arrives.
            continue
        known_hashes = {revision.content_hash for revision in prior_revisions if revision.content_hash}
        if current is not None and current.content_hash:
            known_hashes.add(current.content_hash)
        if incoming["content_hash"] in known_hashes:
            continue

        if current is None:
            revision_number = (
                max(revision.revision_number for revision in prior_revisions) + 1
                if prior_revisions
                else 0
            )
            incoming["revision_number"] = revision_number
            current = StockPrice(**incoming)
            db.add(current)
            current_by_pair[pair] = current
            inserted += 1
        else:
            if not prior_revisions and not current.content_hash:
                legacy = _legacy_revision_mapping(current)
                legacy["stock_price_id"] = current.id
                legacy_revision = StockPriceRevision(**legacy)
                db.add(legacy_revision)
                prior_revisions.append(legacy_revision)
                revision_number = 1
            else:
                revision_number = max(
                    [revision.revision_number for revision in prior_revisions]
                    + ([current.revision_number] if current.revision_number is not None else [-1])
                ) + 1
            incoming["revision_number"] = revision_number
            for field, value in incoming.items():
                setattr(current, field, value)
            updated += 1

        revision = StockPriceRevision(
            **_revision_mapping(incoming, stock_price_id=current.id)
        )
        prior_revisions.append(revision)
        pending_revision_rows.append((revision, current))

    db.flush()
    for revision, current in pending_revision_rows:
        revision.stock_price_id = current.id
        db.add(revision)
    db.flush()
    return {"inserted": inserted, "updated": updated}


def _revision_mapping(row: Mapping[str, Any], *, stock_price_id: int | None) -> dict[str, Any]:
    fields = (
        "symbol", "date", "revision_number", "open", "high", "low", "close", "volume",
        "adj_close", "adjustment_factor", "dividend_cash", "split_ratio", "provider",
        "source_timestamp", "normalization_version", "price_basis", "content_hash",
    )
    return {"stock_price_id": stock_price_id, **{field: row.get(field) for field in fields}}


def _legacy_revision_mapping(current: StockPrice) -> dict[str, Any]:
    legacy = {
        "symbol": current.symbol,
        "date": current.date,
        "revision_number": 0,
        "open": current.open,
        "high": current.high,
        "low": current.low,
        "close": current.close,
        "volume": current.volume,
        "adj_close": current.adj_close,
        "adjustment_factor": current.adjustment_factor,
        "dividend_cash": current.dividend_cash,
        "split_ratio": current.split_ratio,
        "provider": current.provider,
        "source_timestamp": current.source_timestamp,
        "normalization_version": "legacy_unversioned",
        "price_basis": current.price_basis or "legacy_unversioned",
    }
    legacy["content_hash"] = price_row_content_hash(legacy)
    return legacy
