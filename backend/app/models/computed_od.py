"""手动重算快照，独立元数据避免改变八表原始数据导入逻辑。"""

from datetime import datetime

from sqlalchemy import DateTime, Float, Integer, JSON, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class ComputedBase(DeclarativeBase):
    pass


class ComputedOd(ComputedBase):
    __tablename__ = "od_computed"
    origin_road_id: Mapped[str] = mapped_column(String(10), primary_key=True)
    dest_road_id: Mapped[str] = mapped_column(String(10), primary_key=True)
    time_period: Mapped[str] = mapped_column(String(20), primary_key=True)
    trip_count: Mapped[int] = mapped_column(Integer, nullable=False)
    avg_duration_min: Mapped[float | None] = mapped_column(Float)


class Computation(ComputedBase):
    __tablename__ = "od_computation"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    completed_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    stats: Mapped[dict] = mapped_column(JSON, nullable=False)
