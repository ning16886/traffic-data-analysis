"""大屏聚合服务：一次请求返回四张指标卡 + 拥堵路段 Top10 + 数据源统计。

口径三条，都是照 data/raw 实测情况定的，改之前先看注释里的数字：

1. 时间粒度用 settings.dashboard_window（默认 1 小时），不用落表的 5 分钟窗。
   50 万点摊到 7 天 518 条路段，每个 5 分钟窗平均只有约 10 条路有指标，Top10 排不满；
   按小时聚合后有指标的路段中位数升到 59 条（最少 2 条是夜间死窗）。
2. 拥堵指数 = 1 − 实际速度 / 自由流速度，不落库、在这里现算。自由流速度按需求第 58 行
   取「该路段历史 7 天最低流量时刻的均速」——这条口径要 7 天数据才算得出来，
   旧版 76 条路段单日数据里 16 条全天只有 1 个窗，用路段自己最低流量窗当分母必然算出
   指数 0，等于把那 16 条路永久判成畅通。7 天内凑不够可信窗的路段退回同等级 p90 兜底。
3. 四张卡片与拥堵路段 Top10 锚在「数据最后一天」，不是当前时刻快照，也不是 7 天全期。
   原始数据时间戳固定在过去 7 天，最后一个 5 分钟窗只剩几辆车几个点，快照口径下大屏会
   显示「在线车辆 1」，看着像坏了；用全期数字又配不上卡片上「今日」这个标题。
   实测末日 2024-03-10：100 辆车 / 71414 个原始点 / 均速 35.1 km/h / 拥堵占比 22.9%
   （当日 275 条有指标路段中 63 条最堵窗指数 > 阈值 0.25）。
   全期（7 天）口径的字段仍一并返回，路段覆盖数与未覆盖数的分母用的是全期口径。
"""

from datetime import timedelta

import pandas as pd
from sqlalchemy import and_, distinct, func, select
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


def _free_flow_speed(g: pd.DataFrame) -> tuple[pd.Series, int]:
    """需求第 58 行：把该路段历史 free_flow_days 天内「最低流量时刻」的均速当自由流基准。

    「最低流量」不能真的只取一个窗：5 分钟窗摊 7 天，流量最小的窗常年是刚过门槛的 5 点孤窗，
    一个偶然样本就能把这条路整周的指数定死（实测最低流量窗的均速中位数 20.5 km/h，
    主干道自由流被压到 19.2，一半路段的拥堵指数直接被 clip 成 0）。
    所以取「流量最低那 1/4 个可信窗」均速的中位数：选的仍然是最低流量的时刻，只是不单独信一个窗。
    - 可信窗门槛沿用 settings.min_points_for_speed，不设门槛等于专挑样本最少的窗。
    - 7 天内凑不够一个可信窗的路段，退回同等级窗速 p90：没有可信分母就不能算拥堵指数。
    """
    end = g["window"].max()
    q = g[(g["point_count"] >= settings.min_points_for_speed)
          & (g["window"] > end - pd.Timedelta(days=settings.free_flow_days))]
    ff = pd.Series(dtype=float)
    if not q.empty:
        quiet_cut = q.groupby("road_id")["point_count"].quantile(0.25)
        quiet = q[q["point_count"] <= q["road_id"].map(quiet_cut)]
        ff = quiet.groupby("road_id")["avg_speed"].median()
        ff = ff[ff > 0]

    roads = g.groupby("road_id")["level"].first()
    missing = roads.index.difference(ff.index)
    fallback = 0
    if len(missing):
        level_p90 = g.groupby("level")["avg_speed"].quantile(0.9)
        fill = roads.loc[missing].map(level_p90).dropna()
        fill = fill[fill > 0]
        ff = pd.concat([ff, fill]).sort_index()
        fallback = int(len(fill))
    return ff, fallback


def build_windows(db: Session) -> pd.DataFrame:
    """把 5 分钟落表按 dashboard_window 重聚合，并算出每个「窗 × 路段」的拥堵指数。"""
    df = _metrics_frame(db)
    if df.empty:
        return df

    df = df.dropna(subset=["avg_speed", "point_count"])
    if df.empty:
        return df

    freq = _FREQ.get(settings.dashboard_window, "1h")
    # 自由流按落表的 5 分钟窗找「最低流量时刻」（需求第 58 行原文口径）：
    # 先重聚合成小时再找，point_count 是 12 个窗的和，min_points_for_speed 门槛形同不存在。
    df["window"] = pd.to_datetime(df["time_window"])
    ff, fallback = _free_flow_speed(df)

    df["disp"] = df["window"].dt.floor(freq)
    # 按点数加权，不是把各 5 分钟窗的均速直接平均
    df["weighted"] = df["avg_speed"] * df["point_count"]
    g = df.groupby(["disp", "road_id"], as_index=False).agg(
        point_count=("point_count", "sum"),
        weighted=("weighted", "sum"),
        road_name=("road_name", "first"),
        level=("level", "first"),
    ).rename(columns={"disp": "window"})
    g["avg_speed"] = g["weighted"] / g["point_count"]

    g["free_flow_speed"] = g["road_id"].map(ff)
    g = g.dropna(subset=["free_flow_speed"])
    g["congestion_index"] = (1 - g["avg_speed"] / g["free_flow_speed"]).clip(0.0, 1.0)
    g.attrs["free_flow_fallback"] = fallback
    return g


