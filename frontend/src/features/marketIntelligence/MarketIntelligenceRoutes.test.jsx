import { screen } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { describe, expect, it, vi } from 'vitest';

import { renderWithProviders } from '../../test/renderWithProviders';
import MarketIntelligenceRoutes from './MarketIntelligenceRoutes';


vi.mock('./TodayPage', () => ({ default: () => <div>Today route content</div> }));
vi.mock('./MoversPage', () => ({ default: () => <div>Movers route content</div> }));
vi.mock('./SectorsPage', () => ({ default: () => <div>Sectors route content</div> }));
vi.mock('./EtfsPage', () => ({ default: () => <div>ETFs route content</div> }));
vi.mock('./DataHealthPage', () => ({ default: () => <div>Health route content</div> }));

const renderRoute = (path) => renderWithProviders(
  <MemoryRouter initialEntries={[path]}>
    <Routes>
      <Route path="/market-intelligence/*" element={<MarketIntelligenceRoutes />} />
    </Routes>
  </MemoryRouter>
);

describe('MarketIntelligenceRoutes', () => {
  it.each([
    ['/market-intelligence', 'Today route content', 'Today'],
    ['/market-intelligence/movers', 'Movers route content', 'Movers'],
    ['/market-intelligence/sectors', 'Sectors route content', 'Sectors'],
    ['/market-intelligence/etfs', 'ETFs route content', 'ETFs'],
    ['/market-intelligence/health', 'Health route content', 'Data Health'],
  ])('routes %s through the shared product shell', (path, content, selectedTab) => {
    renderRoute(path);

    expect(screen.getByRole('heading', { name: 'Market Intelligence' })).toBeInTheDocument();
    expect(screen.getByText(content)).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: selectedTab })).toHaveAttribute('aria-selected', 'true');
  });
});
