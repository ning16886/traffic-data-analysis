import { useCallback, useEffect, useState } from 'react'
import {
  Alert,
  Button,
  Card,
  Col,
  Descriptions,
  Empty,
  Input,
  InputNumber,
  message,
  Row,
  Select,
  Skeleton,
  Spin,
  Statistic,
  Table,
  Tabs,
  Tag,
} from 'antd'
import { ReloadOutlined } from '@ant-design/icons'
import {
  getHotzoneDetail,
  getHotzoneMap,
  getHotzoneSummary,
  listHotzones,
  recomputeHotzones,
} from '../../api/hotzone'
import HotzoneMap, { TYPE_COLORS } from './HotzoneMap'
import './hotzone.css'

const TYPE_OPTIONS = [
  { key: 'all', label: '全部' },
  { key: 'residential', label: '居住区' },
  { key: 'business', label: '商业区' },
  { key: 'transit', label: '交通枢纽' },
]

const columns = [
  { title: '序号', dataIndex: 'rank', width: 56 },
  {
    title: '热区编号',
    dataIndex: 'zone_id',
    width: 110,
    render: (value) => <span className="hotzone-id">{value}</span>,
  },
  {
    title: '类型',
    dataIndex: 'type_label',
    width: 110,
    render: (label, row) => <Tag color={TYPE_COLORS[row.zone_type]}>{label}</Tag>,
  },
  {
    title: '中心经度',
    dataIndex: 'center_lng',
    width: 120,
    render: (v) => v.toFixed(5),
  },
  {
    title: '中心纬度',
    dataIndex: 'center_lat',
    width: 120,
    render: (v) => v.toFixed(5),
  },
  { title: '半径 (m)', dataIndex: 'radius_m', width: 110, render: (v) => v.toFixed(1) },
  { title: '活动点数', dataIndex: 'point_count', width: 110, align: 'right' },
  { title: '生成时间', dataIndex: 'create_time', width: 170 },
]

function MetricCards({ summary }) {
  const latest = summary.last_recompute || '尚未重算'
  return (
    <Row gutter={16} className="hotzone-cards">
      <Col span={6}>
        <Card>
          <Statistic title="热区总数" value={summary.total_zones} suffix="个" valueStyle={{ color: '#31a7fc' }} />
        </Card>
      </Col>
      <Col span={6}>
        <Card>
          <Statistic title="聚类活动点" value={summary.total_points} suffix="点" valueStyle={{ color: '#52c41a' }} />
        </Card>
      </Col>
      <Col span={6}>
        <Card>
          <Statistic title="平均点数 / 热区" value={summary.avg_points} valueStyle={{ color: '#fa8c16' }} />
        </Card>
      </Col>
      <Col span={6}>
        <Card>
          <Statistic title="DBSCAN 参数" value={`ε=${summary.params.eps_m}m`} suffix={`/ min=${summary.params.min_samples}`} valueStyle={{ fontSize: 20 }} />
          <div className="hotzone-foot">最近计算 {latest}</div>
        </Card>
      </Col>
    </Row>
  )
}

function ZoneDetail({ detail }) {
  if (!detail) {
    return <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="点击地图散点或表格行查看热区详情" />
  }
  return (
    <div>
      <Statistic
        title={`${detail.zone_id} · ${detail.type_label}`}
        value={detail.point_count}
        suffix="活动点"
        valueStyle={{ color: TYPE_COLORS[detail.zone_type] }}
      />
      <Descriptions column={1} size="small" className="hotzone-detail">
        <Descriptions.Item label="全局点数排名">第 {detail.rank} 名</Descriptions.Item>
        <Descriptions.Item label="中心坐标">
          {detail.center_lng.toFixed(5)}, {detail.center_lat.toFixed(5)}
        </Descriptions.Item>
        <Descriptions.Item label="簇半径">{detail.radius_m} m</Descriptions.Item>
        <Descriptions.Item label="生成时间">{detail.create_time}</Descriptions.Item>
      </Descriptions>
    </div>
  )
}

