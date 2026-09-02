import { Route, Routes } from 'react-router-dom';

import DataHealthPage from './DataHealthPage';
import EtfsPage from './EtfsPage';
import MarketIntelligenceShell from './MarketIntelligenceShell';
import MoversPage from './MoversPage';
import SectorsPage from './SectorsPage';
import TodayPage from './TodayPage';


export default function MarketIntelligenceRoutes() {
  return (
    <Routes>
      <Route element={<MarketIntelligenceShell />}>
        <Route index element={<TodayPage />} />
        <Route path="movers" element={<MoversPage />} />
        <Route path="sectors" element={<SectorsPage />} />
        <Route path="etfs" element={<EtfsPage />} />
        <Route path="health" element={<DataHealthPage />} />
      </Route>
    </Routes>
  );
}
