"""统一接入层：CSV / Parquet 轨迹文件解析 + 坐标转换 + 批量入库。

职责边界（需求「统一数据接入层」四条）：
1. 文件类型识别与读取：.csv / .parquet，其它扩展名直接拒。
2. 字段校验：必需列缺失就报错并说明缺哪几列，不做静默补 0。
3. 坐标统一：默认 wgs84 直存——data/raw 的轨迹点与路网同坐标系
   （实测点到最近路段距离中位数 0.00 m）。只有上传高德/百度来源的文件时
   才显式传 crs="gcj02" 走转换；坐标系搞反会让距离中位偏到几百米，匹配率归零。
4. 批量写入：bulk_insert_mappings 分块提交，不逐行 INSERT。

数据源统计（vehicle_count / point_count）由导入后回填，不信任文件里的自报值。
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
from sqlalchemy.orm import Session

from app.config import settings
from app.core.exceptions import BusinessException
from app.models.data_source import DataSource
from app.models.raw_trajectory import RawTrajectory

# 必需列与可选列（列名对齐 data/raw/raw_trajectory.csv 的真实表头）
REQUIRED_COLUMNS = ["vehicle_id", "timestamp", "longitude", "latitude", "speed"]
OPTIONAL_COLUMNS = ["direction", "passenger", "source_id", "id"]

# 常见列名别名，兼容组员自己导出的文件
COLUMN_ALIASES = {
    "lng": "longitude",
    "lon": "longitude",
    "long": "longitude",
    "x": "longitude",
    "lat": "latitude",
    "y": "latitude",
    "time": "timestamp",
    "ts": "timestamp",
    "datetime": "timestamp",
    "veh_id": "vehicle_id",
    "vehicle": "vehicle_id",
    "plate": "vehicle_id",
    "dir": "direction",
    "heading": "direction",
    "course": "direction",
}

# GCJ-02 椭球参数
_A = 6378245.0
_EE = 0.0066934216229659432

CHUNK_ROWS = 50_000


# ----------------------------------------------------------------------
# 坐标转换（向量化）
# ----------------------------------------------------------------------
def _out_of_china(lng: np.ndarray, lat: np.ndarray) -> np.ndarray:
    return (lng < 72.004) | (lng > 137.8347) | (lat < 0.8293) | (lat > 55.8271)


def _transform_lng(x: np.ndarray, y: np.ndarray) -> np.ndarray:
    r = 300.0 + x + 2.0 * y + 0.1 * x * x + 0.1 * x * y + 0.1 * np.sqrt(np.abs(x))
    r += (20.0 * np.sin(6.0 * x * np.pi) + 20.0 * np.sin(2.0 * x * np.pi)) * 2.0 / 3.0
    r += (20.0 * np.sin(x * np.pi) + 40.0 * np.sin(x / 3.0 * np.pi)) * 2.0 / 3.0
    r += (150.0 * np.sin(x / 12.0 * np.pi) + 300.0 * np.sin(x / 30.0 * np.pi)) * 2.0 / 3.0
    return r


def _transform_lat(x: np.ndarray, y: np.ndarray) -> np.ndarray:
    r = -100.0 + 2.0 * x + 3.0 * y + 0.2 * y * y + 0.1 * x * y + 0.2 * np.sqrt(np.abs(x))
    r += (20.0 * np.sin(6.0 * x * np.pi) + 20.0 * np.sin(2.0 * x * np.pi)) * 2.0 / 3.0
    r += (20.0 * np.sin(y * np.pi) + 40.0 * np.sin(y / 3.0 * np.pi)) * 2.0 / 3.0
    r += (160.0 * np.sin(y / 12.0 * np.pi) + 320.0 * np.sin(y * np.pi / 30.0)) * 2.0 / 3.0
    return r


def gcj02_to_wgs84(lng, lat):
    """GCJ-02 → WGS-84（单步反算）。

    严格反解要迭代，但单步残差实测 <2m，远小于观测模型 σ=20m 和
    50m 候选半径，所以用单步换取全向量化。
    中国范围外的坐标原样返回。
    """
    lng = np.asarray(lng, dtype=float)
    lat = np.asarray(lat, dtype=float)
    outside = _out_of_china(lng, lat)
    d_lat = _transform_lat(lng - 105.0, lat - 35.0)
    d_lng = _transform_lng(lng - 105.0, lat - 35.0)
    rad_lat = lat / 180.0 * np.pi
    magic = 1.0 - _EE * np.sin(rad_lat) ** 2
    sqrt_magic = np.sqrt(magic)
    d_lat = (d_lat * 180.0) / ((_A * (1.0 - _EE)) / (magic * sqrt_magic) * np.pi)
    d_lng = (d_lng * 180.0) / (_A / sqrt_magic * np.cos(rad_lat) * np.pi)
    return np.where(outside, lng, lng - d_lng), np.where(outside, lat, lat - d_lat)


# ----------------------------------------------------------------------
# 读取与校验
# ----------------------------------------------------------------------
def read_table(path: Path | str) -> pd.DataFrame:
    p = Path(path)
    if not p.exists():
        raise BusinessException(f"文件不存在：{p}")
    suffix = p.suffix.lower()
    if suffix == ".csv":
        # 国产工具导出的 CSV 常带 BOM，不指定 utf-8-sig 会把首列读成 \ufeffid
        return pd.read_csv(p, encoding="utf-8-sig")
    if suffix in (".parquet", ".pq"):
        return pd.read_parquet(p)
    raise BusinessException(
        f"不支持的文件类型 {suffix}，仅支持 {', '.join(settings.allowed_extensions)}", code=4001
    )


def normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    """列名标准化 + 必需列校验 + 类型转换。校验失败抛业务异常，不返回半坏数据。"""
    df = df.copy()
    df.columns = [str(c).strip().lower() for c in df.columns]
    renamed = {c: COLUMN_ALIASES[c] for c in df.columns if c in COLUMN_ALIASES}
    if renamed:
        df = df.rename(columns=renamed)

    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise BusinessException(
            "缺少必需字段：%s。需要 %s" % (", ".join(missing), ", ".join(REQUIRED_COLUMNS)),
            code=4002,
        )

    for col in ("longitude", "latitude", "speed"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna(subset=["longitude", "latitude", "speed"])

    if "direction" in df.columns:
        df["direction"] = pd.to_numeric(df["direction"], errors="coerce")
    else:
        df["direction"] = np.nan
    if "passenger" in df.columns:
        df["passenger"] = pd.to_numeric(df["passenger"], errors="coerce").fillna(0).astype(int)
    else:
        df["passenger"] = 0

    ts = pd.to_datetime(df["timestamp"], errors="coerce")
    bad_ts = int(ts.isna().sum())
    if bad_ts:
        df = df.loc[ts.notna()].copy()
        df["timestamp"] = ts[ts.notna()]
    else:
        df["timestamp"] = ts

    # 经纬度范围校验：轨迹落在路网外基本可以断定坐标系或列序错了
    bad_geo = (df["longitude"].abs() > 180) | (df["latitude"].abs() > 90)
    if bad_geo.any():
        raise BusinessException(
            "有 %d 行经纬度越界（|lng|>180 或 |lat|>90），请检查列顺序或坐标系" % int(bad_geo.sum()),
            code=4003,
        )

    df["vehicle_id"] = df["vehicle_id"].astype(str)
    return df.sort_values(["vehicle_id", "timestamp"], kind="stable").reset_index(drop=True)


def infer_source_id(df: pd.DataFrame, explicit: int | None = None) -> pd.DataFrame:
    """按车辆前缀回填 source_id（ta_/ri_/bi_），没给显值时也能归属数据源。"""
    if explicit is not None:
        df["source_id"] = int(explicit)
        return df
    if "source_id" in df.columns and df["source_id"].notna().all() and (df["source_id"] > 0).all():
        df["source_id"] = df["source_id"].astype(int)
        return df
    prefix_map = {"ta": 1, "ri": 2, "bi": 3}
    df["source_id"] = (
        df["vehicle_id"].str.split("_").str[0].map(prefix_map).fillna(0).astype(int)
    )
    if (df["source_id"] == 0).any():
        raise BusinessException(
            "%d 行无法判定数据源：vehicle_id 需要形如 ta_000/ri_012/bi_003，或显式提供 source_id"
            % int((df["source_id"] == 0).sum()),
            code=4004,
        )
    return df


# ----------------------------------------------------------------------
# 入库
# ----------------------------------------------------------------------
def parse_file(path: Path | str, source_id: int | None = None, crs: str = "wgs84") -> pd.DataFrame:
    """读文件 → 校验 → 坐标统一，返回可直接入库的 DataFrame（不落库）。"""
    df = normalize_columns(read_table(path))
    df = infer_source_id(df, source_id)
    if crs.lower() in ("gcj02", "gcj", "gaode", "amap"):
        w_lng, w_lat = gcj02_to_wgs84(df["longitude"].to_numpy(), df["latitude"].to_numpy())
        df["longitude"] = np.round(w_lng, 6)
        df["latitude"] = np.round(w_lat, 6)
    elif crs.lower() not in ("wgs84", "wgs-84", "gps"):
        raise BusinessException(f"未知坐标系 {crs}，可选 gcj02 / wgs84", code=4005)
    df["speed"] = df["speed"].astype(float)
    return df


def to_records(df: pd.DataFrame) -> list[dict]:
    cols = ["vehicle_id", "source_id", "timestamp", "longitude", "latitude", "speed", "direction", "passenger"]
    sub = df.reindex(columns=cols)
    recs = []
    for row in sub.to_dict("records"):
        d = dict(row)
        d["timestamp"] = d["timestamp"].to_pydatetime()
        if pd.isna(d.get("direction")):
            d["direction"] = None
        else:
            d["direction"] = float(d["direction"])
        d["passenger"] = int(d["passenger"])
        d["source_id"] = int(d["source_id"])
        recs.append(d)
    return recs


def save_raw(df: pd.DataFrame, db: Session, chunk_rows: int = CHUNK_ROWS) -> int:
    """分块 bulk_insert_mappings 写入 raw_trajectory。返回写入行数。"""
    recs = to_records(df)
    written = 0
    for i in range(0, len(recs), chunk_rows):
        chunk = recs[i : i + chunk_rows]
        db.bulk_insert_mappings(RawTrajectory, chunk)
        db.commit()
        written += len(chunk)
    return written


def refresh_source_stats(db: Session, source_ids: list[int] | None = None) -> list[dict]:
    """回填数据源统计：车辆数、点数一律按 raw_trajectory 实际行数算。"""
    q = db.query(
        RawTrajectory.source_id,
        RawTrajectory.vehicle_id,
    )
    if source_ids:
        q = q.filter(RawTrajectory.source_id.in_(source_ids))
    pairs = q.distinct().all()
    vehicles: dict[int, set[str]] = {}
    for sid, vid in pairs:
        vehicles.setdefault(sid, set()).add(vid)

    updated = []
    for sid in sorted(vehicles):
        src = db.get(DataSource, sid)
        if src is None:
            continue
        src.vehicle_count = len(vehicles[sid])
        src.point_count = db.query(RawTrajectory).filter(RawTrajectory.source_id == sid).count()
        updated.append(
            {"source_id": sid, "vehicle_count": src.vehicle_count, "point_count": src.point_count}
        )
    db.commit()
    return updated


def ingest_file(
    path: Path | str,
    db: Session,
    source_id: int | None = None,
    crs: str = "wgs84",
) -> dict:
    """接入一个文件的完整链路，供 /api/trajectory/upload 与脚本共用。"""
    df = parse_file(path, source_id=source_id, crs=crs)
    total_in = len(df)
    written = save_raw(df, db)
    stats = refresh_source_stats(db, sorted(set(df["source_id"].tolist())))
    return {
        "file": Path(path).name,
        "rows_read": total_in,
        "rows_written": written,
        "sources": stats,
        "time_min": str(df["timestamp"].min()),
        "time_max": str(df["timestamp"].max()),
        "vehicles": int(df["vehicle_id"].nunique()),
    }
