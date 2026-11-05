import { NavLink } from 'react-router-dom'
import { menuItems } from '../config/menu'

export default function SideMenu() {
  return (
    <nav className="sider-menu">
      {menuItems.map((item) => (
        <NavLink
          key={item.path}
          to={item.path}
          end={item.path === '/'}
          className={({ isActive }) => `menu-item${isActive ? ' is-active' : ''}`}
        >
          <span className="menu-icon">{item.icon}</span>
          <span>{item.title}</span>
        </NavLink>
      ))}
    </nav>
  )
}
