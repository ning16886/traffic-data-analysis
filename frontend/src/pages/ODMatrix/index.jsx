import { useEffect, useRef, useState } from 'react'
import { Alert, Button, Card, Empty, message, Radio, Select, Skeleton, Table, Tabs, theme } from 'antd'
import { getOdComputation, getOdMatrix, getTopOdPairs, listOdMatrix, recomputeOd } from '../../api/od'
import MatrixGrid from './MatrixGrid'
import OdRanking from './OdRanking'
import useOdResource from './useOdResource'
import OdDefinitions, { formatOdTime } from './OdDefinitions'
import './od.css'

const periods = [
  { key: 'all', label: '全部出行' },
  { key: 'morning_peak', label: '早高峰' },
  { key: 'evening_peak', label: '晚高峰' },
  { key: 'normal', label: '平峰' },
  { key: 'transit', label: '接驳出行' },
]

const columns = [
  { title: '起点路段', key: 'origin', width: 240, render: (_, row) => <Road id={row.origin_road_id} name={row.origin_road_name} /> },
  { title: '终点路段', key: 'dest', width: 240, render: (_, row) => <Road id={row.dest_road_id} name={row.dest_road_name} /> },
  { title: '出行次数', dataIndex: 'trip_count', align: 'right', width: 120, render: (value) => value.toLocaleString() },
  { title: '平均时长（分钟）', dataIndex: 'avg_duration_min', align: 'right', width: 170,
    render: (value) => value == null ? '—' : value.toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 }) },
]

function Road({ id, name }) {
  return <div className="od-road"><span title={name}>{name}</span><small>{id}</small></div>
}

export function ResourceState({ resource, title, retry, children }) {
  if (resource.loading) return <div className="od-loading" role="status" aria-label="正在加载数据"><Skeleton active paragraph={{ rows: 4 }} /></div>
  if (resource.error) return <Alert type="error" message={`${title}加载失败`} description={resource.error.message}
    action={<Button onClick={retry}>重试</Button>} />
  return children
}