export default function HotZone() {
  const [activeType, setActiveType] = useState('all')
  const [keyword, setKeyword] = useState('')
  const [minPoints, setMinPoints] = useState(0)
  const [sortBy, setSortBy] = useState('point_count')
  const [pageNo, setPageNo] = useState(1)
  const [pageSize, setPageSize] = useState(20)
  const [refreshKey, setRefreshKey] = useState(0)

  const [summary, setSummary] = useState(null)
  const [mapData, setMapData] = useState({ zones: [], max_points: 0 })
  const [listData, setListData] = useState({ items: [], total: 0 })
  const [selectedId, setSelectedId] = useState(null)
  const [detail, setDetail] = useState(null)
  const [loading, setLoading] = useState(true)
  const [recomputing, setRecomputing] = useState(false)
  const [error, setError] = useState('')

  const zoneTypeParam = activeType === 'all' ? undefined : activeType

  useEffect(() => {
    let alive = true
    setLoading(true)
    setError('')
    Promise.all([getHotzoneSummary(), getHotzoneMap(zoneTypeParam)])
      .then(([s, m]) => {
        if (!alive) return
        setSummary(s)
        setMapData(m)
      })
      .catch((err) => alive && setError(err.message || '热区数据加载失败'))
      .finally(() => alive && setLoading(false))
    return () => { alive = false }
  }, [zoneTypeParam, refreshKey])

  useEffect(() => {
    let alive = true
    listHotzones({
      zone_type: zoneTypeParam,
      keyword,
      min_points: minPoints,
      page: pageNo,
      page_size: pageSize,
      sort_by: sortBy,
    })
      .then((data) => alive && setListData(data))
      .catch((err) => alive && setError(err.message || '热区列表加载失败'))
    return () => { alive = false }
  }, [zoneTypeParam, keyword, minPoints, pageNo, pageSize, sortBy, refreshKey])

  const handleSelect = useCallback((zoneId) => {
    setSelectedId(zoneId)
    getHotzoneDetail(zoneId)
      .then(setDetail)
      .catch((err) => message.error(err.message || '详情加载失败'))
  }, [])

  const handleRecompute = useCallback(() => {
    setRecomputing(true)
    recomputeHotzones()
      .then((result) => {
        message.success(
          `重算完成：${result.zone_count} 个热区 / ${result.activity_points} 个活动点，用时 ${result.elapsed_seconds}s`,
        )
        setSelectedId(null)
        setDetail(null)
        setPageNo(1)
        setRefreshKey((k) => k + 1)
      })
      .catch((err) => message.error(err.message || '重算失败'))
      .finally(() => setRecomputing(false))
  }, [])

  const tabs = TYPE_OPTIONS.map((item) => ({
    ...item,
    children: null,
    label: `${item.label}${summary?.by_type?.[item.key] ? ` (${summary.by_type[item.key].zone_count})` : ''}`,
  }))

  if (error && !summary) {
    return (
      <Alert
        type="error"
        showIcon
        message="出行热区数据加载失败"
        description={`${error}。请确认后端已启动，并已执行 python scripts/import_raw_data.py 导入原始数据。`}
        action={<Button size="small" onClick={() => setRefreshKey((k) => k + 1)}>重试</Button>}
      />
    )
  }

  return (
    <div>
      <div className="hotzone-toolbar">
        <span className="hotzone-hint">
          匹配成功轨迹的停留点 / 低速点 / 段首尾 → UTM 投影 → DBSCAN 聚类（ε=500m，min_samples=10）
        </span>
        <Button
          type="primary"
          icon={<ReloadOutlined />}
          loading={recomputing}
          onClick={handleRecompute}
        >
          一键重算热区
        </Button>
      </div>

      {error && summary && (
        <Alert type="warning" showIcon message={error} style={{ marginBottom: 12 }} />
      )}

      {loading && !summary ? (
        <Card><Skeleton active paragraph={{ rows: 8 }} /></Card>
      ) : (
        <>
          <MetricCards summary={summary} />
          <Tabs activeKey={activeType} items={tabs} onChange={(key) => { setActiveType(key); setPageNo(1); setSelectedId(null); setDetail(null) }} />

          <Row gutter={16} className="hotzone-map-row">
            <Col span={16}>
              <Card title="热区空间分布" extra={<span className="hotzone-hint">可缩放 / 拖拽，点击涟漪点查看详情</span>}>
                <Spin spinning={loading}>
                  {mapData.zones.length > 0 ? (
                    <HotzoneMap
                      zones={mapData.zones}
                      bounds={summary.map_bounds}
                      selectedZoneId={selectedId}
                      onSelect={handleSelect}
                    />
                  ) : (
                    <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="当前类型没有热区" />
                  )}
                </Spin>
                <div className="hotzone-foot">
                  点色=热区类型，点径=活动点数量；底层色阶=活动点密度（当前类型最多 {mapData.max_points} 点）
                </div>
              </Card>
            </Col>
            <Col span={8}>
              <Card title="热区详情" className="hotzone-detail-card">
                <ZoneDetail detail={detail} />
              </Card>
            </Col>
          </Row>

          <Card title="热区列表" className="hotzone-table-card">
            <div className="hotzone-filters">
              <Input.Search
                allowClear
                placeholder="搜索热区编号，如 HZ_012"
                style={{ width: 220 }}
                onSearch={(value) => { setKeyword(value); setPageNo(1) }}
                onChange={(e) => { if (!e.target.value) { setKeyword(''); setPageNo(1) } }}
              />
              <span>点数 ≥</span>
              <InputNumber
                min={0}
                max={100000}
                value={minPoints}
                onChange={(value) => { setMinPoints(value || 0); setPageNo(1) }}
              />
              <Select
                value={sortBy}
                style={{ width: 150 }}
                onChange={(value) => { setSortBy(value); setPageNo(1) }}
                options={[
                  { value: 'point_count', label: '按点数排序' },
                  { value: 'radius_m', label: '按半径排序' },
                  { value: 'zone_id', label: '按编号排序' },
                ]}
              />
            </div>
            <Table
              rowKey="zone_id"
              size="small"
              columns={columns}
              dataSource={listData.items}
              loading={loading}
              scroll={{ x: 1000 }}
              rowClassName={(row) => (row.zone_id === selectedId ? 'hotzone-row-selected' : '')}
              onRow={(row) => ({ onClick: () => handleSelect(row.zone_id) })}
              pagination={{
                current: pageNo,
                pageSize,
                total: listData.total,
                showSizeChanger: true,
                pageSizeOptions: [10, 20, 50],
                showTotal: (total) => `共 ${total} 个热区`,
                onChange: (p, ps) => { setPageNo(p); setPageSize(ps) },
              }}
            />
          </Card>
        </>
      )}
    </div>
  )
}
