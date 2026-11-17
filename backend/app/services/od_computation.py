"""手动 OD 重算：读原始业务表，事务写独立快照，不覆盖导入结果。"""

import logging
from datetime import datetime, timezone
from threading import Lock

import numpy as np
import pandas as pd
from sqlalchemy import delete, func, insert, inspect, select
from sqlalchemy.orm import Session

from app.core import BusinessException
from app.models.clean_trajectory import CleanTrajectory
from app.models.computed_od import Computation, ComputedBase, ComputedOd
from app.models.match_result import MatchResult
from app.models.road_segment import RoadSegment

logger = logging.getLogger("traffic.od")
compute_lock = Lock()


def computation_status(db: Session) -> dict:
    if not inspect(db.get_bind()).has_table(Computation.__tablename__):
        return {"available": False, "running": compute_lock.locked(), "completed_at": None, "stats": None}
    run = db.get(Computation, 1)
    return {
        "available": run is not None,
        "running": compute_lock.locked(),
        "completed_at": run.completed_at.replace(tzinfo=timezone.utc).isoformat() if run else None,
        "stats": run.stats if run else None,
    }


def calculate_records(db: Session) -> tuple[list[dict], dict]:
    segment_keys = select(CleanTrajectory.vehicle_id, CleanTrajectory.segment_id).distinct().subquery()
    total_segments = db.scalar(select(func.count()).select_from(segment_keys))
    input_points = db.scalar(select(func.count()).select_from(CleanTrajectory))
    query = select(
        CleanTrajectory.id, CleanTrajectory.vehicle_id, CleanTrajectory.segment_id,
        CleanTrajectory.timestamp, MatchResult.road_id,
    ).join(MatchResult, MatchResult.clean_trajectory_id == CleanTrajectory.id).join(
        RoadSegment, RoadSegment.road_id == MatchResult.road_id,
    ).where(MatchResult.is_matched == 1)
    frame = pd.DataFrame(db.execute(query).all(), columns=["point_id", "vehicle_id", "segment_id", "timestamp", "road_id"])
    if frame.point_id.duplicated().any():
        raise BusinessException("清洗点存在重复匹配，请先修正地图匹配结果后重算")
    frame = frame.loc[
        frame.vehicle_id.notna() & frame.segment_id.notna()
        & frame.vehicle_id.str.strip().ne("") & frame.segment_id.str.strip().ne("")
    ]
    stats = {"input_point_count": input_points, "matched_point_count": len(frame),
             "total_segments": total_segments, "trip_count": 0, "od_rows": 0,
             "skipped_segments": total_segments, "periods": {"morning_peak": 0, "evening_peak": 0, "normal": 0}}
    if frame.empty:
        return [], stats
    frame["timestamp"] = pd.to_datetime(frame.timestamp, errors="raise")
    frame = frame.sort_values(["vehicle_id", "segment_id", "timestamp", "point_id"])
    trips = frame.groupby(["vehicle_id", "segment_id"], sort=False).agg(
        origin_road_id=("road_id", "first"), dest_road_id=("road_id", "last"),
        started=("timestamp", "first"), ended=("timestamp", "last"),
    ).reset_index(drop=True)
    trips["duration"] = (trips.ended - trips.started).dt.total_seconds() / 60
    trips = trips.loc[trips.duration.gt(0)].copy()
    hours = trips.started.dt.hour
    trips["time_period"] = np.select(
        [hours.ge(7) & hours.lt(10), hours.ge(17) & hours.lt(20)],
        ["morning_peak", "evening_peak"], default="normal",
    )
    result = trips.groupby(["origin_road_id", "dest_road_id", "time_period"], as_index=False).agg(
        trip_count=("duration", "size"), avg_duration_min=("duration", "mean"),
    )
    stats.update(trip_count=len(trips), od_rows=len(result), skipped_segments=total_segments-len(trips))
    stats["periods"].update({str(key): int(value) for key, value in trips.time_period.value_counts().items()})
    return result.to_dict(orient="records"), stats


def recompute(db: Session) -> dict:
    if not compute_lock.acquire(blocking=False):
        raise BusinessException("OD 重算正在进行，请稍后刷新结果", code=4090)
    try:
        ComputedBase.metadata.create_all(db.get_bind())
        records, stats = calculate_records(db)
        db.execute(delete(ComputedOd))
        if records:
            db.execute(insert(ComputedOd), records)
        db.merge(Computation(id=1, completed_at=datetime.now(timezone.utc).replace(tzinfo=None), stats=stats))
        db.commit()
    except BusinessException:
        db.rollback()
        raise
    except Exception as exc:
        db.rollback()
        logger.exception("OD 重算失败，事务已回滚")
        raise BusinessException("重算失败，原有结果已保留；请检查轨迹及匹配数据后重试", code=5001) from exc
    finally:
        compute_lock.release()
    return computation_status(db)
