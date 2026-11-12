"""服务层：业务逻辑与算法编排，不直接暴露 HTTP。

已落地：dashboard_service（大屏聚合：指标卡 / 拥堵路段 Top N / 数据源统计，
拥堵指数在这层现算不落库）、trajectory_parser（CSV/Parquet 解析 + 坐标系处理 +
批量入库）、trajectory_cleaner（漂移/隧道/停留/分段，全向量化）、
road_loader（路网加载与候选路段查询）、
matching/（HMM 简易版地图匹配：候选生成 → 高斯观测 → 指数转移 → 对数维特比，
含服务层缓存与三元层可视化载荷）。

待建：road_metrics_service（5 分钟窗口聚合）、congestion_service（自由流速度估算）、
od_service（OD 矩阵与通勤规律）、hotzone_service（DBSCAN 聚类）、
scheduler（APScheduler 定时调度）。
"""
