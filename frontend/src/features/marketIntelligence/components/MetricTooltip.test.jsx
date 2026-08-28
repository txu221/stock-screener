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

    await user.hover(screen.getByText('Flow Pressure'));

    expect(await screen.findByRole('tooltip')).toHaveTextContent(
      'OHLCV-derived pressure proxy. Not measured institutional or exchange net flow.'
    );
    expect(METRIC_HELP.rvol20).toMatch(/previous 20 completed sessions/i);
    expect(METRIC_HELP.relativeStrength).toMatch(/sector or ETF return minus SPY return/i);
    expect(METRIC_HELP.rankChange).toMatch(/smaller rank number is stronger/i);
  });
});
