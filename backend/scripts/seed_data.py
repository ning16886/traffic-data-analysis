"""模拟数据生成器：520 条路段 + 50 万轨迹点 + 早晚高峰，产出 data/raw 八张原始表。

需求口径（docs/项目需求markdown版.md 第 9、24、26、42、59、245、262 行）：
    50 辆出租车（3s）+ 30 辆网约车（5s）+ 20 辆共享单车（10s），合计 50 万模拟轨迹点；
    方格状 + 环形放射混合路网 ≥ 500 条路段；含 3-5% 脏数据（漂移/隧道丢失/停留/方向异常）；
    早高峰 7-9 / 晚高峰 17-19 / 平峰；road_metrics 按 5 分钟时间窗落表。

用法：
    python backend/scripts/seed_data.py                                        # 50 万点 / 7 天 / 明文 csv
    python backend/scripts/seed_data.py --total-points 8000 --days 1 --format csv
    python backend/scripts/seed_data.py --out-dir D:/tmp/raw --format csv      # 试跑，不碰 data/raw

生成链路（八张表之间有因果，不是八张独立的表）：
    路网 → Dijkstra 路径 → 按需求曲线发车 → 沿路段插值取点 → 注入脏数据 → raw_trajectory
        → 复用 app.services.trajectory_cleaner.clean_dataframe → clean_trajectory
        → 清洗点投影到所属路段 + 按投影距离定匹配成败 → match_result
        → 匹配点按（路段 × 5min 窗）groupby，5% 截尾均速 → road_metrics
        → 每个 segment 的首末匹配路段 → od_matrix
        → 停留点 + 起终点 + 低速点 DBSCAN(eps=500m, min_samples=10) → hotzone

四个必须知道的取舍：
1. 「50 万点 / 7 天 / 100 辆车」= 每辆车每天只在路上约 50 分钟。按 3s/5s/10s 全时采样，
   100 辆车 7 天会产出 1000 万+ 点，所以这是**低占空比的抽样子集**，不是连续轨迹流。
2. 份额：出租车 62% / 网约车 25% / 共享单车 13%（单车 10s 采样且行程短，天然点少）。
3. road_metrics 的均速排除 is_stay=1 的停留点：停留点速度接近 0，留着会让一条停下过车的路
   整窗均速掉到 1 km/h，Top10 就变成「谁停车了」而不是「谁堵了」。
4. 热区聚类用活动点（停留 / 起终点 / 低速），不用全部路过点：eps=500m 摊在稠密路网上会把
   整个中心城区糊成一两个几千点的巨型簇，hotzone 表里就没有可读的「热区」了。
"""

from __future__ import annotations

import argparse
import heapq
import math
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.config import settings  # noqa: E402
from app.services.trajectory_cleaner import clean_dataframe  # noqa: E402

# ------------------------------------------------------------------ 地理常数
LNG0, LNG1 = 116.30, 116.55          # 与旧版 76 条路段同范围，前端底图中心不用改
LAT0, LAT1 = 39.85, 40.00
LNGC, LATC = (LNG0 + LNG1) / 2, (LAT0 + LAT1) / 2
M_LAT = 111_033.0                    # 39.9°N 处 1° 纬度对应的米
M_LNG = 111_320.0 * float(np.cos(np.radians(LATC)))
EARTH_R = 6_371_008.8

FREE_FLOW = {"highway": 82.0, "main": 55.0, "secondary": 40.0, "branch": 26.0}
# 拥堵敏感度：高速受影响最小，次干道是早晚高峰最先堵死的那批路
SENSITIVITY = {"highway": 0.55, "main": 1.00, "secondary": 1.10, "branch": 0.85}

# 某时刻这条路还跑不跑得动（1=畅通，0.5=速度腰斩）：8 点与 18 点两个深 V
WEEKDAY_FACTOR = [
    0.97, 0.98, 0.99, 0.99, 0.99, 0.97, 0.86, 0.62, 0.50, 0.66, 0.82, 0.81,
    0.78, 0.80, 0.82, 0.81, 0.74, 0.56, 0.47, 0.63, 0.80, 0.88, 0.94, 0.96,
]
WEEKEND_FACTOR = [
    0.94, 0.96, 0.98, 0.99, 0.99, 0.96, 0.90, 0.82, 0.72, 0.70, 0.74, 0.76,
    0.78, 0.80, 0.81, 0.79, 0.74, 0.68, 0.66, 0.72, 0.82, 0.88, 0.92, 0.94,
]
# 发车时刻分布（早晚两个驼峰，夜间留一条薄尾巴）
WEEKDAY_DEMAND = [
    1.0, 0.6, 0.4, 0.3, 0.4, 1.6, 6.5, 12.5, 11.0, 6.0, 4.2, 4.6,
    4.8, 4.6, 4.8, 5.4, 7.2, 11.5, 10.0, 6.2, 4.4, 3.4, 2.6, 1.8,
]
WEEKEND_DEMAND = [
    2.2, 1.2, 0.7, 0.4, 0.4, 0.8, 1.8, 3.4, 5.2, 6.4, 6.0, 5.6,
    5.4, 5.2, 5.4, 5.8, 6.0, 6.2, 5.6, 4.8, 4.0, 3.4, 3.0, 2.6,
]


def haversine(lng1, lat1, lng2, lat2):
    p1, p2 = np.radians(lat1), np.radians(lat2)
    dp = (p2 - p1) / 2.0
    dl = np.radians(lng2 - lng1) / 2.0
    a = np.sin(dp) ** 2 + np.cos(p1) * np.cos(p2) * np.sin(dl) ** 2
    return 2.0 * EARTH_R * np.sqrt(np.clip(a, 0.0, 1.0))


def bearings(lng1, lat1, lng2, lat2):
    dy = (np.asarray(lat2) - np.asarray(lat1)) * M_LAT
    dx = (np.asarray(lng2) - np.asarray(lng1)) * M_LNG
    return (np.degrees(np.arctan2(dx, dy)) + 360.0) % 360.0


def to_xy(lng, lat):
    """局部米制平面（等价于本范围内的 UTM 投影，误差 <0.1%）。DBSCAN 的 eps=500 必须喂米。"""
    return np.column_stack(((np.asarray(lng, dtype=float) - LNGC) * M_LNG,
                            (np.asarray(lat, dtype=float) - LATC) * M_LAT))


# ------------------------------------------------------------------ 路网
@dataclass
class Edge:
    idx: int
    road_id: str
    road_name: str
    level: str
    u: int
    v: int
    xy: np.ndarray       # N×2 折点，顺序 u→v
    length_m: float
    cum: np.ndarray      # 折点累计里程（米）


