"""Service for attributing daily breadth movers (4% up/down) to IBD industry groups.

Given the symbol universe for a market and their cached price histories, this
service classifies each ±4% daily mover into its IBD industry group (US) so the
breadth UI can show "what is driving the breadth" per session. Symbols with no
IBD group assignment fall into the synthetic "No Group" bucket.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping
from datetime import date
from typing import Any

import pandas as pd

from app.services.breadth.formulas import prepare_feature_frame, signal_flags_at
from app.services.breadth.types import BreadthFormulaPolicy

logger = logging.getLogger(__name__)


NO_GROUP_LABEL = "No Group"


class BreadthAttributionService:
    """Compute per-date IBD-group attribution for ±4% daily movers."""

    def compute(
        self,
        *,
        symbols_meta: Iterable[Mapping[str, Any]],
        price_data: Mapping[str, pd.DataFrame | None],
        target_dates: Iterable[date],
        currencies_by_symbol: Mapping[str, str] | None = None,
        fx_by_currency: Mapping[str, pd.Series] | None = None,
        symbols_by_date: Mapping[date, Iterable[str]] | None = None,
        policy: BreadthFormulaPolicy | None = None,
    ) -> list[dict[str, Any]]:
        """Return attribution rows ordered oldest → newest.

        Args:
            symbols_meta: Iterable of dicts with at minimum ``symbol``; optional
                ``company_name`` and ``ibd_industry_group``.
            price_data: ``{symbol: DataFrame}`` of cached price history (Close
                required, datetime index). Missing/empty frames are skipped.
            target_dates: Trading dates to attribute. Each yields one entry in
                the returned list.

        Each entry shape::

            {
                "date": "YYYY-MM-DD",
                "stocks_up_4pct": int,
                "stocks_down_4pct": int,
                "groups": [
                    {
                        "group": str,
                        "up_count": int,
                        "down_count": int,
                        "net": int,                # up - down
                        "up_stocks":   [{symbol, name, pct_change, close}, ...],
                        "down_stocks": [{symbol, name, pct_change, close}, ...],
                    },
                    ...
                ],
            }
        """
        meta_by_symbol: dict[str, Mapping[str, Any]] = {}
        for entry in symbols_meta:
            if not entry:
                continue
            symbol = entry.get("symbol")
            if not symbol:
                continue
            meta_by_symbol[str(symbol).upper()] = entry

        ordered_dates = sorted({d for d in target_dates if d is not None})
        if not ordered_dates or not meta_by_symbol:
            return []

        formula_policy = policy or BreadthFormulaPolicy()
        per_date: dict[date, dict[str, dict[str, Any]]] = {d: {} for d in ordered_dates}
        normalized_symbols_by_date = (
            {
                calculation_date: frozenset(
                    str(symbol).upper() for symbol in symbols
                )
                for calculation_date, symbols in symbols_by_date.items()
            }
            if symbols_by_date is not None
            else None
        )

        for symbol, meta in meta_by_symbol.items():
            history = price_data.get(symbol)
            if history is None or getattr(history, "empty", True):
                continue
            currency = str((currencies_by_symbol or {}).get(symbol) or "USD").upper()
            fx = (fx_by_currency or {}).get(currency)
            if fx is None:
                if currency != "USD":
                    raise ValueError(f"Missing historical FX series for {currency}")
                fx = pd.Series(1.0, index=history.index)
            rates_by_date = {
                pd.Timestamp(value).date(): float(rate)
                for value, rate in fx.items()
            }
            aligned_fx = pd.Series(
                (
                    rates_by_date.get(pd.Timestamp(value).date())
                    for value in history.index
                ),
                index=history.index,
                dtype=float,
            )
            if aligned_fx.isna().any():
                missing_date = pd.Timestamp(
                    aligned_fx[aligned_fx.isna()].index[0]
                ).date()
                raise ValueError(
                    f"Missing historical {currency}->USD FX for "
                    f"{missing_date.isoformat()}"
                )
            try:
                features = prepare_feature_frame(
                    history,
                    aligned_fx,
                    atr_period=formula_policy.atr_period,
                )
            except ValueError as exc:
                logger.warning(
                    "Skipping malformed breadth attribution prices for %s: %s",
                    symbol,
                    exc,
                )
                continue

            group = self._resolve_group(meta.get("ibd_industry_group"))
            name = meta.get("company_name") or meta.get("name")

            for d in ordered_dates:
                if (
                    normalized_symbols_by_date is not None
                    and symbol not in normalized_symbols_by_date.get(d, frozenset())
                ):
                    continue
                flags = signal_flags_at(features, d, formula_policy)
                direction = (
                    "up"
                    if flags.up_4pct
                    else "down"
                    if flags.down_4pct
                    else None
                )
                if direction is None:
                    continue
                row = features.loc[pd.Timestamp(d)]
                pct_val = float(row.daily_return) * 100.0
                stock_entry = {
                    "symbol": symbol,
                    "name": name,
                    "pct_change": round(pct_val, 2),
                    "close": round(float(row.raw_close), 2),
                }

                bucket = per_date[d].setdefault(
                    group,
                    {"up_stocks": [], "down_stocks": []},
                )
                bucket[f"{direction}_stocks"].append(stock_entry)

        results: list[dict[str, Any]] = []
        for d in ordered_dates:
            groups_for_day = per_date[d]
            groups_payload: list[dict[str, Any]] = []
            for group_name, bucket in groups_for_day.items():
                up_stocks = sorted(
                    bucket["up_stocks"],
                    key=lambda row: row["pct_change"],
                    reverse=True,
                )
                down_stocks = sorted(
                    bucket["down_stocks"],
                    key=lambda row: row["pct_change"],
                )
                up_count = len(up_stocks)
                down_count = len(down_stocks)
                if up_count == 0 and down_count == 0:
                    continue
                groups_payload.append(
                    {
                        "group": group_name,
                        "up_count": up_count,
                        "down_count": down_count,
                        "net": up_count - down_count,
                        "up_stocks": up_stocks,
                        "down_stocks": down_stocks,
                    }
                )

            # Sort by total activity descending, then net descending, then name.
            groups_payload.sort(
                key=lambda row: (
                    -(row["up_count"] + row["down_count"]),
                    -row["net"],
                    row["group"],
                )
            )
            total_up = sum(row["up_count"] for row in groups_payload)
            total_down = sum(row["down_count"] for row in groups_payload)
            results.append(
                {
                    "date": d.isoformat(),
                    "stocks_up_4pct": total_up,
                    "stocks_down_4pct": total_down,
                    "groups": groups_payload,
                }
            )

        return results

    @staticmethod
    def _resolve_group(raw_group: Any) -> str:
        if raw_group is None:
            return NO_GROUP_LABEL
        text = str(raw_group).strip()
        if not text:
            return NO_GROUP_LABEL
        return text
