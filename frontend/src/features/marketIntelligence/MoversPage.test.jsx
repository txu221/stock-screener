import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import * as api from '../../api/marketIntelligence';
import { renderWithProviders } from '../../test/renderWithProviders';
import MoversPage from './MoversPage';


vi.mock('../../api/marketIntelligence', () => ({
  marketIntelligenceKeys: {
    movers: (filters) => ['market-intelligence', 'movers', filters],
  },
  getMarketMovers: vi.fn(),
}));

const nvda = {
  symbol: 'NVDA', company_name: 'NVIDIA', price: 190, change_1d: 0.08,
  volume: 80_000_000, rvol20: 3.5, sector: 'Technology', industry: 'Semiconductors',
  market_cap: 4_000_000_000_000,
};
const mu = {
  symbol: 'MU', company_name: 'Micron', price: 135, change_1d: -0.06,
  volume: 50_000_000, rvol20: 4.1, sector: 'Technology', industry: 'Semiconductors',
  market_cap: 150_000_000_000,
};
const payload = {
  as_of: '2026-08-26',
  published_at: '2026-08-26T22:05:00Z',
  provider: 'existing_stock_prices',
  metric_version: 'market_intelligence_mvp_v1',
  price_basis: 'cached_adjusted_close',
  price_history_quality: 'not_corporate_action_reconciled',
  expected_session: '2026-08-27',
  freshness_status: 'STALE',
  eligible_count: 420,
  gainers: [nvda],
  losers: [mu],
  unusual_volume: [mu, nvda],
  sectors: [
    { sector: 'Technology', advancers: 35, decliners: 25, unchanged: 2, total: 62 },
    { sector: 'Energy', advancers: 10, decliners: 12, unchanged: 0, total: 22 },
  ],
  unavailable_reason: null,
};

describe('MoversPage', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    api.getMarketMovers.mockResolvedValue(payload);
  });

  it('renders backend-ordered top lists, freshness, and sector grouping', async () => {
    renderWithProviders(<MoversPage />);

    expect(await screen.findByRole('heading', { name: 'S&P 500 Movers' })).toBeInTheDocument();
    expect(await screen.findByText('NVDA')).toBeInTheDocument();
    expect(screen.getByText('Eligible universe 420')).toBeInTheDocument();
    expect(screen.getByText('35 advancers')).toBeInTheDocument();
    expect(screen.getByText('25 decliners')).toBeInTheDocument();
    expect(screen.getByText(/As of 2026-08-26/)).toBeInTheDocument();
    expect(api.getMarketMovers).toHaveBeenCalledWith({
      direction: 'all',
      limit: 20,
      min_price: 5,
    });
  });

  it('changes API query parameters from the approved filters', async () => {
    const user = userEvent.setup();
    renderWithProviders(<MoversPage />);
    await screen.findByText('NVDA');

    await user.type(screen.getByRole('textbox', { name: 'Search ticker' }), 'MU');
    await user.click(screen.getByRole('combobox', { name: 'Direction' }));
    await user.click(screen.getByRole('option', { name: 'Gainers' }));
    await user.clear(screen.getByRole('spinbutton', { name: 'Minimum RVOL' }));
    await user.type(screen.getByRole('spinbutton', { name: 'Minimum RVOL' }), '2');

    await waitFor(() => {
      expect(api.getMarketMovers).toHaveBeenLastCalledWith({
        direction: 'gainers',
        limit: 20,
        min_price: 5,
        min_rvol: 2,
        search: 'MU',
      });
    });
  });

  it('distinguishes high-volume gains from selloffs in the unusual-volume view', async () => {
    const user = userEvent.setup();
    renderWithProviders(<MoversPage />);
    await screen.findByText('NVDA');

    await user.click(screen.getByRole('tab', { name: 'Unusual Volume' }));

    expect(screen.getByText('MU')).toBeInTheDocument();
    expect(screen.getByText('↑ GAIN')).toBeInTheDocument();
    expect(screen.getByText('↓ LOSS')).toBeInTheDocument();
  });

  it('shows request failure and transparent empty states', async () => {
    api.getMarketMovers.mockRejectedValueOnce(new Error('movers unavailable'));
    const { unmount } = renderWithProviders(<MoversPage />);
    expect(await screen.findByText('Unable to load movers: movers unavailable')).toBeInTheDocument();
    unmount();

    api.getMarketMovers.mockResolvedValueOnce({ ...payload, eligible_count: 0, gainers: [], losers: [], unusual_volume: [], sectors: [] });
    renderWithProviders(<MoversPage />);
    expect(await screen.findByText('No eligible S&P 500 movers match the current filters.')).toBeInTheDocument();
  });

  it('distinguishes unavailable source data from a valid empty filter result', async () => {
    api.getMarketMovers.mockResolvedValueOnce({
      ...payload,
      eligible_count: 0,
      gainers: [],
      losers: [],
      unusual_volume: [],
      sectors: [],
      unavailable_reason: 'no_published_us_feature_run',
    });

    renderWithProviders(<MoversPage />);

    expect(await screen.findByText(/Movers source unavailable: no published us feature run/i)).toBeInTheDocument();
    expect(screen.queryByText('No eligible S&P 500 movers match the current filters.')).not.toBeInTheDocument();
  });
});
