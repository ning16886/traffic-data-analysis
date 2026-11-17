# OD 子页面：运行与验收说明

OD 子页面及配套后端已完成，个人分支为 `feature/zhangwenye`。开发过程、模块验收及踩坑见根目录 `OD开发进度.md`。

## 已实现的功能

- 起终点明细：类别筛选、稳定排序、分页、按出行次数加权的平均时长。
- 交叉矩阵：按出发／到达次数各选最多 20 个路段，展示次数、色阶及覆盖范围；不是完整路网矩阵。
- Top20 OD 对：按整个筛选集合排名，提供柱状图、提示信息和可展开的文字排名，与明细页码无关。
- 全部、早高峰、晚高峰、平峰、接驳筛选，加载／空结果／失败重试，以及过期响应保护。
- 基于清洗轨迹与匹配结果手动重算；可切换原始模拟结果与持久化重算结果。
- 桌面和窄屏布局；仅在 OD 路由生效，不修改其他成员页面。

## 页面操作与说明入口

- 工作区依次为结果来源／操作、五类筛选、出行汇总和明细／矩阵；下方为 Top20 排名，保留两个主卡片。
- “统计口径”集中提供时段边界、来源差异、矩阵选轴及当前覆盖次数、最近重算时间和分段统计。点击或键盘 Enter 打开，Escape 关闭并返回触发按钮；点击外部也可关闭。
- “刷新”读取当前结果；“重算 OD”完成后切至重算结果，显示约 4 秒的“重算完成”提示，也可手动关闭。失败保留原因和“重试重算”入口。
- 重算来源下显示“更新于 MM-DD HH:mm”，完整时间含年份和北京时间，可悬停查看，也提供辅助阅读标签；详细时间同样位于统计口径。
- 矩阵标明“重点路段矩阵”、真实行列数和数值色标，起点列／表头固定，键盘聚焦后可用方向键滚动；完整覆盖次数在统计口径中，加载／失败时显示“—”。
- 窄屏下被收起的类别可从页签右侧更多入口选择；图表起终点编号换行，文字排名仍可展开。页面和图表响应系统“减少动态效果”设置。
- 视觉沿用项目蓝色主题、现有字体和 Ant Design；共享导航与汽车标识未纳入本次调整，OD 内容区未新增 emoji。

## 本机启动（仓库根目录）

M1 已完成依赖安装和隔离导入。本机不要再次导入数据库，直接启动即可。

后端，在一个 PowerShell 会话运行：

```powershell
$env:TRAFFIC_DATABASE_URL = 'sqlite:///data/od-dev/traffic.db'
$env:TRAFFIC_RAW_DIR = (Resolve-Path -LiteralPath 'data\raw').Path
.\.venv\Scripts\python.exe -X utf8 -m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8000
```

前端，在另一个会话运行：

```powershell
npm.cmd run dev --prefix frontend -- --host 127.0.0.1 --port 5173 --strictPort
```

- 页面：<http://127.0.0.1:5173/od>
- 后端 Swagger：<http://127.0.0.1:8000/docs>
- 两个会话可各按 Ctrl+C 停止。启动时不要占用已有的 8000／5173 服务。

## 其他机器首次准备

使用 Python 3.11+ 创建项目 `.venv`。本机实际解释器为 `D:\Anaconda\python.exe`，其他机器替换为其已安装的 Python 路径。

```powershell
& 'D:\Anaconda\python.exe' -m venv .venv
.\.venv\Scripts\python.exe -X utf8 -m pip install -r backend\requirements.txt
npm.cmd ci --prefix frontend
```

首次隔离导入：**原有导入脚本会删表重建，因此只有确认目标文件不存在时才运行**。下面的命令在文件存在时主动终止，不覆盖已有数据。

```powershell
if (Test-Path -LiteralPath 'backend\data\od-dev\traffic.db') {
    throw '目标数据库已存在，请使用只读核验命令，不要重新导入。'
}
New-Item -ItemType Directory -Path 'backend\data\od-dev' -Force | Out-Null
$env:TRAFFIC_DATABASE_URL = 'sqlite:///data/od-dev/traffic.db'
$env:TRAFFIC_RAW_DIR = (Resolve-Path -LiteralPath 'data\raw').Path
.\.venv\Scripts\python.exe -X utf8 backend\scripts\import_raw_data.py
```

原始 CSV 使用 `data/raw/`；`data/raw/raw/` 是本机重复副本，不重复导入或提交。`.venv`、运行库、浏览器依赖和构建目录已经忽略。

## 统计口径与结果保护

