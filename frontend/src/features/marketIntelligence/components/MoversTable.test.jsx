import { screen, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { renderWithProviders } from '../../../test/renderWithProviders';
import MoversTable from './MoversTable';


const rows = [
  {
    symbol: 'NVDA',
    company_name: 'NVIDIA',
    price: 190.5,
    change_1d: 0.075,
    volume: 80_000_000,
    rvol20: 3.2,
    sector: 'Technology',
    industry: 'Semiconductors',
    market_cap: 4_000_000_000_000,
  },
  {
    symbol: 'AAPL',
    company_name: 'Apple',
    price: 230,
    change_1d: -0.025,
    volume: null,
    rvol20: null,
    sector: 'Technology',
    industry: null,
    market_cap: null,
  },
];

describe('MoversTable', () => {
  it('preserves backend order, shows nulls, and labels gain/loss without color alone', () => {
    renderWithProviders(<MoversTable title="Unusual Volume" rows={rows} />);

    const bodyRows = screen.getAllByRole('row').slice(1);
    expect(within(bodyRows[0]).getByText('NVDA')).toBeInTheDocument();
    expect(within(bodyRows[1]).getByText('AAPL')).toBeInTheDocument();
    expect(within(bodyRows[0]).getByText('↑ GAIN')).toBeInTheDocument();
    expect(within(bodyRows[1]).getByText('↓ LOSS')).toBeInTheDocument();
    expect(within(bodyRows[1]).getAllByText('—').length).toBeGreaterThanOrEqual(3);
    expect(screen.getByText('3.20')).toBeInTheDocument();
  });

  it('renders an explicit empty state', () => {
    renderWithProviders(<MoversTable title="Top Gainers" rows={[]} />);

    expect(screen.getByText('No Top Gainers match the current filters.')).toBeInTheDocument();
  });
});

