"""CSV 装载：路网 + 清洗后轨迹。

数据集全部是 utf-8-sig 编码（Excel 导出的 BOM），用 utf-8 读第一列会变成 "\\ufeffid"，
所以统一 utf-8-sig。48 万行走 pandas 读约 1.5s，比 csv 模块快一个量级。
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from .geo import parse_linestring
from .network import Road

TRACK_COLUMNS = [
    "id", "vehicle_id", "source_id", "segment_id", "timestamp",
    "longitude", "latitude", "speed", "direction", "is_stay", "is_gap", "flag",
]


def load_roads(path: Path | str) -> list[Road]:
    """读 road_segment.csv → Road 列表。WKT 解析失败的行直接跳过（并保持 road_id 唯一）。"""
    df = pd.read_csv(path, encoding="utf-8-sig",
                     usecols=["road_id", "road_name", "level", "geometry"])
    roads: list[Road] = []
    seen: set[str] = set()
    for road_id, road_name, level, wkt in df.itertuples(index=False):
        pts = parse_linestring(wkt)
        if len(pts) < 2 or road_id in seen:
            continue
        seen.add(road_id)
        roads.append(Road(str(road_id), str(road_name), str(level or "branch"), pts))
    if not roads:
        raise ValueError(f"{path} 里没有可用的路段几何")
    return roads


def load_clean_points(path: Path | str) -> pd.DataFrame:
    """读 clean_trajectory.csv → 匹配引擎的输入。

    列裁剪 + 类型收敛，只留下匹配要用的字段；缺列时按空值补齐，
    队友改清洗脚本加字段不会把匹配跑挂。
    """
    df = pd.read_csv(path, encoding="utf-8-sig")
    for col in TRACK_COLUMNS:
        if col not in df.columns:
            df[col] = np.nan
    df = df[TRACK_COLUMNS].copy()

    df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce")
    for col in ("longitude", "latitude", "speed", "direction"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna(subset=["longitude", "latitude"])
    df["id"] = df["id"].astype(int)
    df["vehicle_id"] = df["vehicle_id"].astype(str)
    df["segment_id"] = df["segment_id"].fillna("").astype(str)
    df["flag"] = df["flag"].fillna("ok").astype(str)

    # 同一辆车按时间排序：维特比的转移依赖正确的时间顺序
    df = df.sort_values(["vehicle_id", "timestamp", "id"], kind="stable").reset_index(drop=True)

    # 换车或换行程都算新的一段：trip_seq 是跨车全局递增的连程序号。
    # 只按 segment_id 判断会漏掉「上一辆车的末段与下一辆车的首段同名」的情况，
    # 那样维特比会把两辆车的轨迹接成一条，出现横跨全城的假转移。
    new_trip = (
        (df["vehicle_id"] != df["vehicle_id"].shift())
        | (df["segment_id"] != df["segment_id"].shift())
    )
    df["trip_seq"] = new_trip.cumsum().astype(int)
    return df


def fingerprint(*files: Path | str) -> dict:
    """数据指纹：改名 / 重跑清洗后缓存自动失效，不用手工删文件。"""
    out = {}
    for f in files:
        p = Path(f)
        st = p.stat() if p.exists() else None
        out[p.name] = f"{int(st.st_mtime)}:{st.st_size}" if st else "missing"
    return out