class Network:
    """方格网 472 条 + 环线 24 条 + 8 根放射高速 24 条 ≈ 520 条，节点全部共用保证连通。"""

    NX, NY = 18, 14
    DLNG = (LNG1 - LNG0) / (NX - 1)     # ≈0.0147° ≈ 1.26 km
    DLAT = (LAT1 - LAT0) / (NY - 1)     # ≈0.0115° ≈ 1.28 km

    def __init__(self):
        self.xy: list[tuple[float, float]] = []
        self.adj: list[list[tuple[int, int]]] = []
        self.edges: list[Edge] = []
        self.pair: dict[tuple[int, int], int] = {}
        self.seq = 0
        self._build()
        self.classify()

    def new_node(self, lng: float, lat: float) -> int:
        self.xy.append((float(lng), float(lat)))
        self.adj.append([])
        return len(self.xy) - 1

    def grid(self, i: int, j: int) -> int:
        return i * self.NY + j

    def snap(self, lng: float, lat: float) -> int:
        i = min(max(int(round((lng - LNG0) / self.DLNG)), 0), self.NX - 1)
        j = min(max(int(round((lat - LAT0) / self.DLAT)), 0), self.NY - 1)
        return self.grid(i, j)

    def add(self, chain: list[int], name: str, level: str) -> None:
        key = (chain[0], chain[-1]) if chain[0] < chain[-1] else (chain[-1], chain[0])
        if key in self.pair or chain[0] == chain[-1]:
            return
        pts = np.array([self.xy[n] for n in chain], dtype=float)
        m = to_xy(pts[:, 0], pts[:, 1])
        seg = np.hypot(np.diff(m[:, 0]), np.diff(m[:, 1]))
        self.seq += 1
        e = Edge(idx=len(self.edges), road_id=f"R{self.seq:05d}", road_name=name, level=level,
                 u=chain[0], v=chain[-1], xy=pts, length_m=float(seg.sum()),
                 cum=np.concatenate(([0.0], np.cumsum(seg))))
        self.edges.append(e)
        self.pair[key] = e.idx
        self.adj[chain[0]].append((e.idx, chain[-1]))
        self.adj[chain[-1]].append((e.idx, chain[0]))

    def _build(self) -> None:
        for i in range(self.NX):
            for j in range(self.NY):
                self.new_node(LNG0 + i * self.DLNG, LAT0 + j * self.DLAT)
        self.center = self.new_node(LNGC, LATC)

        def vl(i):
            return "main" if i % 6 == 0 else ("secondary" if i % 3 == 0 else "branch")

        def hl(j):
            return "main" if j % 6 == 0 else ("secondary" if j % 3 == 0 else "branch")

        cn = {"main": "干", "secondary": "次", "branch": "支", "highway": "高"}
        for i in range(self.NX):                       # 竖线 18 条 × 13 段 = 234
            for j in range(self.NY - 1):
                self.add([self.grid(i, j), self.grid(i, j + 1)], f"南北{cn[vl(i)]}{i:02d}段{j:02d}", vl(i))
        for j in range(self.NY):                       # 横线 14 条 × 17 段 = 238
            for i in range(self.NX - 1):
                self.add([self.grid(i, j), self.grid(i + 1, j)], f"东西{cn[hl(j)]}{j:02d}段{i:02d}", hl(j))

        a, b = 0.098, 0.058                            # 环线：24 个椭圆点吸附到路口
        ring: list[int] = []
        for k in range(24):
            th = np.radians(k * 15.0)
            n = self.snap(LNGC + a * np.cos(th), LATC + b * np.sin(th))
            if not ring or ring[-1] != n:
                ring.append(n)
        if ring[0] == ring[-1]:
            ring.pop()
        self.ring = ring
        for k in range(len(ring)):
            self.add([ring[k], ring[(k + 1) % len(ring)]], f"环线{k:02d}段", "main")

        radials: list[int] = []
        for r in range(8):                             # 放射：中心路口→环线路口，各拆 3 段
            target = ring[int(round(r * len(ring) / 8)) % len(ring)]
            if target == self.center:
                continue
            tl = self.xy[target]
            chain = [self.center]
            for f in (1 / 3, 2 / 3):
                chain.append(self.new_node(
                    self.xy[self.center][0] + (tl[0] - self.xy[self.center][0]) * f,
                    self.xy[self.center][1] + (tl[1] - self.xy[self.center][1]) * f))
            chain.append(target)
            for s in range(3):
                self.add(chain[s:s + 2], f"放射{r:02d}段{s:02d}", "highway")
            radials.append(target)
        self.radials = radials

    def classify(self) -> None:
        arr = to_xy([p[0] for p in self.xy], [p[1] for p in self.xy])
        self.node_m = arr
        self.dist_c = np.hypot(arr[:, 0], arr[:, 1])
        self.business = np.flatnonzero((np.abs(arr[:, 0]) < 2600) & (np.abs(arr[:, 1]) < 1700))
        self.residential = np.flatnonzero(self.dist_c > 6200)
        self.transit = np.array(sorted({self.ring[k] for k in range(0, len(self.ring), 4)}), dtype=int)
        self.max_dist = float(self.dist_c.max())

    def quality(self, e: Edge) -> float:
        """每条路段一个固定素质系数 0.86~1.06：同一时刻有的路就是更堵，Top10 才有稳定常客。"""
        h = (e.idx * 2654435761) % 10007 / 10007.0
        return 0.86 + 0.20 * h

    def speed_kmh(self, e: Edge, hour_frac: float, weekday: bool) -> float:
        table = WEEKDAY_FACTOR if weekday else WEEKEND_FACTOR
        h = int(hour_frac) % 24
        w = (hour_frac % 1.0)
        base = table[h] * (1 - w) + table[(h + 1) % 24] * w
        cen = 1.0 - min(1.0, (self.dist_c[e.u] + self.dist_c[e.v]) / (2 * self.max_dist))
        dip = (1.0 - base) * SENSITIVITY[e.level] * (0.72 + 0.56 * cen)
        return float(np.clip(FREE_FLOW[e.level] * (1 - dip) * self.quality(e), 3.0, 118.0))

    def route(self, src: int, dst: int, hour: int, weekday: bool,
              no_highway: bool = False) -> list[tuple[int, bool]]:
        """Dijkstra，代价 = 该时刻的实际通过时间（秒）。返回 [(路段索引, 是否 u→v 正向)]。

        no_highway=True 走「不含放射高速」的子图：网格 + 环线本身连通，给共享单车用。
        """
        if src == dst:
            return []
        best = {src: 0.0}
        prev: dict[int, tuple[int, int, bool]] = {}
        pq = [(0.0, src)]
        done: set[int] = set()
        while pq:
            d, u = heapq.heappop(pq)
            if u in done:
                continue
            done.add(u)
            if u == dst:
                break
            for eidx, w in self.adj[u]:
                e = self.edges[eidx]
                if no_highway and e.level == "highway":
                    continue
                nd = d + e.length_m / max(self.speed_kmh(e, hour, weekday), 3.0) * 3.6
                if nd < best.get(w, float("inf")):
                    best[w] = nd
                    prev[w] = (u, eidx, e.u == u)
                    heapq.heappush(pq, (nd, w))
        if dst not in prev:
            return []
        out, cur = [], dst
        while cur != src:
            u, eidx, forward = prev[cur]
            out.append((eidx, forward))
            cur = u
        return out[::-1]

    def polyline(self, e: Edge, forward: bool):
        xy = e.xy if forward else e.xy[::-1]
        cum = e.cum if forward else (e.length_m - e.cum[::-1])
        brg = bearings(xy[:-1, 0], xy[:-1, 1], xy[1:, 0], xy[1:, 1])
        return xy, cum, np.round(brg, 1)

    def along(self, e: Edge, forward: bool, frac: np.ndarray):
        xy, cum, brg = self.polyline(e, forward)
        target = frac * e.length_m
        k = np.clip(np.searchsorted(cum, target, side="right") - 1, 0, len(xy) - 2)
        local = (target - cum[k]) / np.maximum(cum[k + 1] - cum[k], 1e-9)
        return (xy[k, 0] + (xy[k + 1, 0] - xy[k, 0]) * local,
                xy[k, 1] + (xy[k + 1, 1] - xy[k, 1]) * local, brg[k])

    def project(self, e: Edge, forward: bool, lng, lat):
        """点到该路段折线的垂足与距离（米）——HMM 观测高斯概率的输入。"""
        xy, _, _ = self.polyline(e, forward)
        p = to_xy(lng, lat)
        a = to_xy(xy[:-1, 0], xy[:-1, 1])
        b = to_xy(xy[1:, 0], xy[1:, 1])
        best_d = np.full(len(p), np.inf)
        best_pt = np.zeros((len(p), 2))
        for s in range(len(a)):
            vec = b[s] - a[s]
            t = np.clip(((p[:, 0] - a[s, 0]) * vec[0] + (p[:, 1] - a[s, 1]) * vec[1])
                        / max(float(vec @ vec), 1e-9), 0.0, 1.0)
            cand = np.column_stack((a[s, 0] + t * vec[0], a[s, 1] + t * vec[1]))
            d = np.hypot(p[:, 0] - cand[:, 0], p[:, 1] - cand[:, 1])
            upd = d < best_d
            best_d = np.where(upd, d, best_d)
            best_pt = np.where(upd[:, None], cand, best_pt)
        return LNGC + best_pt[:, 0] / M_LNG, LATC + best_pt[:, 1] / M_LAT, best_d


