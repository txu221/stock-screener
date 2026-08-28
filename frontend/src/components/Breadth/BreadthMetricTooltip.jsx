import InfoOutlinedIcon from '@mui/icons-material/InfoOutlined';
import { Box, IconButton, Tooltip, Typography } from '@mui/material';

import { breadthMetricDefinitions } from './breadthMetricDefinitions';


function BreadthMetricTooltip({ metric, compact = false }) {
  const definition = breadthMetricDefinitions[metric];
  if (!definition) return null;

  const title = (
    <Box sx={{ p: 0.5 }}>
      <Typography variant="caption" sx={{ display: 'block', fontWeight: 700 }}>
        {definition.formulaOrigin}
        {definition.groupOrigin ? ` · ${definition.groupOrigin}` : ''}
      </Typography>
      <Typography variant="caption" sx={{ display: 'block', mt: 0.5 }}>
        {definition.description}
      </Typography>
      <Typography variant="caption" sx={{ display: 'block', mt: 0.5 }}>
        History: {definition.requiredHistory}
      </Typography>
      {definition.liquidityRule && (
        <Typography variant="caption" sx={{ display: 'block', mt: 0.5 }}>
          Liquidity: {definition.liquidityRule}
        </Typography>
      )}
    </Box>
  );

  return (
    <Tooltip title={title} arrow>
      <IconButton
        size="small"
        aria-label={`${definition.label} formula details`}
        sx={{ p: compact ? 0.15 : 0.3, ml: 0.25 }}
      >
        <InfoOutlinedIcon sx={{ fontSize: compact ? 12 : 14 }} />
      </IconButton>
    </Tooltip>
  );
}

export default BreadthMetricTooltip;
