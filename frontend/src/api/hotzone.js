import { api } from './client'

// 接口对应 backend/app/routers/hotzone.py，Swagger 见 http://127.0.0.1:8000/docs
export const getHotzoneSummary = () => api.get('/hotzone/summary')

export const getHotzoneMap = (zoneType) => api.get('/hotzone/map', { zone_type: zoneType })

export const listHotzones = (params) => api.get('/hotzone/list', params)

export const getHotzoneDetail = (zoneId) => api.get(`/hotzone/${encodeURIComponent(zoneId)}`)

export const recomputeHotzones = (params) => api.post('/hotzone/recompute', params)
