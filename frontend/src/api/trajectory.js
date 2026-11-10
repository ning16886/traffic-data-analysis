import { api } from './client'

// 接口路径目前是草案，以后端 Swagger（http://localhost:8000/docs）为准。

export const listDataSources = () => api.get('/data_source/list')

export const createDataSource = (data) => api.post('/data_source', data)

export const updateDataSource = (id, data) => api.put(`/data_source/${id}`, data)

export const deleteDataSource = (id) => api.del(`/data_source/${id}`)

export const switchDataSourceStatus = (id, status) =>
  api.put(`/data_source/${id}/status`, { status })

export const listTrajectories = (params) => api.get('/trajectory/list', params)

export const getCleanConfig = () => api.get('/trajectory/clean-config')

export const saveCleanConfig = (data) => api.put('/trajectory/clean-config', data)

export const rerunClean = () => api.post('/trajectory/clean')

export const getCleanCompare = () => api.get('/trajectory/clean-compare')
