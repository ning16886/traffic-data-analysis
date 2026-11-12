import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  Alert,
  Button,
  Card,
  Input,
  InputNumber,
  Select,
  Skeleton,
  Space,
  Tooltip,
  message,
} from 'antd'
import {
  DownloadOutlined,
  ExperimentOutlined,
  ReloadOutlined,
  SearchOutlined,
  ThunderboltOutlined,
} from '@ant-design/icons'
import { exportTrackUrl, getTrack, listVehicles, runMatch } from '../../api/match'
import MatchLayerChart from './MatchLayerChart'
import VehicleMatchTable from './VehicleMatchTable'
import './match.css'

const PAGE_SIZE = 10
const DEFAULT_THRESHOLD = 100

// 支持 /match?vehicle=ri_007&threshold=80 直接定位，方便截图和相互分享
function urlParams() {
  const search = new URLSearchParams(window.location.search)
  const th = Number(search.get('threshold'))
  return {
    vehicle: search.get('vehicle') || '',
    threshold: Number.isFinite(th) && th >= 10 && th <= 500 ? th : DEFAULT_THRESHOLD,
  }
}

const LEGEND = [
  { key: 'trajectory', label: '原始轨迹（箭头=行驶方向）', color: '#1677ff' },
  { key: 'roads', label: '匹配路段', color: '#52c41a' },
  { key: 'network', label: '背景路网', color: '#b7eb8f' },
  { key: 'unmatched', label: '未匹配点', color: '#ff4d4f' },
  { key: 'markers', label: '起终点', color: '#fa8c16' },
]

const pct = (v) => `${(v * 100).toFixed(2)}%`

function LegendChips({ layers, counts, onToggle }) {
  return (
    <div className="match-legend">
      {LEGEND.map((item) => (
        <Tooltip key={item.key} title={`点击${layers[item.key] ? '隐藏' : '显示'}该图层`}>
          <span
            className={`match-legend-item${layers[item.key] ? '' : ' is-off'}`}
            onClick={() => onToggle(item.key)}
          >
            <i className="match-legend-dot" style={{ background: item.color }} />
            {item.label}
            {counts[item.key] !== undefined && counts[item.key] !== null ? ` ${counts[item.key]}` : ''}
          </span>
        </Tooltip>
      ))}
    </div>
  )
}

function StatusBar({ stats }) {
  if (!stats) return null
  return (
    <div className="match-status">
      <span>
        总点 <b>{stats.total.toLocaleString()}</b>
      </span>
      <span>
        匹配 <b>{stats.matched.toLocaleString()}</b>
      </span>
      <span>
        未匹配 <b style={{ color: stats.unmatched > 0 ? '#ff4d4f' : undefined }}>{stats.unmatched}</b>
      </span>
      <span>
        路段 <b>{stats.roadCount}</b>
      </span>
      <span>
        行程 <b>{stats.tripCount}</b>
      </span>
      <span>
        匹配率 <b>{pct(stats.matchRate)}</b>
      </span>
    </div>
  )
}

const INITIAL = urlParams()

