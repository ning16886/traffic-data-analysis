"""地图匹配离线跑批：产出 match_result.csv + 车辆统计，并与数据集标准答案对账。

用法（在 backend/ 下）：
    python scripts/run_matching.py                     # 默认阈值 100m，写 backend/out/
    python scripts/run_matching.py --threshold 50
    python scripts/run_matching.py --write             # 额外覆盖 data/raw/match_result.csv
    python scripts/run_matching.py --verify-only       # 只做数据体检，不写文件

输出：
    out/match_result.csv     与数据集同构的匹配结果（id 按 clean_trajectory_id 升序）
    out/vehicle_stats.csv    每辆车 总点/匹配/未匹配/匹配率/路段数
    out/match_summary.json   汇总 + 参数 + 耗时
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from app.core import settings  # noqa: E402
from app.services.matching import MapMatcher, MatchParams, load_clean_points, load_roads  # noqa: E402

COLUMNS = [
    "id", "vehicle_id", "road_id", "raw_trajectory_id", "clean_trajectory_id",
    "match_longitude", "match_latitude", "match_probability", "is_matched", "create_time",
]


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="HMM 地图匹配跑批")
    ap.add_argument("--data", type=Path, default=settings.raw_dir, help="数据目录（八张 CSV）")
    ap.add_argument("--out", type=Path, default=BACKEND_DIR / "out", help="输出目录")
    ap.add_argument("--threshold", type=float, default=settings.match_threshold_m, help="垂距阈值（米）")
    ap.add_argument("--write", action="store_true", help="同时覆盖 data/raw/match_result.csv")
    ap.add_argument("--verify-only", action="store_true", help="只体检数据集标准答案，不重新匹配")
    return ap.parse_args()


def raw_id_map(data_dir: Path) -> pd.Series:
    """clean → raw 的主键映射。(vehicle_id, timestamp) 在单车内唯一，数据集就是这么回连的。"""
    path = data_dir / "raw_trajectory.csv"
    if not path.exists():
        return pd.Series(dtype="int64")
    raw = pd.read_csv(path, encoding="utf-8-sig", usecols=["id", "vehicle_id", "timestamp"])
    raw["timestamp"] = pd.to_datetime(raw["timestamp"], errors="coerce")
    return raw.set_index(["vehicle_id", "timestamp"])["id"]


def run_match(points: pd.DataFrame, threshold: float, data_dir: Path) -> tuple[pd.DataFrame, dict]:
    roads = load_roads(data_dir / "road_segment.csv")
    matcher = MapMatcher(roads, MatchParams(threshold_m=threshold,
                                            sigma_m=settings.match_sigma_m,
                                            beta_m=settings.match_beta_m))
    t0 = time.perf_counter()
    res = matcher.match(
        points["longitude"].to_numpy(),
        points["latitude"].to_numpy(),
        directions=points["direction"].to_numpy(dtype=float),
        trip_ids=points["trip_seq"].to_numpy(),
        threshold_m=threshold,
    )
    elapsed = time.perf_counter() - t0

    road_ids = np.array([r.road_id for r in roads], dtype=object)
    out = pd.DataFrame({
        "vehicle_id": points["vehicle_id"].to_numpy(),
        "clean_trajectory_id": points["id"].to_numpy(),
        "road_id": np.where(res.road_idx >= 0, road_ids[np.maximum(res.road_idx, 0)], ""),
        "match_longitude": res.snap_lng,
        "match_latitude": res.snap_lat,
        "match_probability": res.probability,
        "is_matched": res.matched.astype(int),
        "distance_m": res.distance_m,
        "timestamp": points["timestamp"].to_numpy(),
    })
    # 未匹配一律清空路段与吸附坐标（可能命中候选但垂距超阈值，这算未匹配）
    out.loc[out["is_matched"] == 0, "match_longitude"] = np.nan
    out.loc[out["is_matched"] == 0, "match_latitude"] = np.nan
    out.loc[out["is_matched"] == 0, "road_id"] = "" 
    out = out.sort_values("clean_trajectory_id").reset_index(drop=True)
    out.insert(0, "id", np.arange(1, len(out) + 1))
    summary = {
        "threshold_m": threshold,
        "total": int(len(out)),
        "matched": int(out["is_matched"].sum()),
        "unmatched": int((out["is_matched"] == 0).sum()),
        "matchRate": round(float(out["is_matched"].mean()), 4),
        "elapsedMs": int(elapsed * 1000),
        "roadCount": int(out.loc[out["is_matched"] == 1, "road_id"].nunique()),
        "params": matcher.params.as_dict(),
        "network": {"roads": matcher.network.n_roads, "segments": matcher.network.n_segments},
    }
    return out, summary


def attach_raw_id(df: pd.DataFrame, data_dir: Path) -> pd.DataFrame:
    mapping = raw_id_map(data_dir)
    if mapping.empty:
        df["raw_trajectory_id"] = ""
        return df
    keys = pd.MultiIndex.from_arrays([df["vehicle_id"], pd.to_datetime(df["timestamp"])])
    df["raw_trajectory_id"] = mapping.reindex(keys).to_numpy()
    df["raw_trajectory_id"] = df["raw_trajectory_id"].fillna(0).astype("int64")
    return df


def compare_with_reference(our: pd.DataFrame, ref_path: Path) -> dict:
    """与数据集标准答案对账：is_matched 一致率、road_id 一致率、分歧点特征。"""
    if not ref_path.exists():
        return {}
    ref = pd.read_csv(ref_path, encoding="utf-8-sig",
                      usecols=["clean_trajectory_id", "road_id", "is_matched", "match_probability"])
    m = our.merge(ref, on="clean_trajectory_id", how="left", suffixes=("", "_ref"))
    both = m["is_matched_ref"].notna()
    m = m[both]
    same_flag = (m["is_matched"] == m["is_matched_ref"])
    matched_both = (m["is_matched"] == 1) & (m["is_matched_ref"] == 1)
    same_road = (m.loc[matched_both, "road_id"] == m.loc[matched_both, "road_id_ref"])

    ref_unmatched = m[m["is_matched_ref"] == 0]
    return {
        "reference_rows": int(len(ref)),
        "overlap": int(len(m)),
        "flagAgreement": round(float(same_flag.mean()), 4),
        "roadAgreement": round(float(same_road.mean()), 4),
        "refUnmatched": int(len(ref_unmatched)),
        "refUnmatchedDistance": {
            "median": round(float(ref_unmatched["distance_m"].median()), 2)
            if len(ref_unmatched) else None,
            "p95": round(float(ref_unmatched["distance_m"].quantile(0.95)), 2)
            if len(ref_unmatched) else None,
            "max": round(float(ref_unmatched["distance_m"].max()), 2)
            if len(ref_unmatched) else None,
        },
        "refUnmatchedProbMean": round(float(ref_unmatched["match_probability"].mean()), 4)
        if len(ref_unmatched) else None,
    }


def check_reference_quality(data_dir: Path, threshold: float) -> dict:
    """体检数据集自带的 match_result.csv：它标成「未匹配」的点到底离路多远。"""
    ref_path = data_dir / "match_result.csv"
    clean_path = data_dir / "clean_trajectory.csv"
    if not ref_path.exists() or not clean_path.exists():
        return {}

    roads = load_roads(data_dir / "road_segment.csv")
    matcher = MapMatcher(roads, MatchParams(threshold_m=threshold))
    points = load_clean_points(clean_path)
    lng = points["longitude"].to_numpy()
    lat = points["latitude"].to_numpy()
    # 用最近的候选（不管阈值）量出「到最近路段的真实垂距」
    idx, dist, _, _ = matcher.network.candidates(lng, lat, 500.0, 1)
    nearest = dist[:, 0]
    nearest = np.where(np.isfinite(nearest), nearest, np.inf)

    ref = pd.read_csv(ref_path, encoding="utf-8-sig", usecols=["clean_trajectory_id", "is_matched"])
    ref = ref.drop_duplicates("clean_trajectory_id").set_index("clean_trajectory_id")
    flag = ref["is_matched"].reindex(points["id"].to_numpy()).to_numpy()

    un = flag == 0
    mt = flag == 1
    return {
        "referenceUnmatched": int(un.sum()),
        "referenceMatched": int(mt.sum()),
        "unmatchedNearestDistance": {
            "median": round(float(np.median(nearest[un])), 2) if un.any() else None,
            "p95": round(float(np.percentile(nearest[un], 95)), 2) if un.any() else None,
            "withinThresholdRatio": round(float((nearest[un] <= threshold).mean()), 4) if un.any() else None,
        },
        "matchedNearestDistance": {
            "median": round(float(np.median(nearest[mt])), 2) if mt.any() else None,
            "max": round(float(np.max(nearest[mt])), 2) if mt.any() else None,
        },
        "trueOffRoadPoints": {
            "overThreshold": int((nearest > threshold).sum()),
            "over200m": int((nearest > 200).sum()),
        },
    }


def main() -> int:
    args = parse_args()
    data_dir: Path = args.data
    out_dir: Path = args.out
    out_dir.mkdir(parents=True, exist_ok=True)

    quality = check_reference_quality(data_dir, args.threshold)
    print("=== 数据集 match_result.csv 体检（阈值 %.0fm）===" % args.threshold)
    print(json.dumps(quality, ensure_ascii=False, indent=2))
    if args.verify_only:
        return 0

    points = load_clean_points(data_dir / "clean_trajectory.csv")
    print(f"\n轨迹点 {len(points):,}（{points['vehicle_id'].nunique()} 辆车，"
          f"{points['trip_seq'].nunique()} 条行程），阈值 {args.threshold:.0f}m ...")
    result, summary = run_match(points, args.threshold, data_dir)
    result = attach_raw_id(result, data_dir)
    result["create_time"] = datetime.now().strftime("%Y-%m-%d")

    out = result[COLUMNS]
    path = out_dir / "match_result.csv"
    out.to_csv(path, index=False, encoding="utf-8-sig")

    stats = (result.groupby("vehicle_id")
             .agg(total=("is_matched", "size"), matched=("is_matched", "sum"))
             .reset_index())
    stats["unmatched"] = stats["total"] - stats["matched"]
    stats["match_rate"] = (stats["matched"] / stats["total"]).round(4)
    stats.to_csv(out_dir / "vehicle_stats.csv", index=False, encoding="utf-8-sig")

    cmp = compare_with_reference(result, data_dir / "match_result.csv")
    summary["referenceCompare"] = cmp
    (out_dir / "match_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print(f"匹配 {summary['matched']:,}/{summary['total']:,} = "
          f"{summary['matchRate'] * 100:.2f}%，命中路段 {summary['roadCount']}，"
          f"耗时 {summary['elapsedMs'] / 1000:.1f}s")
    if cmp:
        print(f"与数据集标准答案：is_matched 一致 {cmp['flagAgreement'] * 100:.2f}%，"
              f"命中路段一致 {cmp['roadAgreement'] * 100:.2f}%")
    print(f"输出：{path}")
    print("\n车辆匹配率区间 %.3f ~ %.3f（中位 %.3f）" % (
        stats["match_rate"].min(), stats["match_rate"].max(), stats["match_rate"].median()))

    if args.write:
        target = data_dir / "match_result.csv"
        out.to_csv(target, index=False, encoding="utf-8-sig")
        print(f"已覆盖 {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
