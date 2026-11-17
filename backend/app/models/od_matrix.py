"""OD 矩阵表：起止路段 × 时间段一行。

原始模拟数据包含 morning_peak / evening_peak / normal / transit 四类，
起终路段与类别三元组唯一。OD 子页面只读保留此表；手动重算写独立快照，
见 computed_od.py，不能用重算覆盖这里的导入结果。

这里不给 road_id 加外键：OD 的路段来自匹配结果，允许路网更新后留下
历史孤儿行，跟 init_db.sql 保持一致。
"""

from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class OdMatrix(Base):
    __tablename__ = "od_matrix"
    __table_args__ = (
        UniqueConstraint("origin_road_id", "dest_road_id", "time_period", name="uk_od_triple"),
        Index("idx_od_period", "time_period", "trip_count"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    origin_road_id: Mapped[str] = mapped_column(String(10), nullable=False)
    dest_road_id: Mapped[str] = mapped_column(String(10), nullable=False)
    time_period: Mapped[str] = mapped_column(
        String(20), nullable=False, comment="morning_peak/evening_peak/normal/transit"
    )
    trip_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    avg_duration_min: Mapped[float | None] = mapped_column(Float)
    create_time: Mapped[datetime | None] = mapped_column(DateTime)
