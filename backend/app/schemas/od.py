"""OD 查询和响应约定。"""

from typing import Literal
from datetime import datetime

from pydantic import BaseModel

TimePeriod = Literal["morning_peak", "evening_peak", "normal", "transit"]
Dataset = Literal["original", "computed"]


class OdPair(BaseModel):
    origin_road_id: str
    origin_road_name: str
    dest_road_id: str
    dest_road_name: str
    trip_count: int
    avg_duration_min: float | None


class OdPage(BaseModel):
    items: list[OdPair]
    total: int
    page: int
    page_size: int
    pages: int


class OdListResponse(BaseModel):
    code: Literal[0] = 0
    data: OdPage
    msg: str = ""


class RoadAxis(BaseModel):
    road_id: str
    road_name: str


class OdMatrixData(BaseModel):
    origins: list[RoadAxis]
    destinations: list[RoadAxis]
    counts: list[list[int]]
    total_trip_count: int
    displayed_trip_count: int


class OdMatrixResponse(BaseModel):
    code: Literal[0] = 0
    data: OdMatrixData
    msg: str = ""


class OdTopData(BaseModel):
    items: list[OdPair]


class OdTopResponse(BaseModel):
    code: Literal[0] = 0
    data: OdTopData
    msg: str = ""


class ComputationStats(BaseModel):
    input_point_count: int
    matched_point_count: int
    total_segments: int
    trip_count: int
    od_rows: int
    skipped_segments: int
    periods: dict[str, int]


class ComputationStatus(BaseModel):
    available: bool
    running: bool
    completed_at: datetime | None
    stats: ComputationStats | None


class ComputationResponse(BaseModel):
    code: Literal[0] = 0
    data: ComputationStatus
    msg: str = ""
