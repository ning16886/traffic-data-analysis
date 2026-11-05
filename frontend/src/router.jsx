import { Navigate, Route, Routes } from 'react-router-dom'
import MainLayout from './layouts/MainLayout'
import PlaceholderPage from './pages/PlaceholderPage'
import { menuItems } from './config/menu'

export default function AppRouter() {
  return (
    <Routes>
      <Route element={<MainLayout />}>
        {menuItems.map((item) => (
          <Route key={item.path} path={item.path} element={<PlaceholderPage item={item} />} />
        ))}
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  )
}
