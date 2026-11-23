"""只读核对模拟数据基线；不导入 app，不建库、不建表、不修复数据。

在仓库根目录执行：
    .venv/Scripts/python.exe -X utf8 backend/scripts/check_od_baseline.py \
        --db backend/data/od-dev/traffic.db
"""

from __future__ import annotations

import argparse
import csv
import math
import sqlite3
from contextlib import closing
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[2]
EXPECTED_ROWS = {
    "data_source": 3,
    "raw_trajectory": 500_000,
    "clean_trajectory": 488_204,
    "match_result": 488_204,
    "road_segment": 518,
    "road_metrics": 15_375,
    "od_matrix": 2_141,
    "hotzone": 151,
}
EXPECTED_PERIODS = {
    "morning_peak": (453, 471),
    "evening_peak": (424, 455),
    "normal": (1_063, 1_134),
    "transit": (201, 214),
}


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def check_baseline(
    db_path: Path,
    csv_dir: Path,
    *,
    expected_rows: dict[str, int] | None = None,
    expected_periods: dict[str, tuple[int, int]] | None = None,
) -> dict:
    expected_rows = EXPECTED_ROWS if expected_rows is None else expected_rows
    expected_periods = EXPECTED_PERIODS if expected_periods is None else expected_periods
    require(expected_rows.keys() == EXPECTED_ROWS.keys(), "必须检查完整八表")
    db_path = db_path.resolve()
    require(db_path.is_file(), f"数据库不存在（未创建）：{db_path}")
    require(csv_dir.is_dir(), f"CSV 目录不存在：{csv_dir}")

    csv_counts = {}
    csv_od = {}
    for table, expected in expected_rows.items():
        with (csv_dir / f"{table}.csv").open(encoding="utf-8-sig", newline="") as stream:
            reader = csv.DictReader(stream)
            require(bool(reader.fieldnames), f"{table} CSV 缺少表头")
            count = 0
            for row in reader:
                count += 1
                if table == "od_matrix":
                    key = (row["origin_road_id"], row["dest_road_id"], row["time_period"])
                    require(key not in csv_od, f"CSV 存在重复 OD 三元组：{key}")
                    trips = int(row["trip_count"])
                    duration = float(row["avg_duration_min"])
                    require(trips > 0, f"CSV OD 次数无效：{key}")
                    require(math.isfinite(duration) and duration >= 0, f"CSV OD 时长无效：{key}")
                    csv_od[key] = (trips, duration)
            require(count == expected, f"{table} CSV 行数 {count} != 基线 {expected}")
            csv_counts[table] = count

    # mode=ro 防止 sqlite3 在路径拼错时悄悄创建空库；query_only 禁止本连接写入。
    with closing(sqlite3.connect(db_path.as_uri() + "?mode=ro", uri=True)) as db:
        db.execute("PRAGMA query_only = ON")
        db.execute("BEGIN")
        require(db.execute("PRAGMA quick_check").fetchone() == ("ok",), "SQLite 完整性检查失败")
        counts = {}
        for table in EXPECTED_ROWS:
            count = db.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
            require(count == csv_counts[table], f"{table} 数据库行数 {count} != CSV {csv_counts[table]}")
            counts[table] = count

        duplicate = db.execute("""
            SELECT origin_road_id, dest_road_id, time_period FROM od_matrix
            GROUP BY origin_road_id, dest_road_id, time_period HAVING COUNT(*) > 1 LIMIT 1
        """).fetchone()
        require(duplicate is None, f"数据库存在重复 OD 三元组：{duplicate}")
        orphan = db.execute("""
            SELECT COUNT(*) FROM od_matrix od
            LEFT JOIN road_segment origin ON origin.road_id = od.origin_road_id
            LEFT JOIN road_segment dest ON dest.road_id = od.dest_road_id
            WHERE origin.road_id IS NULL OR dest.road_id IS NULL
        """).fetchone()[0]
        require(orphan == 0, f"{orphan} 条 OD 缺少路段关联")

        rows = db.execute("""
            SELECT origin_road_id, dest_road_id, time_period, trip_count, avg_duration_min
            FROM od_matrix
        """).fetchall()
        periods: dict[str, tuple[int, int]] = {}
        for origin, dest, period, trips, duration in rows:
            key = (origin, dest, period)
            require(key in csv_od, f"数据库 OD 未在 CSV 中找到：{key}")
            require(isinstance(trips, int) and trips > 0, f"数据库 OD 次数无效：{key}")
            require(
                isinstance(duration, (int, float)) and math.isfinite(duration) and duration >= 0,
                f"数据库 OD 时长无效：{key}",
            )
            csv_trips, csv_duration = csv_od[key]
            require(trips == csv_trips, f"OD 次数与 CSV 不一致：{key}")
            require(math.isclose(duration, csv_duration, rel_tol=0, abs_tol=1e-8), f"OD 时长与 CSV 不一致：{key}")
            pairs, total = periods.get(period, (0, 0))
            periods[period] = (pairs + 1, total + trips)
        require(periods == expected_periods, f"分类统计不符：{periods}")

        for table, column in (
            ("raw_trajectory", "timestamp"),
            ("clean_trajectory", "timestamp"),
            ("road_metrics", "time_window"),
        ):
            invalid = db.execute(f"""
                SELECT COUNT(*) FROM "{table}"
                WHERE "{column}" IS NULL OR strftime('%Y-%m-%d %H:%M:%f', "{column}") IS NULL
            """).fetchone()[0]
            require(invalid == 0, f"{table}.{column} 有 {invalid} 条空值或无效时间")

        missing_clean = db.execute("""
            SELECT COUNT(*) FROM match_result m
            LEFT JOIN clean_trajectory c ON c.id = m.clean_trajectory_id
            WHERE c.id IS NULL
        """).fetchone()[0]
        require(missing_clean == 0, f"{missing_clean} 条匹配结果缺少清洗点关联")
        repeated_clean = db.execute("""
            SELECT clean_trajectory_id FROM match_result
            GROUP BY clean_trajectory_id HAVING COUNT(*) > 1 LIMIT 1
        """).fetchone()
        require(repeated_clean is None, f"清洗点存在重复匹配记录：{repeated_clean}")

    return {"rows": counts, "periods": periods, "trip_count": sum(value[1] for value in periods.values())}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True, type=Path, help="要检查的 SQLite 文件（只读）")
    parser.add_argument("--csv-dir", type=Path, default=ROOT_DIR / "data" / "raw")
    args = parser.parse_args()
    try:
        report = check_baseline(args.db, args.csv_dir)
    except (OSError, sqlite3.Error, ValueError, KeyError, TypeError) as exc:
        print(f"[失败] {exc}")
        return 1
    for table, count in report["rows"].items():
        print(f"[通过] {table}: CSV = 数据库 = {count:,} 行")
    for period, (pairs, trips) in report["periods"].items():
        print(f"[通过] {period}: {pairs:,} 条记录 / {trips:,} 次出行")
    print(f"[通过] 总出行 {report['trip_count']:,} 次；OD 值、关联、时间、重复记录及完整性检查通过（只读）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
