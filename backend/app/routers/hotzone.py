"""出行热区路由：地图点、分页列表、详情、统计与一键重算。"""

from fastapi import APIRouter, Body, Depends, Query
from sqlalchemy.orm import Session

from app.core import get_db, success
from app.schemas.hotzone import HotzoneRecomputeRequest
from app.services import hotzone_service

router = APIRouter(prefix="/hotzone", tags=["出行热区"])


@router.get("/summary")
def get_summary(db: Session = Depends(get_db)):
    """类型计数、点数合计、地图中心与当前 DBSCAN 参数。"""
    return success(hotzone_service.summary(db))


@router.get("/map")
def get_map(
    zone_type: str | None = Query(None, description="residential/business/transit，空表示全部"),
    db: Session = Depends(get_db),
):
    """ECharts effectScatter 用的完整点集（151 条量级，无需分页）。"""
    return success({
        "zones": hotzone_service.map_zones(db, zone_type),
        "max_points": hotzone_service.max_points(db),
    })


@router.get("/list")
def list_zones(
    zone_type: str | None = Query(None, description="residential/business/transit"),
    keyword: str | None = Query(None, description="按 zone_id 模糊搜索"),
    min_points: int = Query(0, ge=0, le=100_000, description="点数下限"),
    page_no: int = Query(1, ge=1, alias="page", description="页码，从 1 开始"),
    page_size: int = Query(20, ge=1, le=200, description="每页条数"),
    sort_by: str = Query("point_count", pattern="^(point_count|radius_m|zone_id)$"),
    db: Session = Depends(get_db),
):
    """热区详情表格：服务端分页 + 筛选 + 排序。"""
    return success(
        hotzone_service.list_zones(db, zone_type, keyword, min_points, page_no, page_size, sort_by)
    )


@router.post("/recompute")
def recompute(
    body: HotzoneRecomputeRequest = Body(default_factory=HotzoneRecomputeRequest),
    db: Session = Depends(get_db),
):
    """从 clean_trajectory + match_result 现算 DBSCAN 并替换 hotzone 表。

    请求体可省略；要调参时传 {"eps": 500, "min_samples": 10, ...}。
    注意：重算结果会覆盖 import_raw_data.py 从 data/raw/hotzone.csv 导入的行。
    """
    return success(
        hotzone_service.recompute(
            db,
            eps=body.eps,
            min_samples=body.min_samples,
            min_points=body.min_points,
            max_zones=body.max_zones,
            sample_ratio=body.sample_ratio,
        )
    )


@router.get("/{zone_id}")
def get_zone(zone_id: str, db: Session = Depends(get_db)):
    """单个热区详情与排名（点击地图散点时用）。"""
    return success(hotzone_service.get_zone(db, zone_id))
