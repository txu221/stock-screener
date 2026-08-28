import {
  Alert,
  Chip,
  Paper,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  Typography,
} from '@mui/material';

import { formatCompact, formatNumber, formatPercent, formatPrice } from '../formatters';
import MetricTooltip from './MetricTooltip';


const moveLabel = (value) => {
  if (value > 0) return '↑ GAIN';
  if (value < 0) return '↓ LOSS';
  return '→ FLAT';
};

export default function MoversTable({ title, rows }) {
  if (!rows?.length) {
    return <Alert severity="info">No {title} match the current filters.</Alert>;
  }

  return (
    <TableContainer component={Paper} variant="outlined">
      <Typography variant="subtitle1" sx={{ px: 1.25, py: 1, fontWeight: 700 }}>{title}</Typography>
      <Table size="small" aria-label={title}>
        <TableHead>
          <TableRow>
            <TableCell>Ticker</TableCell>
            <TableCell>Company</TableCell>
            <TableCell align="right">Price</TableCell>
            <TableCell align="right">Change</TableCell>
            <TableCell>Direction</TableCell>
            <TableCell align="right">Volume</TableCell>
            <TableCell align="right"><MetricTooltip metric="rvol20">RVOL20</MetricTooltip></TableCell>
            <TableCell>Sector / Industry</TableCell>
            <TableCell align="right">Market Cap</TableCell>
          </TableRow>
        </TableHead>
        <TableBody>
          {rows.map((row) => (
            <TableRow key={row.symbol}>
              <TableCell sx={{ fontWeight: 700 }}>{row.symbol}</TableCell>
              <TableCell>{row.company_name || '—'}</TableCell>
              <TableCell align="right">{formatPrice(row.price)}</TableCell>
              <TableCell align="right" sx={{ color: row.change_1d > 0 ? 'success.main' : row.change_1d < 0 ? 'error.main' : 'text.secondary' }}>
                {formatPercent(row.change_1d)}
              </TableCell>
              <TableCell>
                <Chip
                  size="small"
                  variant="outlined"
                  color={row.change_1d > 0 ? 'success' : row.change_1d < 0 ? 'error' : 'default'}
                  label={moveLabel(row.change_1d)}
                />
              </TableCell>
              <TableCell align="right">{formatCompact(row.volume)}</TableCell>
              <TableCell align="right">{formatNumber(row.rvol20)}</TableCell>
              <TableCell>{row.sector || '—'} / {row.industry || '—'}</TableCell>
              <TableCell align="right">{formatCompact(row.market_cap)}</TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </TableContainer>
  );
}

