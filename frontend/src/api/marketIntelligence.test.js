import { beforeEach, describe, expect, it, vi } from 'vitest';

import apiClient from './client';
import {
  marketIntelligenceKeys,
  getEtfRadar,
  getMarketIntelligenceHealth,
  getMarketIntelligenceOverview,
  getMarketMovers,
  getSectorHistory,
  getSectorLatest,
} from './marketIntelligence';

vi.mock('./client', () => ({
  default: { get: vi.fn() },
}));

describe('market intelligence API', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    apiClient.get.mockResolvedValue({ data: { ok: true } });
  });

  it('uses the existing versioned router for overview, sectors, health, and ETFs', async () => {
    await getMarketIntelligenceOverview();
    await getSectorLatest();
    await getMarketIntelligenceHealth();
    await getEtfRadar('semiconductor');

    expect(apiClient.get).toHaveBeenNthCalledWith(1, '/v1/market-intelligence/overview');
    expect(apiClient.get).toHaveBeenNthCalledWith(2, '/v1/market-intelligence/sectors/latest');
    expect(apiClient.get).toHaveBeenNthCalledWith(3, '/v1/market-intelligence/sectors/health');
    expect(apiClient.get).toHaveBeenNthCalledWith(4, '/v1/market-intelligence/etfs', {
      params: { category: 'semiconductor' },
    });
  });

  it('sends only defined mover filters and exact history query parameters', async () => {
    await getMarketMovers({
      direction: 'gainers',
      sector: 'Technology',
      min_price: 10,
      min_rvol: undefined,
      search: '',
      limit: 20,
    });
    await getSectorHistory({ symbol: 'XLK', limit: 30 });

    expect(apiClient.get).toHaveBeenNthCalledWith(1, '/v1/market-intelligence/movers', {
      params: {
        direction: 'gainers',
        sector: 'Technology',
        min_price: 10,
        limit: 20,
      },
    });
    expect(apiClient.get).toHaveBeenNthCalledWith(2, '/v1/market-intelligence/sectors/history', {
      params: { symbol: 'XLK', limit: 30 },
    });
  });

  it('returns response data and propagates request failures', async () => {
    apiClient.get.mockResolvedValueOnce({ data: { as_of: '2026-08-26' } });
    await expect(getMarketIntelligenceOverview()).resolves.toEqual({ as_of: '2026-08-26' });

    const error = new Error('network unavailable');
    apiClient.get.mockRejectedValueOnce(error);
    await expect(getMarketMovers()).rejects.toBe(error);
  });

  it('provides stable query keys for each data product and filter set', () => {
    expect(marketIntelligenceKeys.overview()).toEqual(['market-intelligence', 'overview']);
    expect(marketIntelligenceKeys.sectors()).toEqual(['market-intelligence', 'sectors', 'latest']);
    expect(marketIntelligenceKeys.health()).toEqual(['market-intelligence', 'health']);
    expect(marketIntelligenceKeys.movers({ direction: 'all' })).toEqual([
      'market-intelligence',
      'movers',
      { direction: 'all' },
    ]);
    expect(marketIntelligenceKeys.etfs('all')).toEqual(['market-intelligence', 'etfs', 'all']);
  });
});

