import { useEffect, useId, useRef, useState } from 'react'
import { Button, Popover } from 'antd'

export function formatOdTime(value, compact = false) {
  const date = new Date(value)
  if (!value || Number.isNaN(date.getTime())) return '—'
  const options = { timeZone: 'Asia/Shanghai', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hourCycle: 'h23' }
  if (compact) {
    const parts = Object.fromEntries(new Intl.DateTimeFormat('zh-CN', options).formatToParts(date).map(({ type, value: part }) => [type, part]))
    return `${parts.month}-${parts.day} ${parts.hour}:${parts.minute}`
  }
  return `${date.toLocaleString('zh-CN', { ...options, year: 'numeric', second: '2-digit' })}（北京时间）`
}

export default function OdDefinitions({ matrix, computation }) {
  const [open, setOpen] = useState(false)
  const trigger = useRef(null)
  const contentId = useId()
  useEffect(() => {
    if (!open) return undefined
    const close = (event) => {
      if (event.key !== 'Escape') return
      event.preventDefault()
      setOpen(false)
      trigger.current?.focus()
    }
    document.addEventListener('keydown', close)
    return () => document.removeEventListener('keydown', close)
  }, [open])
  const stats = computation?.stats
  return <Popover trigger="click" placement="bottomRight" open={open} onOpenChange={setOpen}
    rootClassName="od-definitions-popup" content={
      <section id={contentId} className="od-definitions" aria-label="统计口径详情">
        <dl>
          <dt>时段</dt><dd>早高峰 [07:00, 10:00)，晚高峰 [17:00, 20:00)，其余为平峰。</dd>
          <dt>结果来源</dt><dd>原始模拟结果包含接驳分类；轨迹重算按首个有效匹配点划分时段，不含接驳标记。重算仅替换上次快照，保留原始结果。</dd>
          <dt>重点路段矩阵</dt><dd>按出发量、到达量各选最多 20 个路段；行是起点，列是终点，单位为次。</dd>
          <dt>当前覆盖</dt><dd data-testid="od-matrix-coverage">{matrix
            ? `${matrix.displayed_trip_count.toLocaleString()} / ${matrix.total_trip_count.toLocaleString()} 次出行`
            : '—'}</dd>
          {computation?.completed_at && <><dt>最近重算</dt><dd>{formatOdTime(computation.completed_at)}</dd></>}
          {stats && <><dt>轨迹分段</dt><dd>有效 {stats.trip_count.toLocaleString()}，跳过 {stats.skipped_segments.toLocaleString()}</dd></>}
        </dl>
      </section>
    }>
    <Button ref={trigger} type="text" aria-expanded={open} aria-controls={open ? contentId : undefined}>统计口径</Button>
  </Popover>
}
