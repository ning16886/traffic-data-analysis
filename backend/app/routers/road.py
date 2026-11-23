"""路段分析路由：拥堵排行 / 路段详情 / 拥堵历史 / 上下游路段 / CSV 导出。

拥堵指数口径与大屏完全一致（dashboard_service.build_windows）：
自由流速度按「历史 7 天最低流量时刻的均速」动态估算，
拥堵指数 = 1 − 实际速度 / 自由流速度，clamp(0, 1)。
"""

import csv
import io

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import BusinessException, get_db, success
from app.models import RoadSegment
from app.services import dashboard_service

router = APIRouter(prefix="/road", tags=["路段分析"])


def _worst_rows(db: Session):
    """每条路段取样本可信的最堵窗，拥堵排行的唯一数据源。"""
    windows = dashboard_service.build_windows(db)
    return dashboard_service._worst_per_road(windows)


def _row_dict(row) -> dict:
    return {
        "road_id": row.road_id,
        "road_name": row.road_name,
        "level": row.level,
        "window": row.window.strftime("%Y-%m-%d %H:%M"),
        "avg_speed": round(float(row.avg_speed), 1),
        "free_flow_speed": round(float(row.free_flow_speed), 1),
        "congestion_index": round(float(row.congestion_index), 4),
        "congestion_percent": round(float(row.congestion_index) * 100, 1),
        "point_count": int(row.point_count),
    }


@router.get("/metrics")
def road_metrics(
    db: Session = Depends(get_db),
    limit: int = Query(100, ge=1, le=600, description="排行条数，518 条路段最多全量"),
):
    """路段拥堵排行（按拥堵指数降序），路段分析页左侧表格。"""
    worst = _worst_rows(db)
    if worst.empty:
        return success([])
    return success([_row_dict(row) for row in worst.head(limit).itertuples()])


@router.get("/export")
def export_csv(db: Session = Depends(get_db)):
    """全量拥堵排行导出 CSV。文件流接口，不走统一 JSON 信封。"""
    worst = _worst_rows(db)
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["road_id", "road_name", "level", "window", "avg_speed", "free_flow_speed", "congestion_index", "point_count"])
    if not worst.empty:
        for row in worst.itertuples():
            writer.writerow(
                [row.road_id, row.road_name, row.level, row.window.strftime("%Y-%m-%d %H:%M"),
                 round(float(row.avg_speed), 2), round(float(row.free_flow_speed), 2),
                 round(float(row.congestion_index), 4), int(row.point_count)]
            )
    return Response(
        content=buf.getvalue().encode("utf-8-sig"),  # 带 BOM，Excel 打开不乱码
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=road_congestion.csv"},
    )


@router.get("/{road_id}/congestion")
def congestion_history(road_id: str, db: Session = Depends(get_db)):
    """该路段全部时间窗的拥堵指数序列，详情页历史曲线用（按时间升序）。"""
    windows = dashboard_service.build_windows(db)
    g = windows[windows["road_id"] == road_id].sort_values("window")
    return success(
        [
            {
                "window": row.window.strftime("%Y-%m-%d %H:%M"),
                "avg_speed": round(float(row.avg_speed), 2),
                "free_flow_speed": round(float(row.free_flow_speed), 2),
                "congestion_index": round(float(row.congestion_index), 4),
                "point_count": int(row.point_count),
            }
            for row in g.itertuples()
        ]
    )


@router.get("/{road_id}/adjacent")
def adjacent_roads(road_id: str, db: Session = Depends(get_db)):
    """上下游路段：路网是编号连续的方格网，取编号 ±1 的两条路段。"""
    try:
        num = int(road_id[1:])  # R00067 → 67
    except ValueError:
        raise BusinessException(400, "路段编号格式应为 R + 数字")
    ids = [f"R{num - 1:05d}", f"R{num + 1:05d}"]
    rows = db.execute(select(RoadSegment).where(RoadSegment.road_id.in_(ids))).scalars().all()

    windows = dashboard_service.build_windows(db)
    worst = {} if windows.empty else dashboard_service._worst_per_road(windows).set_index("road_id")
    out = []
    for seg in rows:
        item = {
            "road_id": seg.road_id,
            "road_name": seg.road_name,
            "level": seg.level,
            "congestion_index": None,
            "avg_speed": None,
        }
        if seg.road_id in worst.index:
            r = worst.loc[seg.road_id]
            item["congestion_index"] = round(float(r["congestion_index"]), 4)
            item["avg_speed"] = round(float(r["avg_speed"]), 1)
        out.append(item)
    return success(out)


@router.get("/{road_id}")
def road_detail(road_id: str, db: Session = Depends(get_db)):
    """路段详情：基础信息 + 最堵窗的指标，路段分析页右侧面板。"""
    seg = db.execute(
        select(RoadSegment).where(RoadSegment.road_id == road_id)
    ).scalar_one_or_none()
    if seg is None:
        raise BusinessException(404, f"路段 {road_id} 不存在")

    windows = dashboard_service.build_windows(db)
    detail = {
        "road_id": seg.road_id,
        "road_name": seg.road_name,
        "level": seg.level,
        "length": seg.length,
        "avg_speed": None,
        "free_flow_speed": None,
        "congestion_index": None,
        "congestion_percent": None,
        "window": None,
        "point_count": None,
    }
    if not windows.empty and road_id in set(windows["road_id"]):
        cand = windows[windows["road_id"] == road_id]
        worst = dashboard_service._worst_per_road(cand)
        if not worst.empty:
            detail.update(_row_dict(worst.iloc[0]))
    return success(detail)
