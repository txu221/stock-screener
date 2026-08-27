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
  status,
  stableAsOf,
}) {
  const partial = status === 'PARTIAL' || status === 'FAILED';

  return (
    <Alert
      severity={partial ? 'warning' : 'info'}
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
        <Typography variant="caption">As of {asOf || 'Unavailable'}</Typography>
        <Typography variant="caption">Last updated {formatTimestamp(lastUpdated)}</Typography>
        <Typography variant="caption">Provider {provider || 'Unavailable'}</Typography>
        <Typography variant="caption">Metric {metricVersion || 'Unavailable'}</Typography>
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

