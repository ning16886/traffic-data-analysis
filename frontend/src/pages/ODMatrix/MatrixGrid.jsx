import { Empty } from 'antd'

export default function MatrixGrid({ data, emptyDescription = '暂无出行数据' }) {
  if (!data || data.origins.length === 0) return <Empty description={emptyDescription} />
  const maximum = Math.max(0, ...data.counts.flat())
  return (
    <>
      <div className="od-matrix-meta">
        <h2>重点路段矩阵</h2><span>{data.origins.length} × {data.destinations.length}</span>
        <span className="od-matrix-scale" aria-label={`出行次数色标：0 至 ${maximum} 次`}>
          0 <i aria-hidden="true" /> {maximum} 次
        </span>
      </div>
      <div className="od-matrix-scroll" tabIndex={0} aria-label="重点路段矩阵，可横向滚动">
        <table className="od-matrix">
          <caption className="od-sr-only">重点路段矩阵 · 行为起点，列为终点 · 单位：次</caption>
          <thead><tr><th scope="col">起点 ↓ / 终点 →</th>{data.destinations.map((road) => (
            <th key={road.road_id} scope="col" title={road.road_name}><b>{road.road_id}</b><span>{road.road_name}</span></th>
          ))}</tr></thead>
          <tbody>{data.origins.map((road, i) => (
            <tr key={road.road_id}>
              <th scope="row" title={road.road_name}><b>{road.road_id}</b><span>{road.road_name}</span></th>
              {data.counts[i].map((count, j) => (
                <td key={data.destinations[j].road_id}
                  title={`${road.road_name} → ${data.destinations[j].road_name}：${count} 次`}
                  style={{ background: count
                    ? `color-mix(in srgb, var(--theme-color) ${7 + 25 * count / (maximum || 1)}%, var(--od-surface))`
                    : 'var(--od-surface)' }}>
                  {count}
                </td>
              ))}
            </tr>
          ))}</tbody>
        </table>
      </div>
    </>
  )
}
