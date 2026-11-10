import { api } from './client'

// 接口路径目前是草案，以后端 Swagger（http://localhost:8000/docs）为准。

export const listHotzones = (params) => api.get('/hotzone/list', params)

export const recomputeHotzones = () => api.post('/hotzone/recompute')
