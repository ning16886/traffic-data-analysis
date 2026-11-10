"""路网路段表。几何用 WKT 字符串存，与 data/raw 轨迹点同坐标系（原始数据 76 条）。

bounds_* 四个字段是冗余的包围盒，专为 HMM 候选路段的第一步粗筛存在：
先用 bounds 过滤掉 99% 的路段，再用 shapely 精算距离。
"""

from sqlalchemy import Float, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class RoadSegment(Base):
    __tablename__ = "road_segment"
    __table_args__ = (
        UniqueConstraint("road_id", name="uk_road_segment_road_id"),
        Index("idx_road_segment_level", "level"),
        Index("idx_road_segment_bounds_lng", "bounds_min_lng", "bounds_max_lng"),
        Index("idx_road_segment_bounds_lat", "bounds_min_lat", "bounds_max_lat"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    road_id: Mapped[str] = mapped_column(String(10), nullable=False, comment="业务主键，形如 R00001")
    road_name: Mapped[str] = mapped_column(String(50), nullable=False)
    level: Mapped[str] = mapped_column(String(20), nullable=False, comment="highway/main/secondary/branch")
    length: Mapped[float] = mapped_column(Float, nullable=False, default=0, comment="米")
    geometry: Mapped[str] = mapped_column(Text, nullable=False, comment="WKT LINESTRING，WGS-84")
    bounds_min_lng: Mapped[float] = mapped_column(Float, nullable=False)
    bounds_min_lat: Mapped[float] = mapped_column(Float, nullable=False)
    bounds_max_lng: Mapped[float] = mapped_column(Float, nullable=False)
    bounds_max_lat: Mapped[float] = mapped_column(Float, nullable=False)
