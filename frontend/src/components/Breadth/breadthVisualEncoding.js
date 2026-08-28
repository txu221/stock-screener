import { breadthMetricDefinitions } from './breadthMetricDefinitions';


export const BREADTH_VISUAL_COLORS = {
  neutral: '#10151d',
  'up-soft': '#123d2a',
  'up-strong': '#0d7a3e',
  'down-soft': '#452126',
  'down-strong': '#9b1c31',
  newHigh: '#2f80d1',
  newLow: '#d99016',
  unchanged: '#4b5563',
};

const upMetrics = new Set([
  'stocks_up_4pct',
  'stocks_up_25pct_quarter',
  'stocks_up_25pct_month',
  'stocks_up_50pct_month',
  'stocks_up_13pct_34days',
]);

const downMetrics = new Set([
  'stocks_down_4pct',
  'stocks_down_25pct_quarter',
  'stocks_down_25pct_month',
  'stocks_down_50pct_month',
  'stocks_down_13pct_34days',
]);

const ratioMetrics = new Set(['ratio_5day', 'ratio_10day']);

const quantile = (sortedValues, percentile) => {
  const position = (sortedValues.length - 1) * percentile;
  const lowerIndex = Math.floor(position);
  const upperIndex = Math.ceil(position);
  const lower = sortedValues[lowerIndex];
  const upper = sortedValues[upperIndex];
  return lower + ((upper - lower) * (position - lowerIndex));
};

const comparableValue = (row, metric) => {
  const rawValue = row?.[metric];
  if (rawValue == null) return null;
  const value = Number(rawValue);
  if (!Number.isFinite(value)) return null;
  if (ratioMetrics.has(metric)) return value;

  const eligibleField = breadthMetricDefinitions[metric]?.eligibleField;
  const rawEligible = eligibleField ? row?.[eligibleField] : null;
  if (rawEligible == null) return value;

  const eligible = Number(rawEligible);
  if (!Number.isFinite(eligible) || eligible <= 0) return null;
  return value / eligible;
};

export const buildDirectionalToneThresholds = (rows) => {
  const thresholds = {};
  [...upMetrics, ...downMetrics].forEach((metric) => {
    const values = rows
      .map((row) => comparableValue(row, metric))
      .filter((value) => value != null && value > 0)
      .sort((left, right) => left - right);
    if (values.length < 5 || values[0] === values[values.length - 1]) return;
    thresholds[metric] = {
      soft: quantile(values, 0.75),
      strong: quantile(values, 0.9),
    };
  });
  return thresholds;
};

const ratioTone = (value) => {
  if (value >= 2) return 'up-strong';
  if (value > 1) return 'up-soft';
  if (value <= 0.8) return 'down-strong';
  if (value < 1) return 'down-soft';
  return 'neutral';
};

export const metricTone = (row, metric, thresholds) => {
  const value = comparableValue(row, metric);
  if (value == null) return 'neutral';
  if (ratioMetrics.has(metric)) return ratioTone(value);

  const direction = upMetrics.has(metric)
    ? 'up'
    : downMetrics.has(metric)
      ? 'down'
      : null;
  const metricThresholds = thresholds[metric];
  if (!direction || !metricThresholds || value <= 0) return 'neutral';
  if (value >= metricThresholds.strong) return `${direction}-strong`;
  if (value >= metricThresholds.soft) return `${direction}-soft`;
  return 'neutral';
};

export const isDirectionalMetric = (metric) => (
  upMetrics.has(metric) || downMetrics.has(metric) || ratioMetrics.has(metric)
);
