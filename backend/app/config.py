"""全局配置。所有可调项集中在这里，代码其余部分一律 from app.core import settings。"""

from functools import lru_cache
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# backend/ 目录。所有相对路径都以它为基准，这样无论从哪个工作目录启动都不会跑偏。
BASE_DIR = Path(__file__).resolve().parents[1]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="TRAFFIC_", extra="ignore")

    app_name: str = "城市交通轨迹大数据分析平台"
    debug: bool = True

    # 本地开发默认 SQLite（零依赖，跑起来就有库）；生产用 MySQL 8.0
    # MySQL 连接串形如：mysql+pymysql://root:pwd@localhost:3306/traffic?charset=utf8mb4
    database_url: str = "sqlite:///traffic.db"

    # 数据目录：raw_dir 是唯一数据源（仓库根 data/raw 的八张原始 CSV），
    # uploads 是统一接入层收到的上传落盘位置
    data_dir: Path = BASE_DIR / "data"
    raw_dir: Path = BASE_DIR.parent / "data" / "raw"
    upload_dir: Path = BASE_DIR / "data" / "uploads"

    # 允许上传的扩展名（需求：统一接入层）
    allowed_extensions: list[str] = [".csv", ".json", ".geojson", ".wkt", ".parquet"]

    # ---- 清洗阈值（需求文档 33-35 行的三条规则，逐条可配）----
    drift_speed_kmh: float = 120.0        # 规则一a：speed 超过此值判为漂移点
    drift_jump_m: float = 200.0           # 规则一b：与上一点距离超过此值…
    drift_jump_seconds: float = 10.0      #         …且时间间隔小于此值，判为漂移点
    stay_seconds: int = 120               # 停留持续时长下限
    stay_radius_m: float = 30.0           # 停留位移半径上限（文档写的 30m）
    gap_seconds: int = 180                # 相邻点间隔超过此值判为隧道丢失（段内）
    trip_break_seconds: int = 400         # 间隔超过此值视为另一次出行，先分段再判空洞
    # 分段阈值必须落在「隧道空洞 200~250s」与「车辆停放 ≥600s」之间，
    # 否则停放会被当成隧道丢失，轨迹段也会串。

    # ---- HMM 地图匹配参数（以任务截图为准：σ=20m, β=π/4）----
    # 注意：这组 hmm_* 目前只被 scripts/seed_data.py 造数据用，
    # 真正跑匹配的引擎读下面的 match_*，两套不要混用。
    hmm_search_radius_m: float = 50.0     # 候选路段搜索半径 R
    hmm_sigma_m: float = 20.0             # 观测高斯标准差 σ
    hmm_beta: float = 0.7853981633974483  # 转移指数 β = π/4
    hmm_transition_prob: float = 0.01     # 无路匹配的概率

    # ---- 地图匹配引擎（需求第 44-49 行：候选生成 + 高斯观测 + 指数转移 + 维特比）----
    match_threshold_m: float = 100.0       # 页面默认垂距阈值，超过判为未匹配
    match_sigma_m: float = 30.0            # 观测概率 σ（需求第 46 行）
    match_beta_m: float = 30.0             # 转移概率 β（需求第 47 行）
    match_max_candidates: int = 5          # 每点最多候选路段数（需求第 45 行）
    match_candidate_radius_m: float = 200.0  # 候选搜索半径（含漂移点容错）
    match_direction_weight: float = 0.4    # 方向一致性权重（对数空间，仅打破同距平局）
    match_level_weight: float = 0.15       # 道路等级权重（同距优先主干道）
    match_cache_dir: Path = BASE_DIR / "out" / "match_cache"  # 全量匹配结果缓存

    # ---- 指标口径 ----
    metrics_window: str = "5min"          # road_metrics 落表粒度（需求硬规定）
    dashboard_window: str = "1h"          # 大屏展示粒度：5min 快照只有 14 条路有数据，Top10 排不满
    congestion_threshold: float = 0.25    # 拥堵指数超过此值记为拥堵路段（需求口径下指数全网最高 0.510，0.5 会只剩 1 条路段过线）
    min_points_for_speed: int = 5         # 窗内样本下限，不足则不出均速
    trim_ratio: float = 0.10              # 截尾均值比例
    free_flow_days: int = 7               # 自由流速度回看天数

    # ---- 定时调度 ----
    metrics_interval_minutes: int = 5
    od_interval_minutes: int = 60

    cors_origins: list[str] = [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ]

    @field_validator("database_url")
    @classmethod
    def _abs_sqlite_path(cls, v: str) -> str:
        """SQLite 的相对路径同样以 backend/ 为基准，避免 traffic.db 落到别处。"""
        prefix = "sqlite:///"
        if v.startswith(prefix) and not Path(v[len(prefix):]).is_absolute():
            return prefix + str(BASE_DIR / v[len(prefix):])
        return v


@lru_cache
def get_settings() -> Settings:
    return Settings()


# 模块级单例：业务代码统一 `from app.core import settings` 直接取用。
# get_settings() 保留给需要在改环境变量后强制重读的场合。
settings: Settings = get_settings()
