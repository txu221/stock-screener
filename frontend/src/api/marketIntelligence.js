import apiClient from './client';

const compactParams = (params = {}) => Object.fromEntries(
  Object.entries(params).filter(([, value]) => value !== undefined && value !== null && value !== '')
);

const getData = async (url, config) => {
  const response = config
    ? await apiClient.get(url, config)
    : await apiClient.get(url);
  return response.data;
};

export const marketIntelligenceKeys = {
  all: ['market-intelligence'],
  overview: () => ['market-intelligence', 'overview'],
  sectors: () => ['market-intelligence', 'sectors', 'latest'],
  sectorHistory: (filters = {}) => ['market-intelligence', 'sectors', 'history', filters],
  health: () => ['market-intelligence', 'health'],
  movers: (filters = {}) => ['market-intelligence', 'movers', filters],
  etfs: (category = 'all') => ['market-intelligence', 'etfs', category],
};

export const getMarketIntelligenceOverview = () => (
  getData('/v1/market-intelligence/overview')
);

export const getSectorLatest = () => (
  getData('/v1/market-intelligence/sectors/latest')
);

export const getSectorHistory = (filters = {}) => (
  getData('/v1/market-intelligence/sectors/history', { params: compactParams(filters) })
);

export const getMarketIntelligenceHealth = () => (
  getData('/v1/market-intelligence/sectors/health')
);

export const getMarketMovers = (filters = {}) => (
  getData('/v1/market-intelligence/movers', { params: compactParams(filters) })
);

export const getEtfRadar = (category = 'all') => (
  getData('/v1/market-intelligence/etfs', { params: { category } })
);
