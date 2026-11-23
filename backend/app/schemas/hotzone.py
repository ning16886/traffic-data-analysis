"""出行热区接口的 Pydantic 校验模型。

只有重算是写接口，需要完整入参校验；查询接口用 Query 做范围校验，返回结构
沿用统一信封 { code, data, msg }，由 service 直接给字典。
"""

from pydantic import BaseModel, ConfigDict, Field

from app.core import settings


class HotzoneRecomputeRequest(BaseModel):
    """重算参数。不传 Body 时全部取 config.py 默认值（ε=500m / min_samples=10）。"""

    model_config = ConfigDict(extra="forbid")

    eps: float = Field(
        default_factory=lambda: settings.hotzone_dbscan_eps_m,
        gt=50,
        le=3_000,
        description="DBSCAN 邻域半径 ε（米）",
    )
    min_samples: int = Field(
        default_factory=lambda: settings.hotzone_min_samples,
        ge=2,
        le=100,
        description="DBSCAN 核心点最小样本数",
    )
    min_points: int = Field(
        default_factory=lambda: settings.hotzone_min_points,
        ge=2,
        le=1_000,
        description="簇进入结果表的最少点数（DBSCAN 之外再过滤一次）",
    )
    max_zones: int = Field(
        default_factory=lambda: settings.hotzone_max_zones,
        ge=1,
        le=2_000,
        description="最多保留多少个热区",
    )
    sample_ratio: float = Field(
        default_factory=lambda: settings.hotzone_slow_sample_ratio,
        gt=0,
        le=1,
        description="6~12km/h 之间的中速点抽样比例",
    )
