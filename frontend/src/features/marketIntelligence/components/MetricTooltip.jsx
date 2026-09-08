import InfoOutlinedIcon from '@mui/icons-material/InfoOutlined';
import { Box, IconButton, Tooltip } from '@mui/material';

import { METRIC_HELP } from './metricHelp';

export default function MetricTooltip({ metric, children }) {
  return (
    <Box component="span" sx={{ display: 'inline-flex', alignItems: 'center', gap: 0.2 }}>
      {children}
      <Tooltip title={METRIC_HELP[metric] || ''} arrow>
        <IconButton
          size="small"
          aria-label={`Explain ${String(children)}`}
          sx={{ p: 0.2 }}
        >
          <InfoOutlinedIcon aria-hidden="true" sx={{ fontSize: 13, color: 'text.secondary' }} />
        </IconButton>
      </Tooltip>
    </Box>
  );
}
