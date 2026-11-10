import { api } from './client'

// 接口路径目前是草案，以后端 Swagger（http://localhost:8000/docs）为准。

export const listOdMatrix = (params) => api.get('/od/list', params)

export const getTopOdPairs = (params) => api.get('/od/top', params)
