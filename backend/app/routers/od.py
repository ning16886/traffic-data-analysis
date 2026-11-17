"""OD 子页面接口，沿用全局响应和异常处理。"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core import get_db, success
from app.schemas.od import ComputationResponse, Dataset, OdListResponse, OdMatrixResponse, OdTopResponse, TimePeriod
from app.services import od_computation, od_service

router = APIRouter(prefix="/od", tags=["OD 出行分析"])


@router.get("/list", response_model=OdListResponse)
def list_od(
    time_period: TimePeriod | None = Query(None, description="省略表示全部类别"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    dataset: Dataset = Query("original", description="原始模拟或轨迹重算结果"),
    db: Session = Depends(get_db),
):
    """按 OD 对聚合的分页列表；平均时长按有效出行次数加权。"""
    return success(od_service.list_pairs(db, time_period, page, page_size, dataset))


@router.get("/matrix", response_model=OdMatrixResponse)
def od_matrix(
    time_period: TimePeriod | None = Query(None),
    dataset: Dataset = Query("original"),
    db: Session = Depends(get_db),
):
    """按全量筛选结果选取各 20 个重点起终路段，返回子矩阵及覆盖次数。"""
    return success(od_service.matrix(db, time_period, dataset))


@router.get("/top", response_model=OdTopResponse)
def top_od(
    time_period: TimePeriod | None = Query(None),
    limit: int = Query(20, ge=1, le=20),
    dataset: Dataset = Query("original"),
    db: Session = Depends(get_db),
):
    """在整个筛选结果中排名，与列表分页无关。"""
    return success(od_service.top_pairs(db, time_period, limit, dataset))


@router.get("/computation", response_model=ComputationResponse)
def get_computation(db: Session = Depends(get_db)):
    return success(od_computation.computation_status(db))


@router.post("/recompute", response_model=ComputationResponse)
def recompute_od(db: Session = Depends(get_db)):
    """手动重算并原子替换独立快照，原始模拟结果保持不变。"""
    return success(od_computation.recompute(db))
