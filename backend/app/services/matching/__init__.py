"""地图匹配服务包（HMM 简易版）。

对外只用这几个名字：
    MapMatcher / MatchParams        算法
    RoadNetwork / Road              路网
    load_roads / load_clean_points  数据装载
    MatchService / get_service      服务层（缓存 + 页面载荷）
"""

from .engine import MapMatcher, MatchBatch, MatchParams
from .geo import (
    Projector,
    Segment,
    SegmentGrid,
    bearing_deg,
    haversine_m,
    parse_linestring,
    project_point_to_segment,
)
from .loader import fingerprint, load_clean_points, load_roads
from .network import LEVEL_RANK, Road, RoadNetwork
from .service import Dataset, MatchService, get_service

__all__ = [
    "Dataset",
    "LEVEL_RANK",
    "MapMatcher",
    "MatchBatch",
    "MatchParams",
    "MatchService",
    "Projector",
    "Road",
    "RoadNetwork",
    "Segment",
    "SegmentGrid",
    "bearing_deg",
    "fingerprint",
    "get_service",
    "haversine_m",
    "load_clean_points",
    "load_roads",
    "parse_linestring",
    "project_point_to_segment",
]
