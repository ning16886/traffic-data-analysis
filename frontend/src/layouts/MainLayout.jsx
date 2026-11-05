import { Outlet } from 'react-router-dom'
import SideMenu from '../components/SideMenu'
import HeaderBar from '../components/HeaderBar'

export default function MainLayout() {
  return (
    <div className="layout">
      <aside className="sider">
        <div className="sider-logo">
          <span className="sider-logo-emoji">🚗</span>
          <span>交通轨迹分析</span>
        </div>
        <SideMenu />
      </aside>
      <div className="layout-main">
        <HeaderBar />
        <main className="layout-content">
          <Outlet />
        </main>
      </div>
    </div>
  )
}
