import { useQuery } from '@tanstack/react-query';
import {
  Alert,
  Box,
  Card,
  CardContent,
  Chip,
  CircularProgress,
  Grid,
  Paper,
  Stack,
  Typography,
} from '@mui/material';

import {
  getMarketIntelligenceHealth,
  marketIntelligenceKeys,
} from '../../api/marketIntelligence';
import FreshnessBanner from './components/FreshnessBanner';


const counters = [
  ['expected_symbols', 'Expected'],
  ['symbols_received', 'Received'],
  ['valid_bars', 'Valid'],
  ['rejected_bars', 'Rejected'],
  ['missing_symbols', 'Missing'],
  ['duplicate_rows', 'Duplicate Rows'],
  ['invalid_volume', 'Invalid Volume'],
  ['invalid_ohlc', 'Invalid OHLC'],
];

function RunCard({ title, run }) {
  return (
    <Card variant="outlined" sx={{ height: '100%' }}>
      <CardContent>
        <Stack direction="row" justifyContent="space-between" alignItems="center" sx={{ mb: 1 }}>
          <Typography component="h3" variant="subtitle1" sx={{ fontWeight: 700 }}>{title}</Typography>
          <Chip
            size="small"
            label={run.status}
            color={run.status === 'SUCCEEDED' ? 'success' : run.status === 'PARTIAL' ? 'warning' : 'error'}
          />
        </Stack>
        <Stack spacing={0.4}>
          <Typography variant="body2">Trading date {run.as_of}</Typography>
          <Typography variant="body2">Provider {run.provider} · {run.provider_status}</Typography>
          <Typography variant="body2">Lifecycle {run.lifecycle_status}</Typography>
          <Typography variant="body2">Metric {run.metric_version}</Typography>
          <Typography variant="body2">Freshness {run.source_freshness?.status || 'Unavailable'}</Typography>
          <Typography variant="body2">Published {run.published_at || 'No'}</Typography>
        </Stack>
      </CardContent>
    </Card>
  );
}

export default function DataHealthPage() {
  const query = useQuery({
    queryKey: marketIntelligenceKeys.health(),
    queryFn: getMarketIntelligenceHealth,
  });

  if (query.isPending) {
    return (
      <Box sx={{ display: 'grid', placeItems: 'center', minHeight: 260 }}>
        <CircularProgress aria-label="Loading data health" />
      </Box>
    );
  }
  if (query.isError) {
    return <Alert severity="error">Unable to load Data Health: {query.error.message}</Alert>;
  }

  const data = query.data;
  const attempt = data.latest_attempt;
  const published = data.latest_published;
  if (!attempt && !published) {
    return (
      <Box>
        <Typography component="h2" variant="h6" sx={{ mb: 1 }}>Data Health</Typography>
        <Alert severity="info">No Market Intelligence run has been recorded yet.</Alert>
      </Box>
    );
  }

  return (
    <Box>
      <Typography component="h2" variant="h6" sx={{ mb: 1 }}>Data Health</Typography>
      <FreshnessBanner
        asOf={attempt?.as_of || published?.as_of}
        lastUpdated={attempt?.ingestion_timestamp || data.current_run_timestamp}
        provider={attempt?.provider || published?.provider}
        metricVersion={attempt?.metric_version || published?.metric_version}
        status={attempt?.status}
        stableAsOf={data.last_complete_published_snapshot}
      />

      <Grid container spacing={1.25} sx={{ mb: 1.5 }}>
        {attempt && (
          <Grid item xs={12} md={6}>
            <RunCard title="Latest Data Attempt" run={attempt} />
          </Grid>
        )}
        {published && (
          <Grid item xs={12} md={6}>
            <RunCard title="Currently Displayed Stable Snapshot" run={published} />
          </Grid>
        )}
      </Grid>

      {attempt && (
        <Paper variant="outlined" sx={{ p: 1.5 }}>
          <Typography variant="subtitle1" sx={{ fontWeight: 700, mb: 1 }}>Latest Attempt Coverage</Typography>
          <Grid container spacing={1}>
            {counters.map(([key, label]) => (
              <Grid item xs={6} sm={4} md={3} key={key}>
                <Box sx={{ p: 1, border: 1, borderColor: 'divider', borderRadius: 1 }}>
                  <Typography variant="caption" color="text.secondary">{label}</Typography>
                  <Typography
                    variant="h6"
                    data-health-value={key}
                  >
                    {attempt.counters?.[key] ?? '—'}
                  </Typography>
                </Box>
              </Grid>
            ))}
          </Grid>
          <Stack direction="row" spacing={0.75} alignItems="center" sx={{ mt: 1.25 }}>
            <Typography variant="body2">Missing symbols</Typography>
            {(attempt.missing_symbols || []).length
              ? attempt.missing_symbols.map((symbol) => <Chip key={symbol} size="small" label={symbol} />)
              : <Typography variant="body2" color="text.secondary">None</Typography>}
          </Stack>
          {!data.publication_occurred && attempt.status !== 'SUCCEEDED' && (
            <Alert severity="warning" sx={{ mt: 1.25 }}>
              This attempt was not published. The last complete snapshot remains active.
            </Alert>
          )}
        </Paper>
      )}
    </Box>
  );
}
