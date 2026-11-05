import {
  DesktopOutlined,
  FileTextOutlined,
  CompassOutlined,
  SendOutlined,
  ShareAltOutlined,
  EnvironmentOutlined,
} from '@ant-design/icons'

export const menuItems = [
  {
    path: '/',
    title: '监控大屏',
    icon: <DesktopOutlined />,
    desc: '指标卡片 / 路网热力图 / 拥堵路段 Top10 / 24 小时趋势',
  },
  {
    path: '/trajectory',
    title: '轨迹管理',
    icon: <FileTextOutlined />,
    desc: '数据源表格 / 轨迹查询筛选分页 / 清洗配置与一键重跑',
  },
  {
    path: '/match',
    title: '地图匹配',
    icon: <CompassOutlined />,
    desc: '三层叠加可视化 / 车辆选择器 / 匹配率 Gauge / 匹配轨迹列表',
  },
  {
    path: '/road',
    title: '路段分析',
    icon: <SendOutlined />,
    desc: '路段详情 / 拥堵指数历史曲线 / 上下游路段 / 区域筛选与导出',
  },
  {
    path: '/od',
    title: 'OD 矩阵',
    icon: <ShareAltOutlined />,
    desc: 'OD 矩阵表格 / Top20 OD 对柱状图 / 早晚平峰 Tab 切换',
  },
  {
    path: '/hotzone',
    title: '出行热区',
    icon: <EnvironmentOutlined />,
    desc: 'DBSCAN 热力图 / 热区详情表格 / 热区类型 Tab',
  },
]
