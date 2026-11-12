"""地图匹配接口的出入参模型（Pydantic v2）。

出参统一套 app/core/response.py 的信封：{"code": 0, "data": {...}, "msg": ""}，
所以这里是 data 内部的结构，字段名一律 camelCase，与前端 src/api/match.js 对齐。
"""

from typing import Generic, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")

THRESHOLD_MIN = 10.0
THRESHOLD_MAX = 500.0


class ApiResponse(BaseModel, Generic[T]):
    """统一响应信封（仅用于 Swagger 展示与出参校验）。"""

    code: int = 0
    data: T | None = None
    msg: str = ""


# ---------------------------------------------------------------- 统计
class DataRange(BaseModel):
    start: str | None = None
    end: str | None = None


class MatchSummary(BaseModel):
    totalPoints: int
    matched: int
    unmatched: int
    matchRate: float
    roadCount: int = Field(description="本次匹配命中的不同路段数")
    totalRoadCount: int = Field(description="路网路段总数")
    segmentCount: int = Field(description="路网拆成的最短线元数")
    vehicleCount: int
    tripCount: int
    avgDistanceM: float | None = None
    gapPoints: int = Field(description="隧道断链点（flag != ok），不参与匹配率分母剔除")
    stayPoints: int
    dataRange: DataRange


class VehicleMatchItem(BaseModel):
    vehicleId: str
    sourceName: str
    total: int
    matched: int
    unmatched: int
    matchRate: float
    roadCount: int
    firstTs: str | None = None
    lastTs: str | None = None


class VehicleListData(BaseModel):
    threshold: float
    summary: MatchSummary
    params: dict
    elapsedMs: int
    cachedAt: str
    cacheHit: bool
    items: list[VehicleMatchItem]
    total: int
    page: int
    pageSize: int
    pages: int


# ---------------------------------------------------------------- 单车三层
class MatchPoint(BaseModel):
    id: int
    lng: float
    lat: float
    ts: str | None = None
    speed: float | None = None
    direction: float | None = None
    isGap: bool = False
    isStay: bool = False
    segmentId: str | None = None
    matched: bool
    roadId: str | None = None
    roadName: str | None = None
    level: str | None = None
    snapLng: float
    snapLat: float
    distanceM: float | None = None
    probability: float


class TrackRoad(BaseModel):
    roadId: str
    roadName: str
    level: str
    coords: list[list[float]]
    pointCount: int


class TrackTrip(BaseModel):
    segmentId: str
    start: str | None = None
    end: str | None = None
    total: int
    matched: int
    unmatched: int
    matchRate: float
    startPoint: list[float]
    endPoint: list[float]


class TrackStats(BaseModel):
    total: int
    matched: int
    unmatched: int
    matchRate: float
    roadCount: int
    tripCount: int


class NetworkRoad(BaseModel):
    roadId: str
    level: str
    coords: list[list[float]]


class TrackData(BaseModel):
    vehicleId: str
    sourceName: str
    threshold: float
    stats: TrackStats
    points: list[MatchPoint]
    roads: list[TrackRoad]
    trips: list[TrackTrip]
    network: list[NetworkRoad] = Field(description="全路网几何，前端画浅色底图用")


# ---------------------------------------------------------------- 重新匹配
class RematchRequest(BaseModel):
    threshold: float = Field(100.0, ge=THRESHOLD_MIN, le=THRESHOLD_MAX,
                             description="垂距阈值（米），10~500")
    vehicleId: str | None = Field(None, description="预留：只重算某辆车，当前为全量重算")


class RematchData(BaseModel):
    threshold: float
    summary: MatchSummary
    elapsedMs: int
    cachedAt: str
