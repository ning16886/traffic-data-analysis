"""HMM 地图匹配结果表：一个清洗点对应一条记录。

match_longitude/latitude 是点投影到所选路段折线上的坐标（前端画「匹配后轨迹」用的就是它），
is_matched=0 表示维特比判定该点无路可匹配（离开路网或搜索半径内无候选）。
"""

from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Index, Integer, SmallInteger, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class MatchResult(Base):
    __tablename__ = "match_result"
    __table_args__ = (
        Index("idx_match_vehicle", "vehicle_id", "id"),
        Index("idx_match_road", "road_id"),
        Index("idx_match_clean", "clean_trajectory_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    vehicle_id: Mapped[str] = mapped_column(String(20), nullable=False)
    road_id: Mapped[str | None] = mapped_column(ForeignKey("road_segment.road_id"), comment="未匹配时为 NULL")
    raw_trajectory_id: Mapped[int | None] = mapped_column(Integer)
    clean_trajectory_id: Mapped[int] = mapped_column(Integer, nullable=False, comment="重匹配按此覆盖")
    match_longitude: Mapped[float | None] = mapped_column(Float)
    match_latitude: Mapped[float | None] = mapped_column(Float)
    match_probability: Mapped[float | None] = mapped_column(Float, comment="维特比路径概率")
    is_matched: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)
    create_time: Mapped[datetime | None] = mapped_column(DateTime)