export default function ODMatrix() {
  const { token } = theme.useToken()
  const [query, setQuery] = useState({ period: 'all', page: 1, pageSize: 20, dataset: 'original' })
  const [view, setView] = useState('list')
  const [revision, setRevision] = useState(0)
  const [computing, setComputing] = useState(false)
  const [notice, setNotice] = useState(null)
  const [messages, messageContext] = message.useMessage()
  const mounted = useRef(true)
  const status = useOdResource(getOdComputation, {}, revision)
  const busy = Boolean(computing || status.data?.running)
  const params = { time_period: query.period === 'all' ? undefined : query.period, dataset: query.dataset }
  const list = useOdResource(listOdMatrix, { ...params, page: query.page, page_size: query.pageSize }, revision)
  const matrix = useOdResource(getOdMatrix, params, revision)
  const ranking = useOdResource(getTopOdPairs, params, revision)
  const refresh = () => setRevision((value) => value + 1)
  useEffect(() => {
    mounted.current = true
    document.body.classList.add('od-route')
    return () => { mounted.current = false; document.body.classList.remove('od-route') }
  }, [])
  const compute = async () => {
    setComputing(true)
    setNotice(null)
    try {
      await recomputeOd()
      if (!mounted.current) return
      setQuery((current) => ({ ...current, dataset: 'computed', period: 'all', page: 1 }))
      messages.success({ key: 'od-recomputed', className: 'od-toast', duration: 4, content: <span role="status">重算完成
        <Button type="text" size="small" aria-label="关闭提示" onClick={() => messages.destroy('od-recomputed')}>关闭</Button>
      </span> })
      refresh()
    } catch (error) {
      if (mounted.current) setNotice({ type: 'error', message: '重算失败', description: error.message })
    } finally {
      if (mounted.current) setComputing(false)
    }
  }
  const emptyDescription = query.dataset === 'computed' && query.period === 'transit' ? '重算结果不含接驳分类' : '暂无出行数据'
  return (
    <section className="od-page" aria-labelledby="od-title" style={{
      '--od-text': token.colorText, '--od-secondary': token.colorTextSecondary,
      '--od-border': token.colorBorderSecondary, '--od-surface': token.colorBgContainer, '--od-fill': token.colorFillAlter,
    }}>
      {messageContext}
      <header className="od-page-header">
        <h1 id="od-title">OD 出行分析</h1>
        <OdDefinitions matrix={matrix.data} computation={status.data} />
      </header>
      {notice && <Alert className="od-notice" type={notice.type} message={notice.message} description={notice.description}
        action={notice.type === 'error' ? <Button onClick={compute} disabled={busy}>重试重算</Button> : null} />}
      <Card className="od-workspace">
        <div className="od-source-row">
          <label htmlFor="od-source">结果来源</label>
          <Select id="od-source" aria-label="结果来源" value={query.dataset} disabled={busy || status.loading}
            options={[{ value: 'original', label: '原始模拟结果' }, { value: 'computed', label: '轨迹重算结果', disabled: !status.data?.available }]}
            onChange={(dataset) => { setQuery({ ...query, dataset, page: 1 }); setNotice(null) }} />
          <div className="od-actions">
            <Button onClick={refresh}>刷新</Button>
            <Button type="primary" onClick={compute} loading={busy} disabled={busy}>重算 OD</Button>
          </div>
        </div>
        {status.error && <Alert className="od-notice" type="error" message="重算状态加载失败" description={status.error.message}
          action={<Button onClick={refresh}>重试</Button>} />}
        <Tabs activeKey={query.period} items={periods} onChange={(period) => setQuery({ ...query, period, page: 1 })} />
        <div className="od-stats" aria-label="出行统计">
          <div><span>出行总次数</span><output aria-label="出行总次数">{matrix.data?.total_trip_count.toLocaleString() ?? '—'}</output><small>次</small></div>
          <div><span>OD 对数量</span><output aria-label="OD 对数量">{list.data?.total.toLocaleString() ?? '—'}</output><small>对</small></div>
          {query.dataset === 'computed' && status.data?.completed_at && <time className="od-updated"
            dateTime={status.data.completed_at} title={formatOdTime(status.data.completed_at)}
            aria-label={`更新于 ${formatOdTime(status.data.completed_at)}`}>更新于 {formatOdTime(status.data.completed_at, true)}</time>}
        </div>
        <div className="od-view-toolbar">
          <Radio.Group value={view} onChange={(event) => setView(event.target.value)} aria-label="展示方式">
            <Radio.Button value="list">OD 明细</Radio.Button><Radio.Button value="matrix">交叉矩阵</Radio.Button>
          </Radio.Group>
        </div>
        {view === 'list' ? (
          <ResourceState resource={list} title="OD 明细" retry={refresh}>
            <Table className="od-detail-table" rowKey={(row) => `${row.origin_road_id}:${row.dest_road_id}`}
              columns={columns} dataSource={list.data?.items ?? []} scroll={{ x: 770, y: 460 }}
              locale={{ emptyText: <Empty description={emptyDescription} /> }}
              pagination={{ current: query.page, pageSize: query.pageSize, total: list.data?.total ?? 0,
                showSizeChanger: true, pageSizeOptions: [10, 20, 50, 100], showTotal: (total) => `共 ${total.toLocaleString()} 个 OD 对`,
                onChange: (page, pageSize) => setQuery({ ...query, page: pageSize === query.pageSize ? page : 1, pageSize }) }} />
          </ResourceState>
        ) : (
          <ResourceState resource={matrix} title="矩阵" retry={refresh}><MatrixGrid data={matrix.data} emptyDescription={emptyDescription} /></ResourceState>
        )}
      </Card>
      <Card className="od-ranking-card" title={<h2>Top20 OD 对</h2>}>
        <ResourceState resource={ranking} title="出行排名" retry={refresh}>
          <OdRanking rows={ranking.data?.items ?? []} />
        </ResourceState>
      </Card>
    </section>
  )
}
