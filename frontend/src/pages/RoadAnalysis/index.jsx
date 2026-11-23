import { useCallback, useEffect, useMemo, useState } from 'react'
import { Button, Card, Col, Descriptions, Empty, Row, Table, Tag, message } from 'antd'
import { ReloadOutlined, ThunderboltOutlined } from '@ant-design/icons'
import { listRoadMetrics } from '../../api/road'
import { LEVEL_LABELS } from './roadConstants'
import './road.css'

// 拥堵条颜色：>=70% 红，>=40% 橙黄，其余绿（阈值口径见右侧说明文字）
function barColor(percent) {
  if (percent >= 70) return '#ff4d4f'
  if (percent >= 40) return '#faad14'
  return '#52c41a'
}

function CongestionBar({ percent }) {
  return (
    <div className="road-congestion-cell">
      <div className="road-congestion-track">
        <div
          className="road-congestion-fill"
          style={{ width: `${Math.round(percent)}%`, background: barColor(percent) }}
        />
      </div>
      <span className="road-congestion-num">{Math.round(percent)}%</span>
    </div>
  )
}

export default function RoadAnalysis() {
  const [rows, setRows] = useState([])
  const [loading, setLoading] = useState(true)
  const [selected, setSelected] = useState(null)
  const [messageApi, contextHolder] = message.useMessage()

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const data = await listRoadMetrics({ limit: 600 })
      setRows(data)
      setSelected((prev) => data.find((r) => r.road_id === prev?.road_id) || data[0] || null)
    } catch (err) {
      messageApi.error(err.message || '加载路段指标失败，请确认后端已启动')
    } finally {
      setLoading(false)
    }
  }, [messageApi])

  useEffect(() => {
    load()
  }, [load])

  const columns = useMemo(
    () => [
      {
        title: '路段ID',
        dataIndex: 'road_id',
        width: 100,
        render: (v, record) => (
          <span className={selected?.road_id === v ? 'road-id-selected' : ''}>
            {v}
            {selected?.road_id === v && <span style={{ color: '#ff4d4f', marginLeft: 4 }}>★</span>}
          </span>
        ),
      },
      {
        title: '平均速度',
        dataIndex: 'avg_speed',
        width: 100,
        render: (v) => (v === null ? '-' : `${v.toFixed(1)}`),
      },
      {
        title: '拥堵指数',
        dataIndex: 'congestion_percent',
        render: (percent) => <CongestionBar percent={percent || 0} />,
      },
    ],
    [selected],
  )

  const total = rows.length || 1
  const congested = rows.filter((r) => r.congestion_index > 0.25).length

  return (
    <div className="page-road">
      {contextHolder}
      <div className="page-titlebar">
        <h2 className="page-title">路段分析</h2>
        <Button type="primary" icon={<ThunderboltOutlined />}>一键全链路</Button>
      </div>

      <Card
        title="路段拥堵分析"
        extra={
          <Button icon={<ReloadOutlined />} onClick={load} loading={loading}>
            刷新
          </Button>
        }
        className="page-card"
      >
        <Row gutter={24}>
          <Col span={12}>
            <Table
              rowKey="road_id"
              columns={columns}
              dataSource={rows}
              loading={loading}
              pagination={{ pageSize: 15, showSizeChanger: false, showTotal: (t) => `共 ${t} 条路段` }}
              size="middle"
              onRow={(record) => ({
                onClick: () => setSelected(record),
                style: { cursor: 'pointer' },
              })}
              rowClassName={(record) => (selected?.road_id === record.road_id ? 'road-row-selected' : '')}
            />
          </Col>
          <Col span={12}>
            {selected ? (
              <div className="road-detail-panel">
                <div className="road-detail-title">
                  <ThunderboltOutlined style={{ color: '#ff4d4f', marginRight: 8 }} />
                  {selected.road_id}
                </div>
                <div className="road-detail-name">
                  {selected.road_name} · {LEVEL_LABELS[selected.level] || selected.level}
                </div>
                <Descriptions
                  column={2}
                  bordered
                  size="small"
                  className="road-detail-desc"
                  items={[
                    { key: 'id', label: '路段ID', children: selected.road_id },
                    { key: 'speed', label: '平均速度', children: `${selected.avg_speed?.toFixed(1) ?? '-'} km/h` },
                    {
                      key: 'ff',
                      label: '自由流速度',
                      children:
                        selected.free_flow_speed === null || selected.free_flow_speed === undefined
                          ? '-'
                          : `${selected.free_flow_speed.toFixed(1)} km/h`,
                    },
                    {
                      key: 'idx',
                      label: '拥堵指数',
                      children: `${((selected.congestion_index || 0) * 100).toFixed(1)}%`,
                    },
                  ]}
                />
                <div className="road-detail-legend">
                  <p>
                    拥堵指数 = 1 − 实际速度 / 自由流速度（clamp 0~1）
                  </p>
                  <p>
                    <span className="legend-dot" style={{ background: '#52c41a' }} />0 = 畅通，
                    <span className="legend-dot" style={{ background: '#faad14' }} />1 = 完全拥堵；
                    阈值参考：&gt; 0.4 为拥堵
                  </p>
                  <p>
                    当前窗 {selected.window || '-'} · 窗内样本 {selected.point_count ?? '-'} 点 ·
                    本页拥堵路段（指数&gt;0.25）{congested} / {rows.length} 条
                  </p>
                </div>
              </div>
            ) : (
              <Empty description={loading ? '加载中…' : '点击左侧路段查看详情'} />
            )}
          </Col>
        </Row>
      </Card>
    </div>
  )
}
