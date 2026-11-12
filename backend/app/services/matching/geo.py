"""几何基础：WKT 解析、等距圆柱投影、点到线段垂距、网格索引。

纯标准库 + numpy 实现，不依赖 shapely / geopandas，队友只需要 requirements 里的
numpy/pandas 就能跑。所有距离都在米制平面里算，经纬度只作为输入输出。
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass

import numpy as np

_COORD = re.compile(r"(-?\d+(?:\.\d+)?)\s+(-?\d+(?:\.\d+)?)")

EARTH_R = 6_371_000.0


# ---------------------------------------------------------------- WKT / 角度
def parse_linestring(wkt: str) -> list[tuple[float, float]]:
    """解析 WKT LINESTRING 为 [(lng, lat), ...]。"""
    if not wkt or "LINESTRING" not in wkt.upper():
        return []
    body = wkt[wkt.find("(") + 1 : wkt.rfind(")")]
    return [(float(a), float(b)) for a, b in _COORD.findall(body)]


def bearing_deg(x1, y1, x2, y2):
    """平面坐标下的方位角，0°=正北，顺时针 0~360。支持标量或 numpy 数组。"""
    return (np.degrees(np.arctan2(np.asarray(x2) - x1, np.asarray(y2) - y1)) + 360.0) % 360.0


def angle_diff(a, b):
    """两个方位角的最小夹角（0~180）。"""
    d = np.abs((np.asarray(a) - b) % 360.0)
    return np.where(d <= 180.0, d, 360.0 - d)


class Projector:
    """以研究区中心为原点的等距圆柱投影，米制平面坐标。

    城市尺度（<50km）误差可忽略，远优于直接用经纬度算距离。
    """

    def __init__(self, lat0: float, lng0: float) -> None:
        self.lat0 = float(lat0)
        self.lng0 = float(lng0)
        self.kx = 111_320.0 * math.cos(math.radians(self.lat0))
        self.ky = 110_540.0

    def to_xy(self, lng, lat):
        return ((np.asarray(lng, dtype=float) - self.lng0) * self.kx,
                (np.asarray(lat, dtype=float) - self.lat0) * self.ky)

    def to_lnglat(self, x, y):
        return (self.lng0 + np.asarray(x, dtype=float) / self.kx,
                self.lat0 + np.asarray(y, dtype=float) / self.ky)


# ---------------------------------------------------------------- 距离计算
@dataclass(frozen=True)
class Segment:
    """可匹配的最短路网单元：一条道路的一个线段。"""

    seg_idx: int
    road_id: str
    road_name: str
    level: str
    x1: float
    y1: float
    x2: float
    y2: float
    bearing: float
    length_m: float
    lng1: float
    lat1: float
    lng2: float
    lat2: float

    @property
    def bbox(self) -> tuple[float, float, float, float]:
        return (min(self.x1, self.x2), min(self.y1, self.y2),
                max(self.x1, self.x2), max(self.y1, self.y2))


def project_arrays(px, py, ax, ay, bx, by):
    """向量化「点 → 线段」投影，参数按 numpy 广播规则对齐。

    返回 (垂距, 投影点x, 投影点y)，投影点被夹在线段范围内。
    典型用法：px/py 形状 (N,1)，ax..by 形状 (1,S) → 结果形状 (N,S)。
    """
    dx = bx - ax
    dy = by - ay
    l2 = dx * dx + dy * dy
    # 退化线段（长度 0）保护：l2 用 1 代替，后面把距离改成到端点的距离
    safe_l2 = np.where(l2 > 0, l2, 1.0)
    t = ((px - ax) * dx + (py - ay) * dy) / safe_l2
    t = np.clip(t, 0.0, 1.0)
    cx = ax + t * dx
    cy = ay + t * dy
    return np.sqrt((px - cx) ** 2 + (py - cy) ** 2), cx, cy


def project_arrays_sq(px, py, ax, ay, bx, by):
    """只要「平方垂距 + 投影点」的版本：候选筛选只排序不需要开方，省一次全量 sqrt。"""
    dx = bx - ax
    dy = by - ay
    l2 = dx * dx + dy * dy
    safe_l2 = np.where(l2 > 0, l2, 1.0)
    t = np.clip(((px - ax) * dx + (py - ay) * dy) / safe_l2, 0.0, 1.0)
    cx = ax + t * dx
    cy = ay + t * dy
    return (px - cx) ** 2 + (py - cy) ** 2, cx, cy


def project_point_to_segment(px: float, py: float, s: Segment) -> tuple[float, float, float]:
    """单点版本，保持旧接口（单元测试 / 调试用）。"""
    d, cx, cy = project_arrays(px, py, s.x1, s.y1, s.x2, s.y2)
    return float(d), float(cx), float(cy)


class SegmentGrid:
    """均匀网格索引：把「点 → 候选线段」的暴力比较降到局部。

    HMM 引擎本身走 numpy 全量比较（518 段 × 48 万点只要几秒），
    网格索引留给需要单点查询的调用方（如核查脚本、接口里的按点回查）。
    """

    def __init__(self, segments: list[Segment], cell_m: float = 500.0) -> None:
        self.cell = cell_m
        self.cells: dict[tuple[int, int], list[int]] = {}
        self.segments = segments
        for s in segments:
            xmin, ymin, xmax, ymax = s.bbox
            for ix in range(int(math.floor(xmin / cell_m)), int(math.floor(xmax / cell_m)) + 1):
                for iy in range(int(math.floor(ymin / cell_m)), int(math.floor(ymax / cell_m)) + 1):
                    self.cells.setdefault((ix, iy), []).append(s.seg_idx)

    def candidates(self, x: float, y: float, radius_m: float) -> list[Segment]:
        r = max(radius_m, 1.0)
        seen: set[int] = set()
        out: list[Segment] = []
        for ix in range(int(math.floor((x - r) / self.cell)), int(math.floor((x + r) / self.cell)) + 1):
            for iy in range(int(math.floor((y - r) / self.cell)), int(math.floor((y + r) / self.cell)) + 1):
                for idx in self.cells.get((ix, iy), ()):
                    if idx not in seen:
                        seen.add(idx)
                        out.append(self.segments[idx])
        return out


def haversine_m(lng1, lat1, lng2, lat2):
    """球面距离（米）。支持标量或数组，用于 road_segment.length 缺失时补算。"""
    p = math.pi / 180.0
    dlat = (np.asarray(lat2, dtype=float) - lat1) * p
    dlng = (np.asarray(lng2, dtype=float) - lng1) * p
    a = (np.sin(dlat / 2) ** 2
         + np.cos(np.asarray(lat1, dtype=float) * p) * np.cos(np.asarray(lat2, dtype=float) * p)
         * np.sin(dlng / 2) ** 2)
    return 2 * EARTH_R * np.arcsin(np.sqrt(a))
