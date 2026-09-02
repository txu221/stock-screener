import { Alert, Box, Chip, Stack, Typography } from '@mui/material';


const formatTimestamp = (value) => {
  if (!value) return 'Unavailable';
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? String(value) : parsed.toLocaleString();
};

export default function FreshnessBanner({
  asOf,
  lastUpdated,
  provider,
  metricVersion,
  priceBasis,
  priceHistoryQuality,
  status,
  stableAsOf,
  expectedSession,
  freshnessStatus,
  scopeLabel,
}) {
  const partial = status === 'PARTIAL' || status === 'FAILED';
  const stale = freshnessStatus && freshnessStatus !== 'FRESH';
  const limitedPriceHistory = priceHistoryQuality
    && priceHistoryQuality !== 'corporate_action_adjusted';

  return (
    <Alert
      severity={partial || stale || limitedPriceHistory ? 'warning' : 'info'}
      icon={false}
      sx={{ mb: 1.5, py: 0.25, '& .MuiAlert-message': { width: '100%' } }}
    >
      <Stack
        direction={{ xs: 'column', md: 'row' }}
        spacing={1}
        useFlexGap
        flexWrap="wrap"
        alignItems={{ md: 'center' }}
      >
        <Typography variant="caption">
          {scopeLabel ? `${scopeLabel} as of` : 'As of'} {asOf || 'Unavailable'}
        </Typography>
        <Typography variant="caption">Last updated {formatTimestamp(lastUpdated)}</Typography>
        <Typography variant="caption">Provider {provider || 'Unavailable'}</Typography>
        <Typography variant="caption">Metric {metricVersion || 'Unavailable'}</Typography>
        {priceBasis && <Typography variant="caption">Price basis {priceBasis}</Typography>}
        {freshnessStatus && (
          <Chip
            size="small"
            label={`${scopeLabel || ''}${scopeLabel ? ' freshness' : 'Freshness'} ${freshnessStatus}`}
            color={stale ? 'warning' : 'success'}
          />
        )}
        {expectedSession && freshnessStatus !== 'FRESH' && (
          <Typography variant="caption">Expected session {expectedSession}</Typography>
        )}
        {priceHistoryQuality === 'corporate_action_adjusted' && (
          <Typography variant="caption">
            Historical analytical returns use corporate-action-adjusted prices.
          </Typography>
        )}
        {limitedPriceHistory && (
          <Typography variant="caption">
            Historical analytical returns have partial corporate-action-adjusted price coverage;
            {' '}legacy or unverified rows may be included.
          </Typography>
        )}
        {status && <Chip size="small" label={`Latest attempt ${status}`} color={partial ? 'warning' : 'default'} />}
        {stableAsOf && (
          <Box component="span">
            <Chip size="small" label={`Displayed stable snapshot ${stableAsOf}`} variant="outlined" />
          </Box>
        )}
      </Stack>
    </Alert>
  );
}
