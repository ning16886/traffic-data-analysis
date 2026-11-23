"""重算业务与事务测试，使用临时数据库，不修改原始基线。"""

from datetime import datetime, timedelta
import unittest
from unittest.mock import patch

from sqlalchemy import event

import test_od_query as query_fixture
from app.core import Base
from app.models.clean_trajectory import CleanTrajectory
from app.models.match_result import MatchResult
from app.services.od_computation import compute_lock


class ComputeTests(unittest.TestCase):
    # 复用夹具方法而不继承查询用例，避免整组查询测试被重复执行。
    def setUp(self):
        query_fixture.QueryTests.setUp(self)
        Base.metadata.create_all(self.engine)
        self.point_id = 0

    def point(self, vehicle, segment, when, road="A", matched=True):
        self.point_id += 1
        with self.sessions.begin() as db:
            db.add(CleanTrajectory(id=self.point_id, vehicle_id=vehicle, segment_id=segment,
                                   source_id=1, timestamp=when, longitude=0, latitude=0, speed=10))
            db.add(MatchResult(vehicle_id=vehicle, clean_trajectory_id=self.point_id,
                               road_id=road, is_matched=int(matched), match_probability=1,
                               match_longitude=0, match_latitude=0))

    def trip(self, when="07:00", minutes=2, vehicle="v", segment="s", end_road="B"):
        started = datetime.fromisoformat(f"2024-03-04T{when}")
        self.point(vehicle, segment, started)
        self.point(vehicle, segment, started + timedelta(minutes=minutes), road=end_road)

    def run_compute(self):
        body = self.client.post("/api/od/recompute").json()
        self.assertEqual(body["code"], 0, body)
        self.assertTrue(body["data"]["available"])
        self.assertFalse(body["data"]["running"])
        return body["data"]

    def computed(self, **params):
        return self.client.get("/api/od/list", params={"dataset": "computed", **params}).json()["data"]

    def test_not_available_then_valid_empty_snapshot(self):
        self.assertFalse(self.client.get("/api/od/computation").json()["data"]["available"])
        self.assertEqual(self.client.get("/api/od/list?dataset=computed").json()["code"], 4000)
        self.assertEqual(self.run_compute()["stats"]["trip_count"], 0)
        self.assertEqual(self.computed()["total"], 0)
        self.assertNotIn("od_computed", Base.metadata.tables)

    def test_grouping_duration_idempotency_and_original_protection(self):
        query_fixture.QueryTests.add(self)
        original = self.client.get("/api/od/list").json()
        self.trip(minutes=1)
        self.trip(minutes=2, segment="other")
        self.trip(minutes=3, vehicle="another")
        first = self.run_compute()
        row = self.computed()["items"][0]
        self.assertEqual((row["trip_count"], row["avg_duration_min"]), (3, 2))
        self.assertEqual(self.run_compute()["stats"], first["stats"])
        self.assertEqual(self.client.get("/api/od/list").json(), original)
        self.assertEqual(self.client.get("/api/od/matrix?dataset=computed").json()["data"]["counts"], [[3]])
        self.assertEqual(self.client.get("/api/od/top?dataset=computed").json()["data"]["items"], self.computed()["items"])

    def test_period_boundaries(self):
        for i, when in enumerate(["06:59", "07:00", "09:59", "10:00", "16:59", "17:00", "19:59", "20:00"]):
            self.trip(when=when, segment=str(i), minutes=1)
        result = self.run_compute()
        self.assertEqual(result["stats"]["periods"], {"morning_peak": 2, "evening_peak": 2, "normal": 4})
        self.assertEqual(self.computed(time_period="transit")["total"], 0)

    def test_invalid_segments_and_first_valid_match(self):
        moment = datetime(2024, 3, 4, 6, 50)
        self.point("v", "single", moment)
        self.point("v", "same_time", moment)
        self.point("v", "same_time", moment, road="B")
        self.point("v", "unmatched", moment, matched=False)
        self.point("v", "good", moment, matched=False)
        self.point("v", "good", datetime(2024, 3, 4, 7, 5))
        self.point("v", "good", datetime(2024, 3, 4, 7, 10), road="B")
        stats = self.run_compute()["stats"]
        self.assertEqual((stats["trip_count"], stats["skipped_segments"]), (1, 3))
        self.assertEqual(stats["periods"]["morning_peak"], 1)
        self.assertEqual(self.computed()["items"][0]["avg_duration_min"], 5)

    def test_same_road_retained_and_unknown_road_skipped(self):
        self.trip(end_road="A")
        self.trip(segment="bad", end_road="missing")
        result = self.run_compute()
        self.assertEqual(result["stats"]["trip_count"], 1)
        self.assertEqual(self.computed()["items"][0]["dest_road_id"], "A")

    def test_duplicate_matching_is_rejected(self):
        self.trip()
        self.run_compute()
        with self.sessions.begin() as db:
            db.add(MatchResult(vehicle_id="v", clean_trajectory_id=1, road_id="B", is_matched=1))
        self.assertEqual(self.client.post("/api/od/recompute").json()["code"], 4000)
        self.assertEqual(self.computed()["items"][0]["trip_count"], 1)

    def test_failed_insert_rolls_back_previous_snapshot(self):
        self.trip()
        previous = self.run_compute()
        self.trip(segment="new")

        def fail_insert(_conn, _cursor, statement, _parameters, _context, _many):
            if statement.lower().startswith("insert into od_computed"):
                raise RuntimeError("simulated write failure")

        event.listen(self.engine, "before_cursor_execute", fail_insert)
        try:
            with patch("app.services.od_computation.logger.exception"):
                self.assertEqual(self.client.post("/api/od/recompute").json()["code"], 5001)
        finally:
            event.remove(self.engine, "before_cursor_execute", fail_insert)
        self.assertEqual(self.computed()["items"][0]["trip_count"], 1)
        current = self.client.get("/api/od/computation").json()["data"]
        self.assertEqual(current["completed_at"], previous["completed_at"])
        self.assertEqual(current["stats"], previous["stats"])

    def test_conflicting_computation_and_invalid_dataset(self):
        compute_lock.acquire()
        try:
            self.assertEqual(self.client.post("/api/od/recompute").json()["code"], 4090)
        finally:
            compute_lock.release()
        self.assertEqual(self.client.get("/api/od/list?dataset=other").json()["code"], 4220)


if __name__ == "__main__":
    unittest.main()
