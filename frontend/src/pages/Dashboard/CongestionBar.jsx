import { useEffect, useRef } from 'react'
import * as echarts from 'echarts'
import { themeColor } from '../../config/theme'

export const SEVERE_INDEX = 0.45
const SEVERE_COLOR = '#ff4d4f'
const WARN_COLOR = '#faad14'

function barColor(value, threshold) {
  if (value >= SEVERE_INDEX) return SEVERE_COLOR
  if (value >= threshold) return WARN_COLOR
  return themeColor
}

const toPercent = (value) => `${(value * 100).toFixed(1)}%`

export default function CongestionBar({ rows, threshold, levelLabels }) {
  const boxRef = useRef(null)
  const chartRef = useRef(null)

  useEffect(() => {
    const chart = echarts.init(boxRef.current)
    chartRef.current = chart
    const observer = new ResizeObserver(() => chart.resize())
    observer.observe(boxRef.current)
    return () => {
      observer.disconnect()
      chart.dispose()
      chartRef.current = null
    }
  }, [])

  useEffect(() => {
    if (!chartRef.current || rows.length === 0) return

    // echarts 的类目轴从下往上排，反转后最堵的那条落在顶部
    const ordered = [...rows].reverse()
    chartRef.current.setOption(
      {
        animationDuration: 400,
        grid: { left: 8, right: 56, top: 8, bottom: 22, containLabel: true },
        tooltip: {
          trigger: 'item',
          formatter: (params) => {
            const row = ordered[params.dataIndex]
            return [
              `<b>${row.road_id} ${row.road_name}</b>`,
              `${levelLabels[row.level] || row.level} · 最堵窗 ${row.window}`,
              `拥堵指数 ${toPercent(row.congestion_index)}`,
              `实际 ${row.avg_speed} km/h ÷ 自由流 ${row.free_flow_speed} km/h`,
              `窗内轨迹点 ${row.point_count}`,
            ].join('<br/>')
          },
        },
        xAxis: {
          type: 'value',
          min: 0,
          max: 1,
          interval: 0.2,
          axisLabel: { formatter: (v) => `${Math.round(v * 100)}%`, color: '#8c8c8c' },
          splitLine: { lineStyle: { color: '#f0f0f0' } },
        },
        yAxis: {
          type: 'category',
          data: ordered.map((row) => row.road_id),
          axisTick: { show: false },
          axisLine: { show: false },
          axisLabel: { color: '#595959' },
        },
        series: [
          {
            type: 'bar',
            data: ordered.map((row) => ({
              value: row.congestion_index,
              itemStyle: { color: barColor(row.congestion_index, threshold) },
            })),
            barMaxWidth: 16,
            label: {
              show: true,
              position: 'right',
              formatter: (params) => toPercent(params.value),
              color: '#595959',
              fontSize: 12,
            },
          },
        ],
      },
      true,
    )
  }, [rows, threshold, levelLabels])

  return <div ref={boxRef} style={{ width: '100%', height: rows.length * 34 + 40 }} />
}