def _rankable(windows: pd.DataFrame) -> pd.DataFrame:
    """能进排名与占比的窗：点数不够门槛的窗，均速就是那一辆车的速度，不是路段的状态。"""
    return windows[windows["point_count"] >= settings.min_points_for_speed]


def _worst_per_road(windows: pd.DataFrame) -> pd.DataFrame:
    """每条路段取它样本可信的最堵窗，按指数降序。排名和拥堵占比都走这里。"""
    cand = _rankable(windows)
    if cand.empty:
        return cand
    return cand.sort_values(
        ["congestion_index", "point_count"], ascending=[False, False]
    ).drop_duplicates("road_id")


def _day_scope(windows: pd.DataFrame, day: pd.Timestamp | None) -> pd.DataFrame:
    """切到某个自然日；day 为 None 时返回全期。"""
    if windows.empty or day is None:
        return windows
    return windows[windows["window"].dt.normalize() == day]


def _data_day(windows: pd.DataFrame) -> pd.Timestamp | None:
    """数据里最后一个有指标的自然日，大屏四张卡与 Top10 的时间锚点。"""
    if windows.empty:
        return None
    return windows["window"].max().normalize()


def _top_congested(windows: pd.DataFrame, limit: int, day: pd.Timestamp | None = None) -> list[dict]:
    """降序取前 N 条路段；day 给定则只在那一日内排，None 时排全期。"""
    worst = _worst_per_road(_day_scope(windows, day))
    if worst.empty:
        return []
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


def _day_trajectory_stats(db: Session, day: pd.Timestamp | None) -> dict:
    """某个自然日的轨迹表统计，大屏末日卡片用；左闭右开区间，不做 date() 函数转换。"""
    if day is None:
        return {"vehicles": 0, "raw_points": 0, "clean_points": 0, "avg_speed": 0.0}

    start = day.to_pydatetime()
    end = start + timedelta(days=1)
    raw_at_day = and_(RawTrajectory.timestamp >= start, RawTrajectory.timestamp < end)
    clean_at_day = and_(CleanTrajectory.timestamp >= start, CleanTrajectory.timestamp < end)
    return {
        "vehicles": db.scalar(select(func.count(distinct(RawTrajectory.vehicle_id))).where(raw_at_day)) or 0,
        "raw_points": db.scalar(select(func.count()).select_from(RawTrajectory).where(raw_at_day)) or 0,
        "clean_points": db.scalar(select(func.count()).select_from(CleanTrajectory).where(clean_at_day)) or 0,
        "avg_speed": round(
            float(db.scalar(select(func.avg(CleanTrajectory.speed)).where(clean_at_day)) or 0.0), 2
        ),
    }


def _cards(db: Session, windows: pd.DataFrame, day: pd.Timestamp | None = None) -> dict:
    """全期（7 天）口径 + 数据末日口径。大屏四张卡读 today_* 那组，路段覆盖分母读全期那组。"""
    total_roads = db.scalar(select(func.count()).select_from(RoadSegment)) or 0
    full = _worst_per_road(windows)
    covered = int(len(full))
    congested = int((full["congestion_index"] > settings.congestion_threshold).sum())

    today = _worst_per_road(_day_scope(windows, day))
    today_covered = int(len(today))
    today_congested = int((today["congestion_index"] > settings.congestion_threshold).sum())
    stats = _day_trajectory_stats(db, day)

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
        # ---- 以下按数据最后一天口径，与 Top10 一致 ----
        "data_day": day.strftime("%Y-%m-%d") if day is not None else None,
        "online_vehicle_count": int(stats["vehicles"]),
        "today_point_count": int(stats["raw_points"]),
        "today_clean_point_count": int(stats["clean_points"]),
        "today_avg_speed": stats["avg_speed"],
        "today_covered_road_count": today_covered,
        "today_congested_road_count": today_congested,
        "today_congestion_ratio": round(today_congested / today_covered, 4) if today_covered else 0.0,
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
    day = _data_day(windows)
    start, end = _time_range(db)
    return {
        "data_date": start[:10] if start else None,
        "time_range": {"start": start, "end": end},
        "window": settings.dashboard_window,
        "metrics_window": settings.metrics_window,
        "threshold": settings.congestion_threshold,
        "top_n": top_n,
        "cards": _cards(db, windows, day),
        "top_congested": _top_congested(windows, top_n, day),
        "data_sources": _data_sources(db),
    }
