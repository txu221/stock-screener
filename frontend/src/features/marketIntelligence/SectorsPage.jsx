import { useState } from 'react';
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
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  ToggleButton,
  ToggleButtonGroup,
  Typography,
} from '@mui/material';

import { getSectorLatest, marketIntelligenceKeys } from '../../api/marketIntelligence';
import FreshnessBanner from './components/FreshnessBanner';
import MetricTooltip from './components/MetricTooltip';
import { formatNumber, formatPercent } from './formatters';


const PERIODS = ['1d', '5d', '20d', '60d'];
const flowKey = {
  '1d': 'flow_pressure_1d',
  '5d': 'cmf_5d',
  '20d': 'cmf_20d',
  '60d': 'cmf_60d',
};

const rankKey = (period) => `relative_return_vs_spy_${period}`;

const rankChangeLabel = (change, direction) => {
  if (change == null) return '— UNAVAILABLE';
  if (direction === 'IMPROVED') return `+${Math.abs(change)} ↑ IMPROVED`;
  if (direction === 'DECLINED') return `-${Math.abs(change)} ↓ DECLINED`;
  return '0 → UNCHANGED';
};

const tileColor = (value) => {
  if (value > 0) return 'rgba(46, 125, 50, 0.16)';
  if (value < 0) return 'rgba(211, 47, 47, 0.16)';
  return 'transparent';
};