export default function MatchResult() {
  const [threshold, setThreshold] = useState(INITIAL.threshold)
  const [appliedThreshold, setAppliedThreshold] = useState(INITIAL.threshold)
  const [keyword, setKeyword] = useState('')
  const [page, setPage] = useState(1)

  const [list, setList] = useState(null)
  const [vehicles, setVehicles] = useState([])
  const [track, setTrack] = useState(null)
  const [selected, setSelected] = useState(INITIAL.vehicle)
  const [error, setError] = useState('')

  const [refreshKey, setRefreshKey] = useState(0)
  const [loadingTable, setLoadingTable] = useState(true)
  const [loadingTrack, setLoadingTrack] = useState(false)
  const [running, setRunning] = useState(false)
  const [layers, setLayers] = useState({
    trajectory: true,
    roads: true,
    network: true,
    unmatched: true,
    markers: true,
  })

  // ---------------------------------------------------------------- 列表
  const loadList = useCallback(
    async (opts = {}) => {
      const p = opts.page ?? page
      const kw = opts.keyword ?? keyword
      const th = opts.threshold ?? appliedThreshold
      setLoadingTable(true)
      setError('')
      try {
        const data = await listVehicles({
          threshold: th,
          page: p,
          pageSize: PAGE_SIZE,
          keyword: kw,
          sortBy: 'vehicleId',
        })
        setList(data)
        return data
      } catch (err) {
        setError(err.message || '接口返回异常')
        return null
      } finally {
        setLoadingTable(false)
      }
    },
    [appliedThreshold, keyword, page],
  )

  // 下拉框选项：100 辆车一次拉全，Select 本地过滤即可（比每次敲字都请求稳）
  const loadOptions = useCallback(async (th) => {
    try {
      const data = await listVehicles({ threshold: th, pageSize: 200, sortBy: 'vehicleId' })
      setVehicles(data.items)
      return data.items
    } catch {
      return []
    }
  }, [])

  // ---------------------------------------------------------------- 单车
  const loadTrack = useCallback(async (vehicleId, th) => {
    if (!vehicleId) return
    setLoadingTrack(true)
    try {
      const data = await getTrack(vehicleId, { threshold: th })
      setTrack(data)
    } catch (err) {
      setTrack(null)
      message.error(err.message || '匹配结果加载失败')
    } finally {
      setLoadingTrack(false)
    }
  }, [])

  useEffect(() => {
    let alive = true
    ;(async () => {
      const data = await loadList({ page: 1 })
      const options = await loadOptions(appliedThreshold)
      if (!alive || !data) return
      const first = options[0]?.vehicleId || data.items[0]?.vehicleId || ''
      const initial =
        INITIAL.vehicle && options.some((v) => v.vehicleId === INITIAL.vehicle) ? INITIAL.vehicle : first
      setSelected((prev) => prev || initial)
      // URL 里带了车辆就顺带翻到它所在页，否则表格会停在第一页
      const idx = options.findIndex((v) => v.vehicleId === initial)
      const target = idx >= 0 ? Math.floor(idx / PAGE_SIZE) + 1 : 1
      if (target > 1) {
        setPage(target)
        loadList({ page: target })
      }
    })()
    return () => {
      alive = false
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => {
    if (selected) loadTrack(selected, appliedThreshold)
  }, [selected, appliedThreshold, refreshKey, loadTrack])

  // ---------------------------------------------------------------- 动作
  const applyThreshold = async () => {
    const th = Number(threshold) || DEFAULT_THRESHOLD
    setAppliedThreshold(th)
    setPage(1)
    setRefreshKey((k) => k + 1)   // 阈值没变时也能强制刷一次图
    const data = await loadList({ page: 1, threshold: th })
    await loadOptions(th)
    if (data?.items?.length && !data.items.some((v) => v.vehicleId === selected)) {
      setSelected(data.items[0].vehicleId)
    }
  }

  const rematch = async (label) => {
    const th = Number(threshold) || DEFAULT_THRESHOLD
    setRunning(true)
    try {
      const res = await runMatch({ threshold: th })
      message.success(
        `${label}完成：${res.summary.matched.toLocaleString()} / ${res.summary.totalPoints.toLocaleString()} 点匹配成功，耗时 ${(res.elapsedMs / 1000).toFixed(1)}s`,
      )
      setAppliedThreshold(th)
      setPage(1)
      await loadList({ page: 1, threshold: th })
      await loadOptions(th)
      if (selected) await loadTrack(selected, th)
    } catch (err) {
      message.error(err.message || '重新匹配失败')
    } finally {
      setRunning(false)
    }
  }

  // 下拉选车时把表格翻到该车所在页，避免「选了 ri_007，表格还停在第 1 页」
  const selectVehicle = (vehicleId) => {
    const idx = vehicles.findIndex((v) => v.vehicleId === vehicleId)
    if (idx >= 0) {
      const target = Math.floor(idx / PAGE_SIZE) + 1
      if (target !== page) {
        setPage(target)
        loadList({ page: target })
      }
    }
    onSelect(vehicleId)
  }

  const onSelect = (vehicleId) => {
    setSelected(vehicleId)
    const url = new URL(window.location.href)
    url.searchParams.set('vehicle', vehicleId)
    window.history.replaceState(null, '', url)
    if (vehicleId === selected) setRefreshKey((k) => k + 1)
  }

  const counts = useMemo(() => {
    if (!track) return {}
    // 中位数在这里算一次，避免每次切图层重渲染都重排 2600 个点
    const values = track.points.filter((p) => p.matched && p.distanceM !== null).map((p) => p.distanceM)
    values.sort((a, b) => a - b)
    const mid = Math.floor(values.length / 2)
    const median = !values.length
      ? null
      : values.length % 2
        ? values[mid]
        : (values[mid - 1] + values[mid]) / 2
    return {
      trajectory: track.stats.matched,
      roads: track.roads.length,
      unmatched: track.stats.unmatched,
      markers: track.trips.length * 2,
      network: track.network?.length,
      median,
    }
  }, [track])

  if (error) {
    return (
      <Alert
        type="error"
        showIcon
        message="地图匹配接口不可用"
        description={`${error}。请确认后端已启动（backend 目录下 python -m uvicorn app.main:app --host 127.0.0.1 --port 8000）。`}
        action={
          <Button size="small" onClick={() => loadList({ page })}>
            重试
          </Button>
        }
      />
    )
  }

  const summary = list?.summary

  return (
    <div className="match-page">
      <div className="match-toolbar">
        <div>
          <div className="match-title">地图匹配</div>
          {summary ? (
            <div className="match-meta">
              数据 {summary.dataRange.start?.slice(0, 10)} ~ {summary.dataRange.end?.slice(0, 10)} ·{' '}
              {summary.vehicleCount} 辆车 · {summary.tripCount.toLocaleString()} 条行程 · 全量{' '}
              {summary.totalPoints.toLocaleString()} 点 · 匹配率 {pct(summary.matchRate)} · 命中路段{' '}
              {summary.roadCount}/{summary.totalRoadCount} · 阈值 {list.threshold}m · σ={list.params.sigma_m}m β=
              {list.params.beta_m}m
            </div>
          ) : null}
        </div>
        <Space>
          <Tooltip title="按当前阈值重跑全量匹配（48 万点约 5s），刷新统计与图形">
            <Button
              type="primary"
              icon={<ThunderboltOutlined />}
              loading={running}
              onClick={() => rematch('一键全链路')}
            >
              一键全链路
            </Button>
          </Tooltip>
          <Button
            icon={<DownloadOutlined />}
            disabled={!selected}
            href={selected ? exportTrackUrl(selected, appliedThreshold) : undefined}
            target="_blank"
          >
            导出当前车辆
          </Button>
        </Space>
      </div>

      <Card
        className="match-card"
        title={
          <Space size={8}>
            <ExperimentOutlined style={{ color: '#1677ff' }} />
            <span>三层可视化地图匹配</span>
          </Space>
        }
        extra={
          <Space size={8}>
            <Select
              showSearch
              style={{ width: 180 }}
              placeholder="选择车辆"
              value={selected || undefined}
              options={vehicles.map((v) => ({
                value: v.vehicleId,
                label: `${v.vehicleId}（${v.sourceName}）`,
              }))}
              filterOption={(input, option) =>
                option.value.toLowerCase().includes(input.toLowerCase())
              }
              onChange={selectVehicle}
            />
            <Tooltip title="垂距阈值：超过即判为未匹配。10~500m，改完点「加载」生效">
              <InputNumber
                min={10}
                max={500}
                step={10}
                value={threshold}
                onChange={(v) => setThreshold(v || DEFAULT_THRESHOLD)}
                addonAfter="m"
                style={{ width: 110 }}
              />
            </Tooltip>
            <Button icon={<ReloadOutlined />} loading={loadingTrack} onClick={applyThreshold}>
              加载
            </Button>
            <Tooltip title="强制重算（忽略缓存）">
              <Button loading={running} onClick={() => rematch('重新匹配')}>
                重新匹配
              </Button>
            </Tooltip>
          </Space>
        }
      >
        <div className="match-chart-head">
          <LegendChips
            layers={layers}
            counts={counts}
            onToggle={(key) => setLayers((prev) => ({ ...prev, [key]: !prev[key] }))}
          />
          <StatusBar stats={track?.stats} />
        </div>

        {loadingTrack && !track ? (
          <Skeleton active paragraph={{ rows: 8 }} />
        ) : track ? (
          <>
            <MatchLayerChart track={track} layers={layers} />
            <div className="match-chart-foot">
              <span>
                当前：<b>{track.vehicleId}</b>（{track.sourceName}）· 阈值 {track.threshold}m · 匹配点垂距中位{' '}
                {counts.median === null || counts.median === undefined ? '—' : counts.median.toFixed(2)} m
              </span>
              <span>蓝点=原始轨迹 · 绿线=匹配路段 · 红点=未匹配 · 起终点按行程标注</span>
            </div>
          </>
        ) : (
          <Skeleton active paragraph={{ rows: 8 }} />
        )}
      </Card>

      <Card
        className="match-card"
        title="车辆匹配统计（点击整行切换可视化）"
        extra={
          <Input
            allowClear
            size="small"
            prefix={<SearchOutlined />}
            placeholder="按车辆 ID 搜索"
            style={{ width: 200 }}
            onChange={(e) => {
              const kw = e.target.value
              setKeyword(kw)
              setPage(1)
              loadList({ page: 1, keyword: kw })
            }}
          />
        }
      >
        <VehicleMatchTable
          rows={list?.items || []}
          total={list?.total || 0}
          page={page}
          pageSize={PAGE_SIZE}
          loading={loadingTable}
          selectedId={selected}
          onSelect={onSelect}
          onPageChange={(p) => {
            setPage(p)
            loadList({ page: p })
          }}
        />
        {list ? (
          <div className="match-foot">
            <span>
              匹配口径：HMM 简易版（候选路段 → 高斯观测 → 指数转移 → 对数维特比），垂距 ≤ {list.threshold}m 记为匹配
            </span>
            <span>
              本次结果 {list.cacheHit ? '读自缓存' : `现算 ${(list.elapsedMs / 1000).toFixed(1)}s`} · 生成于{' '}
              {list.cachedAt}
            </span>
          </div>
        ) : null}
      </Card>
    </div>
  )
}
