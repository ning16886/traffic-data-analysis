"""清洗后轨迹表：漂移点已剔除，其余脏数据打标记保留。

segment_id 是「轨迹分段号」（形如 ta_000_2），由清洗管道的分段规则产出，
是 HMM 匹配和 OD 计算的分组单位——匹配与 OD 都不得跨 segment。
"""

from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Index, Integer, SmallInteger, String
from sqlalchemy.dialects import mysql
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base

TsMicro = DateTime().with_variant(mysql.DATETIME(fsp=6), "mysql")


class CleanTrajectory(Base):
    __tablename__ = "clean_trajectory"
    __table_args__ = (
        Index("idx_clean_vehicle_ts", "vehicle_id", "timestamp"),
        Index("idx_clean_segment", "segment_id"),
        Index("idx_clean_source_ts", "source_id", "timestamp"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    vehicle_id: Mapped[str] = mapped_column(String(20), nullable=False)
    source_id: Mapped[int] = mapped_column(ForeignKey("data_source.id"), nullable=False)
    segment_id: Mapped[str] = mapped_column(String(30), nullable=False)
    timestamp: Mapped[datetime] = mapped_column(TsMicro, nullable=False)
    longitude: Mapped[float] = mapped_column(Float, nullable=False)
    latitude: Mapped[float] = mapped_column(Float, nullable=False)
    speed: Mapped[float] = mapped_column(Float, nullable=False)
    direction: Mapped[float | None] = mapped_column(Float)
    is_stay: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0, comment="1=停留点")
    is_gap: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0, comment="1=隧道丢失后的首个点")
    flag: Mapped[str | None] = mapped_column(String(20), default="ok", comment="ok / tunnel")
