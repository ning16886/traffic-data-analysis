import { useEffect, useMemo, useRef } from 'react'
import * as echarts from 'echarts'

const COLOR = {
  trajectory: '#1677ff',
  road: '#52c41a',
  unmatched: '#ff4d4f',
  start: '#52c41a',
  end: '#fa8c16',
}

const fmt = (v, digits = 1) => (v === null || v === undefined ? '—' : Number(v).toFixed(digits))

function tripBounds(points) {
  // 同一行程内相邻点才连线：跨行程连线会在图上画出根本不存在的「瞬移」
  const segments = []
  let current = []
  for (const p of points) {
    if (current.length && current[current.length - 1].segmentId !== p.segmentId) {
      segments.push(current)
      current = []
    }
    current.push(p)
  }
  if (current.length) segments.push(current)
  return segments
}

export default function MatchLayerChart({ track, layers }) {
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

  const option = useMemo(() => {
    if (!track) return null
    const { points, roads, trips } = track
    const matched = points.filter((p) => p.matched)
    const unmatched = points.filter((p) => !p.matched)

    const background = layers.network
      ? (track.network || []).map((r) => ({ coords: r.coords, level: r.level }))
      : []

    const roadLines = layers.roads
      ? roads.map((r) => ({
          coords: r.coords,
          roadId: r.roadId,
          roadName: r.roadName,
          level: r.level,
          pointCount: r.pointCount,
        }))
      : []

    const linkLines = []
    if (layers.trajectory) {
      for (const seg of tripBounds(matched)) {
        for (let i = 1; i < seg.length; i += 1) {
          linkLines.push({ coords: [[seg[i - 1].lng, seg[i - 1].lat], [seg[i].lng, seg[i].lat]] })
        }
      }
    }

    const pointData = layers.trajectory
      ? matched.map((p) => ({ value: [p.lng, p.lat], raw: p }))
      : []
    const unmatchedData = layers.unmatched
      ? unmatched.map((p) => ({ value: [p.lng, p.lat], raw: p }))
      : []

    const markers = []
    if (layers.markers) {
      for (const trip of trips) {
        markers.push({
          value: trip.startPoint,
          kind: 'start',
          trip,
          itemStyle: { color: COLOR.start, borderColor: '#fff', borderWidth: 2 },
        })
        markers.push({
          value: trip.endPoint,
          kind: 'end',
          trip,
          itemStyle: { color: COLOR.end, borderColor: '#fff', borderWidth: 2 },
        })
      }
    }

    const pointTooltip = (p) => {
      const lines = [
        `<b>${p.roadName || '未匹配'}${p.roadId ? ` · ${p.roadId}` : ''}</b>`,
        `时间 ${p.ts || '—'}`,
        `速度 ${fmt(p.speed)} km/h · 方向 ${fmt(p.direction, 0)}°`,
      ]
      if (p.matched) {
        lines.push(`垂距 ${fmt(p.distanceM, 2)} m · 置信度 ${(p.probability * 100).toFixed(1)}%`)
      } else {
        lines.push('未匹配：搜索半径内无路段，或垂距超过阈值')
      }
      lines.push(`行程 ${p.segmentId || '—'}`)
      return lines.join('<br/>')
    }

    return {
      animation: false,
      grid: { left: 12, right: 24, top: 28, bottom: 46, containLabel: true },
      tooltip: {
        trigger: 'item',
        confine: true,
        formatter: (params) => {
          const d = params.data || {}
          if (d.raw) return pointTooltip(d.raw)
          if (d.roadId) {
            return `<b>${d.roadName}</b><br/>路段 ${d.roadId} · 等级 ${d.level}<br/>本车在该路段 ${d.pointCount} 个匹配点`
          }
          if (d.trip) {
            const t = d.trip
            return [
              `<b>${d.kind === 'start' ? '起点' : '终点'} · ${t.segmentId}</b>`,
              `${t.start} → ${t.end}`,
              `点数 ${t.total} · 匹配 ${t.matched} · 匹配率 ${(t.matchRate * 100).toFixed(1)}%`,
            ].join('<br/>')
          }
          return ''
        },
      },
      xAxis: {
        type: 'value',
        name: '经度（lng）',
        nameLocation: 'middle',
        nameGap: 28,
        scale: true,
        axisLabel: { color: '#8c8c8c', formatter: (v) => v.toFixed(3) },
        splitLine: { lineStyle: { color: '#f2f4f7' } },
        axisLine: { lineStyle: { color: '#d9d9d9' } },
      },
      yAxis: {
        type: 'value',
        name: '纬度（lat）',
        nameGap: 18,
        scale: true,
        axisLabel: { color: '#8c8c8c', formatter: (v) => v.toFixed(3) },
        splitLine: { lineStyle: { color: '#f2f4f7' } },
        axisLine: { lineStyle: { color: '#d9d9d9' } },
      },
      series: [
        {
          name: '背景路网',
          type: 'lines',
          coordinateSystem: 'cartesian2d',
          polyline: false,
          silent: true,
          z: 0,
          data: background,
          lineStyle: { color: '#b7eb8f', width: 1, opacity: 0.75 },
        },
        {
          name: '匹配路段',
          type: 'lines',
          coordinateSystem: 'cartesian2d',
          polyline: false,
          silent: false,
          z: 1,
          data: roadLines,
          lineStyle: { color: COLOR.road, width: 2, opacity: 0.9 },
        },
        {
          name: '轨迹连线',
          type: 'lines',
          coordinateSystem: 'cartesian2d',
          polyline: false,
          silent: true,
          z: 2,
          data: linkLines,
          lineStyle: { color: COLOR.trajectory, width: 1, opacity: 0.45 },
        },
        {
          name: '原始轨迹点',
          type: 'scatter',
          z: 3,
          symbolSize: 5,
          data: pointData,
          itemStyle: { color: COLOR.trajectory, opacity: 0.85 },
        },
        {
          name: '未匹配点',
          type: 'scatter',
          z: 4,
          symbolSize: 7,
          data: unmatchedData,
          itemStyle: { color: COLOR.unmatched, opacity: 0.9 },
        },
        {
          name: '起终点',
          type: 'scatter',
          z: 5,
          symbolSize: 11,
          data: markers,
          label: {
            show: trips.length <= 4,
            position: 'top',
            formatter: (params) => (params.data.kind === 'start' ? '起点' : '终点'),
            color: '#595959',
            fontSize: 11,
          },
        },
      ],
    }
  }, [track, layers])

  useEffect(() => {
    if (chartRef.current && option) {
      chartRef.current.setOption(option, true)
    }
  }, [option])

  return <div ref={boxRef} style={{ width: '100%', height: 360 }} />
}
