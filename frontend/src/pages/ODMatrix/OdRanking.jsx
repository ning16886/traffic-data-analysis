import { useEffect, useRef } from 'react'
import { Empty, theme } from 'antd'
import * as echarts from 'echarts/core'
import { BarChart } from 'echarts/charts'
import { AriaComponent, GridComponent, TooltipComponent } from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'

echarts.use([BarChart, GridComponent, TooltipComponent, AriaComponent, CanvasRenderer])

export default function OdRanking({ rows }) {
  const { token } = theme.useToken()
  const node = useRef(null)
  useEffect(() => {
    if (!node.current || rows.length === 0) return undefined
    const container = node.current
    const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)')
    const compact = () => container.clientWidth < 480
    const labels = () => rows.map((row) => compact()
      ? `${row.origin_road_id}\n→ ${row.dest_road_id}` : `${row.origin_road_id} → ${row.dest_road_id}`)
    const resizeContainer = () => { container.style.height = `${Math.max(180, rows.length * (compact() ? 36 : 28) + 48)}px` }
    resizeContainer()
    const chart = echarts.init(container)
    chart.setOption({
      animation: !reducedMotion.matches,
      animationDuration: 180,
      animationDurationUpdate: 180,
      textStyle: { fontFamily: token.fontFamily, fontSize: 12, color: token.colorTextSecondary },
      aria: { enabled: true },
      grid: { left: compact() ? 88 : 156, right: 36, top: 12, bottom: 32 },
      tooltip: { trigger: 'item', renderMode: 'richText', formatter: (params) => {
        const row = rows[params.dataIndex]
        return `${row.origin_road_name} → ${row.dest_road_name}\n${row.trip_count} 次 · 平均 ${row.avg_duration_min ?? '—'} 分钟`
      } },
      xAxis: { type: 'value', minInterval: 1, name: '次', axisLabel: { fontSize: 12 },
        splitLine: { lineStyle: { color: token.colorBorderSecondary } } },
      yAxis: { type: 'category', inverse: true, axisTick: { show: false }, axisLine: { show: false },
        axisLabel: { fontSize: 12, color: token.colorTextSecondary, lineHeight: 15 }, data: labels() },
      series: [{ type: 'bar', barMaxWidth: 13, data: rows.map((row) => row.trip_count),
        itemStyle: { color: token.colorPrimary, borderRadius: [0, 3, 3, 0] },
        label: { show: true, position: 'right', fontSize: 12, color: token.colorText } }],
    })
    let wasCompact = compact()
    const observer = new ResizeObserver(() => {
      resizeContainer()
      if (compact() !== wasCompact) {
        wasCompact = compact()
        chart.setOption({ grid: { left: wasCompact ? 88 : 156 }, yAxis: { data: labels() } })
      }
      chart.resize()
    })
    const updateMotion = () => chart.setOption({ animation: !reducedMotion.matches })
    reducedMotion.addEventListener('change', updateMotion)
    observer.observe(container)
    return () => { reducedMotion.removeEventListener('change', updateMotion); observer.disconnect(); chart.dispose() }
  }, [rows, token.fontFamily, token.colorPrimary, token.colorText, token.colorTextSecondary, token.colorBorderSecondary])
  if (rows.length === 0) return <Empty description="暂无排名" />
  return <>
    <div ref={node} className="od-ranking" role="img" aria-label={`Top${rows.length} OD 对出行次数柱状图`}
      style={{ height: Math.max(180, rows.length * 28 + 45) }} />
    <details className="od-ranking-text"><summary>查看排名数据</summary><ol>{rows.map((row) => (
      <li key={`${row.origin_road_id}:${row.dest_road_id}`}>
        {row.origin_road_name}（{row.origin_road_id}） → {row.dest_road_name}（{row.dest_road_id}）：{row.trip_count} 次
      </li>
    ))}</ol></details>
  </>
}
