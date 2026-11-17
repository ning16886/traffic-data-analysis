import { useEffect, useState } from 'react'

export default function useOdResource(loader, params, revision) {
  const key = JSON.stringify(params)
  const [state, setState] = useState({ key: '', loading: true, data: null, error: null })
  useEffect(() => {
    let current = true
    setState({ key, loading: true, data: null, error: null })
    loader(JSON.parse(key)).then(
      (data) => current && setState({ key, loading: false, data, error: null }),
      (error) => current && setState({ key, loading: false, data: null, error }),
    )
    return () => { current = false }
  }, [loader, key, revision])
  // 类别或页码变化的这一帧就隐藏旧结果，不等 effect 执行后再清空。
  return state.key === key ? state : { loading: true, data: null, error: null }
}
