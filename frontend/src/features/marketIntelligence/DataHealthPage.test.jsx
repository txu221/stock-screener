import { screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import * as api from '../../api/marketIntelligence';
import { renderWithProviders } from '../../test/renderWithProviders';
import DataHealthPage from './DataHealthPage';


vi.mock('../../api/marketIntelligence', () => ({
  marketIntelligenceKeys: {
    health: () => ['market-intelligence', 'health'],
  },
  getMarketIntelligenceHealth: vi.fn(),
}));

const partialHealth = {
  universe_expected: 12,
  current_run_timestamp: '2026-08-27T22:05:00Z',
  publication_occurred: false,
  last_complete_published_snapshot: '2026-08-26',
  latest_attempt: {
    run_id: 102,
    as_of: '2026-08-27',
    status: 'PARTIAL',
    lifecycle_status: 'quarantined',
    provider: 'yahoo',
    provider_status: 'DEGRADED',
    metric_version: 'market_intelligence_v1',
    counters: {
      expected_symbols: 12,
      symbols_received: 12,
      valid_bars: 11,
      rejected_bars: 1,
      missing_symbols: 1,
      invalid_volume: 1,
      invalid_ohlc: 0,
      duplicate_rows: 0,
    },
    missing_symbols: ['XLU'],
    source_freshness: { status: 'STALE_OR_MISSING' },
    ingestion_timestamp: '2026-08-27T22:05:00Z',
    published_at: null,
  },
  latest_published: {
    run_id: 101,
    as_of: '2026-08-26',
    status: 'SUCCEEDED',
    lifecycle_status: 'published',
    provider: 'yahoo',
    provider_status: 'AVAILABLE',
    metric_version: 'market_intelligence_v1',
    counters: {
      expected_symbols: 12,
      symbols_received: 12,
      valid_bars: 12,
      rejected_bars: 0,
      missing_symbols: 0,
      invalid_volume: 0,
      invalid_ohlc: 0,
      duplicate_rows: 0,
    },
    missing_symbols: [],
    source_freshness: { status: 'FRESH' },
    ingestion_timestamp: '2026-08-26T22:00:00Z',
    published_at: '2026-08-26T22:10:00Z',
  },
};

describe('DataHealthPage', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    api.getMarketIntelligenceHealth.mockResolvedValue(partialHealth);
  });

  it('separates a partial latest attempt from the displayed stable snapshot', async () => {
    renderWithProviders(<DataHealthPage />);

    expect(await screen.findByRole('heading', { name: 'Data Health' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Latest Data Attempt' })).toBeInTheDocument();
    expect(screen.getByText('PARTIAL')).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Currently Displayed Stable Snapshot' })).toBeInTheDocument();
    expect(screen.getByText('Displayed stable snapshot 2026-08-26')).toBeInTheDocument();
    expect(screen.getByText('XLU')).toBeInTheDocument();
    expect(screen.getByText('Invalid Volume')).toBeInTheDocument();
    expect(screen.getByText('1', { selector: '[data-health-value="invalid_volume"]' })).toBeInTheDocument();
  });

  it('renders a transparent empty state before any attempt exists', async () => {
    api.getMarketIntelligenceHealth.mockResolvedValueOnce({
      universe_expected: 12,
      current_run_timestamp: null,
      publication_occurred: false,
      last_complete_published_snapshot: null,
      latest_attempt: null,
      latest_published: null,
    });

    renderWithProviders(<DataHealthPage />);

    expect(await screen.findByText('No Market Intelligence run has been recorded yet.')).toBeInTheDocument();
  });

  it('shows loading and request failure states', async () => {
    api.getMarketIntelligenceHealth.mockRejectedValueOnce(new Error('health unavailable'));

    renderWithProviders(<DataHealthPage />);

    expect(await screen.findByText('Unable to load Data Health: health unavailable')).toBeInTheDocument();
  });
});
