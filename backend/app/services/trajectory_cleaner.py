"""轨迹清洗管道：分段 → 漂移剔除 → 停留标记 → 隧道空洞标记。

输入是 raw_trajectory（与路网同坐标系，接入层不做转换），输出 clean_trajectory。
四条规则全部走 pandas 向量化，不做逐车 Python 循环。

规则口径（阈值集中在 config.Settings，便于按需求调参）：
- 分段：相邻点间隔 > trip_break_seconds 视为另一次出行，segment_id = 车辆ID_序号。
  分段是一切下游计算的前提——匹配和 OD 都不能跨段。阈值必须夹在
  「隧道空洞 200~250s」与「车辆停放 ≥600s」之间，默认 400s。
- 漂移（需求文档原话）：speed > 120 km/h，或与上一点距离 > 200m 且间隔 < 10s，
  两条取或，判为漂移点并**删除**。一趟算完，不依赖顺序状态。
- 停留：回看 stay_seconds 时长内位移始终 < stay_radius_m 的窗口，
  把该窗口覆盖的整段点都标 is_stay=1（差分数组批量染色），保留点。
- 隧道：同一行程内间隔 > gap_seconds ⇒ 空洞后的首个点 is_gap=1、flag='tunnel'，保留点。

停留与隧道都只打标记不删点：需求要求脏数据「打标记保留」，
只有漂移点会真的从清洗表里消失。
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import settings
from app.core.database import SessionLocal
from app.models.clean_trajectory import CleanTrajectory
from app.models.raw_trajectory import RawTrajectory

EARTH_R = 6371008.8  # 米，平均半径

READ_SQL = """
SELECT id, vehicle_id, source_id, `timestamp`, longitude, latitude, speed, direction
FROM raw_trajectory
{where}
ORDER BY vehicle_id, `timestamp`, id
"""


def _haversine_m(lng1, lat1, lng2, lat2) -> np.ndarray:
    """两点大圆距离（米），全向量化。"""
    p1 = np.radians(lat1)
    p2 = np.radians(lat2)
    dp = (p2 - p1) / 2.0
    dl = np.radians(lng2 - lng1) / 2.0
    a = np.sin(dp) ** 2 + np.cos(p1) * np.cos(p2) * np.sin(dl) ** 2
    return 2.0 * EARTH_R * np.arcsin(np.sqrt(np.clip(a, 0.0, 1.0)))


def _mark_drift(df: pd.DataFrame) -> np.ndarray:
    """需求文档规则一：speed > 120 km/h，或「与上一点距离 > 200m 且间隔 < 10s」。

    两条取或，一趟算完，不依赖「上一个保留点」这种顺序状态。
    """
    lng = df["longitude"].to_numpy(dtype=float)
    lat = df["latitude"].to_numpy(dtype=float)
    ts = df["timestamp"].to_numpy(dtype="datetime64[us]")
    veh = df["vehicle_id"].to_numpy()
    speed = df["speed"].to_numpy(dtype=float)

    same_vehicle = np.concatenate(([False], veh[1:] == veh[:-1]))
    dt_s = np.full(len(df), np.inf)
    dt_s[1:] = np.where(
        same_vehicle[1:], (ts[1:] - ts[:-1]) / np.timedelta64(1, "s"), np.inf
    )
    dist_m = np.full(len(df), np.inf)
    dist_m[1:] = np.where(
        same_vehicle[1:],
        _haversine_m(lng[:-1], lat[:-1], lng[1:], lat[1:]),
        np.inf,
    )

    by_speed = speed > settings.drift_speed_kmh

    # 尖峰判据：与前后邻居都「距离>200m 且间隔<10s」才判漂移。
    # 只查前一个邻居会把漂移点后面那个正常点一起删掉（实测多删 6281 点）。
    back_bad = same_vehicle & (dist_m > settings.drift_jump_m) & (dt_s < settings.drift_jump_seconds)
    fwd_bad = np.append(back_bad[1:], False)  # 位置 i：与下一点是否也构成跳变
    by_jump = back_bad & fwd_bad

    # 每车首末点只有一个邻居，退化为单侧判据：首点看后向、尾点看前向，
    # 否则真尖峰正好落在端点时会漏。
    starts = np.concatenate(([True], veh[1:] != veh[:-1]))
    ends = np.concatenate((veh[1:] != veh[:-1], [True]))
    single_side = (starts & fwd_bad) | (ends & back_bad)

    return by_speed | by_jump | single_side


def clean_dataframe(raw: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """清洗主流程。输入 raw_trajectory 行，返回 (待写表 DataFrame, 统计)。"""
    if raw.empty:
        return raw.copy(), {"raw_count": 0, "clean_count": 0}

    df = raw.sort_values(["vehicle_id", "timestamp", "id"], kind="stable").reset_index(drop=True)
    n_raw = len(df)

    dropped = _mark_drift(df)
    df = df.loc[~dropped].reset_index(drop=True)
    n_drift = int(dropped.sum())

    ts = df["timestamp"].to_numpy(dtype="datetime64[us]")
    lng = df["longitude"].to_numpy(dtype=float)
    lat = df["latitude"].to_numpy(dtype=float)
    veh = df["vehicle_id"].to_numpy()

    prev_veh = np.concatenate(([False], veh[1:] == veh[:-1]))
    dt_s = np.concatenate(
        [[np.nan], (ts[1:] - ts[:-1]) / np.timedelta64(1, "s")]
    )
    dt_s = np.where(prev_veh, dt_s, np.nan)

    # ---- 分段：换车或间隔超阈值都开新段 ----
    is_break = (~prev_veh) | (dt_s > settings.trip_break_seconds)
    seg_seq = np.cumsum(is_break)
    df["segment_id"] = np.char.add(
        np.char.add(veh.astype(str), "_"), seg_seq.astype(str)
    )

    # ---- 隧道空洞：同段内间隔超 gap_seconds 的后一个点 ----
    same_seg = np.concatenate(([False], seg_seq[1:] == seg_seq[:-1]))
    is_gap = same_seg & (dt_s > settings.gap_seconds)
    df["is_gap"] = is_gap.astype(int)
    df["flag"] = np.where(is_gap, "tunnel", "ok")

    # ---- 停留：回看 stay_seconds 时长内的位移上限 ----
    # 对每个点 i，取回看窗口内最早的那个点 j，用 i 与 j 的距离判「这段时间有没有走开」。
    # 时间戳只在单车内有序（整体按 vehicle_id 排），所以 searchsorted 必须按车分片调用。
    back = float(settings.stay_seconds)
    back_us = np.timedelta64(int(back * 1_000_000), "us")
    j = np.arange(len(df))
    starts = np.flatnonzero(np.concatenate(([True], veh[1:] != veh[:-1])))
    ends = np.concatenate((starts[1:], [len(df)]))
    for lo, hi in zip(starts, ends):
        block = ts[lo:hi]
        j[lo:hi] = lo + np.searchsorted(block, block - back_us, side="left")

    sel = j < np.arange(len(df))
    span_m = np.full(len(df), np.inf)
    span_m[sel] = _haversine_m(lng[j[sel]], lat[j[sel]], lng[sel], lat[sel])
    span_s = (ts - ts[j]) / np.timedelta64(1, "s")
    # j 是窗口内最早的点，故静止车辆的 span 最多比 back 短一个采样间隔；15s 容差覆盖 10s 采样
    window_stayed = sel & (span_m < settings.stay_radius_m) & (span_s >= back - 15.0)
    # 点 i 静止 ⇒ [j(i), i] 整段都是停留点。用差分数组批量区间染色，不逐点循环。
    diff = np.zeros(len(df) + 1, dtype=np.int32)
    ends_i = np.nonzero(window_stayed)[0]
    np.add.at(diff, j[ends_i], 1)
    np.add.at(diff, ends_i + 1, -1)
    is_stay = np.cumsum(diff[:-1]) > 0
    df["is_stay"] = is_stay.astype(int)

    # 停留事件数 = 连续停留段的首点个数（供报告用，不落表）
    prev_stay = np.zeros(len(df), dtype=bool)
    prev_stay[1:] = is_stay[:-1]
    stay_events = int(np.sum(is_stay & ~prev_stay))
    gap_events = int(np.sum(df["is_gap"].to_numpy()))

    out = df[
        [
            "vehicle_id",
            "source_id",
            "segment_id",
            "timestamp",
            "longitude",
            "latitude",
            "speed",
            "direction",
            "is_stay",
            "is_gap",
            "flag",
        ]
    ].copy()
    out["direction"] = out["direction"].astype(float).round(1)

    stats = {
        "raw_count": n_raw,
        "clean_count": len(out),
        "drift_removed": n_drift,
        "stay_points": int(is_stay.sum()),
        "stay_events": stay_events,
        "gap_points": gap_events,
        "segments": int(len(set(seg_seq.tolist()))),
        "vehicles": int(df["vehicle_id"].nunique()),
    }
    return out, stats


def read_raw(
    db: Session,
    source_ids: list[int] | None = None,
    vehicle_ids: list[str] | None = None,
) -> pd.DataFrame:
    conds = []
    params: dict[str, object] = {}
    if source_ids:
        conds.append("source_id IN :sources")
        params["sources"] = list(source_ids)
    if vehicle_ids:
        conds.append("vehicle_id IN :vehicles")
        params["vehicles"] = list(vehicle_ids)
    where = ("WHERE " + " AND ".join(conds)) if conds else ""
    sql = text(READ_SQL.format(where=where))
    return pd.read_sql(sql, db.bind, params=params)


def save_clean(df: pd.DataFrame, db: Session, chunk_rows: int = 50_000, reset: bool = True) -> int:
    if reset:
        db.execute(text("DELETE FROM clean_trajectory"))
        db.commit()
    recs = df.to_dict("records")
    for r in recs:
        r["timestamp"] = r["timestamp"].to_pydatetime()
        r["source_id"] = int(r["source_id"])
        r["is_stay"] = int(r["is_stay"])
        r["is_gap"] = int(r["is_gap"])
        if pd.isna(r.get("direction")):
            r["direction"] = None
    written = 0
    for i in range(0, len(recs), chunk_rows):
        db.bulk_insert_mappings(CleanTrajectory, recs[i : i + chunk_rows])
        db.commit()
        written += chunk_rows if i + chunk_rows < len(recs) else len(recs) - i
    return written


def run_cleaning(
    db: Session | None = None,
    source_ids: list[int] | None = None,
    vehicle_ids: list[str] | None = None,
    reset: bool = True,
) -> dict:
    """完整清洗任务：读原始表 → 向量化清洗 → 写清洗表。供路由与调度器共用。"""
    own = db is None
    if own:
        db = SessionLocal()
    try:
        raw = read_raw(db, source_ids=source_ids, vehicle_ids=vehicle_ids)
        clean, stats = clean_dataframe(raw)
        if reset and (source_ids or vehicle_ids):
            # 局部重跑时只清对应车辆的旧行，避免把别人的结果一起删掉
            conds, params = [], {}
            if source_ids:
                conds.append("source_id IN :s")
                params["s"] = list(source_ids)
            if vehicle_ids:
                conds.append("vehicle_id IN :v")
                params["v"] = list(vehicle_ids)
            db.execute(
                text("DELETE FROM clean_trajectory WHERE " + " AND ".join(conds)), params
            )
            db.commit()
            stats["written"] = save_clean(clean, db, reset=False)
        else:
            stats["written"] = save_clean(clean, db, reset=reset)
        return stats
    finally:
        if own:
            db.close()
