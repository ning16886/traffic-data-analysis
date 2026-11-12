import { api } from './client'

// 接口契约见 backend/app/schemas/match.py，Swagger：http://127.0.0.1:8000/docs

export const listVehicles = (params) => api.get('/match/vehicles', params)

export const getTrack = (vehicleId, params) =>
  api.get(`/match/tracks/${encodeURIComponent(vehicleId)}`, params)

export const runMatch = (body) => api.post('/match/run', body)

// 导出走浏览器直下，不经 fetch（避免把 1MB CSV 塞进内存再转 Blob）
export const exportTrackUrl = (vehicleId, threshold) =>
  `/api/match/tracks/${encodeURIComponent(vehicleId)}/export?threshold=${threshold}`
