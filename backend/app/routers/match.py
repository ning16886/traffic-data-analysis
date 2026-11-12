"""地图匹配路由：三层可视化页面所需的全部接口。

四个接口覆盖页面所有动作：
    GET  /api/match/vehicles            车辆统计表（含全量汇总，一次请求够画页面骨架）
    GET  /api/match/tracks/{vehicleId}  单车三层数据（原始轨迹 + 匹配路段 + 未匹配点）
    GET  /api/match/tracks/{vehicleId}/export  导出当前阈值下的匹配结果
    POST /api/match/run                 重新匹配（换阈值 / 数据变了之后手动触发）

阈值统一 10~500m：太小会把正常 GPS 抖动判成未匹配，太大等于关掉阈值。
"""

import csv
import io

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse

from app.core import error, settings, success
from app.schemas.match import (
    ApiResponse,
    RematchData,
    RematchRequest,
    VehicleListData,
)
from app.services.matching import MatchService, get_service

router = APIRouter(prefix="/match", tags=["地图匹配"])

THRESHOLD = Query(None, ge=10, le=500, description="垂距阈值（米），默认取 settings.match_threshold_m")


def _threshold(threshold: float | None) -> float:
    return settings.match_threshold_m if threshold is None else float(threshold)


@router.get("/vehicles", response_model=ApiResponse[VehicleListData], summary="车辆匹配统计")
def list_vehicles(
    threshold: float | None = THRESHOLD,
    keyword: str = Query("", max_length=32, description="车辆 ID 模糊搜索"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200, alias="pageSize"),
    sort_by: str = Query("vehicleId", alias="sortBy",
                         pattern="^(vehicleId|total|matched|matchRate|unmatched|roadCount)$"),
    order: str = Query("asc", pattern="^(asc|desc)$"),
    svc: MatchService = Depends(get_service),
):
    """一次返回 summary（总点/匹配/未匹配/路段数）+ 分页的车辆列表。"""
    return success(svc.vehicles_page(
        _threshold(threshold), keyword=keyword, page_no=page, page_size=page_size,
        sort_by=sort_by, order=order,
    ))


@router.get("/tracks/{vehicle_id}", summary="单车三层匹配数据")
def get_track(
    vehicle_id: str,
    threshold: float | None = THRESHOLD,
    segment_id: str | None = Query(None, alias="segmentId", description="只取某一个行程"),
    svc: MatchService = Depends(get_service),
):
    """点表里同时带 matched 标志，前端按它分蓝/红两组画，不要再调第二个接口。

    响应结构见 app/schemas/match.py::TrackData（约 1MB/车，8k 点量级）。
    """
    data = svc.track(_threshold(threshold), vehicle_id, segment_id=segment_id)
    if data is None:
        return error(404, f"车辆 {vehicle_id} 不存在")
    return success(data)


@router.get("/tracks/{vehicle_id}/export", summary="导出单车匹配结果 CSV")
def export_track(
    vehicle_id: str,
    threshold: float | None = THRESHOLD,
    svc: MatchService = Depends(get_service),
):
    data = svc.track(_threshold(threshold), vehicle_id)
    if data is None:
        return error(404, f"车辆 {vehicle_id} 不存在")

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([
        "clean_trajectory_id", "vehicle_id", "timestamp", "longitude", "latitude",
        "matched", "road_id", "match_longitude", "match_latitude", "distance_m", "match_probability",
    ])
    for p in data["points"]:
        writer.writerow([
            p["id"], data["vehicleId"], p["ts"], p["lng"], p["lat"], int(p["matched"]),
            p["roadId"] or "", p["snapLng"], p["snapLat"],
            "" if p["distanceM"] is None else p["distanceM"], p["probability"],
        ])
    body = "\ufeff" + buf.getvalue()  # BOM：Excel 直接打开不乱码
    filename = f"match_{vehicle_id}_{int(data['threshold'])}m.csv"
    return StreamingResponse(
        iter([body]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/run", response_model=ApiResponse[RematchData], summary="重新匹配")
def rematch(body: RematchRequest, svc: MatchService = Depends(get_service)):
    """全量重算（48 万点约 10s）。换阈值后页面调这个，再拉一次 /vehicles 即可。"""
    return success(svc.rematch(body.threshold))
