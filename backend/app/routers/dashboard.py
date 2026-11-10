"""监控大屏路由：只读聚合，一个接口够一个页面用。"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core import get_db, success
from app.services import dashboard_service

router = APIRouter(prefix="/dashboard", tags=["监控大屏"])


@router.get("/summary")
def get_summary(
    db: Session = Depends(get_db),
    top_n: int = Query(10, ge=1, le=50, description="拥堵路段排名条数"),
):
    """四张指标卡 + 拥堵路段 Top N + 数据源统计。全部后端算完再返回，前端不碰原始点。"""
    return success(dashboard_service.build_summary(db, top_n=top_n))
