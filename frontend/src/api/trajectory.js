import { api } from './client'

// 接口路径目前是草案，以后端 Swagger（http://localhost:8000/docs）为准。

export const listDataSources = () => api.get('/data_source/list')

export const createDataSource = (data) => api.post('/data_source', data)

export const updateDataSource = (id, data) => api.put(`/data_source/${id}`, data)

export const deleteDataSource = (id) => api.del(`/data_source/${id}`)

export const listTrajectories = (params) => api.get('/trajectory/list', params)

export const getCleanConfig = () => api.get('/trajectory/clean-config')

export const saveCleanConfig = (data) => api.put('/trajectory/clean-config', data)

// 不传参数=全量重跑；传 { source_id } 时只重跑该数据源（后端会先删掉该源的旧清洗行）
export const rerunClean = (payload) => api.post('/trajectory/clean', payload)

// 不传参数=全量清洗统计；传 sourceId 时只取该数据源自己的清洗统计
export const getCleanCompare = (sourceId) =>
  api.get('/trajectory/clean-compare', sourceId == null ? undefined : { source_id: sourceId })
