import { api } from './client'

export const listOdMatrix = (params) => api.get('/od/list', params)

export const getOdMatrix = (params) => api.get('/od/matrix', params)

export const getTopOdPairs = (params) => api.get('/od/top', params)

export const getOdComputation = () => api.get('/od/computation')

export const recomputeOd = () => api.post('/od/recompute')
