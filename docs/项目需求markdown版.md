# 项目内容

# 城市交通轨迹大数据分析平台 —— 项目说明

## 一、项目概述

本项目为面向智慧城市交通管理真实业务场景的前后端综合工程项目。通过完整参与「多源轨迹数据采集 → 清洗与标准化 → 路网底图加载 → 地图匹配与路网映射 → 路段级指标计算（流量/速度/拥堵指数）→ 出行特征挖掘（OD/通勤/热区）→ 实时监控大屏 → 前后端平台搭建 → 系统测试与部署」的全流程，系统性地实践交通大数据处理与时空分析的完整工程链路。

项目基于城市浮动车 GPS 轨迹数据（出租车 50 辆 / 网约车 30 辆 / 共享单车 20 辆，合计 50 万模拟轨迹点），依托方格状 + 环形放射混合路网（≥ 500 条路段），构建一套集轨迹数据管理、路段拥堵分析、出行热区识别、通勤规律挖掘、实时监控大屏于一体的分析平台。核心技术包括 HMM 隐马尔可夫地图匹配、DBSCAN 热区聚类、Pandas 向量化轨迹清洗管道、APScheduler 自动调度等，贴合智慧城市交通治理中「让管理者看见路网运行状态、让规划者掌握出行规律、让决策者有数据依据」的真实业务场景。

## 二、项目目标

1. 完整参与项目全流程工程实践，掌握从需求分析、架构设计到系统部署的端到端能力。
2. 培养轨迹数据处理能力：实现多源 GPS 轨迹接入（CSV/Parquet/API 拉取）、坐标系转换（GCJ-02 → WGS-84）、漂移点过滤、停留点识别、轨迹分段等清洗管道，万级车辆数据处理耗时 < 30s。
3. 培养时空分析建模能力：实现 HMM 简易版地图匹配、路段级流量/平均速度/拥堵指数计算（≥ 500 条路段）、OD 矩阵估算与通勤模式识别。
4. 培养全栈开发能力：开发 FastAPI 后端 + react + Element Plus + ECharts 前端一体化平台，掌握前后端分离架构与 RESTful API 规范。
5. 培养数据可视化能力：实现实时监控大屏（路网热力图 + 拥堵路段柱状图 + 实时指标卡片 + 趋势折线图）与交互式路段分析页面，支持时间/区域筛选联动。
6. 培养工程化能力：掌握 Docker Compose 多服务编排（MySQL healthcheck + backend depends_on + Nginx 反向代理）、Python 独立测试脚本（SkipTest + 退出码 0/1）、性能优化（Pandas 向量化 + 联合索引）等工程实践。

## 三、项目内容与阶段

### 多源轨迹数据采集与接入

- 对接三类浮动车数据源：出租车 GPS（50 辆，3s 采样）、网约车 GPS（30 辆，5s 采样）、共享单车 GPS（20 辆，10s 采样）。
- 设计统一数据接入层，支持文件导入（CSV/Parquet）、API 拉取两种接入方式。
- 生成模拟轨迹数据集（50 万点，含约 3-5% 脏数据：漂移点/隧道丢失/停留点/方向异常）。
- 实现数据源 CRUD 管理（创建/查询/更新/删除/启停切换）、数据源统计与状态监控。
- 完成轨迹字段校验、GCJ-02 → WGS-84 坐标系统一、批量入库（bulk_insert_mappings）。

### 轨迹数据清洗与标准化

- 实现完整清洗管道（全 Pandas 向量化，禁止 iterrows）：
  - 漂移点过滤：speed > 120 km/h 或前后点距离 > 200m 但时间间隔 < 10s。
  - 隧道丢失标记：同一车辆相邻点时间间隔 > 180s，标记 gap 后点。
  - 停留点识别：连续位置距离 < 30m 且持续时间 ≥ 120s。
  - 轨迹分段：时间间隔 > 300s 自动切分，生成 segment_id。
