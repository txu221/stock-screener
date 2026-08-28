import { useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import {
  Alert,
  Box,
  Chip,
  CircularProgress,
  Grid,
  MenuItem,
  Paper,
  Stack,
  Tab,
  Tabs,
  TextField,
  Typography,
} from '@mui/material';

import { getMarketMovers, marketIntelligenceKeys } from '../../api/marketIntelligence';
import FreshnessBanner from './components/FreshnessBanner';
import MoversTable from './components/MoversTable';


const LISTS = {
  gainers: { key: 'gainers', title: 'Top Gainers' },
  losers: { key: 'losers', title: 'Top Losers' },
  unusual_volume: { key: 'unusual_volume', title: 'Unusual Volume' },
};

const optionalNumber = (value) => (value === '' ? undefined : Number(value));

export default function MoversPage() {
  const [search, setSearch] = useState('');
  const [direction, setDirection] = useState('all');
  const [minPrice, setMinPrice] = useState(5);
  const [minRvol, setMinRvol] = useState('');
  const [sector, setSector] = useState('');
  const [marketCapGroup, setMarketCapGroup] = useState('');
  const [activeList, setActiveList] = useState('gainers');

  const filters = useMemo(() => {
    const result = {
      direction,
      limit: 20,
      min_price: optionalNumber(minPrice),
    };
    if (minRvol !== '') result.min_rvol = Number(minRvol);
    if (search.trim()) result.search = search.trim().toUpperCase();
    if (sector) result.sector = sector;
    if (marketCapGroup) result.market_cap_group = marketCapGroup;
    return result;
  }, [direction, marketCapGroup, minPrice, minRvol, search, sector]);

  const query = useQuery({
    queryKey: marketIntelligenceKeys.movers(filters),
    queryFn: () => getMarketMovers(filters),
  });

  if (query.isError) {
    return <Alert severity="error">Unable to load movers: {query.error.message}</Alert>;
  }

  const data = query.data;
  const sectorOptions = data?.sectors?.map((item) => item.sector) || [];
  const selectedList = LISTS[activeList];

  return (
    <Box>
      <Stack direction={{ xs: 'column', sm: 'row' }} justifyContent="space-between" alignItems={{ sm: 'center' }} spacing={1} sx={{ mb: 1 }}>
        <Typography component="h2" variant="h6">S&amp;P 500 Movers</Typography>
        {data && <Chip size="small" variant="outlined" label={`Eligible universe ${data.eligible_count}`} />}
      </Stack>

      {data && (
        <FreshnessBanner
          asOf={data.as_of}
          lastUpdated={data.published_at}
          provider={data.provider}
          metricVersion={data.metric_version}
        />
      )}

      <Paper variant="outlined" sx={{ p: 1.25, mb: 1.5 }}>
        <Grid container spacing={1}>
          <Grid item xs={12} sm={6} md={3}>
            <TextField fullWidth size="small" label="Search ticker" value={search} onChange={(event) => setSearch(event.target.value)} />
          </Grid>
          <Grid item xs={12} sm={6} md={2}>
            <TextField select fullWidth size="small" label="Direction" value={direction} onChange={(event) => setDirection(event.target.value)}>
              <MenuItem value="all">All</MenuItem>
              <MenuItem value="gainers">Gainers</MenuItem>
              <MenuItem value="losers">Losers</MenuItem>
            </TextField>
          </Grid>
          <Grid item xs={12} sm={6} md={2}>
            <TextField fullWidth size="small" type="number" label="Minimum Price" value={minPrice} onChange={(event) => setMinPrice(event.target.value)} />
          </Grid>
          <Grid item xs={12} sm={6} md={2}>
            <TextField fullWidth size="small" type="number" label="Minimum RVOL" value={minRvol} onChange={(event) => setMinRvol(event.target.value)} />
          </Grid>
          <Grid item xs={12} sm={6} md={1.5}>
            <TextField select fullWidth size="small" label="Sector" value={sector} onChange={(event) => setSector(event.target.value)}>
              <MenuItem value="">All</MenuItem>
              {sectorOptions.map((value) => <MenuItem key={value} value={value}>{value}</MenuItem>)}
            </TextField>
          </Grid>
          <Grid item xs={12} sm={6} md={1.5}>
            <TextField select fullWidth size="small" label="Market Cap Group" value={marketCapGroup} onChange={(event) => setMarketCapGroup(event.target.value)}>
              <MenuItem value="">All</MenuItem>
              <MenuItem value="mega">Mega</MenuItem>
              <MenuItem value="large">Large</MenuItem>
              <MenuItem value="mid">Mid</MenuItem>
            </TextField>
          </Grid>
        </Grid>
      </Paper>

      {query.isPending && (
        <Box sx={{ display: 'grid', placeItems: 'center', minHeight: 220 }}>
          <CircularProgress aria-label="Loading S&P 500 movers" />
        </Box>
      )}

      {data?.eligible_count === 0 && (
        <Alert severity="info">No eligible S&amp;P 500 movers match the current filters.</Alert>
      )}

      {data && data.eligible_count > 0 && (
        <>
          <Tabs value={activeList} onChange={(_, value) => setActiveList(value)} aria-label="Mover lists" sx={{ mb: 1 }}>
            {Object.entries(LISTS).map(([value, item]) => <Tab key={value} value={value} label={item.title} />)}
          </Tabs>
          <MoversTable title={selectedList.title} rows={data[selectedList.key]} />

          <Typography component="h3" variant="subtitle1" sx={{ mt: 1.5, mb: 0.75, fontWeight: 700 }}>Sector Breadth</Typography>
          <Grid container spacing={1}>
            {data.sectors.map((item) => (
              <Grid item xs={12} sm={6} md={4} key={item.sector}>
                <Paper variant="outlined" sx={{ p: 1.25 }}>
                  <Typography variant="subtitle2">{item.sector}</Typography>
                  <Stack direction="row" spacing={1.5} sx={{ mt: 0.5 }}>
                    <Typography variant="caption" color="success.main">{item.advancers} advancers</Typography>
                    <Typography variant="caption" color="error.main">{item.decliners} decliners</Typography>
                    <Typography variant="caption">{item.unchanged} unchanged</Typography>
                  </Stack>
                </Paper>
              </Grid>
            ))}
          </Grid>
        </>
      )}
    </Box>
  );
}
