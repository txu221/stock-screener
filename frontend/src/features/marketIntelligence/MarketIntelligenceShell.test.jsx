import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { renderWithProviders } from '../../test/renderWithProviders';
import MarketIntelligenceShell from './MarketIntelligenceShell';


describe('MarketIntelligenceShell', () => {
  it('renders the five approved destinations with the current section selected', () => {
    renderWithProviders(
      <MemoryRouter initialEntries={['/market-intelligence/sectors']}>
        <Routes>
          <Route path="/market-intelligence/*" element={<MarketIntelligenceShell />} />
        </Routes>
      </MemoryRouter>
    );

    expect(screen.getByRole('heading', { name: 'Market Intelligence' })).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: 'Today' })).toHaveAttribute('href', '/market-intelligence');
    expect(screen.getByRole('tab', { name: 'Movers' })).toHaveAttribute('href', '/market-intelligence/movers');
    expect(screen.getByRole('tab', { name: 'Sectors' })).toHaveAttribute('href', '/market-intelligence/sectors');
    expect(screen.getByRole('tab', { name: 'ETFs' })).toHaveAttribute('href', '/market-intelligence/etfs');
    expect(screen.getByRole('tab', { name: 'Data Health' })).toHaveAttribute('href', '/market-intelligence/health');
    expect(screen.getByRole('tab', { name: 'Sectors' })).toHaveAttribute('aria-selected', 'true');
  });
});

