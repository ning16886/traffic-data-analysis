"""OD HTTP 查询测试：生命周期和会话均隔离，不创建默认数据库。"""

import hashlib
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core import Base, get_db
from app.main import app
from app.models.od_matrix import OdMatrix
from app.models.road_segment import RoadSegment


class QueryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "query.db"
        self.engine = create_engine(f"sqlite:///{self.path}", connect_args={"check_same_thread": False})
        self.addCleanup(self.engine.dispose)
        Base.metadata.create_all(self.engine, tables=[RoadSegment.__table__, OdMatrix.__table__])
        self.sessions = sessionmaker(self.engine)

        def override_db():
            with self.sessions() as db:
                yield db

        app.dependency_overrides[get_db] = override_db
        self.addCleanup(app.dependency_overrides.clear)
        startup = patch("app.main.init_database")
        startup.start()
        self.addCleanup(startup.stop)
        self.client = TestClient(app)
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)
        with self.sessions.begin() as db:
            for key, name in [("A", "甲路"), ("B", "乙路"), ("C", " ")]:
                db.add(RoadSegment(road_id=key, road_name=name, level="main", length=1,
                                   geometry="LINESTRING(0 0,1 1)", bounds_min_lng=0,
                                   bounds_min_lat=0, bounds_max_lng=1, bounds_max_lat=1))

    def add(self, origin="A", dest="B", period="normal", trips=2, duration=10):
        with self.sessions.begin() as db:
            db.add(OdMatrix(origin_road_id=origin, dest_road_id=dest, time_period=period,
                            trip_count=trips, avg_duration_min=duration))

    def get(self, **params):
        response = self.client.get("/api/od/list", params=params)
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["code"], 0)
        return body["data"]

    def test_aggregation_and_weighted_duration(self):
        self.add()
        self.add(period="morning_peak", trips=3, duration=20)
        result = self.get()
        self.assertEqual((result["total"], result["pages"]), (1, 1))
        item = result["items"][0]
        self.assertEqual((item["trip_count"], item["avg_duration_min"]), (5, 16))
        self.assertEqual((item["origin_road_name"], item["dest_road_name"]), ("甲路", "乙路"))
        self.assertNotIn("id", item)

    def test_all_period_filters(self):
        for period in ("normal", "morning_peak", "evening_peak", "transit"):
            self.add(period=period)
            self.assertEqual(self.get(time_period=period)["items"][0]["trip_count"], 2)
        self.assertEqual(self.get()["items"][0]["trip_count"], 8)

    def test_partial_and_all_null_duration(self):
        self.add(duration=None)
        self.add(period="transit", trips=3, duration=20)
        self.add(origin="B", dest="A", duration=None)
        items = self.get()["items"]
        self.assertEqual((items[0]["trip_count"], items[0]["avg_duration_min"]), (5, 20))
        self.assertIsNone(items[1]["avg_duration_min"])

    def test_zero_duration_and_same_road(self):
        self.add(dest="A", duration=0)
        self.assertEqual(self.get()["items"][0]["avg_duration_min"], 0)

    def test_missing_and_blank_names(self):
        self.add(origin="missing", dest="C")
        item = self.get()["items"][0]
        self.assertEqual((item["origin_road_name"], item["dest_road_name"]), ("missing", "C"))

    def test_pagination_stable_and_out_of_range(self):
        for origin, dest in [("B", "C"), ("A", "C"), ("A", "B")]:
            self.add(origin=origin, dest=dest)
        items = [self.get(page=p, page_size=1)["items"][0] for p in (1, 2, 3)]
        self.assertEqual([(r["origin_road_id"], r["dest_road_id"]) for r in items],
                         [("A", "B"), ("A", "C"), ("B", "C")])
        result = self.get(page=10**30, page_size=2)
        self.assertEqual((result["items"], result["total"], result["pages"]), ([], 3, 2))

    def test_empty_database_and_empty_filter(self):
        result = self.get()
        self.assertEqual((result["items"], result["total"], result["pages"]), ([], 0, 0))
        self.add()
        self.assertEqual(self.get(time_period="transit")["total"], 0)

    def test_invalid_parameters(self):
        for params in ({"page": 0}, {"page": "abc"}, {"page_size": 101}, {"page_size": 0},
                       {"time_period": "all"}, {"time_period": ""}, {"time_period": "other"}):
            with self.subTest(params=params):
                response = self.client.get("/api/od/list", params=params)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()["code"], 4220)

    def test_read_only_and_openapi(self):
        self.add()
        before = hashlib.sha256(self.path.read_bytes()).digest()
        self.get()
        self.assertEqual(hashlib.sha256(self.path.read_bytes()).digest(), before)
        paths = self.client.get("/openapi.json").json()["paths"]
        self.assertIn("/api/od/list", paths)

    def test_matrix_counts_axes_and_filter(self):
        self.add(trips=2)
        self.add(period="transit", trips=3)
        self.add(origin="B", dest="C", trips=1)
        data = self.client.get("/api/od/matrix").json()["data"]
        self.assertEqual(data["counts"], [[5, 0], [0, 1]])
        self.assertEqual((data["total_trip_count"], data["displayed_trip_count"]), (6, 6))
        self.assertEqual(data["destinations"][1]["road_name"], "C")
        data = self.client.get("/api/od/matrix?time_period=transit").json()["data"]
        self.assertEqual(data["counts"], [[3]])

    def test_matrix_empty_and_twenty_axis_limit(self):
        self.assertEqual(self.client.get("/api/od/matrix").json()["data"]["counts"], [])
        for i in range(25):
            self.add(origin=f"R{i:02}", dest=f"R{i:02}", trips=1)
        data = self.client.get("/api/od/matrix").json()["data"]
        self.assertEqual(len(data["origins"]), 20)
        self.assertEqual([x["road_id"] for x in data["origins"]], [f"R{i:02}" for i in range(20)])
        self.assertEqual((data["total_trip_count"], data["displayed_trip_count"]), (25, 20))

    def test_top_ranking_and_filter(self):
        for i in range(25):
            self.add(origin=f"R{i:02}", trips=25-i)
        data = self.client.get("/api/od/top").json()["data"]["items"]
        self.assertEqual(len(data), 20)
        self.assertEqual([r["trip_count"] for r in data], list(range(25, 5, -1)))
        self.assertEqual(self.client.get("/api/od/top?limit=1").json()["data"]["items"], data[:1])
        self.assertEqual(self.client.get("/api/od/top?time_period=transit").json()["data"]["items"], [])
        for limit in (0, 21, "abc"):
            self.assertEqual(self.client.get(f"/api/od/top?limit={limit}").json()["code"], 4220)

    def test_top_empty_ties_and_short_result(self):
        self.assertEqual(self.client.get("/api/od/top").json()["data"]["items"], [])
        self.add(origin="B")
        self.add()
        rows = self.client.get("/api/od/top").json()["data"]["items"]
        self.assertEqual([r["origin_road_id"] for r in rows], ["A", "B"])


if __name__ == "__main__":
    unittest.main()
