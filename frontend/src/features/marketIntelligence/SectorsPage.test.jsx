import { screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import * as api from '../../api/marketIntelligence';
import { renderWithProviders } from '../../test/renderWithProviders';
import SectorsPage from './SectorsPage';


vi.mock('../../api/marketIntelligence', () => ({
  marketIntelligenceKeys: {
    sectors: () => ['market-intelligence', 'sectors', 'latest'],
  },
  getSectorLatest: vi.fn(),
}));

const symbols = ['XLC', 'XLY', 'XLP', 'XLE', 'XLF', 'XLV', 'XLI', 'XLB', 'XLRE', 'XLK', 'XLU'];

const sectorPayload = {
  as_of: '2026-08-26',
  published_at: '2026-08-26T22:10:00Z',
  provider: 'yahoo',
  metric_version: 'market_intelligence_v1',
  status: 'SUCCEEDED',
  benchmark: { symbol: 'SPY', returns: { '1d': 0.005, '20d': 0.04 } },
  sectors: symbols.map((symbol, index) => ({
    symbol,
    name: symbol === 'XLK' ? 'Technology' : `Sector ${index + 1}`,
    returns: { '1d': (index + 1) / 1000, '5d': 0.02, '20d': 0.10 + index / 100, '60d': 0.20 },
    relative_strength: {
      '1d_vs_spy': index / 1000,
      '5d_vs_spy': 0.01,
      '20d_vs_spy': 0.06 + index / 100,
      '60d_vs_spy': 0.08,
    },
    rvol20: 1.1 + index / 10,
    flow_pressure_proxy: {
      metric_type: 'derived_proxy',
      flow_pressure_1d: 0.1,
      cmf_5d: 0.2,
      cmf_20d: 0.3,
      cmf_60d: 0.4,
    },
    ranks: {
      relative_return_vs_spy_1d: index + 1,
      relative_return_vs_spy_5d: index + 1,
      relative_return_vs_spy_20d: index + 1,
      relative_return_vs_spy_60d: index + 1,
    },
    previous_ranks: {
      relative_return_vs_spy_1d: index === 0 ? 5 : index + 1,
      relative_return_vs_spy_5d: index + 1,
      relative_return_vs_spy_20d: index + 1,
      relative_return_vs_spy_60d: index + 1,
    },
    rank_changes: {
      relative_return_vs_spy_1d: index === 0 ? 4 : index === 1 ? -2 : 0,
      relative_return_vs_spy_5d: 0,
      relative_return_vs_spy_20d: 0,
      relative_return_vs_spy_60d: 0,
    },
    rank_directions: {
      relative_return_vs_spy_1d: index === 0 ? 'IMPROVED' : index === 1 ? 'DECLINED' : 'UNCHANGED',
      relative_return_vs_spy_5d: 'UNCHANGED',
      relative_return_vs_spy_20d: 'UNCHANGED',
      relative_return_vs_spy_60d: 'UNCHANGED',
    },
  })),
};

describe('SectorsPage', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    api.getSectorLatest.mockResolvedValue(sectorPayload);
  });

  it('renders exactly the fixed 11 sectors and keeps SPY benchmark-only', async () => {
    renderWithProviders(<SectorsPage />);

    expect(await screen.findByRole('heading', { name: 'Sector Heatmap' })).toBeInTheDocument();
    expect(screen.getAllByTestId('sector-tile')).toHaveLength(11);
    expect(screen.getByText('Benchmark SPY')).toBeInTheDocument();
    expect(screen.queryByTestId('sector-SPY')).not.toBeInTheDocument();
    expect(screen.getByText('+4 ↑ IMPROVED')).toBeInTheDocument();
    expect(screen.getByText('-2 ↓ DECLINED')).toBeInTheDocument();
  });

  it('switches periods without recalculating backend financial values', async () => {
    const user = userEvent.setup();
    renderWithProviders(<SectorsPage />);
    await screen.findByRole('heading', { name: 'Sector Heatmap' });

    await user.click(screen.getByRole('button', { name: '20D' }));

    const xlc = screen.getByTestId('sector-XLC');
    expect(xlc).toHaveTextContent('+10.00%');
    expect(xlc).toHaveTextContent('RS +6.00%');
  });

  it('uses the exact derived-proxy disclosure in the Flow Pressure tooltip', async () => {
    const user = userEvent.setup();
    renderWithProviders(<SectorsPage />);
    await screen.findByRole('heading', { name: 'Sector Heatmap' });

    await user.hover(screen.getByText('Flow Pressure'));

    expect(await screen.findByRole('tooltip')).toHaveTextContent(
      'OHLCV-derived pressure proxy. Not measured institutional or exchange net flow.'
    );
  });

  it('shows loading, error, and empty states explicitly', async () => {
    api.getSectorLatest.mockRejectedValueOnce(new Error('snapshot unavailable'));
    const { unmount } = renderWithProviders(<SectorsPage />);
    expect(await screen.findByText('Unable to load sector intelligence: snapshot unavailable')).toBeInTheDocument();
    unmount();

    api.getSectorLatest.mockResolvedValueOnce({ ...sectorPayload, sectors: [] });
    renderWithProviders(<SectorsPage />);
    expect(await screen.findByText('No published sector rows are available.')).toBeInTheDocument();
  });
});

