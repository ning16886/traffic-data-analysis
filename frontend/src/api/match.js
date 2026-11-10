import { api } from './client'

// 接口路径目前是草案，以后端 Swagger（http://localhost:8000/docs）为准。

export const listVehicles = (params) => api.get('/match/vehicles', params)

export const getMatchResult = (vehicleId, params) => api.get(`/match/${vehicleId}`, params)

export const getMatchRate = (vehicleId) => api.get(`/match/${vehicleId}/rate`)

export const rematch = (vehicleId) => api.post(`/match/${vehicleId}/rematch`)
