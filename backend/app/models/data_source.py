"""数据源登记表。整表三行：出租车 / 网约车 / 共享单车。

大屏右侧「数据源统计」表格和「今日轨迹点」卡片都读这张表，
所以 point_count 必须由导入/生成脚本统计回填，绝不能手填——
手填过就会和 raw_trajectory 实际行数对不上账。
"""

from datetime import datetime

from sqlalchemy import DateTime, Integer, SmallInteger, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class DataSource(Base):
    __tablename__ = "data_source"
    __table_args__ = (UniqueConstraint("type", name="uk_data_source_type"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(50), nullable=False, comment="中文名：出租车/网约车/共享单车")
    type: Mapped[str] = mapped_column(String(20), nullable=False, comment="taxi/ridehailing/bikeshare")
    description: Mapped[str | None] = mapped_column(String(255), comment="采样间隔等说明")
    status: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=1, comment="1=启用 0=停用")
    vehicle_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    point_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime | None] = mapped_column(DateTime)
