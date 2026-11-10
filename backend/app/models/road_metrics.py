"""路段 5 分钟指标表。一条路段 × 一个时间窗 = 一行。

time_window 对齐到 5 分钟（08:05:00 这种），(road_id, time_window) 唯一，
定时任务每 5 分钟重算走 upsert，不重复插行。

congestion_index / free_flow_speed 不落库：列与 data/raw/road_metrics.csv 严格一致，
拥堵指数由服务层按「1 − 实际速度 / 自由流速度」现算。
"""

from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class RoadMetrics(Base):
    __tablename__ = "road_metrics"
    __table_args__ = (
        UniqueConstraint("road_id", "time_window", name="uk_metrics_road_window"),
        Index("idx_metrics_window", "time_window"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    road_id: Mapped[str] = mapped_column(ForeignKey("road_segment.road_id"), nullable=False)
    time_window: Mapped[datetime] = mapped_column(DateTime, nullable=False, comment="对齐到 5 分钟")
    point_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, comment="窗口内轨迹点数=流量")
    avg_speed: Mapped[float | None] = mapped_column(Float, comment="截尾均值")
    min_speed: Mapped[float | None] = mapped_column(Float)
    max_speed: Mapped[float | None] = mapped_column(Float)
