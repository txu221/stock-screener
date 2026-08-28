import { useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import {
  Alert,
  Box,
  Chip,
  CircularProgress,
  Container,
  Paper,
  Typography,
} from '@mui/material';
import { format, parseISO } from 'date-fns';

import {
  getBreadthBootstrap,
  getBreadthSummary,
  getCurrentBreadth,
  getHistoricalBreadth,
} from '../api/breadth';
import { getPriceHistory } from '../api/stocks';
import BreadthContextStrip from '../components/Breadth/BreadthContextStrip';
import BreadthHistoryTable from '../components/Breadth/BreadthHistoryTable';
import BreadthChart from '../components/Charts/BreadthChart';
import { useMarketForCapability } from '../contexts/MarketContext';
import { useRuntime } from '../contexts/RuntimeContext';

const getDateRange = (range) => {
  const endDate = new Date();
  const startDate = new Date();
  if (range === '1M') startDate.setMonth(startDate.getMonth() - 1);
  else if (range === '3M') startDate.setMonth(startDate.getMonth() - 3);
  else if (range === '6M') startDate.setMonth(startDate.getMonth() - 6);
  else startDate.setFullYear(startDate.getFullYear() - 1);
  return {
    startDate: startDate.toISOString().split('T')[0],
    endDate: endDate.toISOString().split('T')[0],
  };
};

const MARKET_LIVE_BENCHMARK_SYMBOLS = {
  US: 'SPY',
  HK: '2800.HK',
  IN: 'NIFTYBEES.NS',
  JP: '1306.T',
  KR: '069500.KS',
  TW: '0050.TW',
  CN: '000300.SS',
  CA: 'XIU.TO',
  DE: '^GDAXI',
  SG: 'ES3.SI',
  AU: 'IOZ.AX',
  MY: '^KLSE',
};

const BREADTH_MARKET_FALLBACKS = ['US', 'HK', 'IN', 'JP', 'KR', 'TW', 'CN', 'CA', 'DE'];

function BreadthPage() {
  const { runtimeReady, uiSnapshots } = useRuntime();
  const queryClient = useQueryClient();
  const { market: selectedMarket } = useMarketForCapability(
    'breadth',
    BREADTH_MARKET_FALLBACKS,
  );
  const [chartTimeRange, setChartTimeRange] = useState('1M');
  const snapshotEnabled = runtimeReady && Boolean(uiSnapshots?.breadth);
  const chartDateRange = getDateRange(chartTimeRange);
  const defaultChartDateRange = getDateRange('1M');
  const endDate = new Date().toISOString().split('T')[0];
  const startDate = new Date(Date.now() - 90 * 24 * 60 * 60 * 1000)
    .toISOString()
    .split('T')[0];
  const periodMap = { '1M': '1mo', '3M': '3mo', '6M': '6mo', '1Y': '1y' };
  const benchmarkPeriod = periodMap[chartTimeRange] || '1y';
  const benchmarkSymbol = MARKET_LIVE_BENCHMARK_SYMBOLS[selectedMarket] || null;

  const breadthBootstrapQuery = useQuery({
    queryKey: ['breadthBootstrap', selectedMarket],
    queryFn: async () => {
      const snapshot = await getBreadthBootstrap(selectedMarket);
      if (snapshot && !snapshot.is_stale) {
        const payload = snapshot.payload ?? {};
        queryClient.setQueryData(
          ['breadth', 'current', selectedMarket],
          payload.current ?? null,
        );
        queryClient.setQueryData(
          ['breadth', 'historical', selectedMarket, startDate, endDate],
          payload.history_90d ?? [],
        );
        queryClient.setQueryData(
          ['breadth', 'summary', selectedMarket],
          payload.summary ?? {},
        );
        if (payload.chart_range === '1M') {
          queryClient.setQueryData(
            [
              'breadth',
              'chart',
              selectedMarket,
              defaultChartDateRange.startDate,
              defaultChartDateRange.endDate,
            ],
            payload.chart_data ?? [],
          );
          if (benchmarkSymbol) {
            queryClient.setQueryData(
              ['benchmark', 'history', selectedMarket, benchmarkSymbol, '1mo'],
              payload.benchmark_overlay ?? payload.spy_overlay ?? [],
            );
          }
        }
      }
      return snapshot;
    },
    enabled: snapshotEnabled,
    retry: false,
    staleTime: 60_000,
  });
  const liveQueriesEnabled = runtimeReady && (
    !snapshotEnabled || breadthBootstrapQuery.isSuccess || breadthBootstrapQuery.isError
  );

  const {
    data: currentBreadth,
    isLoading: isLoadingCurrent,
    error: errorCurrent,
  } = useQuery({
    queryKey: ['breadth', 'current', selectedMarket],
    queryFn: () => getCurrentBreadth(selectedMarket),
    enabled: liveQueriesEnabled,
    refetchInterval: 60_000,
    staleTime: 60_000,
  });
  const { data: historicalBreadth } = useQuery({
    queryKey: ['breadth', 'historical', selectedMarket, startDate, endDate],
    queryFn: () => getHistoricalBreadth(startDate, endDate, 365, selectedMarket),
    enabled: liveQueriesEnabled,
    staleTime: 60_000,
  });
  useQuery({
    queryKey: ['breadth', 'summary', selectedMarket],
    queryFn: () => getBreadthSummary(selectedMarket),
    enabled: liveQueriesEnabled,
    staleTime: 60_000,
  });
  const {
    data: chartBreadthData,
    isLoading: isLoadingChartBreadth,
    error: errorChartBreadth,
  } = useQuery({
    queryKey: [
      'breadth',
      'chart',
      selectedMarket,
      chartDateRange.startDate,
      chartDateRange.endDate,
    ],
    queryFn: () => getHistoricalBreadth(
      chartDateRange.startDate,
      chartDateRange.endDate,
      730,
      selectedMarket,
    ),
    enabled: liveQueriesEnabled,
    staleTime: 60_000,
  });
  const { data: benchmarkData } = useQuery({
    queryKey: [
      'benchmark',
      'history',
      selectedMarket,
      benchmarkSymbol,
      benchmarkPeriod,
    ],
    queryFn: () => getPriceHistory(benchmarkSymbol, benchmarkPeriod),
    enabled: liveQueriesEnabled && Boolean(benchmarkSymbol),
    staleTime: 60_000,
  });

  if (!runtimeReady || isLoadingCurrent) {
    return (
      <Container maxWidth="xl" sx={{ mt: 4, mb: 4 }}>
        <Box display="flex" justifyContent="center" alignItems="center" minHeight="400px">
          <CircularProgress />
        </Box>
      </Container>
    );
  }
  if (errorCurrent) {
    return (
      <Container maxWidth="xl" sx={{ mt: 2, mb: 2 }}>
        <Alert severity="error">
          Error loading {selectedMarket} breadth data: {errorCurrent.message}
        </Alert>
      </Container>
    );
  }

  return (
    <Container maxWidth="xl" sx={{ mt: 2, mb: 2 }}>
      <Paper variant="outlined" sx={{ p: 1.5, mb: 2 }}>
        <Box
          sx={{
            display: 'flex',
            justifyContent: 'space-between',
            alignItems: 'center',
            mb: 1.25,
          }}
        >
          <Box>
            <Typography variant="h6" sx={{ fontWeight: 700 }}>
              Latest Breadth Data
            </Typography>
            <Typography variant="caption" color="text.secondary">
              One shared calculation layer · revision {currentBreadth?.calculation_revision ?? '—'}
            </Typography>
          </Box>
          {currentBreadth?.date && (
            <Chip label={format(parseISO(currentBreadth.date), 'MMM dd, yyyy')} size="small" />
          )}
        </Box>
        <BreadthContextStrip row={currentBreadth} />
      </Paper>

      <Box sx={{ height: { xs: 390, md: 460 }, mb: 2 }}>
        <BreadthChart
          breadthData={chartBreadthData}
          spyData={benchmarkData || []}
          benchmarkLabel={benchmarkSymbol || 'Benchmark'}
          isLoading={isLoadingChartBreadth}
          error={errorChartBreadth}
          timeRange={chartTimeRange}
          onTimeRangeChange={setChartTimeRange}
          fillContainer
        />
      </Box>

      {historicalBreadth?.length > 0 && (
        <Paper variant="outlined">
          <Box sx={{ p: 1.5, borderBottom: 1, borderColor: 'divider' }}>
            <Typography sx={{ fontSize: 14, fontWeight: 700 }}>
              Recent History
            </Typography>
            <Typography variant="caption" color="text.secondary">
              Primary tracks daily movers and ratios; secondary tracks trend windows; context adds T2108, ATR extension, and universe size.
            </Typography>
          </Box>
          <BreadthHistoryTable rows={historicalBreadth} />
        </Paper>
      )}

      {currentBreadth && (
        <Typography
          component="div"
          variant="caption"
          color="text.secondary"
          sx={{ mt: 2, textAlign: 'center' }}
        >
          Broad universe: {currentBreadth.broad_universe_count
            ?? currentBreadth.total_stocks_scanned
            ?? '—'}
          {' · '}
          Calculation time: {currentBreadth.calculation_duration_seconds?.toFixed?.(2) ?? '—'}s
        </Typography>
      )}
    </Container>
  );
}

export default BreadthPage;
