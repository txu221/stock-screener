import { screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { renderWithProviders } from '../../../test/renderWithProviders';
import FreshnessBanner from './FreshnessBanner';


describe('FreshnessBanner', () => {
  it('shows source lineage and metric version without implying current data', () => {
    renderWithProviders(
      <FreshnessBanner
        asOf="2026-08-26"
        lastUpdated="2026-08-26T22:05:00Z"
        provider="yahoo"
        metricVersion="market_intelligence_v1"
        priceBasis="adjusted"
        freshnessStatus="FRESH"
        expectedSession="2026-08-26"
      />
    );

    expect(screen.getByText(/As of 2026-08-26/)).toBeInTheDocument();
    expect(screen.getByText(/Last updated/)).toHaveTextContent('2026');
    expect(screen.getByText(/Provider yahoo/)).toBeInTheDocument();
    expect(screen.getByText(/Metric market_intelligence_v1/)).toBeInTheDocument();
    expect(screen.getByText(/Price basis adjusted/)).toBeInTheDocument();
    expect(screen.getByText('Freshness FRESH')).toBeInTheDocument();
  });

  it('calls out a partial attempt and the older stable snapshot separately', () => {
    renderWithProviders(
      <FreshnessBanner
        asOf="2026-08-27"
        provider="yahoo"
        metricVersion="market_intelligence_v1"
        status="PARTIAL"
        stableAsOf="2026-08-26"
      />
    );

    expect(screen.getByText('Latest attempt PARTIAL')).toBeInTheDocument();
    expect(screen.getByText('Displayed stable snapshot 2026-08-26')).toBeInTheDocument();
  });

  it('states that historical analytical returns are adjusted when provenance is complete', () => {
    renderWithProviders(
      <FreshnessBanner priceHistoryQuality="corporate_action_adjusted" />
    );

    expect(screen.getByText(
      'Historical analytical returns use corporate-action-adjusted prices.'
    )).toBeInTheDocument();
  });

  it('discloses the limitation when corporate-action provenance is partial', () => {
    renderWithProviders(
      <FreshnessBanner priceHistoryQuality="partial_corporate_action_adjustment" />
    );

    expect(screen.getByText(
      'Historical analytical returns have partial corporate-action-adjusted price coverage; legacy or unverified rows may be included.'
    )).toBeInTheDocument();
    expect(screen.queryByText(
      'Historical analytical returns use corporate-action-adjusted prices.'
    )).not.toBeInTheDocument();
  });
});
