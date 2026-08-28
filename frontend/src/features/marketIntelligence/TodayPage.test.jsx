import { screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import * as api from '../../api/marketIntelligence';
import { renderWithProviders } from '../../test/renderWithProviders';
import TodayPage from './TodayPage';


vi.mock('../../api/marketIntelligence', () => ({
  marketIntelligenceKeys: {
    overview: () => ['market-intelligence', 'overview'],
    sectors: () => ['market-intelligence', 'sectors', 'latest'],
  },
  getMarketIntelligenceOverview: vi.fn(),
  getSectorLatest: vi.fn(),
}));

const overview = {
  as_of: '2026-08-26',
  last_updated: '2026-08-26T22:05:00Z',
  provider: 'existing_stock_prices',
  metric_version: 'market_intelligence_mvp_v1',
  market_status: null,
  missing_symbols: [],
  pulse: ['SPY', 'QQQ', 'DIA', 'IWM'].map((symbol, index) => ({
    symbol,
    available: true,
    price: 100 + index,
    return_1d: [0.01, 0.02, -0.005, 0][index],
    return_5d: 0.03,
    return_20d: 0.05,
    return_60d: 0.10,
  })),
};

const sectors = {
  sectors: [
    { symbol: 'XLK', name: 'Technology', ranks: { relative_return_vs_spy_20d: 1 }, relative_strength: { '20d_vs_spy': 0.05 } },
    { symbol: 'XLE', name: 'Energy', ranks: { relative_return_vs_spy_20d: 2 }, relative_strength: { '20d_vs_spy': 0.03 } },
  ],
};

describe('TodayPage', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    api.getMarketIntelligenceOverview.mockResolvedValue(overview);
    api.getSectorLatest.mockResolvedValue(sectors);
  });

  it('renders the fixed completed-session Market Pulse and no invented status', async () => {
    renderWithProviders(<TodayPage />);

    expect(await screen.findByRole('heading', { name: 'Market Pulse' })).toBeInTheDocument();
    for (const symbol of ['SPY', 'QQQ', 'DIA', 'IWM']) {
      expect(screen.getByRole('heading', { name: symbol })).toBeInTheDocument();
    }
    expect(screen.queryByText('VIX')).not.toBeInTheDocument();
    expect(screen.getByText('Raw completed-session pulse')).toBeInTheDocument();
    expect(screen.getByText(/As of 2026-08-26/)).toBeInTheDocument();
    expect(await screen.findByText('Technology')).toBeInTheDocument();
  });

  it('shows a bounded loading state', () => {
    api.getMarketIntelligenceOverview.mockReturnValue(new Promise(() => {}));

    renderWithProviders(<TodayPage />);

    expect(screen.getByRole('progressbar', { name: 'Loading market overview' })).toBeInTheDocument();
  });

  it('shows an error without presenting stale values as current', async () => {
    api.getMarketIntelligenceOverview.mockRejectedValue(new Error('overview unavailable'));

    renderWithProviders(<TodayPage />);

    expect(await screen.findByText('Unable to load Market Pulse: overview unavailable')).toBeInTheDocument();
    expect(screen.queryByText(/As of 2026-08-26/)).not.toBeInTheDocument();
  });
});
