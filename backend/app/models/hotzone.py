"""出行热区表：DBSCAN 聚类结果，一簇一行。

radius_m 由 UTM 米制投影后的簇内最大距离算出（经纬度直接算米会随纬度漂）。
zone_id 形如 HZ_052，业务上稳定可读，所以给它唯一键。
"""

from datetime import datetime

from sqlalchemy import DateTime, Float, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class Hotzone(Base):
    __tablename__ = "hotzone"
    __table_args__ = (
        UniqueConstraint("zone_id", name="uk_hotzone_zone_id"),
        Index("idx_hotzone_type", "zone_type"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    zone_id: Mapped[str] = mapped_column(String(20), nullable=False)
    center_lng: Mapped[float] = mapped_column(Float, nullable=False)
    center_lat: Mapped[float] = mapped_column(Float, nullable=False)
    radius_m: Mapped[float] = mapped_column(Float, nullable=False, comment="簇半径（米）")
    point_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    zone_type: Mapped[str] = mapped_column(
        String(20), nullable=False, comment="residential/business/transit"
    )
    create_time: Mapped[datetime | None] = mapped_column(DateTime)