- 清洗管道可配置（漂移阈值、隧道阈值、停留阈值通过 [config.py](http://config.py/) 调整）。
- 支持一键重跑清洗，输出清洗前后对比统计（原始点数 / 清洗后点数 / 丢弃点数）。

### 路网底图加载与地图匹配

- 加载方格状 + 环形放射混合路网（≥ 500 条路段，GeoJSON/WKT LINESTRING 格式，WGS-84 坐标系）。
- 路网路段属性：road_name / level（高速/主干道/次干道/支路）/ length（米）/ bounds 四字段。
- 实现 HMM 隐马尔可夫简易版地图匹配：
  - 候选路段生成：bounds 粗筛 + shapely 精筛，每点最多 5 个候选。
  - 高斯观测概率：GPS 点到候选路段的距离转换为观测概率（σ = 30m）。
  - 指数转移概率：相邻点投影距离越近转移概率越高（β = 30m）。
  - 对数空间维特比解码：找到全局最优候选路段序列。
  - shapely 投影：将 GPS 点精确投影到匹配路段上。
- 匹配结果三层可视化（前端）：蓝色=原始轨迹点、绿色=匹配路段、红色=未匹配点。
- 目标匹配率 ≥ 90%。

### 路段级指标计算

- 路段流量统计：按 5 分钟时间窗 Pandas groupby 聚合，统计各路段轨迹点数量。
- 路段平均速度：计算各路段车辆平均速度（极值处理：去掉最高/最低 5%）。
- 拥堵指数：拥堵指数 = 1 – 实际速度 / 自由流速度，clamp(0, 1)，0=畅通，1=完全拥堵。
- 动态自由流速度估算：不用固定值，取该路段历史 7 天最低流量时刻的平均速度作为自由流速度。
- 时间段划分常量：早高峰 7-9 / 晚高峰 17-19 / 平峰。

### 出行特征挖掘

- OD 矩阵构建：识别轨迹起止点，通过路段归属映射构建 OD 矩阵（origin × destination × 时间段）。
- 通勤规律识别：按时间段聚合 OD，早高峰进城 vs 晚高峰出城对比，Top20 OD 对柱状图。
- DBSCAN 热区识别（sklearn.cluster.DBSCAN）：
  - 经纬度转 UTM 米制投影，避免经纬度距离偏差。
  - 聚类参数：ε = 500m，min_samples = 10。
  - 热区属性：中心坐标、半径、点数、类型（居住/商业/交通枢纽）。
  - ECharts geo effectScatter 热力图展示。

### 实时监控大屏开发

- 指标卡片：在线车辆数、今日总轨迹点数、平均速度、拥堵路段占比。
- 路网热力图：ECharts geo 热力图展示路段拥堵分布（颜色 = 拥堵指数）。
- 拥堵路段 Top10：柱状图展示当前拥堵最严重的 10 条路段。
- 24 小时指标趋势：折线图展示近 24 小时核心指标变化。
- 30 秒自动刷新（setInterval）。
- Dashboard 聚合接口 `/api/dashboard/summary` 一次返回全部指标。

### 路段分析与轨迹管理页面

- 轨迹数据管理页面：数据源表格 + 轨迹查询筛选分页 + 清洗配置表单 + 一键重跑 + 清洗前后对比条形图。
- 路段分析页面：路段详情（基础信息 + 拥堵指数历史曲线 + 上下游路段列表）+ 区域筛选 + 数据导出。
- 地图匹配结果页面：三层叠加可视化 + 车辆选择器（远程搜索）+ 时间范围 + 图层开关 + 匹配率 Gauge 卡片 + 匹配轨迹列表。
- OD 矩阵页面：OD 矩阵表格 + Top20 OD 对柱状图 + 早/晚/平峰 Tab 切换。
- 出行热区页面：ECharts geo effectScatter 热力图 + 热区详情表格 + 热区类型 Tab。

### 后端 API、定时任务与部署

- 6 组路由覆盖全部业务模块（data_source / trajectory / match / road / od / hotzone），38+ 接口。
- 统一响应格式 `{"code": 0, "data": {...}, "msg": ""}`，所有 POST/PUT 接口有 Pydantic Schema 校验。
- Swagger UI 可调试全部接口（`/docs`）。
- APScheduler AsyncIOScheduler 定时调度：路段指标 5min 重算 + OD 矩阵 1h 重算，启动时自动执行一次初始化计算。
- 手动触发接口 `POST /api/task/recompute`（async=true 异步执行）+ 进度查询 `GET /api/task/status`。
- Docker Compose 三服务编排：MySQL（healthcheck `mysqladmin ping`）→ backend（depends_on `condition: service_healthy`）→ frontend（Nginx `/api` 反向代理）。
- init_db.sql 自动挂载执行（MySQL 首次启动时自动创建 8 张表）。

## 四、功能需求

| 功能模块    | 功能点         | 说明                                                                               |
| ------- | ----------- | -------------------------------------------------------------------------------- |
| 轨迹数据管理  | 轨迹数据上传      | 支持 CSV/Parquet 格式上传，含字段校验与批量入库                                                   |
|         | 轨迹数据查询      | 按车辆 ID、时间范围、区域多条件筛选，分页返回轨迹点                                                      |
|         | 数据源管理       | 数据源 CRUD（出租车/网约车/共享单车）、启停切换、统计                                                   |
|         | 清洗管道管理      | 清洗规则配置、一键重跑、清洗前后对比                                                               |
| 路网与地图匹配 | 路网底图加载      | 导入 GeoJSON/Shapefile，加载路段列表（≥ 500 条）                                             |
|         | 地图匹配        | HMM 简易版（候选生成 + 高斯观测概率 + 指数转移概率 + 维特比解码）                                          |
|         | 匹配结果可视化     | 三层叠加：蓝=原始、绿=匹配、红=未匹配                                                             |
| 路段级指标计算 | 路段流量统计      | 5 分钟时间窗 Pandas groupby 聚合                                                        |
|         | 路段平均速度      | 极值处理（去掉最高/最低 5%）                                                                 |
|         | 拥堵指数        | 1 – 实际速度 / 自由流速度，clamp(0, 1)                                                     |
| 出行分析    | OD 矩阵构建     | 识别起止点，构建 origin×destination×时间段矩阵                                                |
|         | 通勤规律识别      | 早/晚/平峰 Tab 切换，Top20 OD 对柱状图                                                      |
|         | 热区识别        | DBSCAN 聚类（ε=500m, min=10），UTM 投影                                                 |
| 实时监控大屏  | 指标卡片        | 在线车辆 / 总轨迹点 / 平均速度 / 拥堵占比                                                        |
|         | 路网热力图       | ECharts geo，颜色=拥堵指数                                                              |
|         | 拥堵路段 Top10  | 柱状图，30 秒自动刷新                                                                     |
|         | 24h 趋势      | 折线图，近 24 小时核心指标                                                                  |
| 路段分析页面  | 路段详情        | 基础信息 + 拥堵指数历史 + 上下游路段                                                            |
|         | 区域筛选        | 行政区/地图框选联动                                                                       |
|         | 数据导出        | CSV/Excel 导出                                                                     |
| 后端服务    | RESTful API | 38+ 接口，统一响应，Swagger 可调试                                                          |
|         | 定时任务        | 5min 路段指标 + 1h OD 矩阵，手动触发 + 状态查询                                                 |
|         | 性能优化        | 联合索引（vehicle_id+timestamp / road_id+time_window）、Pandas 向量化、bulk_insert_mappings |

## 五、基础架构

### 总体架构

系统采用前后端分离架构，分为四层：

```Plain
┌──────────────────────────────────────────────────────────────────┐
│  接入层（前端平台）                                                │
│  react + Element Plus + ECharts + Vite + Pinia + react Router      │
└───────────────────────────────┬──────────────────────────────────┘
                                │ HTTP /api/*
┌───────────────────────────────▼──────────────────────────────────┐
│  服务层（后端服务）                                                │
│  FastAPI 路由层 → 服务层 → 算法层                                  │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐│
│  │ 轨迹服务  │ │ 路网服务  │ │ 匹配服务  │ │ 指标服务  │ │ 出行服务  │
│  └──────────┘ └──────────┘ └──────────┘ └──────────┘ └──────────┘│
│  ┌──────────┐ ┌──────────┐                                       │
│  │ 调度服务  │ │ 数据源服务 │                                      │
│  └──────────┘ └──────────┘                                       │
└───────────────────────────────┬──────────────────────────────────┘
                                │
┌───────────────────────────────▼──────────────────────────────────┐
│  算法层（核心计算）                                                │
│  HMM 地图匹配 │ DBSCAN 热区聚类 │ Pandas 向量化清洗 │ 拥堵指数公式   │
└───────────────────────────────┬──────────────────────────────────┘
                                │
┌───────────────────────────────▼──────────────────────────────────┐
│  数据层（持久化存储）                                              │
│  SQLite（本地开发）/ MySQL 8.0（生产）+ GeoJSON/CSV 文件            │
└──────────────────────────────────────────────────────────────────┘
```

### 各层职责

| 层级  | 职责                      | 关键组件                                                                                                                                                                               |
| --- | ----------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 接入层 | 用户交互、数据展示、ECharts 可视化图表 | 7 个 Vue 页面（Dashboard / TrajectoryManage / MatchResult / RoadAnalysis / ODMatrix / HotZone）+ 公共组件                                                                                   |
| 服务层 | 业务逻辑处理、API 接口、定时调度      | 9 个 service（trajectory_parser / trajectory_cleaner / road_loader / map_matcher / road_metrics_service / congestion_service / od_service / hotzone_service / scheduler）+ 7 组 router |
| 算法层 | 核心时空计算                  | HMM 维特比解码、DBSCAN 聚类、Haversine 距离、GCJ-02→WGS-84 坐标转换                                                                                                                                |
| 数据层 | 数据持久化、文件存储              | 8 张数据库表（data_source / raw_trajectory / clean_trajectory / road_segment / match_result / road_metrics / od_matrix / hotzone）+ GeoJSON 路网                                            |

## 六、技术栈

| 类别   | 技术选型                    | 说明                                                   |
| ---- | ----------------------- | ---------------------------------------------------- |
| 开发语言 | Python 3.10+            | 后端与算法开发语言                                            |
| 数据处理 | pandas 2.2 + numpy 1.26 | 轨迹清洗管道、指标聚合计算（向量化，禁止 iterrows）                       |
| 机器学习 | scikit-learn 1.5        | DBSCAN 热区聚类                                          |
| 坐标转换 | pyproj 3.6              | GCJ-02 → WGS-84 转换（numpy 向量化）                        |
| 空间计算 | shapely 2.0             | WKT LINESTRING 距离/投影/bounds 计算                       |
| 后端框架 | FastAPI 0.115           | 高性能异步后端框架 + Pydantic 校验 + Swagger UI                 |
| ORM  | SQLAlchemy 2.0          | 支持 SQLite（本地）和 MySQL 8.0（生产）双模式                      |
| 定时调度 | APScheduler 3.10        | AsyncIOScheduler，与 FastAPI 异步事件循环兼容                  |
| 前端框架 | react + Element Plus    | 组合式 API + 组件库成熟，适合数据管理后台                             |
| 可视化  | ECharts                 | geo 热力图 / 柱状图 / 折线图 / 仪表盘 / 饼图                       |
| 前端构建 | Vite 5                  | /api 代理到 backend:8000                                |
| 状态管理 | Pinia                   | 用户偏好、筛选条件等全局状态                                       |
| 数据存储 | SQLite / MySQL 8.0      | 默认 SQLite（开发），Docker 部署用 MySQL                       |
| 容器化  | Docker + docker-compose | 三服务编排：MySQL healthcheck → backend depends_on → Nginx |
| 测试   | Python 独立脚本             | SkipTest + case_xxx + main + 退出码 0/1                 |

### 工程目录结构

```Plain
code/
├── backend/
│   ├── app/
│   │   ├── __init__.py
│   │   ├── main.py                       # FastAPI 应用入口 + CORS + startup/shutdown
│   │   ├── config.py                     # pydantic_settings 配置类
│   │   ├── core/
│   │   │   ├── __init__.py               # 统一导出 settings / Base / SessionLocal / engine / success / error / BusinessException
│   │   │   ├── database.py               # SQLAlchemy engine + SessionLocal + Base
│   │   │   ├── response.py               # success() / error() 统一响应
│   │   │   └── exceptions.py             # BusinessException + 全局异常处理器
│   │   ├── models/
│   │   │   ├── __init__.py               # 聚合导入 8 个模型
│   │   │   ├── data_source.py
│   │   │   ├── raw_trajectory.py
│   │   │   ├── clean_trajectory.py
│   │   │   ├── road_segment.py
│   │   │   ├── match_result.py
│   │   │   ├── road_metrics.py
│   │   │   ├── od_matrix.py
│   │   │   └── hotzone.py
│   │   ├── schemas/
│   │   │   ├── __init__.py
│   │   │   ├── common.py                 # 统一响应 + PageResponse 分页
│   │   │   ├── data_source.py
│   │   │   ├── trajectory.py
│   │   │   ├── road.py
│   │   │   ├── match.py
│   │   │   ├── od.py
│   │   │   └── hotzone.py
│   │   ├── services/
│   │   │   ├── __init__.py
│   │   │   ├── trajectory_parser.py      # CSV/Parquet 解析 + GCJ02→WGS84 + bulk 入库
│   │   │   ├── trajectory_cleaner.py      # 漂移/隧道/停留/分段（全向量化）
│   │   │   ├── road_loader.py            # GeoJSON 路网加载 + 候选路段查询
│   │   │   ├── map_matcher.py            # HMM 地图匹配（高斯观测 + 指数转移 + 维特比解码）
│   │   │   ├── road_metrics_service.py    # 5min Pandas groupby 流量/速度聚合
│   │   │   ├── congestion_service.py      # 拥堵指数 + 自由流速度动态估算
│   │   │   ├── od_service.py             # OD 矩阵 + 通勤规律识别
│   │   │   ├── hotzone_service.py        # DBSCAN 聚类 + 热区类型判定
│   │   │   └── scheduler.py              # APScheduler + task_status 字典
│   │   └── routers/
│   │       ├── __init__.py
│   │       ├── data_source.py
│   │       ├── trajectory.py
│   │       ├── match.py
│   │       ├── road.py
│   │       ├── od.py
│   │       ├── hotzone.py
│   │       ├── dashboard.py
│   │       └── task.py
│   ├── scripts/
│   │   ├── init_db.sql                   # 8 张表 DDL + 全部索引
│   │   └── seed_data.py                  # 50 万模拟轨迹 + 600 路段生成（含脏数据注入）
│   ├── data/
│   │   ├── uploads/                      # 用户上传的 CSV/Parquet
│   │   └── seed/
│   │       └── road_network.geojson      # 导出的路网 GeoJSON
│   ├── requirements.txt                  # 16 个依赖
│   └── traffic.db                        # SQLite 数据库（本地开发自动生成）
├── frontend/                              # react 前端项目（你需要完成）
└── docker/                                # Docker 部署配置（你需要完成）
```

## 七、成果物清单

| 类别    | 成果物                    | 具体要求                                                                                                                                                                                                                    |
| ----- | ---------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 后端代码  | FastAPI 后端完整项目         | 包含 models（8 张表 ORM）、services（轨迹解析、清洗、路网加载、地图匹配、路段指标、拥堵指数、OD 矩阵、热区识别、定时调度）、routers（6 组路由 + 任务调度 + 大屏聚合）、core（数据库连接、统一响应、异常处理）、schemas（Pydantic 请求/响应模型）                                                                  |
| 前端代码  | react 单页应用完整项目         | 7 个核心页面（实时监控大屏 Dashboard、轨迹数据管理 TrajectoryManage、地图匹配结果 MatchResult、路段分析 RoadAnalysis、OD 矩阵 ODMatrix、出行热区 HotZone）+ 公共组件 + 路由配置 + Pinia 状态管理 + Vite 构建配置                                                                |
| 数据库脚本 | init_db.sql + 模拟数据生成脚本 | 8 张核心表 DDL（含联合索引）+ 路网底图 + 50 万模拟轨迹（含脏数据）                                                                                                                                                                                |
| 部署配置  | Docker Compose 三服务编排   | backend Dockerfile（python:3.11-slim）、frontend Dockerfile（node 构建 → nginx 托管）、docker-compose.yml（MySQL healthcheck mysqladmin ping + backend depends_on condition: service_healthy + Nginx /api 反向代理）、init_db.sql 自动挂载执行 |
| 项目文档  | 项目说明文档                 | 描述清楚你的平台做了什么、怎么做到的、架构怎么设计、核心算法原理、遇到的问题与解决办法                                                                                                                                                                             |
| 演示    | 全链路演示                  | 能够现场演示：导入脏轨迹 → 一键清洗 → 地图匹配 → 路段指标计算 → OD/热区分析 → 大屏展示，要求 ≤ 5 分钟跑通                                                                                                                                                        |
