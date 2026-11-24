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
import { ClearOutlined, RocketOutlined } from '@ant-design/icons'
import { getCleanCompare, listDataSources, rerunClean } from '../../api/trajectory'
import './trajectory.css'

const TYPE_LABELS = {
  taxi: '出租车',
  ridehailing: '网约车',
  bikeshare: '共享单车',
}

// 清洗统计四个数字框。stats 尚未加载完成时统一显示占位符
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

// 清洗统计脚注：漂移点剔除数量与停留、隧道两类标记保留数量
function CleanStatsFoot({ stats }) {
  if (!stats) return null
  return (
    <div className="clean-stat-foot">
      漂移点 {stats.dropped_count.toLocaleString()} 个被剔除；
      停留点 {stats.stay_points.toLocaleString()} 个、隧道丢失 {stats.gap_points.toLocaleString()} 个已标记保留
    </div>
  )
}

export default function TrajectoryManage() {
  const [sources, setSources] = useState([])
  const [stats, setStats] = useState(null) // 全部数据的清洗统计
  const [statsBySource, setStatsBySource] = useState({}) // 每一类车型各自的清洗统计
  const [expandedRowKeys, setExpandedRowKeys] = useState([])
  const [loading, setLoading] = useState(true)
  const [cleaningAll, setCleaningAll] = useState(false)
  const [cleaningId, setCleaningId] = useState(null)
  const [messageApi, contextHolder] = message.useMessage()

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const list = await listDataSources()
      setSources(list)
      // 全量统计与三类车型统计分开请求：每类车型只加载自己那一份，互不依赖
      const [all, ...perSource] = await Promise.all([
        getCleanCompare(),
        ...list.map((item) => getCleanCompare(item.id)),
      ])
      setStats(all)
      setStatsBySource(
        list.reduce((acc, item, index) => ({ ...acc, [item.id]: perSource[index] }), {}),
      )
      // 首次进入默认展开三行，让每类车型的统计直接出现在其清洗按钮下方
      setExpandedRowKeys((prev) => (prev.length ? prev : list.map((item) => item.id)))
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

  // 单源清洗：只重跑该数据源，刷新全量与三类统计，并确保这一行展开
  const cleanOne = async (record) => {
    setCleaningId(record.id)
    try {
      const result = await rerunClean({ source_id: record.id })
      messageApi.success(`${record.name}清洗完成：保留 ${result.clean_count.toLocaleString()} 点`)
      setExpandedRowKeys((prev) => (prev.includes(record.id) ? prev : [...prev, record.id]))
      await load()
    } catch (err) {
      messageApi.error(err.message || '清洗失败')
    } finally {
      setCleaningId(null)
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
      title: '登记状态',
      dataIndex: 'status',
      width: 100,
      // 只读展示：status 是数据源登记表上的一个字段，不参与清洗与统计过滤，
      // 因此这里不再提供启停切换，避免让页面看起来具备实际并不存在的控制能力
      render: (status) =>
        status === 1 ? <Tag color="success">启用</Tag> : <Tag color="default">停用</Tag>,
    },
    {
      title: '操作',
      width: 120,
      render: (_, record) => (
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

      <Card title="全部数据清洗统计" className="page-card">
        <CleanStats stats={stats} />
        <CleanStatsFoot stats={stats} />
      </Card>

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
          expandable={{
            expandedRowKeys,
            onExpandedRowsChange: (keys) => setExpandedRowKeys(keys),
            expandedRowRender: (record) => (
              <div className="source-clean-panel">
                <div className="source-clean-title">{record.name}清洗统计</div>
                <CleanStats stats={statsBySource[record.id]} />
                <CleanStatsFoot stats={statsBySource[record.id]} />
              </div>
            ),
          }}
        />
      </Card>
    </div>
  )
}
