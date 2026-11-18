"""出行热区服务：活动点提取 → UTM 米制投影 → DBSCAN → 类型判定 → 落库/查询。

口径与 scripts/seed_data.py 生成 data/raw/hotzone.csv 时保持一致，但这里从数据库
现算，供前端「一键重算」使用：

1. 输入不是 48 万清洗点全集，而是「活动点」：匹配成功的停留点、低速点、低速点
   随机抽样、每段轨迹的首尾点。eps=500m 摊在密集路网上，若用全部路过点会把整个
   中心城区糊成两三个巨型簇，热区就没有业务意义了。
2. 经纬度先投到 UTM 50N（EPSG:32650）再喂 DBSCAN——直接把经纬度当米用，
   eps=500 在 40°N 实际是 500m×~85m 的椭圆。
3. 类型口径复用 seed_data.Network.classify：
   - 距环线枢纽锚点 < 700m → transit（交通枢纽）
   - 中心城区 ±2600m × ±1700m → business（商业区）
   - 其余 → residential（居住区）
   环线锚点从 road_segment 的「环线NN段」WKT 首点按编号每 4 段取 1 个恢复，
   不需要额外存一张锚点表。
4. 重算先算完再在一个事务里替换整张 hotzone 表；聚类失败时旧数据不动。
"""

from __future__ import annotations

import math
import time
from datetime import datetime

import numpy as np
import pandas as pd
from pyproj import Transformer
from shapely import wkt
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core import BusinessException, page, settings
from app.models import CleanTrajectory, Hotzone, MatchResult, RoadSegment

ZONE_TYPE_LABELS = {
    "residential": "居住区",
    "business": "商业区",
    "transit": "交通枢纽",
}
ZONE_TYPES = tuple(ZONE_TYPE_LABELS)

# 北京数据落在 UTM 50N（114°E~120°E）；always_xy=True 保证输入顺序是 (lng, lat)。
_UTM = Transformer.from_crs("EPSG:4326", "EPSG:32650", always_xy=True)


def to_utm(longitude, latitude) -> np.ndarray:
    """经纬度 → UTM 米制平面坐标，返回 N×2 数组（x=东向米，y=北向米）。"""
    lng = np.asarray(longitude, dtype=float)
    lat = np.asarray(latitude, dtype=float)
    x, y = _UTM.transform(lng, lat)
    return np.column_stack((np.asarray(x, dtype=float), np.asarray(y, dtype=float)))


def haversine_m(lng1: float, lat1: float, lng2, lat2) -> np.ndarray:
    """WGS-84 球面距离（米），向量化；算簇半径用，避免经纬度直接相减。"""
    p1, p2 = np.radians(float(lat1)), np.radians(np.asarray(lat2, dtype=float))
    dp = (p2 - p1) / 2.0
    dl = (np.radians(np.asarray(lng2, dtype=float)) - np.radians(float(lng1))) / 2.0
    a = np.sin(dp) ** 2 + np.cos(p1) * np.cos(p2) * np.sin(dl) ** 2
    return 2.0 * 6_371_008.8 * np.arcsin(np.sqrt(np.clip(a, 0.0, 1.0)))


def _activity_frame(db: Session, sample_ratio: float) -> pd.DataFrame:
    """从 clean_trajectory ⋈ match_result 取活动点。整表一次读进 pandas，约 46 万行。"""
    stmt = (
        select(
            CleanTrajectory.id.label("clean_id"),
            CleanTrajectory.segment_id,
            CleanTrajectory.speed,
            CleanTrajectory.is_stay,
            CleanTrajectory.longitude,
            CleanTrajectory.latitude,
            MatchResult.road_id,
            MatchResult.is_matched,
        )
        .join(MatchResult, MatchResult.clean_trajectory_id == CleanTrajectory.id)
        .where(MatchResult.is_matched == 1, MatchResult.road_id.is_not(None))
        .order_by(CleanTrajectory.id)
    )
    matched = pd.read_sql_query(stmt, db.get_bind())
    if matched.empty:
        return matched

    slow = matched[matched["speed"] < 12.0]
    sampled = (
        slow.sample(frac=sample_ratio, random_state=settings.hotzone_seed)
        if 0 < sample_ratio < 1 and not slow.empty
        else slow.iloc[0:0]
    )
    pool = pd.concat(
        [
            matched[matched["is_stay"] == 1],
            matched[matched["speed"] < 8.0],
            sampled,
            matched.groupby("segment_id", sort=False).head(1),
            matched.groupby("segment_id", sort=False).tail(1),
        ]
    ).drop_duplicates(subset=["clean_id"])
    return pool.reset_index(drop=True)


