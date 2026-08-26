"""Pure completed-session metrics for the Phase 1 sector universe."""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import replace
from datetime import date

from .models import CanonicalBar, SectorMetrics

_CANONICAL_NUMERIC_FIELDS = (
    "adjusted_open",
    "adjusted_high",
    "adjusted_low",
    "adjusted_close",
    "provider_volume",
)


def _unavailable_metrics() -> SectorMetrics:
    return SectorMetrics(
        return_1d=None,
        return_5d=None,
        return_20d=None,
        return_60d=None,
        relative_return_vs_spy_1d=None,
        relative_return_vs_spy_5d=None,
        relative_return_vs_spy_20d=None,
        relative_return_vs_spy_60d=None,
        rvol20=None,
        flow_pressure_1d_proxy=None,
        cmf_5d_proxy=None,
        cmf_20d_proxy=None,
        cmf_60d_proxy=None,
    )


def _valid_bar(bar: CanonicalBar) -> bool:
    values = [float(getattr(bar, field)) for field in _CANONICAL_NUMERIC_FIELDS]
    if not all(math.isfinite(value) for value in values):
        return False
    return (
        bar.adjusted_open > 0
        and bar.adjusted_high > 0
        and bar.adjusted_low > 0
        and bar.adjusted_close > 0
        and bar.provider_volume >= 0
        and bar.adjusted_high >= bar.adjusted_low
        and bar.adjusted_high >= bar.adjusted_open
        and bar.adjusted_high >= bar.adjusted_close
        and bar.adjusted_low <= bar.adjusted_open
        and bar.adjusted_low <= bar.adjusted_close
    )


def _return_at(
    bars_by_date: Mapping[date, CanonicalBar],
    sessions: Sequence[date],
    offset: int,
) -> float | None:
    if len(sessions) <= offset:
        return None
    current = bars_by_date.get(sessions[-1])
    anchor = bars_by_date.get(sessions[-1 - offset])
    if current is None or anchor is None or anchor.adjusted_close <= 0:
        return None
    result = current.adjusted_close / anchor.adjusted_close - 1.0
    return result if math.isfinite(result) else None


def _window(
    bars_by_date: Mapping[date, CanonicalBar],
    sessions: Sequence[date],
    count: int,
) -> tuple[CanonicalBar, ...] | None:
    if len(sessions) < count:
        return None
    dates = sessions[-count:]
    bars = tuple(bars_by_date.get(session) for session in dates)
    if any(bar is None for bar in bars):
        return None
    return tuple(bar for bar in bars if bar is not None)


def _mfm(bar: CanonicalBar) -> float:
    spread = bar.adjusted_high - bar.adjusted_low
    if spread == 0:
        return 0.0
    return (
        2.0 * bar.adjusted_close - bar.adjusted_high - bar.adjusted_low
    ) / spread


def _cmf(window: tuple[CanonicalBar, ...] | None) -> float | None:
    if window is None:
        return None
    denominator = sum(bar.provider_volume for bar in window)
    if denominator == 0:
        return None
    result = sum(_mfm(bar) * bar.provider_volume for bar in window) / denominator
    return result if math.isfinite(result) else None


def _rvol20(
    bars_by_date: Mapping[date, CanonicalBar],
    sessions: Sequence[date],
) -> float | None:
    window = _window(bars_by_date, sessions, 21)
    if window is None:
        return None
    previous = window[:-1]
    historical_average = sum(bar.provider_volume for bar in previous) / 20.0
    if historical_average == 0:
        return None
    result = window[-1].provider_volume / historical_average
    return result if math.isfinite(result) else None


def calculate_symbol_metrics(
    bars: Sequence[CanonicalBar],
    sessions: Sequence[date],
) -> SectorMetrics:
    """Calculate metrics from exact completed-session anchors."""
    ordered_sessions = tuple(sessions)
    if any(
        current >= following
        for current, following in zip(ordered_sessions, ordered_sessions[1:])
    ):
        return _unavailable_metrics()
    key_counts = Counter(bar.trading_date for bar in bars)
    if any(count > 1 for count in key_counts.values()):
        return _unavailable_metrics()
    if not all(_valid_bar(bar) for bar in bars):
        return _unavailable_metrics()

    bars_by_date = {bar.trading_date: bar for bar in bars}
    current_bar = bars_by_date.get(ordered_sessions[-1]) if ordered_sessions else None
    return SectorMetrics(
        return_1d=_return_at(bars_by_date, ordered_sessions, 1),
        return_5d=_return_at(bars_by_date, ordered_sessions, 5),
        return_20d=_return_at(bars_by_date, ordered_sessions, 20),
        return_60d=_return_at(bars_by_date, ordered_sessions, 60),
        relative_return_vs_spy_1d=None,
        relative_return_vs_spy_5d=None,
        relative_return_vs_spy_20d=None,
        relative_return_vs_spy_60d=None,
        rvol20=_rvol20(bars_by_date, ordered_sessions),
        flow_pressure_1d_proxy=(
            _mfm(current_bar) if current_bar is not None else None
        ),
        cmf_5d_proxy=_cmf(_window(bars_by_date, ordered_sessions, 5)),
        cmf_20d_proxy=_cmf(_window(bars_by_date, ordered_sessions, 20)),
        cmf_60d_proxy=_cmf(_window(bars_by_date, ordered_sessions, 60)),
    )


def _relative(sector: float | None, spy: float | None) -> float | None:
    if sector is None or spy is None:
        return None
    result = sector - spy
    return result if math.isfinite(result) else None


def with_relative_returns(
    sector_metrics: SectorMetrics,
    spy_metrics: SectorMetrics,
) -> SectorMetrics:
    """Return sector metrics with descriptive relative returns versus SPY."""
    return replace(
        sector_metrics,
        relative_return_vs_spy_1d=_relative(
            sector_metrics.return_1d, spy_metrics.return_1d
        ),
        relative_return_vs_spy_5d=_relative(
            sector_metrics.return_5d, spy_metrics.return_5d
        ),
        relative_return_vs_spy_20d=_relative(
            sector_metrics.return_20d, spy_metrics.return_20d
        ),
        relative_return_vs_spy_60d=_relative(
            sector_metrics.return_60d, spy_metrics.return_60d
        ),
    )
