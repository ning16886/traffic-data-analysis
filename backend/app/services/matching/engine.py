"""HMM 地图匹配引擎（需求第 44-49 行的简易版 HMM）。

算法链路：
1. 候选生成：每点取搜索半径 R 内最近的至多 K 个线段（bounds/网格粗筛 + 向量化垂距精筛）；
2. 观测概率：高斯 p_obs = exp(-d² / 2σ²)，σ = 30m（需求第 46 行）；
3. 转移概率：指数 p_trans = exp(-|Δ路径 - Δ直线| / β)，β = 30m（需求第 47 行），
   相邻两点候选的间距与 GPS 间距越接近，转移概率越高——这是维特比能压住
   「路口挑错路 / 平行路跳线」的关键；
4. 对数空间维特比解码：在整条行程上取全局最优候选序列，而不是逐点贪心；
5. 匹配判定：路径选中的候选垂距 ≤ threshold 才算匹配，坐标取投影点（shapely 投影的等价实现）。

方向（direction 字段）不进状态转移，只作为同距候选的打破平局项：
地图匹配的主证据是距离与连续性，方向字段本身有 3~5% 异常值，给太重的权重会反噬。

坐标全部投影到米制平面计算，输出仍为 WGS84 经纬度。
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .network import Road, RoadNetwork
from .geo import angle_diff

NEG = -1e9


@dataclass
class MatchBatch:
    """一条轨迹（或多条行程）的匹配结果，全部为 numpy 数组，长度 = 输入点数。"""

    seg_idx: np.ndarray      # (N,) int32 命中线段下标，-1 = 未匹配
    road_idx: np.ndarray     # (N,) int32 命中道路下标，-1 = 未匹配
    distance_m: np.ndarray   # (N,) float32 垂距，未匹配为 inf
    probability: np.ndarray  # (N,) float32 置信度 0~1
    matched: np.ndarray      # (N,) bool
    snap_lng: np.ndarray     # (N,) float64
    snap_lat: np.ndarray     # (N,) float64
    candidates: np.ndarray   # (N,) int8 候选数

    def stats(self) -> dict:
        total = int(self.matched.size)
        matched = int(self.matched.sum())
        roads = {int(r) for r in self.road_idx[self.matched]}
        return {
            "total": total,
            "matched": matched,
            "unmatched": total - matched,
            "roadCount": len(roads),
            "matchRate": round(matched / total, 4) if total else 0.0,
            "avgDistanceM": round(float(np.mean(self.distance_m[self.matched])), 2)
            if matched else None,
        }


@dataclass
class MatchParams:
    """可调参数。默认值对应需求文档，改了要在 docs 里同步。"""

    threshold_m: float = 100.0
    sigma_m: float = 30.0
    beta_m: float = 30.0
    max_candidates: int = 5
    candidate_radius_m: float = 200.0
    direction_weight: float = 0.4
    level_weight: float = 0.15
    extra: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "threshold_m": self.threshold_m,
            "sigma_m": self.sigma_m,
            "beta_m": self.beta_m,
            "max_candidates": self.max_candidates,
            "candidate_radius_m": self.candidate_radius_m,
            "direction_weight": self.direction_weight,
            "level_weight": self.level_weight,
        }


class MapMatcher:
    """一次构造（加载路网），反复匹配多条轨迹。"""

    def __init__(self, roads: list[Road] | RoadNetwork, params: MatchParams | None = None) -> None:
        self.network = roads if isinstance(roads, RoadNetwork) else RoadNetwork(roads)
        self.params = params or MatchParams()

    # ------------------------------------------------------------ 对外入口
    def match(
        self,
        lngs,
        lats,
        directions=None,
        trip_ids=None,
        threshold_m: float | None = None,
    ) -> MatchBatch:
        """匹配一条（或多条按 trip_ids 分开的）轨迹。

        trip_ids 变化处断开维特比链：不同行程之间没有转移关系，
        跨行程强行连起来会让「上一趟的末端」把「下一趟的首点」拽到错误的路上。
        """
        p = self.params
        th = p.threshold_m if threshold_m is None else float(threshold_m)
        lngs = np.asarray(lngs, dtype=float)
        lats = np.asarray(lats, dtype=float)
        n = lngs.size
        if n == 0:
            return _empty()

        radius = max(p.candidate_radius_m, th * 1.5)
        cand_idx, cand_dist, cand_lng, cand_lat = self.network.candidates(
            lngs, lats, radius, p.max_candidates
        )
        cand_align = self._align(directions, cand_idx)

        emit = np.where(
            cand_idx >= 0,
            -0.5 * (cand_dist / p.sigma_m) ** 2
            + p.direction_weight * cand_align
            + p.level_weight * -self.network.level_rank[
                np.where(cand_idx >= 0, self.network.seg_road[np.maximum(cand_idx, 0)], 0)
            ],
            NEG,
        ).astype(np.float64)

        px, py = self.network.projector.to_xy(lngs, lats)
        cx, cy = self.network.projector.to_xy(cand_lng, cand_lat)

        choice = np.full(n, -1, dtype=np.int32)
        for batch in _batches(list(_trips(trip_ids, n))):
            self._viterbi_batch(batch, emit, px, py, cx, cy, cand_idx, choice)

        rows = np.arange(n)
        hit = choice >= 0
        seg = np.where(hit, cand_idx[rows, np.maximum(choice, 0)], -1).astype(np.int32)
        dist = np.where(hit, cand_dist[rows, np.maximum(choice, 0)], np.inf).astype(np.float32)
        align = np.where(hit, cand_align[rows, np.maximum(choice, 0)], 0.0)

        probability = np.where(
            hit,
            np.exp(-0.5 * (dist / p.sigma_m) ** 2) * (0.85 + 0.15 * align),
            0.0,
        )
        matched = hit & (dist <= th)

        snap_lng = np.where(hit, cand_lng[rows, np.maximum(choice, 0)], lngs)
        snap_lat = np.where(hit, cand_lat[rows, np.maximum(choice, 0)], lats)
        road_idx = np.where(seg >= 0, self.network.seg_road[np.maximum(seg, 0)], -1).astype(np.int32)

        return MatchBatch(
            seg_idx=seg,
            road_idx=road_idx,
            distance_m=np.where(matched, dist, np.inf).astype(np.float32),
            probability=np.where(matched, np.clip(probability, 0.0, 0.999), 0.0).astype(np.float32),
            matched=matched,
            snap_lng=np.asarray(snap_lng, dtype=float),
            snap_lat=np.asarray(snap_lat, dtype=float),
            candidates=(cand_idx >= 0).sum(axis=1).astype(np.int8),
        )

    # ------------------------------------------------------------ 维特比
    def _viterbi_batch(self, batch: list[tuple[int, int]], emit, px, py, cx, cy,
                       cand_idx, choice) -> None:
        """批量维特比：一批行程拼成 (B, L, K) 张量一起递推。

        单条行程逐点循环时，每一步只是在 5×5 的小矩阵上做加法/取最大，
        numpy 的调用开销比计算本身还大（48 万点要 27s）。把长度接近的行程摞成一批，
        同样的递推变成 (B,5,5)，numpy 调用次数降到 1/B，实测 27s → 2s。
        """
        if not batch:
            return
        k = emit.shape[1]
        lens = np.array([e - s for s, e in batch], dtype=np.int64)
        long_len = int(lens.max())
        b_size = len(batch)
        beta = max(self.params.beta_m, 1e-6)

        emit_b = np.full((b_size, long_len, k), NEG, dtype=np.float32)
        px_b = np.zeros((b_size, long_len), dtype=np.float32)
        py_b = np.zeros((b_size, long_len), dtype=np.float32)
        cx_b = np.zeros((b_size, long_len, k), dtype=np.float32)
        cy_b = np.zeros((b_size, long_len, k), dtype=np.float32)
        valid = np.zeros((b_size, long_len, k), dtype=bool)

        for bi, (s, e) in enumerate(batch):
            ln = e - s
            emit_b[bi, :ln] = emit[s:e]
            px_b[bi, :ln] = px[s:e]
            py_b[bi, :ln] = py[s:e]
            cx_b[bi, :ln] = cx[s:e]
            cy_b[bi, :ln] = cy[s:e]
            valid[bi, :ln] = cand_idx[s:e] >= 0

        dp = emit_b[:, 0].copy()
        back = np.full((b_size, long_len, k), -1, dtype=np.int8)
        last_good = np.where(valid[:, 0].any(axis=1), 0, -1)
        dp_last = np.where(valid[:, 0].any(axis=1)[:, None], dp, NEG)

        for t in range(1, long_len):
            prev_valid = valid[:, t - 1]
            route = np.hypot(
                cx_b[:, t][:, None, :] - cx_b[:, t - 1][:, :, None],
                cy_b[:, t][:, None, :] - cy_b[:, t - 1][:, :, None],
            )  # (B, K_prev, K_cur)
            gps = np.hypot(px_b[:, t] - px_b[:, t - 1], py_b[:, t] - py_b[:, t - 1])
            trans = -np.abs(route - gps[:, None, None]) / beta
            total = np.where(prev_valid[:, :, None], dp[:, :, None] + trans, NEG)
            best = np.argmax(total, axis=1).astype(np.int8)           # (B, K_cur)
            step = np.take_along_axis(total, best[:, None, :], axis=1)[:, 0, :]
            dp = emit_b[:, t] + step
            # 上一拍该行程没有任何候选：断链，本拍重新起链
            restart = ~valid[:, t - 1].any(axis=1)
            if restart.any():
                dp[restart] = emit_b[restart, t]
            back[:, t] = np.where(valid[:, t], best, -1)

            active = valid[:, t].any(axis=1)
            last_good = np.where(active, t, last_good)
            dp_last = np.where(active[:, None], dp, dp_last)

        # 回溯：每条行程从自己最后一个有候选的点开始
        for bi, (s, e) in enumerate(batch):
            t = int(last_good[bi])
            if t < 0:
                continue
            row = np.where(valid[bi, t], dp_last[bi], NEG)
            j = int(np.argmax(row))
            choice[s + t] = j
            while t > 0:
                if not valid[bi, t - 1].any():
                    break          # 上一拍无候选，链在这里断开
                j = int(back[bi, t, j])
                if j < 0:
                    break
                t -= 1
                choice[s + t] = j
        return choice

    # ------------------------------------------------------------ 方向
    def _align(self, directions, cand_idx) -> np.ndarray:
        """方向一致性 0~1；无方向字段处一律 0（不奖不罚）。"""
        out = np.zeros(cand_idx.shape, dtype=np.float64)
        if directions is None:
            return out
        dirs = np.asarray(directions, dtype=float)
        if dirs.size != cand_idx.shape[0]:
            return out
        ok = np.isfinite(dirs)
        if not ok.any():
            return out
        safe = np.maximum(cand_idx, 0)
        bearing = self.network.bearing[safe]
        d = angle_diff(dirs[:, None], bearing)
        align = np.cos(np.radians(np.minimum(np.minimum(d, 180.0 - d), 90.0)))
        return np.where(ok[:, None] & (cand_idx >= 0), align, 0.0)


def _trips(trip_ids, n: int):
    """按 trip_ids 的变化切段，产出 [start, end)。"""
    if trip_ids is None:
        yield 0, n
        return
    ids = np.asarray(trip_ids)
    if ids.size != n:
        yield 0, n
        return
    change = np.flatnonzero(ids[1:] != ids[:-1]) + 1
    bounds = np.concatenate(([0], change, [n]))
    for a, b in zip(bounds[:-1], bounds[1:]):
        yield int(a), int(b)


def _batches(trips: list[tuple[int, int]], size: int = 96, ratio: float = 3.0):
    """把行程按长度分组成批：长度接近的摞在一起，避免长行程把短行程垫出大量空拍。"""
    ordered = sorted(trips, key=lambda t: t[1] - t[0])
    i = 0
    while i < len(ordered):
        base = ordered[i][1] - ordered[i][0]
        limit = max(base * ratio, base + 8)
        group = []
        while i < len(ordered) and len(group) < size and ordered[i][1] - ordered[i][0] <= limit:
            group.append(ordered[i])
            i += 1
        yield group


def _empty() -> MatchBatch:
    return MatchBatch(
        seg_idx=np.zeros(0, dtype=np.int32),
        road_idx=np.zeros(0, dtype=np.int32),
        distance_m=np.zeros(0, dtype=np.float32),
        probability=np.zeros(0, dtype=np.float32),
        matched=np.zeros(0, dtype=bool),
        snap_lng=np.zeros(0), snap_lat=np.zeros(0),
        candidates=np.zeros(0, dtype=np.int8),
    )
