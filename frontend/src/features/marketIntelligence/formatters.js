export const formatPercent = (value, digits = 2) => (
  value == null || !Number.isFinite(Number(value))
    ? '—'
    : `${Number(value) >= 0 ? '+' : ''}${(Number(value) * 100).toFixed(digits)}%`
);

export const formatPrice = (value) => (
  value == null || !Number.isFinite(Number(value))
    ? '—'
    : Number(value).toLocaleString(undefined, { style: 'currency', currency: 'USD' })
);

export const formatNumber = (value, digits = 2) => (
  value == null || !Number.isFinite(Number(value))
    ? '—'
    : Number(value).toFixed(digits)
);

export const formatCompact = (value) => (
  value == null || !Number.isFinite(Number(value))
    ? '—'
    : Intl.NumberFormat(undefined, { notation: 'compact', maximumFractionDigits: 1 }).format(Number(value))
);

export const directionLabel = (value) => {
  if (value == null || !Number.isFinite(Number(value))) return 'Unavailable';
  if (Number(value) > 0) return '↑ UP';
  if (Number(value) < 0) return '↓ DOWN';
  return '→ UNCHANGED';
};

