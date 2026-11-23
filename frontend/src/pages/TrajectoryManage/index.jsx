import { useCallback, useEffect, useState } from 'react'
import {
  Button,
  Card,
  Col,
  Modal,
  Popconfirm,
  Row,
  Table,
  Tag,
  message,
} from 'antd'
import { ClearOutlined, RocketOutlined, StopOutlined, PlayCircleOutlined } from '@ant-design/icons'
import { getCleanCompare, listDataSources, rerunClean, switchDataSourceStatus } from '../../api/trajectory'
import './trajectory.css'

const TYPE_LABELS = {
  taxi: '出租车',
  ridehailing: '网约车',
  bikeshare: '共享单车',
}

// 清洗统计卡片：value/label 按截图排成一排四个
function CleanStats({ stats }) {
  const items = [
    { title: '原始点数', value: stats ? stats.raw_count : '-', suffix: stats ? '点' : '' },
    { title: '清洗后', value: stats ? stats.clean_count : '-', suffix: stats ? '点' : '' },
    { title: '丢弃点数', value: stats ? stats.dropped_count : '-', suffix: stats ? '点' : '' },
    { title: '清洗阶段', value: stats ? stats.segments : '-', suffix: stats ? '段' : '' },
  ]
  return (
    <Row gutter={16}>
      {items.map((item) => (
        <Col span={6} key={item.title}>
          <div className="clean-stat-box">
            <div className="clean-stat-title">{item.title}</div>
            <div className="clean-stat-value">
              {typeof item.value === 'number' ? item.value.toLocaleString() : item.value}
              {item.suffix && <span className="clean-stat-suffix">{item.suffix}</span>}
            </div>
          </div>
        </Col>
      ))}
    </Row>
  )
}

export default function TrajectoryManage() {
  const [sources, setSources] = useState([])
  const [stats, setStats] = useState(null)
  const [loading, setLoading] = useState(true)
  const [cleaningAll, setCleaningAll] = useState(false)
  const [cleaningId, setCleaningId] = useState(null)
  const [messageApi, contextHolder] = message.useMessage()

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const [list, compare] = await Promise.all([listDataSources(), getCleanCompare()])
      setSources(list)
      setStats(compare)
    } catch (err) {
      messageApi.error(err.message || '加载数据源失败，请确认后端已启动')
    } finally {
      setLoading(false)
    }
  }, [messageApi])

  useEffect(() => {
    load()
  }, [load])

  // 一键全链路：这里执行全量清洗（清洗是链路里唯一可重复安全重跑的一步，
  // 匹配与指标已有缓存/落表，页面提示用词与截图保持一致）
  const runFullPipeline = () => {
    Modal.confirm({
      title: '一键全链路',
      content: '将重跑全量轨迹清洗管道（约 50 万点，预计几十秒），确认继续？',
      okText: '开始',
      cancelText: '取消',
      onOk: async () => {
        setCleaningAll(true)
        try {
          const result = await rerunClean()
          messageApi.success(
            `清洗完成：原始 ${result.raw_count.toLocaleString()} 点，保留 ${result.clean_count.toLocaleString()} 点`,
          )
          await load()
        } catch (err) {
          messageApi.error(err.message || '清洗失败')
        } finally {
          setCleaningAll(false)
        }
      },
    })
  }

  const cleanOne = async (record) => {
    setCleaningId(record.id)
    try {
      const result = await rerunClean({ source_id: record.id })
      messageApi.success(`${record.name}清洗完成：保留 ${result.clean_count.toLocaleString()} 点`)
      await load()
    } catch (err) {
      messageApi.error(err.message || '清洗失败')
    } finally {
      setCleaningId(null)
    }
  }

  const toggleStatus = async (record) => {
    const next = record.status === 1 ? 0 : 1
    try {
      await switchDataSourceStatus(record.id, next)
      messageApi.success(`${record.name}已${next === 1 ? '启用' : '停用'}`)
      await load()
    } catch (err) {
      messageApi.error(err.message || '操作失败')
    }
  }

  const columns = [
    { title: 'ID', dataIndex: 'id', width: 60 },
    { title: '名称', dataIndex: 'name' },
    {
      title: '类型',
      dataIndex: 'type',
      render: (type) => <Tag color="blue">{TYPE_LABELS[type] || type}</Tag>,
    },
    { title: '车辆数', dataIndex: 'vehicle_count', width: 90 },
    {
      title: '轨迹点数',
      dataIndex: 'point_count',
      width: 110,
      render: (v) => (typeof v === 'number' ? v.toLocaleString() : v),
    },
    {
      title: '状态',
      dataIndex: 'status',
      width: 90,
      render: (status) =>
        status === 1 ? <Tag color="success">启用</Tag> : <Tag color="default">停用</Tag>,
    },
    {
      title: '操作',
      width: 200,
      render: (_, record) => (
        <div style={{ display: 'flex', gap: 8, whiteSpace: 'nowrap' }}>
          <Popconfirm
            title={`重新清洗「${record.name}」？`}
            description={`将重跑该数据源 ${record.point_count.toLocaleString()} 个原始点的清洗管道`}
            okText="清洗"
            cancelText="取消"
            onConfirm={() => cleanOne(record)}
          >
            <Button
              size="small"
              type="primary"
              ghost
              icon={<ClearOutlined />}
              loading={cleaningId === record.id}
            >
              清洗
            </Button>
          </Popconfirm>
          <Button size="small" icon={record.status === 1 ? <StopOutlined /> : <PlayCircleOutlined />} onClick={() => toggleStatus(record)}>
            {record.status === 1 ? '停用' : '启用'}
          </Button>
        </div>
      ),
    },
  ]

  return (
    <div className="page-trajectory">
      {contextHolder}
      <div className="page-titlebar">
        <h2 className="page-title">轨迹管理</h2>
        <Button type="primary" icon={<RocketOutlined />} loading={cleaningAll} onClick={runFullPipeline}>
          一键全链路
        </Button>
      </div>

      <Card
        title="数据源管理"
        extra={
          <Button type="primary" size="small" icon={<RocketOutlined />} loading={cleaningAll} onClick={runFullPipeline}>
            一键全链路
          </Button>
        }
        className="page-card"
      >
        <Table
          rowKey="id"
          columns={columns}
          dataSource={sources}
          loading={loading}
          pagination={false}
        />
      </Card>

      <Card title="清洗统计" className="page-card">
        <CleanStats stats={stats} />
        {stats && (
          <div className="clean-stat-foot">
            漂移点 {stats.dropped_count.toLocaleString()} 个被剔除；
            停留点 {stats.stay_points.toLocaleString()} 个、隧道丢失 {stats.gap_points.toLocaleString()} 个已标记保留
          </div>
        )}
      </Card>
    </div>
  )
}
