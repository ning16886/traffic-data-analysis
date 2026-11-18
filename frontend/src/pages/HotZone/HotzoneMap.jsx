import { useEffect, useRef } from 'react'
import * as echarts from 'echarts'

const MAP_NAME = 'traffic-hotzone-region'

export const TYPE_COLORS = {
  residential: '#52c41a',
  business: '#fa8c16',
  transit: '#722ed1',
}

// 仓库里没有地图瓦片/GeoJSON，用路网外接矩形注册一张本地底图。
// 坐标仍是真实 WGS-84 经纬度，geo 的缩放/拖拽和 effectScatter 都按经纬度工作。
function registerRegionMap(bounds) {
  if (!bounds) return
  const { min_lng: minLng, max_lng: maxLng, min_lat: minLat, max_lat: maxLat } = bounds
  if ([minLng, maxLng, minLat, maxLat].some((v) => !Number.isFinite(v))) return
  echarts.registerMap(MAP_NAME, {
    type: 'FeatureCollection',
    features: [
      {
        type: 'Feature',
        properties: { name: '研究区域' },
        geometry: {
          type: 'Polygon',
          coordinates: [[
            [minLng, minLat], [maxLng, minLat], [maxLng, maxLat], [minLng, maxLat], [minLng, minLat],
          ]],
        },
      },
    ],
  })
}

function tooltipHtml(zone) {
  if (!zone) return ''
  return [
    `<b>${zone.zone_id} · ${zone.type_label}</b>`,
    `中心 ${zone.center_lng.toFixed(5)}, ${zone.center_lat.toFixed(5)}`,
    `活动点 ${zone.point_count.toLocaleString()} 个 · 半径 ${zone.radius_m} m`,
    `生成时间 ${zone.create_time || '—'}`,
  ].join('<br/>')
}

export default function HotzoneMap({ zones, bounds, selectedZoneId, onSelect }) {
  const boxRef = useRef(null)
  const chartRef = useRef(null)
  const onSelectRef = useRef(onSelect)
  onSelectRef.current = onSelect

  useEffect(() => {
    const chart = echarts.init(boxRef.current)
    chartRef.current = chart
    chart.on('click', (params) => {
      const zone = params.data?.zone
      if (zone) onSelectRef.current?.(zone.zone_id)
    })
    const observer = new ResizeObserver(() => chart.resize())
    observer.observe(boxRef.current)
    return () => {
      observer.disconnect()
      chart.dispose()
      chartRef.current = null
    }
  }, [])

  useEffect(() => {
    const chart = chartRef.current
    if (!chart) return
    registerRegionMap(bounds)

    const maxPoints = Math.max(1, ...zones.map((z) => z.point_count))
    const data = zones.map((zone) => ({
      value: [zone.center_lng, zone.center_lat, zone.point_count],
      zone,
      itemStyle: { color: TYPE_COLORS[zone.zone_type] || '#1890ff' },
    }))

    chart.setOption({
      animationDuration: 400,
      tooltip: {
        trigger: 'item',
        formatter: (params) => tooltipHtml(params.data?.zone),
      },
      visualMap: [{
        type: 'continuous',
        min: 0,
        max: maxPoints,
        dimension: 2,
        seriesIndex: 0,
        calculable: true,
        orient: 'vertical',
        right: 8,
        top: 'middle',
        text: ['点密', '点疏'],
        textStyle: { color: '#595959', fontSize: 11 },
        inRange: { color: ['#e6f7ff', '#91d5ff', '#40a9ff', '#ff4d4f'] },
      }],
      geo: {
        map: MAP_NAME,
        roam: true,
        zoom: 1.1,
        itemStyle: { areaColor: '#f4f8fd', borderColor: '#9dc9f0', borderWidth: 1 },
        emphasis: { itemStyle: { areaColor: '#e6f4ff' }, label: { show: false } },
        select: { disabled: true },
      },
      series: [
        {
          name: '活动点密度',
          type: 'heatmap',
          coordinateSystem: 'geo',
          data,
          pointSize: 12,
          blurSize: 22,
          minOpacity: 0.05,
          maxOpacity: 0.75,
          emphasis: { itemStyle: { borderColor: '#fff', borderWidth: 1 } },
        },
        {
          name: '热区中心',
          type: 'effectScatter',
          coordinateSystem: 'geo',
          data,
          symbolSize: (value) => 8 + 16 * Math.sqrt((value[2] || 0) / maxPoints),
          rippleEffect: { scale: 3, brushType: 'stroke', period: 4 },
          selectedMode: 'single',
          select: { itemStyle: { borderColor: '#1f2329', borderWidth: 2 } },
          zlevel: 2,
        },
      ],
    }, true)

    const index = zones.findIndex((z) => z.zone_id === selectedZoneId)
    if (index >= 0) {
      chart.dispatchAction({ type: 'select', seriesIndex: 1, dataIndex: index })
    }
  }, [zones, bounds, selectedZoneId])

  return <div ref={boxRef} style={{ width: '100%', height: 520 }} />
}
