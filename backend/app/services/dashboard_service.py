"""大屏聚合服务：一次请求返回四张指标卡 + 拥堵路段 Top10 + 数据源统计。

口径三条，都是照 data/raw 实测情况定的，改之前先看注释里的数字：

1. 时间粒度用 settings.dashboard_window（默认 1 小时），不用落表的 5 分钟窗。
   实测每个 5 分钟窗平均只有 4 条路段有指标（最多 10 条），Top10 在 36 个窗里
   只有 1 个窗排得满；按小时聚合后有指标的路段中位数升到 10 条。
2. 拥堵指数 = 1 − 实际速度 / 自由流速度，不落库、在这里现算。自由流速度取
   「同等级路段当天最快小时窗均速的 p90」，不是需求原文的「该路段最低流量时刻
   均速」：48 条有指标的路段里 16 条全天只出现 1 个窗，用路段自己最快窗当分母
   必然算出指数 0，等于把这 16 条路永久判成畅通。
3. 四张卡片是全天汇总，不是当前时刻快照。原始数据时间戳固定在 2024-03-01，
   最后一个 5 分钟窗只剩 1 辆车 16 个点，快照口径下大屏会显示「在线车辆 1」，
   看着像坏了。
"""

import pandas as pd
from sqlalchemy import distinct, func, select
from sqlalchemy.orm import Session

from app.core import settings
from app.models import CleanTrajectory, DataSource, RawTrajectory, RoadMetrics, RoadSegment

# settings.dashboard_window → pandas freq
_FREQ = {"5min": "5min", "15min": "15min", "30min": "30min", "1h": "1h", "1d": "1D"}


def _metrics_frame(db: Session) -> pd.DataFrame:
    """road_metrics 联 road_segment 取等级与路名，一次查全。"""
    stmt = select(
        RoadMetrics.road_id,
        RoadMetrics.time_window,
        RoadMetrics.avg_speed,
        RoadMetrics.point_count,
        RoadSegment.road_name,
        RoadSegment.level,
    ).join(RoadSegment, RoadSegment.road_id == RoadMetrics.road_id)
    return pd.read_sql_query(stmt, db.get_bind())


def build_windows(db: Session) -> pd.DataFrame:
    """把 5 分钟落表按 dashboard_window 重聚合，并算出每个「窗 × 路段」的拥堵指数。"""
    df = _metrics_frame(db)
    if df.empty:
        return df

    df = df.dropna(subset=["avg_speed", "point_count"])
    if df.empty:
        return df

    freq = _FREQ.get(settings.dashboard_window, "1h")
    df["window"] = pd.to_datetime(df["time_window"]).dt.floor(freq)
    # 按点数加权，不是把各 5 分钟窗的均速直接平均
    df["weighted"] = df["avg_speed"] * df["point_count"]
    g = df.groupby(["window", "road_id"], as_index=False).agg(
        point_count=("point_count", "sum"),
        weighted=("weighted", "sum"),
        road_name=("road_name", "first"),
        level=("level", "first"),
    )
    g["avg_speed"] = g["weighted"] / g["point_count"]

    fastest = g.groupby("road_id").agg(free_flow=("avg_speed", "max"), level=("level", "first"))
    level_free_flow = fastest.groupby("level")["free_flow"].quantile(0.9)
    g["free_flow_speed"] = g["level"].map(level_free_flow)
    g["congestion_index"] = (1 - g["avg_speed"] / g["free_flow_speed"]).clip(0.0, 1.0)
    return g


def _top_congested(windows: pd.DataFrame, limit: int) -> list[dict]:
    """每条路段取它当天最堵的那个窗，降序取前 N。"""
    if windows.empty:
        return []
    worst = windows.sort_values(
        ["congestion_index", "point_count"], ascending=[False, False]
    ).drop_duplicates("road_id")
    return [
        {
            "road_id": row.road_id,
            "road_name": row.road_name,
            "level": row.level,
            "window": row.window.strftime("%Y-%m-%d %H:%M"),
            "avg_speed": round(float(row.avg_speed), 2),
            "free_flow_speed": round(float(row.free_flow_speed), 2),
            "congestion_index": round(float(row.congestion_index), 4),
            "point_count": int(row.point_count),
        }
        for row in worst.head(limit).itertuples()
    ]


def _cards(db: Session, windows: pd.DataFrame) -> dict:
    total_roads = db.scalar(select(func.count()).select_from(RoadSegment)) or 0
    covered = int(windows["road_id"].nunique()) if not windows.empty else 0
    congested = 0
    if not windows.empty:
        worst = windows.sort_values("congestion_index", ascending=False).drop_duplicates("road_id")
        congested = int((worst["congestion_index"] > settings.congestion_threshold).sum())

    return {
        "vehicle_count": db.scalar(select(func.count(distinct(RawTrajectory.vehicle_id)))) or 0,
        "raw_point_count": db.scalar(select(func.count()).select_from(RawTrajectory)) or 0,
        "clean_point_count": db.scalar(select(func.count()).select_from(CleanTrajectory)) or 0,
        "avg_speed": round(float(db.scalar(select(func.avg(CleanTrajectory.speed))) or 0.0), 2),
        "total_road_count": int(total_roads),
        "covered_road_count": covered,
        # 分母是「当前有指标的路段」，不是全部路段：没有数据的路段算不出拥堵
        "congestion_ratio": round(congested / covered, 4) if covered else 0.0,
        "congested_road_count": congested,
        "uncovered_road_count": int(total_roads) - covered,
    }


def _data_sources(db: Session) -> list[dict]:
    """登记表自报数与 raw_trajectory 实测数一起返回，对不上账时前端显示实测。"""
    actual = db.execute(
        select(
            RawTrajectory.source_id,
            func.count().label("points"),
            func.count(distinct(RawTrajectory.vehicle_id)).label("vehicles"),
        ).group_by(RawTrajectory.source_id)
    ).all()
    counts = {row.source_id: (row.points, row.vehicles) for row in actual}

    rows = db.execute(select(DataSource).order_by(DataSource.id)).scalars().all()
    return [
        {
            "id": r.id,
            "name": r.name,
            "type": r.type,
            "description": r.description,
            "status": r.status,
            "point_count": counts.get(r.id, (0, 0))[0],
            "vehicle_count": counts.get(r.id, (0, 0))[1],
            "declared_point_count": r.point_count,
            "declared_vehicle_count": r.vehicle_count,
        }
        for r in rows
    ]


def _time_range(db: Session) -> tuple[str | None, str | None]:
    start = db.scalar(select(func.min(CleanTrajectory.timestamp)))
    end = db.scalar(select(func.max(CleanTrajectory.timestamp)))
    return (
        start.strftime("%Y-%m-%d %H:%M:%S") if start else None,
        end.strftime("%Y-%m-%d %H:%M:%S") if end else None,
    )


def build_summary(db: Session, top_n: int = 10) -> dict:
    windows = build_windows(db)
    start, end = _time_range(db)
    return {
        "data_date": start[:10] if start else None,
        "time_range": {"start": start, "end": end},
        "window": settings.dashboard_window,
        "metrics_window": settings.metrics_window,
        "threshold": settings.congestion_threshold,
        "top_n": top_n,
        "cards": _cards(db, windows),
        "top_congested": _top_congested(windows, top_n),
        "data_sources": _data_sources(db),
    }
