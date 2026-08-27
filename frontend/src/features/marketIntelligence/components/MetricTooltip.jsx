import InfoOutlinedIcon from '@mui/icons-material/InfoOutlined';
import { Box, Tooltip } from '@mui/material';


export const METRIC_HELP = {
  rvol20: 'Today volume divided by the mean of the previous 20 completed sessions. Today is excluded.',
  relativeStrength: 'Descriptive relative strength: sector or ETF return minus SPY return for the same completed-session lookback.',
  flowPressure: 'OHLCV-derived pressure proxy. Not measured institutional or exchange net flow.',
  rankChange: 'A smaller rank number is stronger. Positive change means the instrument improved that many places.',
  strengthScore: 'Deterministic 0–100 descriptive strength score. It is not a prediction, expected return, or recommendation.',
};

export default function MetricTooltip({ metric, children }) {
  return (
    <Tooltip title={METRIC_HELP[metric] || ''} arrow>
      <Box component="span" sx={{ display: 'inline-flex', alignItems: 'center', gap: 0.4, cursor: 'help' }}>
        {children}
        <InfoOutlinedIcon aria-hidden="true" sx={{ fontSize: 13, color: 'text.secondary' }} />
      </Box>
    </Tooltip>
  );
}

