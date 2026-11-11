import { useCallback, useEffect, useState } from 'react'
import { Alert, Button, Card, Col, Row, Skeleton, Statistic, Table, Tag, Tooltip } from 'antd'
import { RocketOutlined } from '@ant-design/icons'
import { getSummary } from '../../api/dashboard'
import { themeColor } from '../../config/theme'
import CongestionBar, { SEVERE_INDEX } from './CongestionBar'
import './dashboard.css'

const LEVEL_LABELS = { highway: '高速公路', main: '主干道', secondary: '次干道', branch: '支路' }

function useSummary() {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  const load = useCallback(() => {
    setLoading(true)
    setError('')
    return getSummary()
      .then(setData)
      .catch((err) => setError(err.message || '接口返回异常'))
      .finally(() => setLoading(false))
  }, [])

  useEffect(() => {
    load()
  }, [load])

  return { data, loading, error, reload: load }
}

const CARD_COLORS = {
  blue: themeColor,
  green: '#52c41a',
  orange: '#fa8c16',
  red: '#ff4d4f',
}

function MetricCards({ cards, threshold }) {
  return (
    <Row gutter={16} className="dashboard-cards">
      <Col span={6}>
        <Card>
          <Statistic
            title="在线车辆数"
            value={cards.online_vehicle_count}
            suffix="辆"
            valueStyle={{ color: CARD_COLORS.blue }}
          />
          <div className="dashboard-metric-foot">
            {cards.data_day} 当日原始轨迹去重后的车辆数
          </div>
        </Card>
      </Col>
      <Col span={6}>
        <Card>
          <Statistic
            title="今日轨迹点"
            value={cards.today_point_count}
            suffix="点"
            valueStyle={{ color: CARD_COLORS.green }}
          />
          <div className="dashboard-metric-foot">清洗后保留 {cards.today_clean_point_count.toLocaleString()} 点</div>
        </Card>
      </Col>
      <Col span={6}>
        <Card>
          <Statistic
            title="平均速度"
            value={cards.today_avg_speed.toFixed(1)}
            suffix="km/h"
            valueStyle={{ color: CARD_COLORS.orange }}
          />
          <div className="dashboard-metric-foot">当日清洗后轨迹点速度均值</div>
        </Card>
      </Col>
      <Col span={6}>
        <Card>
          <Statistic
            title="拥堵路段占比"
            value={(cards.today_congestion_ratio * 100).toFixed(1)}
            suffix="%"
            valueStyle={{ color: cards.today_congested_road_count > 0 ? CARD_COLORS.red : undefined }}
          />
          <div className="dashboard-metric-foot">
            当日 {cards.today_covered_road_count} 条有达标指标的路段中 {cards.today_congested_road_count} 条指数 ≥{' '}
            {threshold}
          </div>
        </Card>
      </Col>
    </Row>
  )
}

function CongestionTop({ data }) {
  const { threshold, cards } = data
  return (
    <Card title={`拥堵路段 Top${data.top_n}`} className="dashboard-full-card">
      {data.top_congested.length === 0 ? (
        <div className="dashboard-metric-foot">当前数据里没有任何路段指标</div>
      ) : (
        <CongestionBar rows={data.top_congested} threshold={threshold} levelLabels={LEVEL_LABELS} />
      )}
      <div className="dashboard-foot">
        <span>
          排名基数：{cards.data_day} 当日有达标指标的 {cards.today_covered_road_count} /{' '}
          {cards.total_road_count} 条路段，每条路段取当日最堵的一窗
        </span>
        <span>
          柱色 红 ≥ {SEVERE_INDEX} · 橙 ≥ {threshold} · 蓝 &lt; {threshold}
        </span>
      </div>
    </Card>
  )
}

const sourceColumns = [
  { title: '名称', dataIndex: 'name' },
  {
    title: '类型',
    dataIndex: 'type',
    render: (type) => <Tag>{type}</Tag>,
  },
  {
    title: '轨迹点数',
    dataIndex: 'point_count',
    align: 'right',
    render: (count, row) =>
      count === row.declared_point_count ? (
        count
      ) : (
        <Tooltip title={`登记表自报 ${row.declared_point_count} 点，与原始轨迹表对不上，这里显示实测值`}>
          <span>{count}</span>
        </Tooltip>
      ),
  },
  { title: '车辆数', dataIndex: 'vehicle_count', align: 'right' },
  {
    title: '状态',
    render: (_, row) =>
      row.status === 1 ? <Tag color="green">运行中</Tag> : <Tag color="default">已停用</Tag>,
  },
]

function SourceTable({ data }) {
  return (
    <Card title="数据源统计" className="dashboard-full-card">
      <Table rowKey="id" size="small" pagination={false} columns={sourceColumns} dataSource={data.data_sources} />
      <div className="dashboard-foot">
        <span>统计口径：raw_trajectory 按 source_id 实测计数</span>
      </div>
    </Card>
  )
}

export default function Dashboard() {
  const { data, loading, error, reload } = useSummary()

  if (error) {
    return (
      <Alert
        type="error"
        showIcon
        message="大屏数据加载失败"
        description={`${error}。请确认后端已启动（backend 目录下 python -m uvicorn app.main:app --host 127.0.0.1 --port 8000），并且已跑过 python scripts/import_raw_data.py 把原始数据导入。`}
        action={
          <Button size="small" onClick={reload}>
            重试
          </Button>
        }
      />
    )
  }

  if (!data) {
    return (
      <Card>
        <Skeleton active paragraph={{ rows: 8 }} />
      </Card>
    )
  }

  return (
    <div>
      <div className="dashboard-toolbar">
        <Tooltip title="后端暂无全链路任务接口，这里只重新聚合一次大屏指标（导入 → 清洗 → 匹配 → 指标的定时任务还没做）">
          <Button type="primary" icon={<RocketOutlined />} loading={loading} onClick={reload}>
            一键全链路
          </Button>
        </Tooltip>
      </div>

      <div className="dashboard-meta">
        <span>
          数据区间 {data.time_range.start?.slice(0, 16)} – {data.time_range.end?.slice(0, 16)}
        </span>
        <span>
          四张卡与 Top{data.top_n} 口径：数据末日 {data.cards.data_day}，底层 {data.metrics_window} 指标聚合到{' '}
          {data.window} 展示
        </span>
      </div>

      <MetricCards cards={data.cards} threshold={data.threshold} />

      <Row gutter={16}>
        <Col span={14}>
          <CongestionTop data={data} />
        </Col>
        <Col span={10}>
          <SourceTable data={data} />
        </Col>
      </Row>
    </div>
  )
}
