"""OD 只读聚合：先按类别筛选和 OD 对汇总，再关联名称和分页。"""

from sqlalchemy import Float, case, cast, func, select
from sqlalchemy.orm import Session, aliased

from app.core import BusinessException, page
from app.models.computed_od import ComputedOd
from app.models.od_matrix import OdMatrix
from app.models.road_segment import RoadSegment
from app.services.od_computation import computation_status


def aggregate_pairs(time_period: str | None = None, dataset: str = "original"):
    model = ComputedOd if dataset == "computed" else OdMatrix
    valid_weight = func.sum(case(
        (model.avg_duration_min.is_not(None), model.trip_count), else_=0,
    ))
    duration_sum = func.sum(model.avg_duration_min * model.trip_count)
    query = select(
        model.origin_road_id,
        model.dest_road_id,
        func.sum(model.trip_count).label("trip_count"),
        func.round(duration_sum / func.nullif(cast(valid_weight, Float), 0), 2).label("avg_duration_min"),
    ).group_by(model.origin_road_id, model.dest_road_id)
    if time_period is not None:
        query = query.where(model.time_period == time_period)
    return query.subquery()


def selected_pairs(db: Session, time_period: str | None, dataset: str):
    if dataset == "computed" and not computation_status(db)["available"]:
        raise BusinessException("尚无轨迹重算结果，请先执行重算")
    return aggregate_pairs(time_period, dataset)


def named_pairs(pairs):
    origin = aliased(RoadSegment)
    dest = aliased(RoadSegment)
    return select(
        pairs.c.origin_road_id,
        func.coalesce(func.nullif(func.trim(origin.road_name), ""), pairs.c.origin_road_id).label("origin_road_name"),
        pairs.c.dest_road_id,
        func.coalesce(func.nullif(func.trim(dest.road_name), ""), pairs.c.dest_road_id).label("dest_road_name"),
        pairs.c.trip_count,
        pairs.c.avg_duration_min,
    ).select_from(pairs).outerjoin(
        origin, origin.road_id == pairs.c.origin_road_id,
    ).outerjoin(dest, dest.road_id == pairs.c.dest_road_id)


def pair_order(pairs):
    return (pairs.c.trip_count.desc(), pairs.c.origin_road_id, pairs.c.dest_road_id)


def list_pairs(db: Session, time_period: str | None, page_no: int, page_size: int, dataset: str = "original") -> dict:
    pairs = selected_pairs(db, time_period, dataset)
    total = db.scalar(select(func.count()).select_from(pairs))
    offset = (page_no - 1) * page_size
    items = []
    if offset < total:
        query = named_pairs(pairs).order_by(*pair_order(pairs)).offset(offset).limit(page_size)
        items = [dict(row) for row in db.execute(query).mappings()]
    return page(items, total, page_no, page_size)


def matrix(db: Session, time_period: str | None, dataset: str = "original") -> dict:
    pairs = selected_pairs(db, time_period, dataset)
    total = db.scalar(select(func.coalesce(func.sum(pairs.c.trip_count), 0)))

    def axis(column):
        volume = func.sum(pairs.c.trip_count)
        return list(db.scalars(select(column).group_by(column).order_by(volume.desc(), column).limit(20)))

    origins = axis(pairs.c.origin_road_id)
    destinations = axis(pairs.c.dest_road_id)
    ids = set(origins + destinations)
    names = {road.road_id: road.road_name.strip() or road.road_id
             for road in db.scalars(select(RoadSegment).where(RoadSegment.road_id.in_(ids)))} if ids else {}
    values = {(row.origin_road_id, row.dest_road_id): row.trip_count for row in db.execute(
        select(pairs).where(pairs.c.origin_road_id.in_(origins), pairs.c.dest_road_id.in_(destinations))
    )} if ids else {}
    counts = [[values.get((origin, dest), 0) for dest in destinations] for origin in origins]
    return {
        "origins": [{"road_id": key, "road_name": names.get(key, key)} for key in origins],
        "destinations": [{"road_id": key, "road_name": names.get(key, key)} for key in destinations],
        "counts": counts,
        "total_trip_count": total,
        "displayed_trip_count": sum(map(sum, counts)),
    }


def top_pairs(db: Session, time_period: str | None, limit: int, dataset: str = "original") -> dict:
    pairs = selected_pairs(db, time_period, dataset)
    rows = db.execute(named_pairs(pairs).order_by(*pair_order(pairs)).limit(limit)).mappings()
    return {"items": [dict(row) for row in rows]}
