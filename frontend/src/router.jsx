import { Navigate, Route, Routes } from 'react-router-dom'
import MainLayout from './layouts/MainLayout'
import { menuItems } from './config/menu'
import Dashboard from './pages/Dashboard'
import TrajectoryManage from './pages/TrajectoryManage'
import MatchResult from './pages/MatchResult'
import RoadAnalysis from './pages/RoadAnalysis'
import ODMatrix from './pages/ODMatrix'
import HotZone from './pages/HotZone'

const pageMap = {
  '/': Dashboard,
  '/trajectory': TrajectoryManage,
  '/match': MatchResult,
  '/road': RoadAnalysis,
  '/od': ODMatrix,
  '/hotzone': HotZone,
}

export default function AppRouter() {
  return (
    <Routes>
      <Route element={<MainLayout />}>
        {menuItems.map((item) => {
          const Page = pageMap[item.path]
          return <Route key={item.path} path={item.path} element={<Page />} />
        })}
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  )
}
