import React from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import { ConfigProvider } from 'antd'
import zhCN from 'antd/locale/zh_CN'
import AppRouter from './router'
import { themeColor, themeColorSoft, themeColorDeep } from './config/theme'
import './styles/global.css'

// CSS 文件读不到 JS 常量，所以把主题色注入成 CSS 变量供 global.css 使用
document.documentElement.style.setProperty('--theme-color', themeColor)
document.documentElement.style.setProperty('--theme-color-soft', themeColorSoft)
document.documentElement.style.setProperty('--theme-color-deep', themeColorDeep)

createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <ConfigProvider locale={zhCN} theme={{ token: { colorPrimary: themeColor } }}>
      <BrowserRouter>
        <AppRouter />
      </BrowserRouter>
    </ConfigProvider>
  </React.StrictMode>,
)
