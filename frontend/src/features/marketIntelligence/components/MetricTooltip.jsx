import InfoOutlinedIcon from '@mui/icons-material/InfoOutlined';
import { Box, Tooltip } from '@mui/material';

import { METRIC_HELP } from './metricHelp';

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