# ------------------------------------------------------------------ 数据源
@dataclass
class Source:
    source_id: int
    name: str
    type: str
    prefix: str
    vehicles: int
    interval_s: float
    share: float
    edges_min: int
    edges_max: int
    night_ok: bool
    bike: bool


SOURCES = [
    Source(1, "出租车", "taxi", "ta", 50, 3.0, 0.62, 4, 14, True, False),
    Source(2, "网约车", "ridehailing", "ri", 30, 5.0, 0.25, 3, 12, True, False),
    Source(3, "共享单车", "bikeshare", "bi", 20, 10.0, 0.13, 2, 5, False, True),
]

# 先多排 25% 的趟再删：既要盖住探针均值的抽样误差，也要盖住被重叠/出窗判据拒掉的那些
OVERGEN = 1.25


def demand_p(weekday: bool, night_ok: bool) -> np.ndarray:
    w = np.array(WEEKDAY_DEMAND if weekday else WEEKEND_DEMAND, dtype=float)
    if not night_ok:
        w[:5] = 0.0
        w[23] *= 0.3
    return w / w.sum()


def period_of(hour: int) -> str:
    if 7 <= hour <= 9:
        return "morning_peak"
    if 17 <= hour <= 19:
        return "evening_peak"
    return "normal"


def pick_od(net: Network, rng, period: str, bike: bool) -> tuple[int, int]:
    """起终点一定不同：route(src, src) 直接返回空，同对OD 会白烧一次排班重试。"""
    res, bus = int(rng.choice(net.residential)), int(rng.choice(net.business))
    if bike:
        a = int(rng.choice(net.residential))
        pool = np.flatnonzero(np.abs(net.dist_c - net.dist_c[a]) < 2200)
        b = int(rng.choice(pool if len(pool) > 1 else net.business))
    elif period == "morning_peak":
        a, b = res, bus
    elif period == "evening_peak":
        a, b = bus, res
    elif period == "transit":
        a, b = int(rng.choice(net.transit)), bus
    elif rng.random() < 0.5:
        a, b = int(rng.choice(net.residential)), bus
    else:
        a, b = res, int(rng.choice(net.residential))
    return (a, bus if a != bus else res) if a == b else (a, b)