def _map_bounds(db: Session) -> dict[str, float]:
    """路网外接矩形，前端用它注册一个轻量 GeoJSON 底图（仓库没有地图瓦片文件）。"""
    row = db.execute(
        select(
            func.min(RoadSegment.bounds_min_lng),
            func.max(RoadSegment.bounds_max_lng),
            func.min(RoadSegment.bounds_min_lat),
            func.max(RoadSegment.bounds_max_lat),
        )
    ).one()
    min_lng, max_lng, min_lat, max_lat = row
    if None in (min_lng, max_lng, min_lat, max_lat):
        min_lng, max_lng, min_lat, max_lat = 116.30, 116.55, 39.85, 40.00
    return {
        "min_lng": float(min_lng),
        "max_lng": float(max_lng),
        "min_lat": float(min_lat),
        "max_lat": float(max_lat),
    }


def _map_context(db: Session) -> tuple[np.ndarray, np.ndarray, float, float]:
    """返回 (环线枢纽锚点 UTM, 路网中心 UTM, 中心经度, 中心纬度)。

    锚点按 seed_data 的做法：环线 24 段，第 0/4/8/12/16/20 段的起点即 6 个枢纽节点。
    若路网不是这套编号（比如用户换了 GeoJSON），退化成沿所有环线顶点等距取 6 个点。
    """
    rows = db.execute(
        select(RoadSegment.road_id, RoadSegment.geometry)
        .where(RoadSegment.road_name.like("环线%"))
        .order_by(RoadSegment.road_id)
    ).all()

    vertices: list[tuple[float, float]] = []
    anchors_lnglat: list[tuple[float, float]] = []
    for idx, (_rid, geometry) in enumerate(rows):
        try:
            coords = list(wkt.loads(geometry).coords)
        except Exception:  # 坏 WKT 不当成致命错误，剩余顶点仍可定位
            continue
        if not coords:
            continue
        vertices.append((float(coords[0][0]), float(coords[0][1])))
        if len(rows) == 24 and idx % 4 == 0:
            anchors_lnglat.append((float(coords[0][0]), float(coords[0][1])))

    if len(rows) != 24 and vertices:
        step = max(1, len(vertices) // 6)
        anchors_lnglat = vertices[::step][:6]

    bounds = _map_bounds(db)
    center_lng = (bounds["min_lng"] + bounds["max_lng"]) / 2.0
    center_lat = (bounds["min_lat"] + bounds["max_lat"]) / 2.0
    anchors = to_utm(*np.asarray(anchors_lnglat, dtype=float).T) if anchors_lnglat else np.empty((0, 2))
    return anchors, to_utm([center_lng], [center_lat])[0], center_lng, center_lat


def _zone_types(
    centers_utm: np.ndarray,
    anchors_utm: np.ndarray,
    center_utm: np.ndarray,
) -> np.ndarray:
    """按枢纽距离 / 中心城区矩形判类型，顺序：transit → business → residential。"""
    kinds = np.full(len(centers_utm), "residential", dtype=object)
    if len(centers_utm) == 0:
        return kinds

    dx = centers_utm[:, 0] - center_utm[0]
    dy = centers_utm[:, 1] - center_utm[1]
    business = (
        (np.abs(dx) < settings.hotzone_business_half_width_m)
        & (np.abs(dy) < settings.hotzone_business_half_height_m)
    )
    kinds[business] = "business"

    if len(anchors_utm):
        dist = np.min(
            np.hypot(
                centers_utm[:, 0][:, None] - anchors_utm[:, 0][None, :],
                centers_utm[:, 1][:, None] - anchors_utm[:, 1][None, :],
            ),
            axis=1,
        )
        kinds[dist < settings.hotzone_transit_radius_m] = "transit"
    return kinds


def compute_zones(
    db: Session,
    eps: float | None = None,
    min_samples: int | None = None,
    max_zones: int | None = None,
    sample_ratio: float | None = None,
    min_points: int | None = None,
) -> tuple[list[dict], dict]:
    """只算不写库。返回 (热区列表, 本次聚类统计)，方便先用脚本验证再落库。"""
    from sklearn.cluster import DBSCAN  # 延迟导入：/list 接口不必付 sklearn 的启动成本

    eps = float(eps if eps is not None else settings.hotzone_dbscan_eps_m)
    min_samples = int(min_samples if min_samples is not None else settings.hotzone_min_samples)
    max_zones = int(max_zones if max_zones is not None else settings.hotzone_max_zones)
    sample_ratio = float(
        sample_ratio if sample_ratio is not None else settings.hotzone_slow_sample_ratio
    )
    min_points = int(min_points if min_points is not None else settings.hotzone_min_points)

    started = time.perf_counter()
    pool = _activity_frame(db, sample_ratio)
    stats: dict = {"activity_points": int(len(pool))}
    if len(pool) < min_samples:
        stats.update({"raw_clusters": 0, "zone_count": 0, "noise_points": int(len(pool)),
                      "elapsed_seconds": round(time.perf_counter() - started, 3)})
        return [], stats

    xy = to_utm(pool["longitude"].to_numpy(), pool["latitude"].to_numpy())
    labels = DBSCAN(eps=eps, min_samples=min_samples).fit_predict(xy)
    stats["raw_clusters"] = int(len(np.unique(labels[labels >= 0])))
    stats["noise_points"] = int((labels < 0).sum())

    lngs = pool["longitude"].to_numpy()
    lats = pool["latitude"].to_numpy()
    rows: list[dict] = []
    for cluster in np.unique(labels[labels >= 0]):
        mask = labels == cluster
        count = int(mask.sum())
        if count < min_points:
            continue
        center_lng = float(lngs[mask].mean())
        center_lat = float(lats[mask].mean())
        radius = float(haversine_m(center_lng, center_lat, lngs[mask], lats[mask]).max())
        rows.append(
            {
                "center_lng": center_lng,
                "center_lat": center_lat,
                # 异常簇（比如环形路网连成一条链）半径可能很大，截断到 6×eps 便于前端画点
                "radius_m": min(radius, eps * 6),
                "point_count": count,
            }
        )

    rows.sort(key=lambda r: (-r["point_count"], r["center_lng"], r["center_lat"]))
    rows = rows[:max_zones]
    if not rows:
        stats.update({"zone_count": 0, "elapsed_seconds": round(time.perf_counter() - started, 3)})
        return [], stats

    anchors_utm, center_utm, _, _ = _map_context(db)
    kinds = _zone_types(to_utm([r["center_lng"] for r in rows], [r["center_lat"] for r in rows]),
                        anchors_utm, center_utm)
    now = datetime.now().replace(microsecond=0)
    for i, (row, kind) in enumerate(zip(rows, kinds)):
        row.update({
            "zone_id": f"HZ_{i:03d}",
            "zone_type": str(kind),
            "create_time": now,
        })

    stats.update({
        "zone_count": len(rows),
        "clustered_points": sum(r["point_count"] for r in rows),
        "elapsed_seconds": round(time.perf_counter() - started, 3),
    })
    return rows, stats


def recompute(
    db: Session,
    eps: float | None = None,
    min_samples: int | None = None,
    max_zones: int | None = None,
    sample_ratio: float | None = None,
    min_points: int | None = None,
) -> dict:
    """重算并整表替换 hotzone。算失败时抛异常，由调用方的 rollback 保住旧数据。"""
    rows, stats = compute_zones(db, eps, min_samples, max_zones, sample_ratio, min_points)
    try:
        db.query(Hotzone).delete()
        if rows:
            db.bulk_insert_mappings(Hotzone, rows)
        db.commit()
    except Exception:
        db.rollback()
        raise

    by_type = {kind: 0 for kind in ZONE_TYPES}
    for row in rows:
        by_type[row["zone_type"]] += 1
    return {
        "zone_count": len(rows),
        "activity_points": stats.get("activity_points", 0),
        "raw_clusters": stats.get("raw_clusters", 0),
        "noise_points": stats.get("noise_points", 0),
        "clustered_points": stats.get("clustered_points", 0),
        "by_type": by_type,
        "params": {
            "eps_m": eps if eps is not None else settings.hotzone_dbscan_eps_m,
            "min_samples": min_samples if min_samples is not None else settings.hotzone_min_samples,
            "min_points": min_points if min_points is not None else settings.hotzone_min_points,
            "max_zones": max_zones if max_zones is not None else settings.hotzone_max_zones,
            "sample_ratio": sample_ratio if sample_ratio is not None else settings.hotzone_slow_sample_ratio,
        },
        "elapsed_seconds": stats.get("elapsed_seconds", 0.0),
        "create_time": rows[0]["create_time"].strftime("%Y-%m-%d %H:%M:%S") if rows else None,
    }


def _zone_dict(zone: Hotzone, rank: int | None = None) -> dict:
    return {
        "id": zone.id,
        "zone_id": zone.zone_id,
        "center_lng": round(float(zone.center_lng), 6),
        "center_lat": round(float(zone.center_lat), 6),
        "radius_m": round(float(zone.radius_m), 1),
        "point_count": int(zone.point_count),
        "zone_type": zone.zone_type,
        "type_label": ZONE_TYPE_LABELS.get(zone.zone_type, zone.zone_type),
        "create_time": zone.create_time.strftime("%Y-%m-%d %H:%M:%S") if zone.create_time else None,
        "rank": rank,
    }


def list_zones(
    db: Session,
    zone_type: str | None = None,
    keyword: str | None = None,
    min_points: int = 0,
    page_no: int = 1,
    page_size: int = 20,
    sort_by: str = "point_count",
) -> dict:
    """分页列表。排序在 SQL 里做，不把 151 条之外的全表拉到前端。"""
    stmt = select(Hotzone)
    if zone_type:
        if zone_type not in ZONE_TYPES:
            raise BusinessException(f"未知的热区类型：{zone_type}")
        stmt = stmt.where(Hotzone.zone_type == zone_type)
    if keyword:
        pattern = f"%{keyword.strip()}%"
        stmt = stmt.where(Hotzone.zone_id.like(pattern))
    if min_points > 0:
        stmt = stmt.where(Hotzone.point_count >= min_points)

    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    order = {
        "point_count": Hotzone.point_count.desc(),
        "radius_m": Hotzone.radius_m.desc(),
        "zone_id": Hotzone.zone_id.asc(),
    }.get(sort_by, Hotzone.point_count.desc())
    rows = db.execute(
        stmt.order_by(order, Hotzone.zone_id.asc()).offset((page_no - 1) * page_size).limit(page_size)
    ).scalars().all()
    start = (page_no - 1) * page_size
    return page([_zone_dict(zone, start + i + 1) for i, zone in enumerate(rows)], total, page_no, page_size)


def get_zone(db: Session, zone_id: str) -> dict:
    zone = db.scalar(select(Hotzone).where(Hotzone.zone_id == zone_id))
    if zone is None:
        raise BusinessException(f"热区 {zone_id} 不存在")
    rank = db.scalar(
        select(func.count()).where(
            (Hotzone.point_count > zone.point_count)
            | ((Hotzone.point_count == zone.point_count) & (Hotzone.zone_id < zone.zone_id))
        )
    )
    return _zone_dict(zone, int(rank or 0) + 1)


def summary(db: Session) -> dict:
    """页面头部卡片 + 地图底图中心；类型计数走一次 groupby。"""
    rows = db.execute(select(Hotzone.zone_type, func.count(), func.coalesce(func.sum(Hotzone.point_count), 0)).group_by(Hotzone.zone_type)).all()
    by_type = {kind: {"zone_count": 0, "point_count": 0} for kind in ZONE_TYPES}
    for kind, count, points in rows:
        by_type[kind] = {"zone_count": int(count), "point_count": int(points)}

    total_zones = sum(v["zone_count"] for v in by_type.values())
    total_points = sum(v["point_count"] for v in by_type.values())
    latest = db.scalar(select(func.max(Hotzone.create_time)))
    _, _, center_lng, center_lat = _map_context(db)

    return {
        "total_zones": total_zones,
        "total_points": total_points,
        "avg_points": round(total_points / total_zones, 1) if total_zones else 0.0,
        "by_type": by_type,
        "type_labels": ZONE_TYPE_LABELS,
        "map_center": {"lng": center_lng, "lat": center_lat},
        "map_bounds": _map_bounds(db),
        "last_recompute": latest.strftime("%Y-%m-%d %H:%M:%S") if latest else None,
        "params": {
            "eps_m": settings.hotzone_dbscan_eps_m,
            "min_samples": settings.hotzone_min_samples,
            "min_points": settings.hotzone_min_points,
        },
    }


def map_zones(db: Session, zone_type: str | None = None) -> list[dict]:
    """给 ECharts 地图用的精简结构：全部/按类型取点，不分页。"""
    stmt = select(Hotzone)
    if zone_type:
        if zone_type not in ZONE_TYPES:
            raise BusinessException(f"未知的热区类型：{zone_type}")
        stmt = stmt.where(Hotzone.zone_type == zone_type)
    rows = db.execute(stmt.order_by(Hotzone.point_count.desc())).scalars().all()
    return [_zone_dict(zone) for zone in rows]


def max_points(db: Session) -> int:
    """前端视觉映射的 value 上限。"""
    return int(db.scalar(select(func.max(Hotzone.point_count))) or 0)


def format_cn(value: float | int) -> str:
    """保留给脚本/日志：大数加千分位。"""
    return f"{int(value):,}" if math.isfinite(float(value)) else "0"
