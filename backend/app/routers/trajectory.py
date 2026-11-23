"""轨迹管理路由：数据源清洗按钮 / 清洗统计 / 轨迹点分页查询。

清洗接口是同步阻塞的（50 万点向量化清洗实测十几秒，SQLite 批量写入占大头），
路由用 def 而不是 async def，FastAPI 会自动丢进线程池，不卡事件循环。
"""

from datetime import datetime

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import distinct, func, select
from sqlalchemy.orm import Session

from app.config import settings
from app.core import BusinessException, get_db, page, success
from app.models import CleanTrajectory, DataSource, RawTrajectory
from app.services.trajectory_cleaner import run_cleaning

router = APIRouter(prefix="/trajectory", tags=["轨迹管理"])


class CleanPayload(BaseModel):
    source_id: int | None = None  # 不传=全量清洗


class CleanConfigPayload(BaseModel):
    drift_speed_kmh: float | None = None
    drift_jump_m: float | None = None
    drift_jump_seconds: float | None = None
    stay_seconds: int | None = None
    stay_radius_m: float | None = None
    gap_seconds: int | None = None
    trip_break_seconds: int | None = None


@router.get("/list")
def list_trajectories(
    db: Session = Depends(get_db),
    page_no: int = Query(1, ge=1, alias="page"),
    page_size: int = Query(20, ge=1, le=200),
    vehicle_id: str | None = None,
    source_id: int | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
):
    """清洗后轨迹点分页查询，按车辆 / 数据源 / 时间范围筛选。"""
    conds = []
    if vehicle_id:
        conds.append(CleanTrajectory.vehicle_id == vehicle_id)
    if source_id:
        conds.append(CleanTrajectory.source_id == source_id)
    if start:
        conds.append(CleanTrajectory.timestamp >= start)
    if end:
        conds.append(CleanTrajectory.timestamp < end)

    total = db.scalar(select(func.count()).select_from(CleanTrajectory).where(*conds)) or 0
    rows = db.execute(
        select(CleanTrajectory)
        .where(*conds)
        .order_by(CleanTrajectory.timestamp, CleanTrajectory.id)
        .offset((page_no - 1) * page_size)
        .limit(page_size)
    ).scalars().all()
    items = [
        {
            "id": r.id,
            "vehicle_id": r.vehicle_id,
            "source_id": r.source_id,
            "segment_id": r.segment_id,
            "timestamp": r.timestamp.strftime("%Y-%m-%d %H:%M:%S"),
            "longitude": r.longitude,
            "latitude": r.latitude,
            "speed": r.speed,
            "is_stay": r.is_stay,
            "is_gap": r.is_gap,
            "flag": r.flag,
        }
        for r in rows
    ]
    return success(page(items, total, page_no, page_size))


@router.get("/clean-compare")
def clean_compare(db: Session = Depends(get_db)):
    """清洗前后对比统计，轨迹管理页「清洗统计」卡片的数据来源。

    丢弃点数 = 原始点数 − 清洗后点数（只有漂移点会被删除，停留/隧道只打标记）。
    清洗阶段 = 轨迹分段数（segment_id 去重），即「切成了多少段」。
    """
    raw_count = db.scalar(select(func.count()).select_from(RawTrajectory)) or 0
    clean_count = db.scalar(select(func.count()).select_from(CleanTrajectory)) or 0
    stay_points = db.scalar(
        select(func.count()).select_from(CleanTrajectory).where(CleanTrajectory.is_stay == 1)
    ) or 0
    gap_points = db.scalar(
        select(func.count()).select_from(CleanTrajectory).where(CleanTrajectory.is_gap == 1)
    ) or 0
    segments = db.scalar(select(func.count(distinct(CleanTrajectory.segment_id)))) or 0
    return success(
        {
            "raw_count": raw_count,
            "clean_count": clean_count,
            "dropped_count": raw_count - clean_count,
            "segments": segments,
            "stay_points": stay_points,
            "gap_points": gap_points,
        }
    )


@router.post("/clean")
def rerun_clean(payload: CleanPayload | None = None, db: Session = Depends(get_db)):
    """一键重跑清洗管道。传 source_id 时只清洗该数据源的车辆，否则全量。"""
    source_ids = None
    if payload and payload.source_id is not None:
        if db.get(DataSource, payload.source_id) is None:
            raise BusinessException(f"数据源 {payload.source_id} 不存在", code=4040)
        source_ids = [payload.source_id]
    stats = run_cleaning(db, source_ids=source_ids)
    return success(stats)


@router.get("/clean-config")
def get_clean_config():
    """清洗阈值配置（与 config.py 同源），轨迹管理页清洗配置表单回显用。"""
    return success(
        {
            "drift_speed_kmh": settings.drift_speed_kmh,
            "drift_jump_m": settings.drift_jump_m,
            "drift_jump_seconds": settings.drift_jump_seconds,
            "stay_seconds": settings.stay_seconds,
            "stay_radius_m": settings.stay_radius_m,
            "gap_seconds": settings.gap_seconds,
            "trip_break_seconds": settings.trip_break_seconds,
        }
    )


@router.put("/clean-config")
def save_clean_config(payload: CleanConfigPayload):
    """运行时调整清洗阈值（写内存单例，重启后回到 config.py 默认值）。"""
    for field, value in payload.model_dump(exclude_none=True).items():
        setattr(settings, field, value)
    return get_clean_config()