# ------------------------------------------------------------------ 脏数据注入
def inject(trip: dict, src: Source, rng, cfg) -> dict:
    lng, lat, spd, brg, ts, eidx = (trip[k] for k in ("lng", "lat", "speed", "brg", "ts", "eidx"))
    n = len(lng)
    if n < 12:
        return trip

    # ① 速度型漂移：位置偏 400~2500m 且 speed>120 ⇒ 命中清洗规则一a，删除
    k = int(n * cfg.dirty_speed)
    if k > 0:
        idx = rng.choice(n, size=min(k, n), replace=False)
        dist, ang = rng.uniform(400, 2500, len(idx)), rng.uniform(0, 2 * np.pi, len(idx))
        lng = lng.copy(); lat = lat.copy(); spd = spd.copy()
        lng[idx] += dist * np.sin(ang) / M_LNG
        lat[idx] += dist * np.cos(ang) / M_LAT
        spd[idx] = rng.uniform(130, 320, len(idx))

    # ② 跳变型漂移：插一个离群点，与前后两点都距离>200m 且间隔<10s ⇒ 命中规则一b（尖峰三邻居判据）
    k = min(max(1, int(n * cfg.dirty_jump)), max(0, n - 2))
    if k > 0:
        pos = np.sort(rng.choice(np.arange(1, n - 1), size=k, replace=False))
        # 侧向偏移 60~120°：随机方向可能把离群点甩到「上一个点附近」（车每拍走 150m 左右），
        # 那样清洗的双侧尖峰判据就不成立，这个几百米外的脏点会活着进清洗表污染指标。
        side = rng.choice((-1.0, 1.0), k) * rng.uniform(60.0, 120.0, k)
        dist, ang = rng.uniform(250, 700, k), np.radians((brg[pos] + side) % 360.0)
        vals = {
            "lng": lng[pos] + dist * np.sin(ang) / M_LNG,
            "lat": lat[pos] + dist * np.cos(ang) / M_LAT,
            "speed": spd[pos],
            "brg": (brg[pos] + rng.uniform(-30, 30, k)) % 360,
            "ts": (ts[pos - 1] + ts[pos]) // 2,
            "eidx": eidx[pos],
        }
        arrays = {"lng": lng, "lat": lat, "speed": spd, "brg": brg, "ts": ts, "eidx": eidx}
        for key, arr in arrays.items():
            arrays[key] = np.insert(np.asarray(arr), pos, vals[key])
        lng, lat, spd, brg, ts, eidx = (arrays[x] for x in ("lng", "lat", "speed", "brg", "ts", "eidx"))
        n = len(lng)

    # ③ 隧道丢失：删掉连续 200~250s 的点 ⇒ 同段内间隔 >180s，清洗规则二标 is_gap / flag=tunnel
    if rng.random() < cfg.tunnel_prob:
        cnt = max(1, int(rng.uniform(200, 250) / src.interval_s))
        if n > cnt + 22:                      # 3s 采样时 cnt 可达 83，短行程装不下整段空洞
            p = int(rng.uniform(10, n - cnt - 10))
            keep = np.ones(n, dtype=bool)
            keep[p:p + cnt] = False
            lng, lat, spd, brg, ts, eidx = (np.asarray(a)[keep] for a in (lng, lat, spd, brg, ts, eidx))
            n = len(lng)

    # ④ 停留：原地驻车 150~380s（位移 <30m）⇒ 清洗规则三标 is_stay
    if rng.random() < cfg.stay_prob and n > 10:
        cnt = int(rng.uniform(150, 380) / src.interval_s)
        p = int(rng.uniform(5, n - 5))
        jrng = np.random.default_rng(cfg_hash(cfg) + p)
        jl, ja = jrng.normal(0, 7, cnt) / M_LNG, jrng.normal(0, 7, cnt) / M_LAT
        jts = ts[p] + np.arange(1, cnt + 1, dtype=np.int64) * int(src.interval_s * 1e6)
        # 驻车会把后半程整体推后 cnt 拍：既避免与原点 p+1 撞时间戳，也更贴近真实
        ts = np.concatenate([ts[:p + 1], jts, ts[p + 1:] + cnt * int(src.interval_s * 1e6)])
        lng = np.insert(lng, p + 1, lng[p] + jl)
        lat = np.insert(lat, p + 1, lat[p] + ja)
        spd = np.insert(spd, p + 1, jrng.uniform(0.0, 2.5, cnt))
        brg = np.insert(brg, p + 1, np.full(cnt, brg[p]))
        eidx = np.insert(eidx, p + 1, np.full(cnt, eidx[p], dtype=eidx.dtype))
        n = len(lng)

    # ⑤ 方向异常：0.4% 的点方位角反 180°。清洗管道不处理方向，所以这些点会原样留在清洗表里
    k = max(1, int(n * cfg.dirty_dir))
    brg = brg.copy()
    idx = rng.choice(n, size=k, replace=False)
    brg[idx] = (brg[idx] + 180.0) % 360.0

    return dict(trip, lng=lng, lat=lat, speed=spd, brg=brg, ts=ts, eidx=eidx)


def cfg_hash(cfg) -> int:
    return int(cfg.seed) & 0xFFFF


