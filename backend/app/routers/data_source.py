"""数据源管理路由：轨迹管理页「数据源管理」表格的数据来源。

列表复用大屏服务的对账口径：登记表自报数 + raw_trajectory 实测数一起返回，
实测数优先展示，避免登记表数字过期后页面与实际对不上。
"""

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core import BusinessException, get_db, success
from app.models import DataSource
from app.services import dashboard_service

router = APIRouter(prefix="/data_source", tags=["数据源"])


class StatusPayload(BaseModel):
    status: int  # 1=启用 0=停用


@router.get("/list")
def list_data_sources(db: Session = Depends(get_db)):
    """数据源列表（含实测车辆数/轨迹点数），轨迹管理页表格直接用。"""
    return success(dashboard_service._data_sources(db))


@router.put("/{source_id}/status")
def switch_status(source_id: int, payload: StatusPayload, db: Session = Depends(get_db)):
    """只更新数据源登记状态，供页面标识展示。

    注意：status 不参与清洗、匹配与统计的过滤。下游的 match_result 按点落表、
    road_metrics 按路段与时间窗聚合、od_matrix 按起止路段与时段聚合，三张表都没有
    数据源维度，因此停用一个数据源不会改变已落库的数据，也不会改变任何统计口径。
    轨迹管理页已不提供启停入口，此接口仅保留登记状态维护能力。
    """
    if payload.status not in (0, 1):
        raise BusinessException("status 只能是 0（停用）或 1（启用）", code=4000)
    row = db.get(DataSource, source_id)
    if row is None:
        raise BusinessException(f"数据源 {source_id} 不存在", code=4040)
    row.status = payload.status
    db.commit()
    return success({"id": row.id, "status": row.status})
