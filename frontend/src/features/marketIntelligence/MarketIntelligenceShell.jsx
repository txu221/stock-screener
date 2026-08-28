import { Box, Paper, Tab, Tabs, Typography } from '@mui/material';
import { Link as RouterLink, Outlet, useLocation } from 'react-router-dom';


const destinations = [
  { label: 'Today', path: '/market-intelligence' },
  { label: 'Movers', path: '/market-intelligence/movers' },
  { label: 'Sectors', path: '/market-intelligence/sectors' },
  { label: 'ETFs', path: '/market-intelligence/etfs' },
  { label: 'Data Health', path: '/market-intelligence/health' },
];

const selectedPath = (pathname) => {
  const exact = destinations.find((item) => item.path === pathname);
  if (exact) return exact.path;
  const nested = destinations
    .filter((item) => item.path !== '/market-intelligence')
    .find((item) => pathname.startsWith(`${item.path}/`));
  return nested?.path || '/market-intelligence';
};

export default function MarketIntelligenceShell() {
  const location = useLocation();

  return (
    <Box>
      <Paper variant="outlined" sx={{ mb: 1.5, overflow: 'hidden' }}>
        <Box sx={{ px: 2, pt: 1.5, pb: 0.5 }}>
          <Typography component="h1" variant="h6">
            Market Intelligence
          </Typography>
          <Typography variant="caption" color="text.secondary">
            Completed-session market leadership, movers, ETF strength, and data quality.
          </Typography>
        </Box>
        <Tabs
          value={selectedPath(location.pathname)}
          aria-label="Market Intelligence sections"
          variant="scrollable"
          scrollButtons="auto"
        >
          {destinations.map((item) => (
            <Tab
              key={item.path}
              label={item.label}
              value={item.path}
              component={RouterLink}
              to={item.path}
            />
          ))}
        </Tabs>
      </Paper>
      <Outlet />
    </Box>
  );
}