# ------------------------------------------------------------------ 行程与总装
def make_trip(net: Network, src: Source, t0_us: int, rng, weekday: bool, cfg) -> dict | None:
    hour = (t0_us // 3_600_000_000) % 24
    period = period_of(int(hour))
    if src.bike and rng.random() < 0.35:
        period = "transit"
    a, b = pick_od(net, rng, period, src.bike)
    path = net.route(a, b, int(hour), weekday, src.bike)
    if not path:
        return None
    # 行程长度截断：只保留路线前 K 条路段，等价于「司机在半路收车」
    limit = int(rng.integers(src.edges_min, src.edges_max + 1))
    if len(path) > limit:
        path = path[:limit]

    step = int(src.interval_s * 1e6)
    cur = int(t0_us)
    parts = []
    for eidx, forward in path:
        e = net.edges[eidx]
        hour_frac = (cur / 3_600_000_000) % 24
        v = net.speed_kmh(e, hour_frac, weekday)
        if src.bike:
            v = min(v, 17.0)
        travel_s = e.length_m * 3.6 / max(v, 3.0)
        cnt = max(1, int(travel_s / src.interval_s))
        lng, lat, brg = net.along(e, forward, np.arange(cnt) / cnt)
        parts.append((
            lng + rng.normal(0, 4.0, cnt) / M_LNG,
            lat + rng.normal(0, 4.0, cnt) / M_LAT,
            np.clip(v * rng.normal(1.0, 0.11, cnt), 1.5, 118.0),
            brg,
            cur + np.arange(cnt, dtype=np.int64) * step,
            np.full(cnt, eidx, dtype=np.int32),
        ))
        cur += int(travel_s * 1e6)
    if not parts:
        return None
    trip = dict(
        lng=np.concatenate([p[0] for p in parts]), lat=np.concatenate([p[1] for p in parts]),
        speed=np.concatenate([p[2] for p in parts]), brg=np.concatenate([p[3] for p in parts]),
        ts=np.concatenate([p[4] for p in parts]), eidx=np.concatenate([p[5] for p in parts]),
        period=period,
    )
    return inject(trip, src, rng, cfg)


def _us(ts: pd.Timestamp) -> int:
    """pd.Timestamp.value 是纳秒，生成器全程用微秒，混用会把时间戳推到公元 5 万年。"""
    return int(ts.value) // 1000


def sample_start(day_us: int, dp: np.ndarray, rng) -> int:
    """从需求曲线抽发车时刻：先按概率抽小时，再在该小时内均匀抖动。"""
    hour = int(rng.choice(24, p=dp))
    return day_us + hour * 3_600_000_000 + int(rng.uniform(0, 3600) * 1e6)


def calibrate(net: Network, s: Source, days, dp, rng, cfg, n: int = 80) -> float:
    """抽 n 趟真实行程量「一趟平均多少点」——行程预算只能这样反推成趟数。"""
    pts = []
    for i in range(n):
        di = int(rng.integers(len(days)))
        t = make_trip(net, s, sample_start(_us(days[di]), dp[di], rng), rng,
                      days[di].dayofweek >= 5, cfg)
        if t is not None:
            pts.append(len(t["ts"]))
    return max(1.0, float(np.mean(pts))) if pts else 1.0


def generate(net: Network, cfg, t0: float):
    """多排 25% 的趟，再整趟随机删到精确点数，所以原始表行数恰好等于 total_points。

    为什么不按「（车 × 天）点数配额」排：一趟行程是原子的，配额截在行程中间就会
    把当日没排完的额度一路推到深夜，实测 23:00 成了全天最大小时，高峰反而看不出来。
    按趟数编排 + 整趟随机删之后，小时分布 = 需求分布 × 高峰趟长放大，双峰是真的。
    """
    rng = np.random.default_rng(cfg.seed)
    days = pd.date_range(cfg.start_date, periods=cfg.days, freq="D")
    alloc = [int(cfg.total_points * s.share) for s in SOURCES]
    alloc[-1] += cfg.total_points - sum(alloc)

    trips: list[dict] = []
    for s, budget_total in zip(SOURCES, alloc):
        dp = [demand_p(d.dayofweek < 5, s.night_ok) for d in days]
        day_us = [_us(d) for d in days]
        # busy[vi][di]：该车当天已占用的时间区间。同一辆车不能同时在两个行程里，
        # 否则按 (车, 时间戳) 排序后两点来自两个位置，清洗器会把它们判成跳变删掉。
        busy = [[[] for _ in days] for _ in range(s.vehicles)]
        mean_pts = calibrate(net, s, days, dp, rng, cfg)
        k = max(1, math.ceil(budget_total * OVERGEN / (s.vehicles * cfg.days * mean_pts)))

        def plan(vi: int, di: int) -> dict | None:
            for _ in range(20):                  # 20 次抽不到不冲突的时刻就放弃这一趟
                trip = make_trip(net, s, sample_start(day_us[di], dp[di], rng), rng,
                                 days[di].dayofweek >= 5, cfg)
                if trip is None:
                    continue                     # 路线没排出来：时刻不前进，重抽
                lo, hi = int(trip["ts"][0]), int(trip["ts"][-1])
                if hi >= day_us[di] + 86_400_000_000:
                    continue                     # 越过当天窗口末尾
                if any(lo < b_hi and hi > b_lo for b_lo, b_hi in busy[vi][di]):
                    continue                     # 与该车当天另一趟重叠
                busy[vi][di].append((lo, hi))
                return dict(trip, vehicle_id=f"{s.prefix}_{vi:03d}", source_id=s.source_id,
                            passenger=int(rng.random() < (0.72 if not s.bike else 0.40)))
            return None

        src: list[dict] = []
        made = rounds = 0
        while made < budget_total and rounds < 4:
            rounds += 1
            for di in range(cfg.days):
                for vi in range(s.vehicles):
                    for _ in range(k):
                        t = plan(vi, di)
                        if t is not None:
                            src.append(t)
                            made += len(t["ts"])

        total = sum(len(t["ts"]) for t in src)
        if total < budget_total:
            print(f"  [警告] {s.name} 只排出 {total:,} 点，比预算少 {budget_total - total:,}", flush=True)
        else:
            keep = np.ones(len(src), dtype=bool)
            for i in rng.permutation(len(src)):
                n = len(src[i]["ts"])
                if total - n >= budget_total:    # 删得掉就删，删到任何一趟都动不了为止
                    total -= n
                    keep[i] = False
            src = [t for t, kp in zip(src, keep) if kp]
            resid = sum(len(t["ts"]) for t in src) - budget_total
            if resid:
                # 收不动尾时任何一趟都比残差长（不然上一轮就删掉了），裁某一趟的尾巴即可精确命中
                j = int(rng.integers(len(src)))
                cut = len(src[j]["ts"]) - resid
                src[j] = {key: (v[:cut] if isinstance(v, np.ndarray) else v) for key, v in src[j].items()}
        trips += src
        emitted_src = sum(len(t["ts"]) for t in src)
        print(f"  [{elapsed(t0)}] {s.name:<6} 预算 {budget_total:>7,} 实产 {emitted_src:>8,}"
              f"（{len(src):,} 趟 × 均 {emitted_src / max(1, len(src)):.0f} 点，"
              f"每车每天 {k} 趟，趟均 {mean_pts:.0f} 点）", flush=True)

    cols = {k: [] for k in ("lng", "lat", "speed", "brg", "ts", "eidx", "veh", "src", "pax", "trip", "period")}
    for i, t in enumerate(trips):
        n = len(t["ts"])
        cols["lng"].append(t["lng"]); cols["lat"].append(t["lat"])
        cols["speed"].append(t["speed"]); cols["brg"].append(t["brg"])
        cols["ts"].append(t["ts"]); cols["eidx"].append(t["eidx"])
        cols["veh"].append(np.full(n, t["vehicle_id"], dtype=object))
        cols["src"].append(np.full(n, t["source_id"], dtype=np.int8))
        cols["pax"].append(np.full(n, t["passenger"], dtype=np.int8))
        cols["trip"].append(np.full(n, i, dtype=np.int32))
        cols["period"].append(np.full(n, t["period"], dtype=object))
    raw = pd.DataFrame({
        "vehicle_id": np.concatenate(cols["veh"]),
        "source_id": np.concatenate(cols["src"]),
        "timestamp": np.concatenate(cols["ts"]),
        "longitude": np.concatenate(cols["lng"]),
        "latitude": np.concatenate(cols["lat"]),
        "speed": np.concatenate(cols["speed"]),
        "direction": np.concatenate(cols["brg"]),
        "passenger": np.concatenate(cols["pax"]),
        "_eidx": np.concatenate(cols["eidx"]),
        "_trip": np.concatenate(cols["trip"]),
        "_period": np.concatenate(cols["period"]),
    })
    raw = raw.sort_values(["vehicle_id", "timestamp"], kind="stable").reset_index(drop=True)
    raw.insert(0, "id", np.arange(1, len(raw) + 1, dtype=np.int64))
    dup = int(raw.duplicated(["vehicle_id", "timestamp"]).sum())
    assert dup == 0, f"{dup} 组 (车, 时间戳) 重复：脏数据注入插出了同拍两点"
    win_lo, win_hi = _us(days[0]), _us(days[-1] + pd.Timedelta(days=1))
    assert int(raw.timestamp.min()) >= win_lo, "有行程起点落在窗口之前"
    assert int(raw.timestamp.max()) < win_hi, "排班时放进了越过窗口末尾的行程"
    assert len(raw) == cfg.total_points, f"实产 {len(raw):,} ≠ 预算 {cfg.total_points:,}"
    return raw


def elapsed(t0: float) -> str:
    return f"{time.perf_counter() - t0:5.1f}s"


# ------------------------------------------------------------------ 派生表
def attach_truth(clean: pd.DataFrame, raw: pd.DataFrame) -> pd.DataFrame:
    """清洗表回连原始表的真值（所属路段 / 行程号）。(vehicle_id, timestamp) 在单车内唯一。"""
    helper = raw[["vehicle_id", "timestamp", "id", "_eidx", "_trip", "_period"]].rename(
        columns={"id": "raw_id"})
    m = clean.merge(helper, on=["vehicle_id", "timestamp"], how="left", validate="one_to_one")
    if m["raw_id"].isna().any():
        raise RuntimeError(f"{int(m['raw_id'].isna().sum())} 个清洗点在原始表里找不到对应，说明 (车,时间戳) 撞了")
    # 清洗表主键：与 clean_out 的写盘顺序一致（1..N），match_result 靠它回连
    m.insert(0, "id", np.arange(1, len(m) + 1, dtype=np.int64))
    return m


def build_match(m: pd.DataFrame, net: Network, cfg, t0: float) -> pd.DataFrame:
    """真值路段 + 点到折线的垂足；匹配成败由投影距离决定（等价于维特比给出的全局最优路径）。"""
    rng = np.random.default_rng(cfg.seed + 1)
    order = np.argsort(m["_eidx"].to_numpy(), kind="stable")
    mlng = np.empty(len(m)); mlat = np.empty(len(m)); dist = np.empty(len(m))
    eidx_arr = m["_eidx"].to_numpy()
    lng_arr, lat_arr = m["longitude"].to_numpy(), m["latitude"].to_numpy()
    # 逐路段批量投影：按 _eidx 排序后切片，整体一次 O(n)
    e_sorted = eidx_arr[order]
    bounds = np.searchsorted(e_sorted, np.unique(e_sorted), side="left")
    bounds = np.append(bounds, len(order))
    for u, bi in zip(np.unique(e_sorted), range(len(bounds) - 1)):
        sel = order[bounds[bi]:bounds[bi + 1]]
        e = net.edges[int(u)]
        forward = True  # 投影与方向无关，折线倒序点集相同
        a, b, d = net.project(e, forward, lng_arr[sel], lat_arr[sel])
        mlng[sel], mlat[sel], dist[sel] = a, b, d

    base = np.exp(-0.5 * (dist / settings.hmm_sigma_m) ** 2)
    prob = np.clip(base * rng.uniform(0.90, 1.0, len(m)), 0.05, 0.999)
    # 匹配率目标 ≥90%：按概率升序砍掉 unmatched_rate 比例，概率最低的点先判「无路可匹配」
    cut = int(len(m) * cfg.unmatched_rate)
    worst = np.argpartition(prob, cut)[:cut] if cut > 0 else np.array([], dtype=int)
    is_matched = np.ones(len(m), dtype=np.int8)
    is_matched[worst] = 0
    out = pd.DataFrame({
        "vehicle_id": m["vehicle_id"],
        "road_id": np.where(is_matched == 1, [net.edges[int(i)].road_id for i in eidx_arr], None),
        "raw_trajectory_id": m["raw_id"],
        "clean_trajectory_id": m["id"],
        "longitude": m["longitude"],
        "latitude": m["latitude"],
        "match_longitude": np.where(is_matched == 1, mlng, np.nan),
        "match_latitude": np.where(is_matched == 1, mlat, np.nan),
        "match_probability": np.where(is_matched == 1, prob, prob * 0.35),
        "is_matched": is_matched,
        "_speed": m["speed"], "_ts": m["timestamp"], "_seg": m["segment_id"],
        "_period": m["_period"], "_stay": m["is_stay"], "_dist": dist, "_src": m["source_id"],
    })
    print(f"  [{elapsed(t0)}] 匹配 {int((out.is_matched == 1).sum()):,}/{len(out):,} = "
          f"{float(out.is_matched.mean())*100:.2f}%，投影距离中位 {np.median(out._dist):.1f} m")
    return out


def build_metrics(matched: pd.DataFrame, t0: float, excl_src: tuple[int, ...] = ()) -> pd.DataFrame:
    """匹配点按（路段 × 5min 窗）聚合；均速做 5% 双侧截尾（需求第 56 行），停留点不入样本。

    excl_src 排除的数据源只影响这张表，OD 和热区照旧全源参与。共享单车必须排除：
    它常年 17 km/h，而需求第 58 行把「该路段最低流量时刻的均速」当自由流分母，
    一条主干道流量最低的窗里只要恰好溜过一辆单车，它整周的拥堵指数就被那 5 个点定死
    （实测主干道 ff 中位数 19.2 vs 机动车口径 46.0，Top10 全变成「谁骑上了主干道」）。
    """
    d = matched[(matched.is_matched == 1) & (matched._stay == 0) & ~matched._src.isin(excl_src)][
        ["road_id", "_speed", "_ts"]].dropna(subset=["road_id"]).rename(
        columns={"_speed": "speed"}).copy()
    d["window"] = pd.to_datetime(d["_ts"]).dt.floor("5min")
    keys = ["road_id", "window"]
    g = d.groupby(keys, sort=False)["speed"]
    n = g.transform("size")
    rank = g.rank(method="first", ascending=True)
    k = (0.05 * n + 0.5).astype(int)              # 每侧截尾掉的样本数：n<20 时为 0，等价于取均值
    keep = (rank > k) & (rank <= n - k)
    agg = d[keep].groupby(keys, sort=False)["speed"].agg(["mean", "min", "max"]).reset_index()
    cnt = d.groupby(keys, sort=False).size().rename("point_count").reset_index()
    out = cnt.merge(agg, on=keys, how="left").rename(
        columns={"mean": "avg_speed", "min": "min_speed", "max": "max_speed", "window": "time_window"})
    out["avg_speed"] = out["avg_speed"].round(2)
    out = out.sort_values(["time_window", "road_id"]).reset_index(drop=True)
    out.insert(0, "id", np.arange(1, len(out) + 1, dtype=np.int64))
    print(f"  [{elapsed(t0)}] road_metrics {len(out):,} 行（路段 × 5min 窗，机动车源，排除数据源 {excl_src}）"
          f"，覆盖路段 {out.road_id.nunique()} 条，单窗样本中位 {int(cnt.point_count.median())} 点")
    return out


def build_od(matched: pd.DataFrame, cfg) -> pd.DataFrame:
    """OD = 每个 segment 的首末匹配路段，时间段按该段首点小时划分。"""
    d = matched[(matched.is_matched == 1) & matched.road_id.notna()].sort_values("clean_trajectory_id")
    first = d.groupby("_seg", sort=False).head(1).set_index("_seg")
    last = d.groupby("_seg", sort=False).tail(1).set_index("_seg")
    common = first.index.intersection(last.index)
    first, last = first.loc[common], last.loc[common]
    od = pd.DataFrame({
        "origin_road_id": first["road_id"].to_numpy(),
        "dest_road_id": last["road_id"].to_numpy(),
        "time_period": first["_period"].to_numpy(),
        "dur_min": (last["_ts"].to_numpy() - first["_ts"].to_numpy()) / np.timedelta64(1, "m"),
    }, index=common)
    od = od.dropna(subset=["origin_road_id", "dest_road_id"])
    out = od.groupby(["origin_road_id", "dest_road_id", "time_period"], as_index=False).agg(
        trip_count=("dur_min", "size"), avg_duration_min=("dur_min", "mean"))
    out["avg_duration_min"] = out["avg_duration_min"].round(2)
    out = out.sort_values("trip_count", ascending=False).reset_index(drop=True)
    out.insert(0, "id", np.arange(1, len(out) + 1, dtype=np.int64))
    return out


def build_hotzone(matched: pd.DataFrame, net: Network, cfg, t0: float) -> pd.DataFrame:
    from sklearn.cluster import DBSCAN

    cand = matched[(matched.is_matched == 1) & matched.road_id.notna()]
    pool = pd.concat([
        cand[cand._stay == 1],
        cand[cand._speed < 8.0],
        cand[cand._speed < 12.0].sample(frac=0.2, random_state=cfg.seed),
        cand.groupby("_seg", sort=False).head(1),
        cand.groupby("_seg", sort=False).tail(1),
    ]).drop_duplicates(subset=["clean_trajectory_id"])
    if len(pool) < 200:
        raise RuntimeError(f"活动点只有 {len(pool)} 个，聚不出热区")
    xy = to_xy(pool.longitude.to_numpy(), pool.latitude.to_numpy())
    lab = DBSCAN(eps=cfg.dbscan_eps, min_samples=cfg.dbscan_min).fit_predict(xy)
    res = []
    lngs, lats = pool.longitude.to_numpy(), pool.latitude.to_numpy()
    for c in np.unique(lab[lab >= 0]):
        sel = lab == c
        n = int(sel.sum())
        if n < 15:
            continue
        clng, clat = float(lngs[sel].mean()), float(lats[sel].mean())
        r = float(haversine(clng, clat, lngs[sel], lats[sel]).max())
        res.append((clng, clat, min(r, cfg.dbscan_eps * 6), n))
    z = pd.DataFrame(res, columns=["center_lng", "center_lat", "radius_m", "point_count"])
    if z.empty:
        return pd.DataFrame(columns=["id", "zone_id", "center_lng", "center_lat", "radius_m",
                                     "point_count", "zone_type", "create_time"])
    z = z.sort_values("point_count", ascending=False).head(cfg.max_zones).reset_index(drop=True)
    z.insert(0, "id", np.arange(1, len(z) + 1, dtype=np.int64))
    z["zone_id"] = [f"HZ_{i:03d}" for i in range(len(z))]
    pxy = to_xy(z.center_lng, z.center_lat)
    txy = net.node_m[net.transit]
    near = np.min(np.hypot(pxy[:, 0][:, None] - txy[:, 0][None, :],
                           pxy[:, 1][:, None] - txy[:, 1][None, :]), axis=1)
    z["zone_type"] = np.where(near < 700, "transit",
                              np.where((np.abs(pxy[:, 0]) < 2600) & (np.abs(pxy[:, 1]) < 1700),
                                       "business", "residential"))
    print(f"  [{elapsed(t0)}] 热区：活动点 {len(pool):,} → 簇 {len(z)} 个，"
          f"类型 {z.zone_type.value_counts().to_dict()}")
    return z


# ------------------------------------------------------------------ 主流程
def main() -> int:
    p = argparse.ArgumentParser(description="生成 50 万模拟轨迹 + 520 条路段，产出 data/raw 八张原始表")
    p.add_argument("--total-points", type=int, default=500_000)
    p.add_argument("--days", type=int, default=7)
    p.add_argument("--start-date", default="2024-03-04", help="起始日（周一）")
    p.add_argument("--seed", type=int, default=20240304)
    p.add_argument("--out-dir", default=str(settings.raw_dir))
    p.add_argument("--format", choices=["csv", "csv.gz"], default="csv")
    p.add_argument("--unmatched-rate", type=float, default=0.060, help="匹配失败比例，需求要求匹配率 ≥90%")
    p.add_argument("--dirty-speed", type=float, default=0.020, help="速度型漂移点比例")
    p.add_argument("--dirty-jump", type=float, default=0.006, help="跳变型漂移点比例（插入后被清洗删除）")
    p.add_argument("--dirty-dir", type=float, default=0.004, help="方向异常点比例（清洗不处理，保留）")
    p.add_argument("--tunnel-prob", type=float, default=0.030, help="单趟行程出现隧道空洞的概率")
    p.add_argument("--stay-prob", type=float, default=0.040, help="单趟行程出现停留驻车的概率")
    p.add_argument("--dbscan-eps", type=float, default=500.0)
    p.add_argument("--dbscan-min", type=int, default=10)
    p.add_argument("--max-zones", type=int, default=300)
    cfg = p.parse_args()
    t0 = time.perf_counter()

    net = Network()
    lv = pd.Series([e.level for e in net.edges]).value_counts().to_dict()
    print(f"[1/6] 路网 {len(net.edges)} 条路段 / {len(net.xy)} 个路口 / 总里程 "
          f"{sum(e.length_m for e in net.edges)/1000:.1f} km\n      等级分布 {lv}")
    print(f"      商业节点 {len(net.business)} 居住锚点 {len(net.residential)} 枢纽 {len(net.transit)}")

    print(f"\n[2/6] 生成轨迹 {cfg.total_points:,} 点 / {cfg.days} 天 / "
          f"{sum(s.vehicles for s in SOURCES)} 辆车（起点 {cfg.start_date}，seed={cfg.seed}）")
    raw = generate(net, cfg, t0)
    print(f"  [{elapsed(t0)}] 原始点合计 {len(raw):,} 行")

    print("\n[3/6] 清洗（调用 app/services/trajectory_cleaner.clean_dataframe，不在旁边另算一套）")
    raw_ts = raw.assign(timestamp=pd.to_datetime(raw["timestamp"], unit="us"))
    rc = raw_ts[["id", "vehicle_id", "source_id", "timestamp", "longitude", "latitude",
                 "speed", "direction"]].copy()
    clean, stats = clean_dataframe(rc)
    dirty_removed = stats["drift_removed"]
    flagged = stats["stay_points"] + stats["gap_points"] + int(stats["raw_count"] * cfg.dirty_dir)
    print(f"  [{elapsed(t0)}] {stats['raw_count']:,} → {stats['clean_count']:,}｜删漂移 {dirty_removed:,}｜"
          f"标停留 {stats['stay_points']:,}｜标隧道 {stats['gap_points']:,}｜分段 {stats['segments']:,}｜"
          f"脏数据合计 {(dirty_removed + flagged) / stats['raw_count'] * 100:.2f}%")

    print("\n[4/6] 派生匹配 / 指标 / OD / 热区")
    m = attach_truth(clean, raw_ts)
    matched = build_match(m, net, cfg, t0)
    metrics = build_metrics(matched, t0, tuple(s.source_id for s in SOURCES if s.bike))
    od = build_od(matched, cfg)
    print(f"  [{elapsed(t0)}] od_matrix {len(od):,} 行（起终路段 × 时间段）")
    hot = build_hotzone(matched, net, cfg, t0)

    print("\n[5/6] 组装八张表")
    ds = pd.DataFrame([{
        "id": s.source_id, "name": s.name, "type": s.type,
        "description": f"{s.vehicles} 辆，{int(s.interval_s)}s 采样，模拟数据集",
        "status": 1, "vehicle_count": s.vehicles,
        "point_count": int((raw.source_id == s.source_id).sum()),
        "created_at": pd.Timestamp(cfg.start_date)} for s in SOURCES])
    seg = pd.DataFrame([{
        "id": i + 1,
        "road_id": e.road_id, "road_name": e.road_name, "level": e.level,
        "length": round(e.length_m, 1),
        "geometry": "LINESTRING(" + ", ".join(f"{x:.5f} {y:.5f}" for x, y in e.xy) + ")",
        "bounds_min_lng": round(float(e.xy[:, 0].min()), 5), "bounds_min_lat": round(float(e.xy[:, 1].min()), 5),
        "bounds_max_lng": round(float(e.xy[:, 0].max()), 5), "bounds_max_lat": round(float(e.xy[:, 1].max()), 5),
    } for i, e in enumerate(net.edges)])
    raw_out = rc[["id", "vehicle_id", "source_id", "timestamp", "longitude", "latitude",
                  "speed", "direction"]].copy()
    raw_out["passenger"] = raw["passenger"].to_numpy()
    clean_out = m[["id", "vehicle_id", "source_id", "segment_id", "timestamp", "longitude", "latitude",
                   "speed", "direction", "is_stay", "is_gap", "flag"]].sort_values("id").reset_index(drop=True)
    match_out = matched.sort_values("clean_trajectory_id").reset_index(drop=True)
    match_out["create_time"] = pd.Timestamp(cfg.start_date) + pd.Timedelta(days=cfg.days)
    match_out.insert(0, "id", np.arange(1, len(match_out) + 1, dtype=np.int64))
    od_out = od.copy()
    od_out["create_time"] = pd.Timestamp(cfg.start_date) + pd.Timedelta(days=cfg.days)
    hot_out = hot.copy()
    hot_out["create_time"] = pd.Timestamp(cfg.start_date) + pd.Timedelta(days=cfg.days)

    tables = {"data_source": ds, "road_segment": seg, "raw_trajectory": raw_out,
              "clean_trajectory": clean_out,
              "match_result": match_out[["id", "vehicle_id", "road_id", "raw_trajectory_id",
                                         "clean_trajectory_id", "match_longitude", "match_latitude",
                                         "match_probability", "is_matched", "create_time"]],
              "road_metrics": metrics, "od_matrix": od_out, "hotzone": hot_out}

    print("\n[6/6] 写盘 → " + str(Path(cfg.out_dir)))
    fmt = cfg.format
    out_dir = Path(cfg.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    report = []
    for name, df in tables.items():
        path = out_dir / f"{name}.csv" if fmt == "csv" else out_dir / f"{name}.csv.gz"
        if fmt == "csv":
            df.to_csv(path, index=False, encoding="utf-8-sig", lineterminator="\n")
        else:
            with gzip_open(path, "wt") as fh:      # 编码在句柄上定，to_csv 不再接 encoding
                df.to_csv(fh, index=False, lineterminator="\n")
        report.append((name, len(df), path.stat().st_size))
        print(f"  [{elapsed(t0)}] {name:<18}{len(df):>9,} 行   {path.name:<26}{path.stat().st_size/1e6:6.2f} MB")

    total_mb = sum(r[2] for r in report) / 1e6
    print(f"\n合计 {sum(r[1] for r in report):,} 行 / {len(report)} 张表 / {total_mb:.1f} MB，"
          f"用时 {elapsed(t0)}")

    hours = pd.to_datetime(clean_out["timestamp"]).dt.hour
    per_hour = clean_out.groupby(hours).size()
    mm = matched[matched.is_matched == 1]
    spd_hour = pd.DataFrame({"h": pd.to_datetime(mm["_ts"]).dt.hour, "v": mm["_speed"]}).groupby("h").v.mean()

    # 同路比值：每个点的速度除以「这条路自己凌晨 3 点的理论速度」。
    # 直接看均速看不出高峰——不同时刻跑在不同等级的路上（凌晨在高速、中午在支路），
    # 共享单车又限死 17 km/h，两条混在一起会把峰形抹平甚至反向。
    bike_src = next(s.source_id for s in SOURCES if s.bike)
    ff_road = {e.road_id: net.speed_kmh(e, 3.0, True) for e in net.edges}
    car = mm[mm._src != bike_src]
    ratio = car["_speed"] / car["road_id"].map(ff_road).astype(float)
    idx_hour = pd.DataFrame({"h": pd.to_datetime(car["_ts"]).dt.hour, "r": ratio}).groupby("h").r.mean()

    w1 = metrics.copy()
    w1["h"] = pd.to_datetime(w1["time_window"]).dt.floor("1h")
    roads_per_win = w1.groupby("h")["road_id"].nunique()
    print(f"\n每个 1 小时展示窗内有指标的路段数：中位 {int(roads_per_win.median())}，"
          f"最少 {int(roads_per_win.min())}，最多 {int(roads_per_win.max())}（大屏 Top10 要看这个数）")

    print("\n按小时（清洗后点数 / 匹配点均速 / 同路比值：1=凌晨畅通，越低越堵）：")
    vmax = int(per_hour.max())
    for h in range(24):
        n = int(per_hour.get(h, 0))
        v = float(spd_hour.get(h, float("nan")))
        r = float(idx_hour.get(h, float("nan")))
        mark = "  ← 早高峰" if 7 <= h <= 9 else ("  ← 晚高峰" if 17 <= h <= 19 else "")
        print(f"  {h:02d}:00 {n:>7,} {v:6.1f} km/h  {r:5.2f}  {'#' * int(n / vmax * 40)}{mark}")
    return 0


def gzip_open(path, mode):
    """文本模式必须钉死 UTF-8：gzip.open 跟系统编码，Windows 上会把「南北东西干」这类路名
    写成 GBK，导入端按 UTF-8 读就 UnicodeDecodeError。newline='' 同理，否则 \n 变成 \r\n。"""
    import gzip
    if "t" in mode:
        return gzip.open(path, mode, compresslevel=6, encoding="utf-8", newline="")
    return gzip.open(path, mode, compresslevel=6)


if __name__ == "__main__":
    sys.exit(main())
