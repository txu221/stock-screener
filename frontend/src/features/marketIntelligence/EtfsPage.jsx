import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import {
  Alert,
  Box,
  CircularProgress,
  Paper,
  Stack,
  Tab,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  Tabs,
  Typography,
} from '@mui/material';

import { getEtfRadar, marketIntelligenceKeys } from '../../api/marketIntelligence';
import FreshnessBanner from './components/FreshnessBanner';
import MetricTooltip from './components/MetricTooltip';
import { formatNumber, formatPercent, formatPrice } from './formatters';


const CATEGORIES = [
  ['all', 'All'],
  ['broad_market', 'Broad Market'],
  ['sector', 'Sector'],
  ['semiconductor', 'Semiconductor'],
  ['software', 'Software'],
  ['biotech', 'Biotech'],
  ['defense', 'Defense'],
  ['energy', 'Energy'],
  ['metals', 'Metals'],
  ['uranium', 'Uranium'],
];

const rankLabel = (rank) => (rank == null ? '—' : `#${rank}`);

const categoryRank = (item, category) => {
  if (category !== 'all') return item.category_ranks?.[category];
  const ranks = Object.values(item.category_ranks || {});
  return ranks.length ? Math.min(...ranks) : null;
};

const weightPercent = (value) => `${Math.round(Number(value) * 100)}%`;

export default function EtfsPage() {
  const [category, setCategory] = useState('all');
  const query = useQuery({
    queryKey: marketIntelligenceKeys.etfs(category),
    queryFn: () => getEtfRadar(category),
  });

  if (query.isError) {
    return <Alert severity="error">Unable to load ETF Radar: {query.error.message}</Alert>;
  }

  const data = query.data;

  return (
    <Box>
      <Typography component="h2" variant="h6" sx={{ mb: 1 }}>ETF Radar</Typography>

      {data && (
        <FreshnessBanner
          asOf={data.as_of}
          lastUpdated={data.last_updated}
          provider={data.provider}
          metricVersion={data.metric_version}
        />
      )}

      <Paper variant="outlined" sx={{ mb: 1.5 }}>
        <Tabs
          value={category}
          onChange={(_, value) => setCategory(value)}
          variant="scrollable"
          scrollButtons="auto"
          aria-label="ETF categories"
        >
          {CATEGORIES.map(([value, label]) => <Tab key={value} value={value} label={label} />)}
        </Tabs>
      </Paper>

      {query.isPending && (
        <Box sx={{ display: 'grid', placeItems: 'center', minHeight: 240 }}>
          <CircularProgress aria-label="Loading ETF Radar" />
        </Box>
      )}

      {data?.missing_symbols?.length > 0 && (
        <Alert severity="warning" sx={{ mb: 1 }}>
          Missing {data.missing_symbols.length}: {data.missing_symbols.join(', ')}
        </Alert>
      )}

      {data && data.items.length === 0 && (
        <Alert severity="info">No ETFs are available for this category.</Alert>
      )}

      {data?.items?.length > 0 && (
        <TableContainer component={Paper} variant="outlined">
          <Table size="small" aria-label="ETF strength rankings">
            <TableHead>
              <TableRow>
                <TableCell>ETF</TableCell>
                <TableCell>Categories</TableCell>
                <TableCell align="right">Price</TableCell>
                <TableCell align="right">Overall Rank</TableCell>
                <TableCell align="right">Category Rank</TableCell>
                <TableCell align="right">1D</TableCell>
                <TableCell align="right">5D</TableCell>
                <TableCell align="right">20D</TableCell>
                <TableCell align="right">60D</TableCell>
                <TableCell align="right"><MetricTooltip metric="relativeStrength">RS 20D</MetricTooltip></TableCell>
                <TableCell align="right"><MetricTooltip metric="relativeStrength">RS 60D</MetricTooltip></TableCell>
                <TableCell align="right"><MetricTooltip metric="rvol20">RVOL20</MetricTooltip></TableCell>
                <TableCell align="right">60D Drawdown</TableCell>
                <TableCell align="right"><MetricTooltip metric="strengthScore">Strength Score</MetricTooltip></TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {data.items.map((item) => (
                <TableRow key={item.symbol}>
                  <TableCell sx={{ fontWeight: 700 }}>{item.symbol}</TableCell>
                  <TableCell>{item.categories.join(', ') || '—'}</TableCell>
                  <TableCell align="right">{formatPrice(item.price)}</TableCell>
                  <TableCell align="right">{rankLabel(item.overall_rank)}</TableCell>
                  <TableCell align="right">{rankLabel(categoryRank(item, category))}</TableCell>
                  <TableCell align="right">{formatPercent(item.return_1d)}</TableCell>
                  <TableCell align="right">{formatPercent(item.return_5d)}</TableCell>
                  <TableCell align="right">{formatPercent(item.return_20d)}</TableCell>
                  <TableCell align="right">{formatPercent(item.return_60d)}</TableCell>
                  <TableCell align="right">{formatPercent(item.relative_strength_20d)}</TableCell>
                  <TableCell align="right">{formatPercent(item.relative_strength_60d)}</TableCell>
                  <TableCell align="right">{formatNumber(item.rvol20)}</TableCell>
                  <TableCell align="right">{formatPercent(item.drawdown_60d)}</TableCell>
                  <TableCell align="right">{formatNumber(item.strength_score)}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </TableContainer>
      )}

      {data && (
        <Paper variant="outlined" sx={{ p: 1.5, mt: 1.5 }}>
          <Typography component="h3" variant="subtitle1" sx={{ fontWeight: 700 }}>
            Strength Score methodology
          </Typography>
          <Typography variant="body2" color="text.secondary" sx={{ mt: 0.5 }}>
            Version {data.score_definition.version}; deterministic 0–100 inclusive empirical percentiles.
            This score is descriptive, not predictive, and is not a trading signal.
          </Typography>
          <Stack direction={{ xs: 'column', md: 'row' }} spacing={1.5} sx={{ mt: 1 }} useFlexGap flexWrap="wrap">
            <Typography variant="caption">{weightPercent(data.score_definition.weights.relative_strength_20d)} RS 20D</Typography>
            <Typography variant="caption">{weightPercent(data.score_definition.weights.relative_strength_60d)} RS 60D</Typography>
            <Typography variant="caption">{weightPercent(data.score_definition.weights.return_20d)} Return 20D</Typography>
            <Typography variant="caption">{weightPercent(data.score_definition.weights.volume_confirmation)} Volume Confirmation</Typography>
            <Typography variant="caption">{weightPercent(data.score_definition.weights.drawdown_60d)} Drawdown 60D</Typography>
          </Stack>
          <Typography variant="caption" display="block" sx={{ mt: 0.75 }}>
            Volume confirmation: clamp(RVOL20, 0, 3) − 1, signed by 20D return direction.
          </Typography>
        </Paper>
      )}
    </Box>
  );
}