export default function SectorsPage() {
  const [period, setPeriod] = useState('1d');
  const query = useQuery({
    queryKey: marketIntelligenceKeys.sectors(),
    queryFn: getSectorLatest,
  });

  if (query.isPending) {
    return (
      <Box sx={{ display: 'grid', placeItems: 'center', minHeight: 260 }}>
        <CircularProgress aria-label="Loading sector intelligence" />
      </Box>
    );
  }
  if (query.isError) {
    return <Alert severity="error">Unable to load sector intelligence: {query.error.message}</Alert>;
  }

  const data = query.data;
  const metricRankKey = rankKey(period);
  const rows = data.sectors || [];
  const improvers = rows
    .filter((item) => item.rank_directions?.[metricRankKey] === 'IMPROVED')
    .sort((left, right) => right.rank_changes[metricRankKey] - left.rank_changes[metricRankKey]);
  const decliners = rows
    .filter((item) => item.rank_directions?.[metricRankKey] === 'DECLINED')
    .sort((left, right) => left.rank_changes[metricRankKey] - right.rank_changes[metricRankKey]);

  return (
    <Box>
      <FreshnessBanner
        asOf={data.as_of}
        lastUpdated={data.published_at}
        provider={data.provider}
        metricVersion={data.metric_version}
        status={data.status}
      />

      <Stack direction={{ xs: 'column', sm: 'row' }} justifyContent="space-between" alignItems={{ sm: 'center' }} sx={{ mb: 1 }}>
        <Box>
          <Typography component="h2" variant="h6">Sector Heatmap</Typography>
          <Stack direction="row" spacing={0.75} alignItems="center">
            <Chip size="small" variant="outlined" label={`Benchmark ${data.benchmark?.symbol || 'SPY'}`} />
            <Typography variant="caption" color="text.secondary">Benchmark is not sector-ranked</Typography>
          </Stack>
        </Box>
        <ToggleButtonGroup
          size="small"
          exclusive
          value={period}
          onChange={(_event, value) => value && setPeriod(value)}
          aria-label="Sector return period"
        >
          {PERIODS.map((value) => (
            <ToggleButton key={value} value={value} aria-label={value.toUpperCase()}>
              {value.toUpperCase()}
            </ToggleButton>
          ))}
        </ToggleButtonGroup>
      </Stack>

      {rows.length === 0 ? (
        <Alert severity="info">No published sector rows are available.</Alert>
      ) : (
        <>
          <Stack direction="row" spacing={2} sx={{ mb: 0.75 }}>
            <Typography variant="caption"><MetricTooltip metric="relativeStrength">Relative Strength</MetricTooltip></Typography>
            <Typography variant="caption"><MetricTooltip metric="rvol20">RVOL20</MetricTooltip></Typography>
            <Typography variant="caption"><MetricTooltip metric="flowPressure">Flow Pressure</MetricTooltip></Typography>
          </Stack>
          <Grid container spacing={1} sx={{ mb: 1.5 }}>
            {rows.map((item) => {
              const sectorReturn = item.returns?.[period];
              const relativeStrength = item.relative_strength?.[`${period}_vs_spy`];
              return (
                <Grid item xs={12} sm={6} md={4} lg={3} key={item.symbol}>
                  <Card
                    variant="outlined"
                    data-testid="sector-tile"
                    sx={{ height: '100%', backgroundColor: tileColor(sectorReturn) }}
                  >
                    <CardContent data-testid={`sector-${item.symbol}`}>
                      <Stack direction="row" justifyContent="space-between">
                        <Typography component="h3" variant="subtitle1" sx={{ fontWeight: 700 }}>{item.symbol}</Typography>
                        <Chip size="small" label={`Rank ${item.ranks?.[metricRankKey] ?? '—'}`} />
                      </Stack>
                      <Typography variant="caption" color="text.secondary">{item.name}</Typography>
                      <Typography variant="h6" sx={{ my: 0.5 }}>{formatPercent(sectorReturn)}</Typography>
                      <Stack spacing={0.25}>
                        <Typography variant="caption">RS {formatPercent(relativeStrength)}</Typography>
                        <Typography variant="caption">RVOL {formatNumber(item.rvol20)}</Typography>
                        <Typography variant="caption">
                          Pressure {formatNumber(item.flow_pressure_proxy?.[flowKey[period]])}
                        </Typography>
                      </Stack>
                    </CardContent>
                  </Card>
                </Grid>
              );
            })}
          </Grid>

          <Grid container spacing={1.25} sx={{ mb: 1.5 }}>
            <Grid item xs={12} md={6}>
              <Paper variant="outlined" sx={{ p: 1.25, height: '100%' }}>
                <Typography variant="subtitle2">Biggest Improvers</Typography>
                <Typography variant="body2" color="text.secondary">
                  {improvers.length
                    ? improvers.map((item) => `${item.symbol} +${item.rank_changes[metricRankKey]}`).join(' · ')
                    : 'No sectors improved for this period.'}
                </Typography>
              </Paper>
            </Grid>
            <Grid item xs={12} md={6}>
              <Paper variant="outlined" sx={{ p: 1.25, height: '100%' }}>
                <Typography variant="subtitle2">Biggest Decliners</Typography>
                <Typography variant="body2" color="text.secondary">
                  {decliners.length
                    ? decliners.map((item) => `${item.symbol} ${item.rank_changes[metricRankKey]}`).join(' · ')
                    : 'No sectors declined for this period.'}
                </Typography>
              </Paper>
            </Grid>
          </Grid>

          <TableContainer component={Paper} variant="outlined">
            <Table size="small" aria-label="Sector Rotation">
              <TableHead>
                <TableRow>
                  <TableCell>Sector</TableCell>
                  <TableCell align="right">Current Rank</TableCell>
                  <TableCell align="right">Previous Rank</TableCell>
                  <TableCell><MetricTooltip metric="rankChange">Rank Change</MetricTooltip></TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {[...rows]
                  .sort((left, right) => (
                    (left.ranks?.[metricRankKey] ?? 999) - (right.ranks?.[metricRankKey] ?? 999)
                    || left.symbol.localeCompare(right.symbol)
                  ))
                  .map((item) => (
                    <TableRow key={item.symbol}>
                      <TableCell>{item.symbol} · {item.name}</TableCell>
                      <TableCell align="right">{item.ranks?.[metricRankKey] ?? '—'}</TableCell>
                      <TableCell align="right">{item.previous_ranks?.[metricRankKey] ?? '—'}</TableCell>
                      <TableCell>{rankChangeLabel(item.rank_changes?.[metricRankKey], item.rank_directions?.[metricRankKey])}</TableCell>
                    </TableRow>
                  ))}
              </TableBody>
            </Table>
          </TableContainer>
        </>
      )}
    </Box>
  );
}
