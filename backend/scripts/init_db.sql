-- =====================================================================
-- 城市交通轨迹大数据分析平台 —— 建表脚本（MySQL 8.0 方言）
--
-- 用法：Docker 部署时把本文件挂载到 /docker-entrypoint-initdb.d/，MySQL 容器
--       首次启动会自动执行。数据库名由 docker-compose 的 MYSQL_DATABASE 指定，
--       所以这里不写 CREATE DATABASE / USE。
--       本地 SQLite 开发不跑本文件，由 SQLAlchemy 的 Base.metadata.create_all()
--       按 app/models/ 里的 ORM 定义建表。
--
-- 建表顺序有依赖：data_source → road_segment → raw_trajectory → clean_trajectory
--                 → match_result → road_metrics → od_matrix → hotzone
--
-- 全库 utf8mb4：路段名、数据源名是中文，用 utf8 会把中文存成问号。
-- =====================================================================

SET NAMES utf8mb4;

DROP TABLE IF EXISTS hotzone;
DROP TABLE IF EXISTS od_matrix;
DROP TABLE IF EXISTS road_metrics;
DROP TABLE IF EXISTS match_result;
DROP TABLE IF EXISTS clean_trajectory;
DROP TABLE IF EXISTS raw_trajectory;
DROP TABLE IF EXISTS road_segment;
DROP TABLE IF EXISTS data_source;

