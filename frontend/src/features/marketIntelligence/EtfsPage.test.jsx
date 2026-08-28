import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import * as api from '../../api/marketIntelligence';
import { renderWithProviders } from '../../test/renderWithProviders';
import EtfsPage from './EtfsPage';


vi.mock('../../api/marketIntelligence', () => ({
  marketIntelligenceKeys: {
    etfs: (category) => ['market-intelligence', 'etfs', category],
  },
  getEtfRadar: vi.fn(),
}));

const spy = {
  symbol: 'SPY', categories: ['broad_market'], available: true, price: 650,
  return_1d: 0.01, return_5d: 0.02, return_20d: 0.04, return_60d: 0.09,
  relative_strength_1d: 0, relative_strength_5d: 0, relative_strength_20d: 0,
  relative_strength_60d: 0, rvol20: 1.2, drawdown_60d: -0.01,
  strength_score: 75.5, score_components: { relative_strength_20d: 0.5 },
  overall_rank: 2, category_ranks: { broad_market: 1 },
};
const qqq = {
  symbol: 'QQQ', categories: ['broad_market'], available: false, price: null,
  return_1d: null, return_5d: null, return_20d: null, return_60d: null,
  relative_strength_1d: null, relative_strength_5d: null, relative_strength_20d: null,
  relative_strength_60d: null, rvol20: null, drawdown_60d: null,
  strength_score: null, score_components: null, overall_rank: null,
  category_ranks: {},
};
const payload = {
  as_of: '2026-08-26',
  last_updated: '2026-08-26T22:10:00Z',
  provider: 'existing_stock_prices',
  metric_version: 'market_intelligence_mvp_v1',
  score_version: 'etf_strength_v1',
  category: 'all',
  items: [spy, qqq],
  missing_symbols: ['QQQ'],
  unavailable_reason: null,
  score_definition: {
    version: 'etf_strength_v1',
    scale: '0_to_100',
    percentile_method: 'inclusive_empirical_percentile',
    weights: {
      relative_strength_20d: 0.3,
      relative_strength_60d: 0.25,
      return_20d: 0.2,
      volume_confirmation: 0.15,
      drawdown_60d: 0.1,
    },
    volume_confirmation: 'clamp(rvol20, 0, 3) - 1, signed by 20d return direction',
    language: 'descriptive_not_predictive',
  },
};

describe('EtfsPage', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    api.getEtfRadar.mockResolvedValue(payload);
  });

  it('renders transparent ETF ranks, metrics, freshness, and missing values', async () => {
    renderWithProviders(<EtfsPage />);

    expect(await screen.findByRole('heading', { name: 'ETF Radar' })).toBeInTheDocument();
    const spyRow = await screen.findByRole('row', { name: /^SPY\b/ });
    expect(within(spyRow).getByText('75.50')).toBeInTheDocument();
    expect(within(spyRow).getByText('#2')).toBeInTheDocument();
    expect(within(spyRow).getByText('#1')).toBeInTheDocument();
    const qqqRow = screen.getByRole('row', { name: /QQQ/ });
    expect(within(qqqRow).getAllByText('—').length).toBeGreaterThan(3);
    expect(screen.getByText(/As of 2026-08-26/)).toBeInTheDocument();
    expect(screen.getByText('Missing 1: QQQ')).toBeInTheDocument();
    expect(api.getEtfRadar).toHaveBeenCalledWith('all');
  });

  it('uses the approved fixed categories and sends category selection to the API', async () => {
    const user = userEvent.setup();
    renderWithProviders(<EtfsPage />);
    await screen.findByText('SPY');

    await user.click(screen.getByRole('tab', { name: 'Semiconductor' }));

    await waitFor(() => expect(api.getEtfRadar).toHaveBeenLastCalledWith('semiconductor'));
    expect(screen.queryByRole('tab', { name: /leveraged/i })).not.toBeInTheDocument();
    expect(screen.queryByRole('tab', { name: /inverse/i })).not.toBeInTheDocument();
  });

  it('explains the deterministic score without predictive or recommendation language', async () => {
    renderWithProviders(<EtfsPage />);
    await screen.findByText('SPY');

    expect(screen.getByRole('heading', { name: 'Strength Score methodology' })).toBeInTheDocument();
    expect(screen.getByText(/30% RS 20D/)).toBeInTheDocument();
    expect(screen.getByText(/descriptive, not predictive/i)).toBeInTheDocument();
    expect(screen.getByText(/signed by 20D return direction/)).toBeInTheDocument();
    expect(screen.queryByText(/buy|expected return|upside potential/i)).not.toBeInTheDocument();
  });

  it('shows request failure and transparent empty states', async () => {
    api.getEtfRadar.mockRejectedValueOnce(new Error('ETF data unavailable'));
    const { unmount } = renderWithProviders(<EtfsPage />);
    expect(await screen.findByText('Unable to load ETF Radar: ETF data unavailable')).toBeInTheDocument();
    unmount();

    api.getEtfRadar.mockResolvedValueOnce({ ...payload, items: [], missing_symbols: [] });
    renderWithProviders(<EtfsPage />);
    expect(await screen.findByText('No ETFs are available for this category.')).toBeInTheDocument();
  });
});