- 早高峰为 [07:00,10:00)，晚高峰为 [17:00,20:00)，其余为平峰；沿用生成器的边界。
- 原始数据包含独立的接驳类别。全部视图先合并同一起终点的不同类别，再分页，不能把原始表行数当成 OD 对数。
- 原始结果：2,141 条类别三元组，汇总后 1,985 个 OD 对、2,274 次出行。
- 重算按车辆＋轨迹段，排序后取首末有效匹配点，必须有正时长；同起终路段的有效出行保留。重复匹配造成歧义时拒绝重算。
- 重算只能依据可用时间生成三时段，无法恢复未导出的接驳标记；不把所有共享单车猜作接驳。因此重算分类和时长可能与模拟统计不同。
- 重算只写 `od_computed` 与 `od_computation` 独立表，成功后事务替换上次快照；失败回滚，原始八表和 CSV 不写入。无有效出行也可以成功生成零结果快照。
- 独立表只在首次手动重算时创建，不纳入原八表 metadata，不要求新增数据 CSV。需有创建／写入这些表的数据库权限。
- 结果是上次手动计算的快照；重新导入或修改轨迹后应再次手动计算。本子页面不运行全平台定时任务。
- 开发环境采用单进程锁防止重复重算；部署多 worker 时需由组长接入共享任务锁。SQLite 已实测，MySQL 采用兼容的 ORM／SQL，但本机未运行 MySQL 验收。

## 接口

统一成功响应为 `{code:0,data:...,msg:""}`，沿用项目全局异常处理。参数错误 HTTP 200／code=4220；预期业务失败使用非零 code。重算冲突为 4090，事务失败为 5001。

| 方法与路径 | 用途 |
|---|---|
| `GET /api/od/list` | 聚合分页；page 默认1、page_size默认20且最大100 |
| `GET /api/od/matrix` | 重点路段子矩阵、总出行次数及覆盖次数 |
| `GET /api/od/top` | 全筛选结果排名；limit默认20，范围1–20 |
| `GET /api/od/computation` | 上次重算时间、可用／运行状态和输入／分段统计 |
| `POST /api/od/recompute` | 无请求体，手动重算独立快照 |

三个查询接口均支持 `dataset=original|computed`（默认 original），以及可选的 `time_period=morning_peak|evening_peak|normal|transit`。全部类别省略 time_period，不传 `all`。未生成快照就查询 computed 会返回明确业务错误。

所有接口模型和参数可在 Swagger 查看。缺少路段名称回退路段编号；没有时长时返回 null，不将空值当零。

## 复现验收

后端测试使用独立测试依赖和临时库，不重建开发库：

```powershell
.\.venv\Scripts\python.exe -X utf8 -m pip install -r backend\requirements-test.txt
.\.venv\Scripts\python.exe -X utf8 -m unittest discover -s backend\tests -p 'test_od_*.py' -v
.\.venv\Scripts\python.exe -X utf8 backend\scripts\check_od_baseline.py --db backend\data\od-dev\traffic.db
npm.cmd run build --prefix frontend
```

启动后端后，可运行真实 HTTP 验证。默认只读；显式 `--recompute` 会更新独立重算结果两次，核对幂等和原始八表逐行摘要不变：

```powershell
.\.venv\Scripts\python.exe -X utf8 backend\scripts\check_od_http.py --db backend\data\od-dev\traffic.db --recompute --report backend\data\od-dev\od-http-report.json
```

真实浏览器测试默认使用本机已安装的 Microsoft Edge（无界面），需要前后端都运行。Playwright 单独安装，不加入前端应用依赖：

```powershell
npm.cmd install --prefix backend\data\od-dev\browser-check --no-save --package-lock=false --no-audit --no-fund playwright
node frontend\tests\od.browser.cjs
```

浏览器用例包含实际重算，因此会更新独立快照；同时使用模拟失败／空结果检验页面状态，测试结束后取消拦截，不会更改原始数据。报告和截图存入被忽略的 `backend/data/od-dev/`，不随源码提交。

2026-11-17 前端改造后，构建及 14 组浏览器检查通过，覆盖既有功能、统计口径键盘操作与动态覆盖、矩阵对比度、1440／768／390 布局、减少动效和忙碌状态；原始八表与 77 个受保护文件核验不变。复跑不需要更新截图时可先设置 `$env:OD_CAPTURE_UI = '0'`，所有交互与布局断言仍执行；正常截图复现需移除该环境变量。

## 五分钟演示顺序

1. 打开 `/od`，查看原始结果的 2,274 次出行与 1,985 个 OD 对。
2. 切换早晚高峰与接驳，翻页，观察次数和 Top20 联动。
3. 切到交叉矩阵，查看尺寸与数值色标；打开“统计口径”查看选轴规则和实时覆盖次数，水平滚动查看终点。
4. 点击“重算 OD”，完成后自动切到重算结果；查看更新时间，打开统计口径查看完整时间和分段统计。
5. 查看重算结果中接驳为零的说明，再切回原始模拟结果，确认仍保留原始分类。

## 已知非阻塞提示

- 原有公共主包超过 Vite 500 kB 提示阈值；构建成功，本次不重构其他模块打包。
- 原有 favicon.ico 缺失导致网站图标 404，已与真正页面运行异常区分。
- 当前 Starlette 对 httpx TestClient 发出弃用提示，测试仍通过；不影响应用请求。
