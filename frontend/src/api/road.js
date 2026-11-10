import { api } from './client'

// 接口路径目前是草案，以后端 Swagger（http://localhost:8000/docs）为准。

export const listRoadMetrics = (params) => api.get('/road/metrics', params)

export const getRoadDetail = (roadId) => api.get(`/road/${roadId}`)

export const getCongestionHistory = (roadId, params) => api.get(`/road/${roadId}/congestion`, params)

export const getAdjacentRoads = (roadId) => api.get(`/road/${roadId}/adjacent`)

// 导出接口返回的是文件流，不是统一 JSON，所以给页面一个下载地址而不是发请求
export const getExportUrl = (params = {}) => {
  const query = new URLSearchParams(
    Object.entries(params).filter(([, value]) => value !== undefined && value !== null && value !== ''),
  )
  const queryString = query.toString()
  return `/api/road/export${queryString ? `?${queryString}` : ''}`
}
