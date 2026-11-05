import { Link, useLocation } from 'react-router-dom'
import { Breadcrumb } from 'antd'
import { menuItems } from '../config/menu'

export default function HeaderBar() {
  const { pathname } = useLocation()
  const current = menuItems.find((item) => item.path === pathname)

  const items = [{ title: <Link to="/">首页</Link> }]
  if (current) {
    items.push({ title: current.title })
  }

  return (
    <header className="header">
      <Breadcrumb className="header-breadcrumb" items={items} />
    </header>
  )
}
