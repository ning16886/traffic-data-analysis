"""原始轨迹点表（data/raw 实测 1140 行）。

与路网同坐标系，入库不做坐标转换（实测点到最近路段中位距离 0.00 m）。
脏数据（漂移点 speed>120）允许存在本表，由清洗管道负责剔除——
「原始」的含义就是不做任何修改。
"""

from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Index, Integer, SmallInteger, String
from sqlalchemy.dialects import mysql
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base

# 采样间隔 3s，必须保微秒，否则同一秒内的点会失去先后顺序
TsMicro = DateTime().with_variant(mysql.DATETIME(fsp=6), "mysql")


class RawTrajectory(Base):
    __tablename__ = "raw_trajectory"
    __table_args__ = (
        # 需求文档点名的联合索引：按车 + 时间段查轨迹必须走它
        Index("idx_raw_vehicle_ts", "vehicle_id", "timestamp"),
        Index("idx_raw_source_ts", "source_id", "timestamp"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    vehicle_id: Mapped[str] = mapped_column(String(20), nullable=False, comment="ta_000 / ri_012 / bi_003")
    source_id: Mapped[int] = mapped_column(ForeignKey("data_source.id"), nullable=False)
    timestamp: Mapped[datetime] = mapped_column(TsMicro, nullable=False)
    longitude: Mapped[float] = mapped_column(Float, nullable=False)
    latitude: Mapped[float] = mapped_column(Float, nullable=False)
    speed: Mapped[float] = mapped_column(Float, nullable=False, comment="km/h")
    direction: Mapped[float | None] = mapped_column(Float, comment="方位角 0~360")
    passenger: Mapped[int | None] = mapped_column(SmallInteger, default=0)
