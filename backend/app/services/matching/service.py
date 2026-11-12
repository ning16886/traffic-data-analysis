"""地图匹配服务层：缓存 + 统计聚合 + 页面载荷。

职责边界：
- 只有这一层知道「结果存在哪」，路由层只管取数据、拼响应；
- 算法参数全部来自 settings.match_*，不在路由里散落魔数；
- 48 万点全量匹配约 10s，结果落盘缓存（backend/out/match_cache/），
  阈值或数据文件变了才重算。页面首次打开不再等十秒。

缓存键 = 阈值 + 数据指纹（clean_trajectory / road_segment 的 mtime+size）+ 算法参数，
任何一个变了都重算，不靠人工删缓存。
"""
from __future__ import annotations

import hashlib
import io
import json
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

from app.core import settings

from .engine import MapMatcher, MatchParams
from .loader import fingerprint, load_clean_points, load_roads
from .network import RoadNetwork

CACHE_VERSION = 3
SORT_KEYS = {
    "vehicleId": "vehicle_id",
    "total": "total",
    "matched": "matched",
    "matchRate": "match_rate",
    "unmatched": "unmatched",
    "roadCount": "road_count",
}


@dataclass
class Dataset:
    """一次全量匹配的产物（阈值维度的快照）。"""

    threshold: float
    params: dict
    vehicles: pd.DataFrame
    summary: dict
    elapsed_ms: int
    created_at: str
    # 与 clean 点一一对应的列式数组
    point_id: np.ndarray
    vehicle_idx: np.ndarray
    ts: np.ndarray
    lng: np.ndarray
    lat: np.ndarray
    speed: np.ndarray
    direction: np.ndarray
    seg_code: np.ndarray
    is_gap: np.ndarray
    is_stay: np.ndarray
    matched: np.ndarray
    road_idx: np.ndarray
    snap_lng: np.ndarray
    snap_lat: np.ndarray
    distance_m: np.ndarray
    probability: np.ndarray
    seg_labels: list[str]
    cache_hit: bool = False


