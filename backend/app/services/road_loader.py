"""路网加载 + 候选路段查询。

两件事：
1. 把 road_segment 表读进内存，摊平成「直线段」数组，
   用 0.005° 网格索引（约 550m 一格），支撑 HMM 的候选路段粗筛。
2. 提供米制投影：给定一批点，返回最近路段、距离、投影点、沿线里程 s。

坐标系约定：本网几何与 data/raw 的轨迹点同坐标系（实测点到最近路段中位距离 0.00 m），
这里不做任何坐标转换——如果两边坐标系不一致，
距离会中位偏到几百米，匹配率直接归零。

米制换算用「以路网中心纬度为基准的等距柱面投影」：
经度 1° ≈ 111320·cos(lat0) 米，纬度 1° ≈ 110574 米。
本网纬度跨度约 0.15°，用单一 lat0 带来的误差约 0.2%，
相对 σ=20m 的观测模型可以忽略，换来的是全向量化。
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from shapely import wkt as shapely_wkt

from app.core.database import SessionLocal

# 网格边长（度）。0.005° ≈ 550m，比搜索半径 50m 大一个量级，
# 3×3 邻域足以覆盖任意点周围 R 内的所有路段。
CELL_DEG = 0.005


@dataclass(slots=True)
class Road:
    road_id: str
    road_name: str
    level: str
    length: float
    coords: tuple[tuple[float, float], ...]


def _parse_wkt(text: str) -> tuple[tuple[float, float], ...]:
    line = shapely_wkt.loads(text)
    return tuple(tuple(map(float, xy)) for xy in line.coords)


def _bounds(coords: np.ndarray) -> tuple[float, float, float, float]:
    return coords[:, 0].min(), coords[:, 1].min(), coords[:, 0].max(), coords[:, 1].max()


class RoadNetwork:
    """内存路网。构造后只读，可被多个请求/任务共享。"""

    def __init__(self, roads: list[Road]):
        if not roads:
            raise ValueError("路网为空：road_segment 表没有数据，先跑 python backend/scripts/import_raw_data.py")
        self.roads = roads
        self.road_id_of = np.empty(len(roads), dtype=object)
        self.road_id_of[:] = [r.road_id for r in roads]
        self.index: dict[str, int] = {r.road_id: i for i, r in enumerate(roads)}
        self.road_names = [r.road_name for r in roads]
        self.road_levels = [r.level for r in roads]
        self.road_lengths = np.array([r.length for r in roads], dtype=float)

        # 米制基准：路网质心纬度
        all_pts = np.vstack([np.asarray(r.coords, dtype=float) for r in roads])
        self.lat0 = float(all_pts[:, 1].mean())
        self.m_deg_lng = 111320.0 * math.cos(math.radians(self.lat0))
        self.m_deg_lat = 110574.0

        # 路段包围盒（粗筛第一步，对应 road_segment 的 bounds_* 列）
        b = np.array([_bounds(np.asarray(r.coords, dtype=float)) for r in roads], dtype=float)
        self.min_lng, self.min_lat = b[:, 0], b[:, 1]
        self.max_lng, self.max_lat = b[:, 2], b[:, 3]

        self._build_segments()
        self._build_grid()

    # ------------------------------------------------------------------
    # 摊平成直线段
    # ------------------------------------------------------------------
    def _build_segments(self) -> None:
        road_idx, ax, ay, bx, by = [], [], [], [], []
        for i, r in enumerate(self.roads):
            c = np.asarray(r.coords, dtype=float)
            for j in range(len(c) - 1):
                road_idx.append(i)
                ax.append(c[j, 0]); ay.append(c[j, 1])
                bx.append(c[j + 1, 0]); by.append(c[j + 1, 1])
        self.seg_road = np.asarray(road_idx, dtype=np.int64)
        self.seg_ax = np.asarray(ax, dtype=float)
        self.seg_ay = np.asarray(ay, dtype=float)
        self.seg_bx = np.asarray(bx, dtype=float)
        self.seg_by = np.asarray(by, dtype=float)

        dx = (self.seg_bx - self.seg_ax) * self.m_deg_lng
        dy = (self.seg_by - self.seg_ay) * self.m_deg_lat
        self.seg_len = np.hypot(dx, dy)
        # 沿线方位角（北为 0，顺时针），转移模型和方向校验都要用
        self.seg_bearing = np.degrees(np.arctan2(dx, dy)) % 360.0

        # 每个路段顶点累计里程，用于把投影点换算成「沿路 s」
        self.seg_offset = np.zeros(len(self.seg_road))
        acc: dict[int, float] = {}
        for k in range(len(self.seg_road)):
            ri = int(self.seg_road[k])
            self.seg_offset[k] = acc.get(ri, 0.0)
            acc[ri] = self.seg_offset[k] + self.seg_len[k]

        self.seg_min_lng, self.seg_min_lat = np.minimum(self.seg_ax, self.seg_bx), np.minimum(
            self.seg_ay, self.seg_by
        )
        self.seg_max_lng, self.seg_max_lat = np.maximum(self.seg_ax, self.seg_bx), np.maximum(
            self.seg_ay, self.seg_by
        )

    def _build_grid(self) -> None:
        grid: dict[tuple[int, int], list[int]] = {}
        pad = CELL_DEG
        for k in range(len(self.seg_road)):
            x0 = int((self.seg_min_lng[k] - pad) / CELL_DEG)
            x1 = int((self.seg_max_lng[k] + pad) / CELL_DEG)
            y0 = int((self.seg_min_lat[k] - pad) / CELL_DEG)
            y1 = int((self.seg_max_lat[k] + pad) / CELL_DEG)
            for cx in range(x0, x1 + 1):
                for cy in range(y0, y1 + 1):
                    grid.setdefault((cx, cy), []).append(k)
        self.grid = {key: np.asarray(v, dtype=np.int64) for key, v in grid.items()}

    # ------------------------------------------------------------------
    # 查询接口
    # ------------------------------------------------------------------
    def roads_in_bbox(self, lng0: float, lat0: float, lng1: float, lat1: float) -> np.ndarray:
        """bounds 粗筛：返回与给定矩形相交的路段下标。"""
        m = (
            (self.max_lng >= lng0)
            & (self.min_lng <= lng1)
            & (self.max_lat >= lat0)
            & (self.min_lat <= lat1)
        )
        return np.nonzero(m)[0]

    def _segments_near(self, lng: float, lat: float) -> np.ndarray:
        cx, cy = int(lng / CELL_DEG), int(lat / CELL_DEG)
        out = []
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                seg = self.grid.get((cx + dx, cy + dy))
                if seg is not None:
                    out.append(seg)
        if not out:
            return np.empty(0, dtype=np.int64)
        return np.unique(np.concatenate(out))

    def _project(self, lng: np.ndarray, lat: np.ndarray, segs: np.ndarray):
        """点 → 候选直线段的批量投影。输入 N 点、K 段，输出 N×K。"""
        x = (lng[:, None] - self.seg_ax[segs][None, :]) * self.m_deg_lng
        y = (lat[:, None] - self.seg_ay[segs][None, :]) * self.m_deg_lat
        dx = (self.seg_bx[segs] - self.seg_ax[segs]) * self.m_deg_lng
        dy = (self.seg_by[segs] - self.seg_ay[segs]) * self.m_deg_lat
        ll = dx * dx + dy * dy
        ll = np.where(ll > 0, ll, 1.0)
        t = np.clip((x * dx + y * dy) / ll, 0.0, 1.0)
        dist = np.hypot(x - t * dx, y - t * dy)
        return t, dist

    def candidates(self, lng: float, lat: float, radius_m: float) -> list[tuple[int, float, float, float, float]]:
        """单点候选路段：返回 [(路段下标, 距离米, 投影经度, 投影纬度, 沿路s), ...]。"""
        res = self.snap_many(
            np.array([lng], dtype=float), np.array([lat], dtype=float), radius_m
        )
        out = []
        for k in range(len(res["road_idx"])):
            if res["road_idx"][k] < 0:
                continue
            out.append(
                (
                    int(res["road_idx"][k]),
                    float(res["dist_m"][k]),
                    float(res["proj_lng"][k]),
                    float(res["proj_lat"][k]),
                    float(res["s_off"][k]),
                )
            )
        return out

    def snap_many(
        self, lng: np.ndarray, lat: np.ndarray, radius_m: float | None = None
    ) -> dict[str, np.ndarray]:
        """批量最近路段吸附（走向量化的关键，逐点 Python 循环不可接受）。

        逐点最近段；radius_m 为 None 时不设上限（用于自由流速度这类
        需要「所有点都有归属」的场合）。找不到时 road_idx = -1。
        """
        n = len(lng)
        best_road = np.full(n, -1, dtype=np.int64)
        best_seg = np.full(n, -1, dtype=np.int64)
        best_dist = np.full(n, np.inf, dtype=float)
        best_t = np.zeros(n, dtype=float)

        r = np.inf if radius_m is None else float(radius_m)
        keys = np.stack(
            [(lng / CELL_DEG).astype(np.int64), (lat / CELL_DEG).astype(np.int64)], axis=1
        )
        # 按网格分桶：同一片区域内的点共用一份候选段列表，一次广播算完
        order = np.lexsort((keys[:, 1], keys[:, 0]))
        cell_changed = np.any(keys[order] != keys[order - 1], axis=1)
        cell_changed[0] = True
        for start in np.nonzero(cell_changed)[0]:
            end = len(order)
            nxt = np.nonzero(cell_changed[start + 1 :])[0]
            if len(nxt):
                end = start + 1 + int(nxt[0])
            idx = order[start:end]
            cx, cy = int(keys[idx[0], 0]), int(keys[idx[0], 1])
            cand = []
            for dxi in (-1, 0, 1):
                for dyi in (-1, 0, 1):
                    s = self.grid.get((cx + dxi, cy + dyi))
                    if s is not None:
                        cand.append(s)
            if not cand:
                continue
            segs = np.unique(np.concatenate(cand))
            t, dist = self._project(lng[idx], lat[idx], segs)
            j = dist.argmin(axis=1)
            dmin = dist[np.arange(len(idx)), j]
            keep = dmin < best_dist[idx]
            sel = idx[keep]
            best_dist[sel] = dmin[keep]
            best_seg[sel] = segs[j[keep]]
            best_t[sel] = t[np.arange(len(idx)), j][keep]
            best_road[sel] = self.seg_road[best_seg[sel]]

        over = best_dist > r
        best_road[over] = -1
        best_seg[over] = -1

        valid = best_seg >= 0
        proj_lng = np.where(valid, lng, np.nan)
        proj_lat = np.where(valid, lat, np.nan)
        s_off = np.zeros(n, dtype=float)
        bearing = np.zeros(n, dtype=float)
        if valid.any():
            k = best_seg[valid]
            t = best_t[valid]
            proj_lng[valid] = self.seg_ax[k] + t * (self.seg_bx[k] - self.seg_ax[k])
            proj_lat[valid] = self.seg_ay[k] + t * (self.seg_by[k] - self.seg_ay[k])
            s_off[valid] = self.seg_offset[k] + t * self.seg_len[k]
            bearing[valid] = self.seg_bearing[k]

        return {
            "road_idx": best_road,
            "seg_idx": best_seg,
            "dist_m": best_dist,
            "proj_lng": proj_lng,
            "proj_lat": proj_lat,
            "s_off": s_off,
            "bearing": bearing,
        }

    def road_geom(self, road_idx: int) -> tuple[tuple[float, float], ...]:
        return self.roads[road_idx].coords

    def bbox(self) -> tuple[float, float, float, float]:
        return (
            float(self.min_lng.min()),
            float(self.min_lat.min()),
            float(self.max_lng.max()),
            float(self.max_lat.max()),
        )


# ----------------------------------------------------------------------
# 装载
# ----------------------------------------------------------------------
def load_from_db(db=None) -> RoadNetwork:
    from app.models.road_segment import RoadSegment

    own = db is None
    if own:
        db = SessionLocal()
    try:
        rows = db.query(RoadSegment).order_by(RoadSegment.id).all()
        roads = [
            Road(
                road_id=r.road_id,
                road_name=r.road_name,
                level=r.level,
                length=float(r.length),
                coords=_parse_wkt(r.geometry),
            )
            for r in rows
        ]
    finally:
        if own:
            db.close()
    return RoadNetwork(roads)


_NETWORK: RoadNetwork | None = None


def get_network(refresh: bool = False) -> RoadNetwork:
    """进程级单例。路网是静态底图，加载一次（约 0.2s）后全程复用。

    唯一来源是 road_segment 表，数据由 scripts/import_raw_data.py 从 data/raw 导入。
    """
    global _NETWORK
    if _NETWORK is None or refresh:
        _NETWORK = load_from_db()
    return _NETWORK
