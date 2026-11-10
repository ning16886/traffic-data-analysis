"""原始数据导入：把仓库根 data/raw 的八张 CSV 灌进数据库。

用法（在哪个目录跑都行，路径一律绝对解析）：
    python backend/scripts/import_raw_data.py

需求口径已变更为「一切以原始数据为准」：这八张表就是 data/raw 的纯投影，
每次运行先重建表再插，重跑即刷新；本脚本不生成、不修改、不补算任何数据。

一个刻意的例外：data_source 的 vehicle_count / point_count 按 CSV 原样入库，
不调 trajectory_parser.refresh_source_stats 按实测重算——那张表本身就是原始数据。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
from sqlalchemy import DateTime, Float, Integer, Numeric, distinct, func, select

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app import models  # noqa: F401  触发 8 个 ORM 模型注册
from app.config import settings
from app.core.database import Base, SessionLocal, engine


def build_records(df: pd.DataFrame, table) -> list[dict]:
    """CSV 列 → 该列 SQLAlchemy 类型要求的 Python 值。

    时间列必须转成 datetime：SQLite 会把 '2024-03-01 08:00:00.000000' 当文本存，
    之后所有按时间窗的查询、排序和分组都会失效。
    """
    columns = {col.name: col for col in table.columns}
    missing = [name for name in columns if name not in df.columns]
    if missing:
        raise ValueError(f"{table.name} 缺列 {missing}：检查 data/raw/{table.name}.csv 表头")

    values: dict[str, list] = {}
    for name, col in columns.items():
        series = df[name]
        coltype = col.type
        if isinstance(coltype, DateTime):
            parsed = pd.to_datetime(series, errors="coerce")
            values[name] = [None if pd.isna(v) else v.to_pydatetime() for v in parsed]
        elif isinstance(coltype, Integer):
            parsed = pd.to_numeric(series, errors="coerce")
            values[name] = [None if pd.isna(v) else int(v) for v in parsed]
        elif isinstance(coltype, (Float, Numeric)):
            parsed = pd.to_numeric(series, errors="coerce")
            values[name] = [None if pd.isna(v) else float(v) for v in parsed]
        else:
            values[name] = [None if pd.isna(v) else str(v) for v in series]

    names = list(values)
    return [dict(zip(names, row)) for row in zip(*values.values())]


def import_table(db, table) -> tuple[int, int]:
    path = settings.raw_dir / f"{table.name}.csv"
    if not path.exists():
        raise FileNotFoundError(f"原始数据文件不存在：{path}")
    df = pd.read_csv(path, encoding="utf-8-sig")
    db.execute(table.insert(), build_records(df, table))
    db.commit()
    db_rows = db.execute(select(func.count()).select_from(table)).scalar()
    return len(df), db_rows


def warn_source_stats(db) -> None:
    """data_source 自报的统计与 raw_trajectory 实测不一致时提示一行，不改写。"""
    src = Base.metadata.tables["data_source"]
    raw = Base.metadata.tables["raw_trajectory"]
    actual = {
        sid: (pts, veh)
        for sid, pts, veh in db.execute(
            select(
                raw.c.source_id,
                func.count(),
                func.count(distinct(raw.c.vehicle_id)),
            ).group_by(raw.c.source_id)
        )
    }
    for sid, name, veh, pts in db.execute(
        select(src.c.id, src.c.name, src.c.vehicle_count, src.c.point_count)
    ):
        got = actual.get(sid, (0, 0))
        if (pts, veh) != got:
            print(f"[提示] data_source#{sid} {name} 自报 {pts} 点/{veh} 车，"
                  f"raw_trajectory 实测 {got[0]} 点/{got[1]} 车（按原样入库，未改写）")


def main() -> int:
    if not settings.raw_dir.is_dir():
        print(f"[失败] 原始数据目录不存在：{settings.raw_dir}")
        return 1

    print(f"原始数据目录：{settings.raw_dir}")
    print(f"目标数据库  ：{settings.database_url}")

    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)

    db = SessionLocal()
    report = []
    try:
        for table in Base.metadata.sorted_tables:
            csv_rows, db_rows = import_table(db, table)
            report.append((table.name, csv_rows, db_rows))
        warn_source_stats(db)
    finally:
        db.close()

    print()
    print(f"{'表名':<18}{'CSV 行数':>10}{'入库行数':>10}")
    mismatched = []
    for name, csv_rows, db_rows in report:
        print(f"{name:<18}{csv_rows:>10}{db_rows:>10}" + ("" if csv_rows == db_rows else "  ← 不一致"))
        if csv_rows != db_rows:
            mismatched.append(name)

    total = sum(rows for _, rows, _ in report)
    print(f"\n共 {len(report)} 张表 / {total} 行")
    if mismatched:
        print(f"[失败] 以下表入库行数与 CSV 不符：{mismatched}")
        return 1
    print("[完成] 八张表行数与 data/raw 逐表一致")
    return 0


if __name__ == "__main__":
    sys.exit(main())
