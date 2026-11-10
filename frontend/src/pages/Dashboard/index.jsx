import { useCallback, useEffect, useState } from 'react'
import { Alert, Button, Card, Col, Progress, Row, Skeleton, Statistic, Table, Tag, Tooltip } from 'antd'
import { getSummary } from '../../api/dashboard'
import { themeColor } from '../../config/theme'
import './dashboard.css'

const LEVEL_LABELS = { highway: '高速公路', main: '主干道', secondary: '次干道', branch: '支路' }

// 拥堵指数 0~1 换算成百分比条，超过阈值的路段单独变红，与演示大屏一致
function barColor(index, threshold) {
  return index >= threshold ? '#ff4d4f' : themeColor
}

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

function MetricCards({ cards, threshold }) {
  return (
    <Row gutter={16} className="dashboard-cards">
      <Col span={6}>
        <Card>
          <Statistic title="今日车辆数" value={cards.vehicle_count} suffix="辆" />
          <div className="dashboard-metric-foot">原始轨迹表去重后的车辆数</div>
        </Card>
      </Col>
      <Col span={6}>
        <Card>
          <Statistic title="今日轨迹点" value={cards.raw_point_count} suffix="点" />
          <div className="dashboard-metric-foot">清洗后保留 {cards.clean_point_count.toLocaleString()} 点</div>
        </Card>
      </Col>
      <Col span={6}>
        <Card>
          <Statistic title="平均速度" value={cards.avg_speed} precision={1} suffix="km/h" />
          <div className="dashboard-metric-foot">全部清洗后轨迹点速度均值</div>
        </Card>
      </Col>
      <Col span={6}>
        <Card>
          <Statistic
            title="拥堵路段占比"
            value={cards.congestion_ratio * 100}
            precision={1}
            suffix="%"
            valueStyle={{ color: cards.congested_road_count > 0 ? '#ff4d4f' : undefined }}
          />
          <div className="dashboard-metric-foot">
            {cards.covered_road_count} 条有数据路段中 {cards.congested_road_count} 条指数 ≥ {threshold}
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
      {data.top_congested.length === 0 && (
        <div className="dashboard-metric-foot">当前数据里没有任何路段指标</div>
      )}
      {data.top_congested.map((row, i) => (
        <div className="dashboard-top-item" key={row.road_id}>
          <span className={`dashboard-top-rank${row.congestion_index >= threshold ? ' is-congested' : ''}`}>
            {i + 1}
          </span>
          <Tooltip title={`${row.road_id} · ${LEVEL_LABELS[row.level] || row.level} · 最堵时段 ${row.window}`}>
            <span className="dashboard-top-name">{row.road_name}</span>
          </Tooltip>
          <div className="dashboard-top-bar">
            <Progress
              percent={row.congestion_index * 100}
              size={['100%', 12]}
              strokeColor={barColor(row.congestion_index, threshold)}
              format={(p) => `${p.toFixed(1)}%`}
            />
          </div>
          <span className="dashboard-top-speed">{row.avg_speed} km/h</span>
        </div>
      ))}
      <div className="dashboard-foot">
        <span>
          排名只覆盖有指标的 {cards.covered_road_count} / {cards.total_road_count} 条路段，另外{' '}
          {cards.uncovered_road_count} 条全天没有轨迹点经过
        </span>
        <span>每条路段取当天最堵的一窗</span>
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

  if (loading || !data) {
    return (
      <Card>
        <Skeleton active paragraph={{ rows: 8 }} />
      </Card>
    )
  }

  return (
    <div>
      <div className="dashboard-meta">
        <span>
          数据日期 {data.data_date} {data.time_range.start?.slice(11, 16)} – {data.time_range.end?.slice(11, 16)}
        </span>
        <span>
          底层指标窗口 {data.metrics_window}，大屏按 {data.window} 聚合
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
