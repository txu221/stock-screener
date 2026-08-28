import { screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';

import { renderWithProviders } from '../../../test/renderWithProviders';
import MetricTooltip from './MetricTooltip';
import { METRIC_HELP } from './metricHelp';


describe('MetricTooltip', () => {
  it('discloses that Flow Pressure is a derived proxy', async () => {
    const user = userEvent.setup();
    renderWithProviders(<MetricTooltip metric="flowPressure">Flow Pressure</MetricTooltip>);

    await user.hover(screen.getByRole('button', { name: 'Explain Flow Pressure' }));

    expect(await screen.findByRole('tooltip')).toHaveTextContent(
      'OHLCV-derived pressure proxy. Not measured institutional or exchange net flow.'
    );
    expect(METRIC_HELP.rvol20).toMatch(/previous 20 completed sessions/i);
    expect(METRIC_HELP.relativeStrength).toMatch(/sector or ETF return minus SPY return/i);
    expect(METRIC_HELP.rankChange).toMatch(/smaller rank number is stronger/i);
  });

  it('exposes metric help from a keyboard-focusable labeled control', async () => {
    const user = userEvent.setup();
    renderWithProviders(<MetricTooltip metric="rvol20">RVOL20</MetricTooltip>);

    await user.tab();

    expect(screen.getByRole('button', { name: 'Explain RVOL20' })).toHaveFocus();
    expect(await screen.findByRole('tooltip')).toHaveTextContent(/previous 20 completed sessions/i);
  });
});
