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
  getMarketIntelligenceOverview,
  getSectorLatest,
  marketIntelligenceKeys,
} from '../../api/marketIntelligence';
import FreshnessBanner from './components/FreshnessBanner';
import { directionLabel, formatPercent, formatPrice } from './formatters';


const valueColor = (value) => {
  if (value > 0) return 'success.main';
  if (value < 0) return 'error.main';
  return 'text.secondary';
};

export default function TodayPage() {
  const overviewQuery = useQuery({
    queryKey: marketIntelligenceKeys.overview(),
    queryFn: getMarketIntelligenceOverview,
  });
  const sectorsQuery = useQuery({
    queryKey: marketIntelligenceKeys.sectors(),
    queryFn: getSectorLatest,
    enabled: overviewQuery.isSuccess,
  });

  if (overviewQuery.isPending) {
    return (
      <Box sx={{ display: 'grid', placeItems: 'center', minHeight: 260 }}>
        <CircularProgress aria-label="Loading market overview" />
      </Box>
    );
  }

  if (overviewQuery.isError) {
    return <Alert severity="error">Unable to load Market Pulse: {overviewQuery.error.message}</Alert>;
  }

  const overview = overviewQuery.data;
  const leaders = [...(sectorsQuery.data?.sectors || [])]
    .filter((item) => item.ranks?.relative_return_vs_spy_20d != null)
    .sort((left, right) => (
      left.ranks.relative_return_vs_spy_20d - right.ranks.relative_return_vs_spy_20d
      || left.symbol.localeCompare(right.symbol)
    ))
    .slice(0, 3);

  return (
    <Box>
      <FreshnessBanner
        asOf={overview.as_of}
        lastUpdated={overview.last_updated}
        provider={overview.provider}
        metricVersion={overview.metric_version}
      />

      <Stack direction="row" alignItems="center" justifyContent="space-between" sx={{ mb: 1 }}>
        <Typography component="h2" variant="h6">Market Pulse</Typography>
        <Chip size="small" variant="outlined" label="Raw completed-session pulse" />
      </Stack>

      <Grid container spacing={1.25} sx={{ mb: 1.5 }}>
        {overview.pulse.map((item) => (
          <Grid item xs={12} sm={6} md={3} key={item.symbol}>
            <Card variant="outlined" sx={{ height: '100%' }}>
              <CardContent>
                <Stack direction="row" justifyContent="space-between" alignItems="baseline">
                  <Typography component="h3" variant="h6">{item.symbol}</Typography>
                  <Typography variant="body2">{formatPrice(item.price)}</Typography>
                </Stack>
                <Typography
                  variant="h6"
                  sx={{ color: valueColor(item.return_1d), mt: 0.75 }}
                >
                  {formatPercent(item.return_1d)} {directionLabel(item.return_1d)}
                </Typography>
                <Stack direction="row" spacing={1.25} sx={{ mt: 0.75 }}>
                  <Typography variant="caption">5D {formatPercent(item.return_5d)}</Typography>
                  <Typography variant="caption">20D {formatPercent(item.return_20d)}</Typography>
                  <Typography variant="caption">60D {formatPercent(item.return_60d)}</Typography>
                </Stack>
                {!item.available && <Chip size="small" color="warning" label="Unavailable" sx={{ mt: 1 }} />}
              </CardContent>
            </Card>
          </Grid>
        ))}
      </Grid>

      <Paper variant="outlined" sx={{ p: 1.5 }}>
        <Typography component="h2" variant="subtitle1" sx={{ fontWeight: 600, mb: 1 }}>
          20D Sector Leadership
        </Typography>
        {sectorsQuery.isError && (
          <Alert severity="warning">Sector context unavailable: {sectorsQuery.error.message}</Alert>
        )}
        {!sectorsQuery.isError && leaders.length === 0 && (
          <Typography variant="body2" color="text.secondary">No published sector leadership is available.</Typography>
        )}
        <Stack direction={{ xs: 'column', md: 'row' }} spacing={1}>
          {leaders.map((item) => (
            <Box key={item.symbol} sx={{ minWidth: 180, p: 1, border: 1, borderColor: 'divider', borderRadius: 1 }}>
              <Typography variant="subtitle2">#{item.ranks.relative_return_vs_spy_20d} {item.symbol}</Typography>
              <Typography variant="body2">{item.name}</Typography>
              <Typography variant="caption">RS vs SPY {formatPercent(item.relative_strength?.['20d_vs_spy'])}</Typography>
            </Box>
          ))}
        </Stack>
      </Paper>
    </Box>
  );
}

