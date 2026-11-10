"""ORM 模型层：八张核心表，一张表一个模块文件。

init_database() 只 import 本模块，靠这里的副作用把八张表全部注册到
Base.metadata，create_all() 才会一张不落。新增表记得在这里补一行。

data_source / raw_trajectory / clean_trajectory / road_segment /
match_result / road_metrics / od_matrix / hotzone
"""

from app.models.clean_trajectory import CleanTrajectory
from app.models.data_source import DataSource
from app.models.hotzone import Hotzone
from app.models.match_result import MatchResult
from app.models.od_matrix import OdMatrix
from app.models.raw_trajectory import RawTrajectory
from app.models.road_metrics import RoadMetrics
from app.models.road_segment import RoadSegment

__all__ = [
    "CleanTrajectory",
    "DataSource",
    "Hotzone",
    "MatchResult",
    "OdMatrix",
    "RawTrajectory",
    "RoadMetrics",
    "RoadSegment",
]
