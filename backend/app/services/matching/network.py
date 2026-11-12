"""路网：把 road_segment.csv 的折线拆成线段数组，提供「批量点 → 候选线段」。

拆线段的原因：一条道路可能是多点折线，候选生成、投影、方向都要以「线段」为单位；
对外输出仍然按 road_id 聚合，前端一条路画一条绿线。

全部几何用 numpy 数组持有，候选生成一次算一批点对全部线段的垂距，
48 万点 × 518 段分块比较约 4s；逐点 Python 循环要 20s 以上，差一个量级。
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .geo import Projector, bearing_deg, project_arrays, project_arrays_sq

# 道路等级权重：同距时优先高等级道路（高速 > 主干 > 次干 > 支路）
LEVEL_RANK = {"highway": 0, "main": 1, "secondary": 2, "branch": 3}


@dataclass
class Road:
    """一条道路（可能是多点折线）。"""

    road_id: str
    road_name: str
    level: str
    lnglats: list[tuple[float, float]]


class RoadNetwork:
    """路网的 numpy 视图，构造一次反复用。"""

    def __init__(self, roads: list[Road]) -> None:
        if not roads:
            raise ValueError("路网为空，无法进行地图匹配")

        self.roads = roads
        self.road_ids = [r.road_id for r in roads]
        self.road_index = {rid: i for i, rid in enumerate(self.road_ids)}

        lngs = [p[0] for r in roads for p in r.lnglats]
        lats = [p[1] for r in roads for p in r.lnglats]
        self.projector = Projector(lat0=float(np.mean(lats)), lng0=float(np.mean(lngs)))
        self.bounds = (min(lngs), min(lats), max(lngs), max(lats))

        seg_road: list[int] = []
        x1: list[float] = []
        y1: list[float] = []
        x2: list[float] = []
        y2: list[float] = []
        lng1: list[float] = []
        lat1: list[float] = []
        lng2: list[float] = []
        lat2: list[float] = []
        for ri, road in enumerate(roads):
            for (a_lng, a_lat), (b_lng, b_lat) in zip(road.lnglats, road.lnglats[1:]):
                ax, ay = self.projector.to_xy(a_lng, a_lat)
                bx, by = self.projector.to_xy(b_lng, b_lat)
                if float(np.hypot(bx - ax, by - ay)) < 1e-6:
                    continue  # 重复点不产生线段
                seg_road.append(ri)
                x1.append(ax); y1.append(ay); x2.append(bx); y2.append(by)
                lng1.append(a_lng); lat1.append(a_lat); lng2.append(b_lng); lat2.append(b_lat)

        self.seg_road = np.asarray(seg_road, dtype=np.int32)
        self.x1 = np.asarray(x1); self.y1 = np.asarray(y1)
        self.x2 = np.asarray(x2); self.y2 = np.asarray(y2)
        self.seg_lng1 = np.asarray(lng1); self.seg_lat1 = np.asarray(lat1)
        self.seg_lng2 = np.asarray(lng2); self.seg_lat2 = np.asarray(lat2)
        self.length_m = np.hypot(self.x2 - self.x1, self.y2 - self.y1)
        self.bearing = bearing_deg(self.x1, self.y1, self.x2, self.y2)
        self.level_rank = np.asarray(
            [LEVEL_RANK.get(r.level, 4) for r in roads], dtype=np.float32
        )
        self._grid_cell = 250.0
        self._grid = self._build_grid()
        self._cell_cache: dict[tuple[int, int, int], np.ndarray] = {}

    def _build_grid(self) -> dict[tuple[int, int], np.ndarray]:
        """粗网格：250m 一格，登记与格子相交的线段。候选生成时先粗筛再精算。"""
        cell = self._grid_cell
        bucket: dict[tuple[int, int], list[int]] = {}
        for i in range(self.n_segments):
            xmin = min(self.x1[i], self.x2[i]); xmax = max(self.x1[i], self.x2[i])
            ymin = min(self.y1[i], self.y2[i]); ymax = max(self.y1[i], self.y2[i])
            for ix in range(int(np.floor(xmin / cell)), int(np.floor(xmax / cell)) + 1):
                for iy in range(int(np.floor(ymin / cell)), int(np.floor(ymax / cell)) + 1):
                    bucket.setdefault((ix, iy), []).append(i)
        return {k: np.asarray(v, dtype=np.int32) for k, v in bucket.items()}

    # ------------------------------------------------------------ 基本属性
    @property
    def n_segments(self) -> int:
        return int(self.seg_road.size)

    @property
    def n_roads(self) -> int:
        return len(self.roads)

    def road(self, road_idx: int) -> Road:
        return self.roads[int(road_idx)]

    def geometry(self, road_idx: int) -> list[list[float]]:
        """前端要的 [[lng, lat], ...]。"""
        return [[float(lng), float(lat)] for lng, lat in self.roads[int(road_idx)].lnglats]

    # ------------------------------------------------------------ 候选生成
    def candidates(self, lngs, lats, radius_m: float, k: int, chunk: int | None = None):
        """批量取每点最近的至多 k 个候选线段。

        按 250m 网格把点分桶，每桶只和「本格 + 邻格」里的几十条线段比距离；
        早期版本按输入顺序切成 4096 一片算全城 518 段，48 万点要 33s，现在约 3s。
        返回值 (seg_idx, dist, snap_lng, snap_lat)，形状均为 (N, k)：
          seg_idx = -1 表示该点半径内没有候选（离开路网 / 落在无路区）
          dist 无候选时为 inf
        """
        lngs = np.asarray(lngs, dtype=float)
        lats = np.asarray(lats, dtype=float)
        n = lngs.size
        out_idx = np.full((n, k), -1, dtype=np.int32)
        out_dist = np.full((n, k), np.inf, dtype=np.float32)
        if n == 0:
            return out_idx, out_dist, lngs, lats

        px_all, py_all = self.projector.to_xy(lngs, lats)
        cell = self._grid_cell
        ci = np.floor(px_all / cell).astype(np.int64)
        cj = np.floor(py_all / cell).astype(np.int64)
        key = (ci + 1_000_000) * 4_000_000 + (cj + 1_000_000)
        order = np.argsort(key, kind="stable")
        sorted_key = key[order]
        cuts = np.flatnonzero(np.diff(sorted_key)) + 1
        groups = np.split(order, cuts)

        rings = int(np.ceil(radius_m / cell)) + 1
        for grp in groups:
            i0 = int(ci[grp[0]])
            j0 = int(cj[grp[0]])
            cols = self._cell_segments(i0, j0, rings)
            if cols.size == 0:
                continue
            px = px_all[grp][:, None]
            py = py_all[grp][:, None]
            ax, ay = self.x1[cols][None, :], self.y1[cols][None, :]
            bx, by = self.x2[cols][None, :], self.y2[cols][None, :]
            # 先算平方垂距（不做 sqrt），排完序只对选中的 k 个开方
            d2, _, _ = project_arrays_sq(px, py, ax, ay, bx, by)
            kk = min(k, d2.shape[1])
            if kk < d2.shape[1]:
                part = np.argpartition(d2, kk - 1, axis=1)[:, :kk]
            else:
                part = np.tile(np.arange(d2.shape[1]), (d2.shape[0], 1))
            dd = np.sqrt(np.take_along_axis(d2, part, axis=1))
            order_sel = np.argsort(dd, axis=1)
            part = np.take_along_axis(part, order_sel, axis=1)
            dd = np.take_along_axis(dd, order_sel, axis=1)
            part = cols[part]                       # 列号映射回全局线段下标
            invalid = dd > radius_m
            out_idx[grp, :kk] = np.where(invalid, -1, part)
            out_dist[grp, :kk] = np.where(invalid, np.inf, dd).astype(np.float32)

        snap_lng, snap_lat = self._snap(lngs, lats, out_idx)
        return out_idx, out_dist, snap_lng, snap_lat

    def _cell_segments(self, i0: int, j0: int, rings: int) -> np.ndarray:
        """(i0, j0) 为中心、rings 圈内的线段下标并集，按格缓存。"""
        key = (i0, j0, rings)
        cached = self._cell_cache.get(key)
        if cached is not None:
            return cached
        parts = [self._grid[(i0 + di, j0 + dj)]
                 for di in range(-rings, rings + 1) for dj in range(-rings, rings + 1)
                 if (i0 + di, j0 + dj) in self._grid]
        if not parts:
            out = np.empty(0, dtype=np.int32)
        elif len(parts) == 1:
            out = parts[0]
        else:
            out = np.unique(np.concatenate(parts))
        self._cell_cache[key] = out
        return out

    def _snap(self, lngs, lats, seg_idx):
        """把点投影到指定线段上，返回吸附坐标。seg_idx=-1 处坐标原样返回。"""
        px, py = self.projector.to_xy(lngs, lats)
        safe = np.where(seg_idx < 0, 0, seg_idx)
        ax, ay = self.x1[safe], self.y1[safe]
        bx, by = self.x2[safe], self.y2[safe]
        _, cx, cy = project_arrays(px[:, None], py[:, None], ax, ay, bx, by)
        lng, lat = self.projector.to_lnglat(cx, cy)
        # 无候选的点不做位移，保留原始坐标（前端画「未匹配点」就用它）
        keep_lng = np.asarray(lngs, dtype=float)[:, None]
        keep_lat = np.asarray(lats, dtype=float)[:, None]
        lng = np.where(seg_idx < 0, keep_lng, lng)
        lat = np.where(seg_idx < 0, keep_lat, lat)
        return lng, lat

    def snap_to(self, lngs, lats, seg_idx):
        """公开投影接口：把一组点投影到各自的线段上。"""
        return self._snap(np.asarray(lngs, dtype=float), np.asarray(lats, dtype=float), seg_idx)
