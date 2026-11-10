import { api } from './client'

// 接口已落地：backend/app/routers/dashboard.py，文档见 http://127.0.0.1:8000/docs
// 路径或参数变了只改本文件，页面不用动。

export const getSummary = (topN) => api.get('/dashboard/summary', { top_n: topN })