-- ---------------------------------------------------------------------
-- 1. 数据源登记表：整张表只有三行（出租车 / 网约车 / 共享单车）
-- ---------------------------------------------------------------------
CREATE TABLE data_source (
    id            INT NOT NULL AUTO_INCREMENT,
    name          VARCHAR(50) NOT NULL COMMENT '中文名：出租车/网约车/共享单车',
    type          VARCHAR(20) NOT NULL COMMENT '程序用类型码：taxi/ridehailing/bikeshare',
    description   VARCHAR(255) DEFAULT NULL COMMENT '采样间隔等说明',
    status        TINYINT NOT NULL DEFAULT 1 COMMENT '1=启用 0=停用',
    vehicle_count INT NOT NULL DEFAULT 0 COMMENT '车辆数，必须由脚本/导入统计回填，不能手填',
    point_count   INT NOT NULL DEFAULT 0 COMMENT '轨迹点数，必须与 raw_trajectory 实际行数一致',
    created_at    DATETIME DEFAULT NULL,
    PRIMARY KEY (id),
    UNIQUE KEY uk_data_source_type (type)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='数据源登记与统计（大屏右侧表格、卡片数据源）';

-- ---------------------------------------------------------------------
-- 2. 路网路段表：几何用 WKT 字符串存，坐标系 WGS-84
-- ---------------------------------------------------------------------
CREATE TABLE road_segment (
    id              INT NOT NULL AUTO_INCREMENT,
    road_id         VARCHAR(10) NOT NULL COMMENT '业务主键，形如 R00001',
    road_name       VARCHAR(50) NOT NULL COMMENT '南北路3段12 / 环线2段5 / 放射路7段2',
    level           VARCHAR(20) NOT NULL COMMENT 'highway/main/secondary/branch',
    length          FLOAT NOT NULL DEFAULT 0 COMMENT '路段长度（米），必须大于 0',
    geometry        TEXT NOT NULL COMMENT 'WKT：LINESTRING(116.30000 39.85000, ...)',
    bounds_min_lng  DOUBLE NOT NULL,
    bounds_min_lat  DOUBLE NOT NULL,
    bounds_max_lng  DOUBLE NOT NULL,
    bounds_max_lat  DOUBLE NOT NULL,
    PRIMARY KEY (id),
    UNIQUE KEY uk_road_segment_road_id (road_id),
    KEY idx_road_segment_level (level),
    -- HMM 候选路段生成的第一步就是 bounds 粗筛，先走这两个索引再 shapely 精筛
    KEY idx_road_segment_bounds_lng (bounds_min_lng, bounds_max_lng),
    KEY idx_road_segment_bounds_lat (bounds_min_lat, bounds_max_lat)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='路网路段（原始数据 76 条）';

-- ---------------------------------------------------------------------
-- 3. 原始轨迹表：与路网同坐标系，入库不做坐标转换
--    （实测 data/raw/raw_trajectory.csv 的点到最近路段中位距离 0.00 m）
-- ---------------------------------------------------------------------
CREATE TABLE raw_trajectory (
    id          BIGINT NOT NULL AUTO_INCREMENT,
    vehicle_id  VARCHAR(20) NOT NULL COMMENT '形如 ta_000 / ri_012 / bi_003',
    source_id   INT NOT NULL COMMENT '归属数据源',
    `timestamp` DATETIME(6) NOT NULL COMMENT '带微秒，采样间隔 3s/5s/10s',
    longitude   DOUBLE NOT NULL,
    latitude    DOUBLE NOT NULL,
    speed       FLOAT NOT NULL COMMENT 'km/h，脏数据允许 >120，由清洗管道剔除',
    direction   FLOAT DEFAULT NULL COMMENT '方位角 0~360',
    passenger   TINYINT DEFAULT 0 COMMENT '载客状态，共享单车恒为 0',
    PRIMARY KEY (id),
    -- 需求文档点名的联合索引：按车 + 时间段查轨迹必须走它（原始数据 1140 行）
    KEY idx_raw_vehicle_ts (vehicle_id, `timestamp`),
    KEY idx_raw_source_ts (source_id, `timestamp`),
    CONSTRAINT fk_raw_source FOREIGN KEY (source_id) REFERENCES data_source (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='原始轨迹点（原始数据 1140 行）';

-- ---------------------------------------------------------------------
-- 4. 清洗后轨迹表：漂移点已被剔除，其余脏数据打标记保留
-- ---------------------------------------------------------------------
CREATE TABLE clean_trajectory (
    id          BIGINT NOT NULL AUTO_INCREMENT,
    vehicle_id  VARCHAR(20) NOT NULL,
    source_id   INT NOT NULL,
    segment_id  VARCHAR(30) NOT NULL COMMENT '轨迹分段号，形如 ta_000_2（车辆ID_段序号）',
    `timestamp` DATETIME(6) NOT NULL,
    longitude   DOUBLE NOT NULL,
    latitude    DOUBLE NOT NULL,
    speed       FLOAT NOT NULL,
    direction   FLOAT DEFAULT NULL,
    is_stay     TINYINT NOT NULL DEFAULT 0 COMMENT '1=停留点（连续 <30m 且 ≥120s）',
    is_gap      TINYINT NOT NULL DEFAULT 0 COMMENT '1=隧道丢失后的首个点（间隔 >180s）',
    flag        VARCHAR(20) DEFAULT 'ok' COMMENT 'ok / tunnel',
    PRIMARY KEY (id),
    KEY idx_clean_vehicle_ts (vehicle_id, `timestamp`),
    KEY idx_clean_segment (segment_id),
    KEY idx_clean_source_ts (source_id, `timestamp`),
    CONSTRAINT fk_clean_source FOREIGN KEY (source_id) REFERENCES data_source (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='清洗后轨迹点（地图匹配的输入）';

-- ---------------------------------------------------------------------
-- 5. 地图匹配结果表：一个清洗点对应一条匹配记录
-- ---------------------------------------------------------------------
CREATE TABLE match_result (
    id                  BIGINT NOT NULL AUTO_INCREMENT,
    vehicle_id          VARCHAR(20) NOT NULL,
    road_id             VARCHAR(10) DEFAULT NULL COMMENT '未匹配时为 NULL',
    raw_trajectory_id   BIGINT DEFAULT NULL,
    clean_trajectory_id BIGINT NOT NULL COMMENT '匹配的主输入，重匹配按此覆盖',
    match_longitude     DOUBLE DEFAULT NULL COMMENT '投影到路段后的经度',
    match_latitude      DOUBLE DEFAULT NULL,
    match_probability   FLOAT DEFAULT NULL COMMENT '维特比路径概率',
    is_matched          TINYINT NOT NULL DEFAULT 0 COMMENT '1=匹配成功（前端绿色），0=未匹配（红色）',
    create_time         DATETIME DEFAULT NULL,
    PRIMARY KEY (id),
    KEY idx_match_vehicle (vehicle_id, id),
    KEY idx_match_road (road_id),
    KEY idx_match_clean (clean_trajectory_id),
    CONSTRAINT fk_match_road FOREIGN KEY (road_id) REFERENCES road_segment (road_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='HMM 地图匹配结果（匹配率目标 ≥ 90%）';

-- ---------------------------------------------------------------------
-- 6. 路段指标表：5 分钟一个时间窗，一条路段一行
--    列与 data/raw/road_metrics.csv 完全一致；拥堵指数不落库，由服务层现算
-- ---------------------------------------------------------------------
CREATE TABLE road_metrics (
    id               BIGINT NOT NULL AUTO_INCREMENT,
    road_id          VARCHAR(10) NOT NULL,
    time_window      DATETIME NOT NULL COMMENT '对齐到 5 分钟，如 08:05:00',
    point_count      INT NOT NULL DEFAULT 0 COMMENT '窗口内轨迹点数（流量）',
    avg_speed        FLOAT DEFAULT NULL COMMENT '截尾均值：去掉最高最低 5%',
    min_speed        FLOAT DEFAULT NULL,
    max_speed        FLOAT DEFAULT NULL,
    PRIMARY KEY (id),
    -- 唯一约束：定时任务每 5 分钟重算一次，重算走 upsert 而不是重复插行
    UNIQUE KEY uk_metrics_road_window (road_id, time_window),
    KEY idx_metrics_window (time_window),
    CONSTRAINT fk_metrics_road FOREIGN KEY (road_id) REFERENCES road_segment (road_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='路段级 5 分钟指标';

-- ---------------------------------------------------------------------
-- 7. OD 矩阵表：起止路段 × 时间段
-- ---------------------------------------------------------------------
CREATE TABLE od_matrix (
    id               BIGINT NOT NULL AUTO_INCREMENT,
    origin_road_id   VARCHAR(10) NOT NULL,
    dest_road_id     VARCHAR(10) NOT NULL,
    time_period      VARCHAR(20) NOT NULL COMMENT 'morning_peak/evening_peak/normal',
    trip_count       INT NOT NULL DEFAULT 0,
    avg_duration_min FLOAT DEFAULT NULL,
    create_time      DATETIME DEFAULT NULL,
    PRIMARY KEY (id),
    UNIQUE KEY uk_od_triple (origin_road_id, dest_road_id, time_period),
    KEY idx_od_period (time_period, trip_count)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='OD 矩阵与通勤规律';

-- ---------------------------------------------------------------------
-- 8. 出行热区表：DBSCAN 聚类结果
-- ---------------------------------------------------------------------
CREATE TABLE hotzone (
    id          BIGINT NOT NULL AUTO_INCREMENT,
    zone_id     VARCHAR(20) NOT NULL COMMENT '形如 HZ_052',
    center_lng  DOUBLE NOT NULL,
    center_lat  DOUBLE NOT NULL,
    radius_m    FLOAT NOT NULL COMMENT '簇半径（米），由 UTM 米制投影算出',
    point_count INT NOT NULL DEFAULT 0,
    zone_type   VARCHAR(20) NOT NULL COMMENT 'residential/business/transit',
    create_time DATETIME DEFAULT NULL,
    PRIMARY KEY (id),
    UNIQUE KEY uk_hotzone_zone_id (zone_id),
    KEY idx_hotzone_type (zone_type)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='DBSCAN 热区（ε=500m，min_samples=10）';