class MatchService:
    """单例：路网常驻内存，数据集按阈值缓存。"""

    def __init__(self, raw_dir: Path | None = None, cache_dir: Path | None = None) -> None:
        self.raw_dir = Path(raw_dir or settings.raw_dir)
        self.cache_dir = Path(cache_dir or settings.match_cache_dir)
        self._lock = threading.RLock()
        self._network: RoadNetwork | None = None
        # 阈值维度的内存缓存：最多留 3 个阈值，来回切阈值不用重算
        self._cache: dict[str, Dataset] = {}
        self._cache_order: list[str] = []
        self._network_payload: list[dict] | None = None
        # 数据源名称 3 行，直接读；不走 network 惰性加载，
        # 否则命中磁盘缓存时永远走不到那段代码，车辆列表会全变「未知数据源」
        self._source_names: dict[int, str] = self._load_source_names()

    # ------------------------------------------------------------ 路网
    @property
    def network(self) -> RoadNetwork:
        with self._lock:
            if self._network is None:
                roads = load_roads(self.raw_dir / "road_segment.csv")
                self._network = RoadNetwork(roads)
                self._source_names = self._load_source_names()
            return self._network

    def _load_source_names(self) -> dict[int, str]:
        path = self.raw_dir / "data_source.csv"
        if not path.exists():
            return {}
        df = pd.read_csv(path, encoding="utf-8-sig")
        return {int(r.id): str(r.name) for r in df.itertuples() if hasattr(r, "name")}

    def source_name(self, source_id: int) -> str:
        return self._source_names.get(int(source_id), "未知数据源")

    # ------------------------------------------------------------ 参数
    def params(self, threshold: float) -> MatchParams:
        return MatchParams(
            threshold_m=float(threshold),
            sigma_m=settings.match_sigma_m,
            beta_m=settings.match_beta_m,
            max_candidates=settings.match_max_candidates,
            candidate_radius_m=settings.match_candidate_radius_m,
            direction_weight=settings.match_direction_weight,
            level_weight=settings.match_level_weight,
        )

    # ------------------------------------------------------------ 数据集
    def dataset(self, threshold: float, refresh: bool = False) -> Dataset:
        th = float(threshold)
        key = self._cache_key(th)
        with self._lock:
            if not refresh and key in self._cache:
                ds = self._cache[key]
                ds.cache_hit = True
                return ds

            cached = None if refresh else self._read_cache(th, key)
            if cached is not None:
                cached.cache_hit = True
                self._remember(key, cached)
                return cached

            ds = self._compute(th)
            self._write_cache(ds, key)
            self._remember(key, ds)
            return ds

    def _remember(self, key: str, ds: Dataset) -> None:
        """内存里最多留 3 个阈值的结果（48 万点约 60MB/份），超了淘汰最旧的。"""
        self._cache[key] = ds
        if key in self._cache_order:
            self._cache_order.remove(key)
        self._cache_order.append(key)
        while len(self._cache_order) > 3:
            drop = self._cache_order.pop(0)
            self._cache.pop(drop, None)

    def _cache_key(self, threshold: float) -> str:
        fp = fingerprint(self.raw_dir / "clean_trajectory.csv", self.raw_dir / "road_segment.csv")
        payload = json.dumps(
            {"v": CACHE_VERSION, "t": round(threshold, 2), "fp": fp,
             "p": self.params(threshold).as_dict()},
            sort_keys=True,
        )
        return hashlib.md5(payload.encode()).hexdigest()[:16]

    # ------------------------------------------------------------ 计算
    def _compute(self, threshold: float) -> Dataset:
        t0 = time.perf_counter()
        points = load_clean_points(self.raw_dir / "clean_trajectory.csv")
        params = self.params(threshold)
        matcher = MapMatcher(self.network, params)
        result = matcher.match(
            points["longitude"].to_numpy(),
            points["latitude"].to_numpy(),
            directions=points["direction"].to_numpy(dtype=float),
            trip_ids=points["trip_seq"].to_numpy(),
            threshold_m=threshold,
        )
        elapsed = int((time.perf_counter() - t0) * 1000)

        vehicles = points["vehicle_id"].to_numpy()
        # 排序后再编号：与下面 groupby(sort=True) 的行序严格一致，
        # 前端拿 vehicle_idx 就能直接索引 ds.vehicles，不用再查一次字典
        vehicle_ids = np.sort(pd.unique(vehicles))
        vid_index = {v: i for i, v in enumerate(vehicle_ids)}
        vehicle_idx = np.fromiter((vid_index[v] for v in vehicles), dtype=np.int16, count=vehicles.size)

        seg_labels = sorted(points["segment_id"].unique().tolist())
        seg_code = pd.Categorical(points["segment_id"], categories=seg_labels).codes.astype(np.int32)

        matched_roads = pd.Series(result.road_idx[result.matched]).value_counts()
        df = pd.DataFrame({
            "vehicle_id": vehicles,
            "matched": result.matched.astype(np.int8),
            "road_idx": result.road_idx,
        })
        g = df.groupby("vehicle_id", sort=True)
        stats = pd.DataFrame({
            "total": g.size(),
            "matched": g["matched"].sum(),
        })
        stats["unmatched"] = stats["total"] - stats["matched"]
        stats["match_rate"] = stats["matched"] / stats["total"]
        roads_per_vehicle = (
            df[df.matched == 1].groupby("vehicle_id")["road_idx"].nunique().reindex(stats.index).fillna(0)
        )
        stats["road_count"] = roads_per_vehicle.astype(int)

        first_ts = points.groupby("vehicle_id")["timestamp"].min()
        last_ts = points.groupby("vehicle_id")["timestamp"].max()
        src = points.groupby("vehicle_id")["source_id"].first()
        stats["source_id"] = src.reindex(stats.index).fillna(0).astype(int)
        stats["first_ts"] = first_ts
        stats["last_ts"] = last_ts
        stats = stats.reset_index()

        total = int(len(points))
        matched = int(result.matched.sum())
        summary = {
            "totalPoints": total,
            "matched": matched,
            "unmatched": total - matched,
            "matchRate": round(matched / total, 4) if total else 0.0,
            "roadCount": int(len(matched_roads)),
            "totalRoadCount": self.network.n_roads,
            "segmentCount": self.network.n_segments,
            "vehicleCount": int(len(vehicle_ids)),
            "tripCount": int(len(seg_labels)),
            "avgDistanceM": round(float(np.mean(result.distance_m[result.matched])), 2) if matched else None,
            "gapPoints": int(points["is_gap"].sum()),
            "stayPoints": int(points["is_stay"].sum()),
            "dataRange": {
                "start": _fmt(points["timestamp"].min()),
                "end": _fmt(points["timestamp"].max()),
            },
        }

        return Dataset(
            threshold=float(threshold),
            params=params.as_dict(),
            vehicles=stats,
            summary=summary,
            elapsed_ms=elapsed,
            created_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            point_id=points["id"].to_numpy(dtype=np.int64),
            vehicle_idx=vehicle_idx,
            ts=points["timestamp"].to_numpy(dtype="datetime64[ns]").astype(np.int64),
            lng=points["longitude"].to_numpy(dtype=float),
            lat=points["latitude"].to_numpy(dtype=float),
            speed=points["speed"].to_numpy(dtype=np.float32),
            direction=points["direction"].to_numpy(dtype=np.float32),
            seg_code=seg_code,
            is_gap=points["is_gap"].to_numpy(dtype=bool),
            is_stay=points["is_stay"].to_numpy(dtype=bool),
            matched=result.matched,
            road_idx=result.road_idx,
            snap_lng=result.snap_lng,
            snap_lat=result.snap_lat,
            distance_m=result.distance_m,
            probability=result.probability,
            seg_labels=seg_labels,
        )

    # ------------------------------------------------------------ 缓存落盘
    def _cache_path(self, key: str) -> Path:
        return self.cache_dir / f"match_{key}.npz"

    def _write_cache(self, ds: Dataset, key: str) -> None:
        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            meta = {
                "threshold": ds.threshold,
                "params": ds.params,
                "summary": ds.summary,
                "elapsed_ms": ds.elapsed_ms,
                "created_at": ds.created_at,
                "seg_labels": ds.seg_labels,
            }
            np.savez_compressed(
                self._cache_path(key),
                point_id=ds.point_id, vehicle_idx=ds.vehicle_idx, ts=ds.ts,
                lng=ds.lng, lat=ds.lat, speed=ds.speed, direction=ds.direction,
                seg_code=ds.seg_code, is_gap=ds.is_gap, is_stay=ds.is_stay,
                matched=ds.matched, road_idx=ds.road_idx,
                snap_lng=ds.snap_lng, snap_lat=ds.snap_lat,
                distance_m=ds.distance_m, probability=ds.probability,
                vehicles=ds.vehicles.to_csv(index=False).encode("utf-8"),
                meta=json.dumps(meta, ensure_ascii=False).encode(),
            )
            # 磁盘上留最近 3 个阈值，来回切换阈值不用再等 5s 重算
            files = sorted(self.cache_dir.glob("match_*.npz"), key=lambda p: p.stat().st_mtime, reverse=True)
            for old in files[3:]:
                old.unlink(missing_ok=True)
        except OSError:
            # 缓存写不进去只影响启动速度，不影响功能，静默跳过
            pass

    def _read_cache(self, threshold: float, key: str) -> Dataset | None:
        path = self._cache_path(key)
        if not path.exists():
            return None
        try:
            with np.load(path, allow_pickle=False) as z:
                meta = json.loads(z["meta"].tobytes().decode())
                vehicles = pd.read_csv(
                    io.BytesIO(z["vehicles"].tobytes()), encoding="utf-8",
                    parse_dates=["first_ts", "last_ts"],
                )
                return Dataset(
                    threshold=float(threshold),
                    params=meta["params"],
                    vehicles=vehicles,
                    summary=meta["summary"],
                    elapsed_ms=int(meta["elapsed_ms"]),
                    created_at=meta["created_at"],
                    point_id=z["point_id"], vehicle_idx=z["vehicle_idx"], ts=z["ts"],
                    lng=z["lng"], lat=z["lat"], speed=z["speed"], direction=z["direction"],
                    seg_code=z["seg_code"], is_gap=z["is_gap"], is_stay=z["is_stay"],
                    matched=z["matched"], road_idx=z["road_idx"],
                    snap_lng=z["snap_lng"], snap_lat=z["snap_lat"],
                    distance_m=z["distance_m"], probability=z["probability"],
                    seg_labels=list(meta["seg_labels"]),
                )
        except (OSError, ValueError, KeyError):
            return None

    # ------------------------------------------------------------ 查询
    def vehicles_page(
        self,
        threshold: float,
        keyword: str = "",
        page_no: int = 1,
        page_size: int = 20,
        sort_by: str = "vehicleId",
        order: str = "asc",
    ) -> dict:
        ds = self.dataset(threshold)
        df = ds.vehicles
        if keyword:
            kw = keyword.strip().lower()
            df = df[df["vehicle_id"].str.lower().str.contains(kw, regex=False)]
        col = SORT_KEYS.get(sort_by, "vehicle_id")
        df = df.sort_values(col, ascending=(order != "desc"), kind="stable")
        total = int(len(df))
        page_no = max(1, int(page_no))
        page_size = max(1, int(page_size))
        chunk = df.iloc[(page_no - 1) * page_size: page_no * page_size]
        items = [
            {
                "vehicleId": r.vehicle_id,
                "sourceName": self.source_name(r.source_id),
                "total": int(r.total),
                "matched": int(r.matched),
                "unmatched": int(r.unmatched),
                "matchRate": round(float(r.match_rate), 4),
                "roadCount": int(r.road_count),
                "firstTs": _fmt(r.first_ts),
                "lastTs": _fmt(r.last_ts),
            }
            for r in chunk.itertuples()
        ]
        return {
            "threshold": ds.threshold,
            "summary": ds.summary,
            "params": ds.params,
            "elapsedMs": ds.elapsed_ms,
            "cachedAt": ds.created_at,
            "cacheHit": ds.cache_hit,
            "items": items,
            "total": total,
            "page": page_no,
            "pageSize": page_size,
            "pages": (total + page_size - 1) // page_size if page_size else 0,
        }

    def track(self, threshold: float, vehicle_id: str, segment_id: str | None = None) -> dict | None:
        """单车三层数据：点（含匹配/未匹配标志）+ 命中路段几何 + 行程列表。"""
        ds = self.dataset(threshold)
        row = ds.vehicles[ds.vehicles["vehicle_id"] == vehicle_id]
        if row.empty:
            return None
        info = row.iloc[0]

        pos = int(np.searchsorted(ds.vehicles["vehicle_id"].to_numpy(), vehicle_id))
        if pos >= len(ds.vehicles) or ds.vehicles["vehicle_id"].iloc[pos] != vehicle_id:
            return None
        mask = ds.vehicle_idx == pos
        if segment_id:
            codes = [i for i, s in enumerate(ds.seg_labels) if s == segment_id]
            if codes:
                mask &= ds.seg_code == codes[0]

        idx = np.flatnonzero(mask)
        network = self.network
        points = []
        for i in idx:
            # 未匹配点不挂路段：命中候选但垂距超阈值的情况也按未匹配处理，
            # 否则 tooltip 会给红点显示一个「匹配到的路名」，与图例自相矛盾
            road_i = int(ds.road_idx[i]) if ds.matched[i] else -1
            seg_label = ds.seg_labels[int(ds.seg_code[i])]
            points.append({
                "id": int(ds.point_id[i]),
                "lng": round(float(ds.lng[i]), 6),
                "lat": round(float(ds.lat[i]), 6),
                "ts": _fmt_ns(int(ds.ts[i])),
                "speed": _round_or_none(ds.speed[i], 1),
                "direction": _round_or_none(ds.direction[i], 1),
                "isGap": bool(ds.is_gap[i]),
                "isStay": bool(ds.is_stay[i]),
                "segmentId": seg_label,
                "matched": bool(ds.matched[i]),
                "roadId": network.road(road_i).road_id if road_i >= 0 else None,
                "roadName": network.road(road_i).road_name if road_i >= 0 else None,
                "level": network.road(road_i).level if road_i >= 0 else None,
                "snapLng": round(float(ds.snap_lng[i]), 6),
                "snapLat": round(float(ds.snap_lat[i]), 6),
                "distanceM": _round_or_none(ds.distance_m[i], 2),
                "probability": round(float(ds.probability[i]), 4),
            })

        roads = self._roads_payload(ds, idx)
        trips = self._trips_payload(ds, idx)
        matched = int(ds.matched[idx].sum())
        return {
            "vehicleId": vehicle_id,
            "sourceName": self.source_name(int(info.source_id)),
            "threshold": ds.threshold,
            "stats": {
                "total": int(len(idx)),
                "matched": matched,
                "unmatched": int(len(idx) - matched),
                "matchRate": round(matched / len(idx), 4) if len(idx) else 0.0,
                "roadCount": len(roads),
                "tripCount": len(trips),
            },
            "points": points,
            "roads": roads,
            "trips": trips,
            "network": self.network_payload(),
        }

    def _roads_payload(self, ds: Dataset, idx: np.ndarray) -> list[dict]:
        """只返回这条轨迹“走过”的路段：全路网 518 条画上去图就没法看了。"""
        network = self.network
        used = np.unique(ds.road_idx[idx][ds.matched[idx]])
        used = used[used >= 0]
        counts = pd.Series(ds.road_idx[idx][ds.matched[idx]]).value_counts()
        out = []
        for ri in used:
            road = network.road(int(ri))
            out.append({
                "roadId": road.road_id,
                "roadName": road.road_name,
                "level": road.level,
                "coords": network.geometry(int(ri)),
                "pointCount": int(counts.get(int(ri), 0)),
            })
        out.sort(key=lambda r: -r["pointCount"])
        return out

    def network_payload(self) -> list[dict]:
        """全路网几何：前端拿它画浅色底图，否则只画「走过」的 90 条路，图看着像没路网。"""
        with self._lock:
            if self._network_payload is None:
                network = self.network
                self._network_payload = [
                    {"roadId": r.road_id, "level": r.level, "coords": network.geometry(i)}
                    for i, r in enumerate(network.roads)
                ]
            return self._network_payload

    def _trips_payload(self, ds: Dataset, idx: np.ndarray) -> list[dict]:
        if len(idx) == 0:
            return []
        codes = ds.seg_code[idx]
        out = []
        for code in pd.unique(codes):
            sel = idx[codes == code]
            if sel.size == 0:
                continue
            m = int(ds.matched[sel].sum())
            out.append({
                "segmentId": ds.seg_labels[int(code)],
                "start": _fmt_ns(int(ds.ts[sel[0]])),
                "end": _fmt_ns(int(ds.ts[sel[-1]])),
                "total": int(sel.size),
                "matched": m,
                "unmatched": int(sel.size - m),
                "matchRate": round(m / sel.size, 4),
                "startPoint": [round(float(ds.lng[sel[0]]), 6), round(float(ds.lat[sel[0]]), 6)],
                "endPoint": [round(float(ds.lng[sel[-1]]), 6), round(float(ds.lat[sel[-1]]), 6)],
            })
        out.sort(key=lambda t: t["start"] or "")
        return out

    def rematch(self, threshold: float) -> dict:
        ds = self.dataset(threshold, refresh=True)
        return {
            "threshold": ds.threshold,
            "summary": ds.summary,
            "elapsedMs": ds.elapsed_ms,
            "cachedAt": ds.created_at,
        }


def _fmt(value) -> str | None:
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return None
    if isinstance(value, pd.Timestamp):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    return str(value)


def _fmt_ns(ns: int) -> str | None:
    if ns <= 0:
        return None
    return pd.Timestamp(int(ns)).strftime("%Y-%m-%d %H:%M:%S")


def _round_or_none(value, digits: int):
    v = float(value)
    if not np.isfinite(v):
        return None
    return round(v, digits)


_service: MatchService | None = None
_service_lock = threading.Lock()


def get_service() -> MatchService:
    """FastAPI 依赖注入用；模块级单例，进程内共享缓存。"""
    global _service
    with _service_lock:
        if _service is None:
            _service = MatchService()
        return _service
