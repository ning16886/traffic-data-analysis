"""地图匹配引擎单元测试（标准库 unittest，不需要 pytest）。

运行（在 backend/ 下）：
    python -m unittest discover -s tests -v

用例全部用合成路网，不依赖 data/raw 的 48 万点，几毫秒跑完。
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np  # noqa: E402

from app.services.matching import MapMatcher, MatchParams, Road, RoadNetwork  # noqa: E402

# 纬度 1° ≈ 110 540 m（与 Projector 一致），用于把「米」换算成「度」
M_PER_DEG_LAT = 110_540.0
M_PER_DEG_LNG = 111_320.0 * np.cos(np.radians(39.90))


def deg_lat(m: float) -> float:
    return m / M_PER_DEG_LAT


def deg_lng(m: float) -> float:
    return m / M_PER_DEG_LNG


def cross_roads() -> list[Road]:
    """十字路口：东西向主干路 + 南北向次干路，交于 (116.40, 39.90)。"""
    return [
        Road("R_EW", "东西路", "main", [(116.30, 39.90), (116.50, 39.90)]),
        Road("R_NS", "南北街", "secondary", [(116.40, 39.85), (116.40, 39.95)]),
    ]


def parallel_roads() -> list[Road]:
    """两条平行东西向道路，相距 44m，用来验证维特比的连续性。"""
    return [
        Road("R_A", "南侧路", "secondary", [(116.30, 39.9000), (116.50, 39.9000)]),
        Road("R_B", "北侧路", "secondary", [(116.30, 39.9004), (116.50, 39.9004)]),
    ]


def matcher(roads: list[Road], threshold: float = 100.0) -> MapMatcher:
    return MapMatcher(RoadNetwork(roads), MatchParams(threshold_m=threshold))


class GeometryTest(unittest.TestCase):
    def test_point_on_centerline_is_perfect_match(self) -> None:
        res = matcher(cross_roads()).match([116.35], [39.90], directions=[90.0])
        self.assertTrue(bool(res.matched[0]))
        self.assertLess(float(res.distance_m[0]), 0.01)
        self.assertAlmostEqual(float(res.probability[0]), 1.0, places=2)
        self.assertEqual(res.road_idx[0], 0)

    def test_snap_coordinate_lies_on_road(self) -> None:
        # 向北偏 30m，吸附后纬度应回到路中心线 39.90
        res = matcher(cross_roads()).match([116.35], [39.90 + deg_lat(30)], directions=[90.0])
        self.assertTrue(bool(res.matched[0]))
        self.assertAlmostEqual(float(res.snap_lat[0]), 39.90, places=6)
        self.assertAlmostEqual(float(res.snap_lng[0]), 116.35, places=4)

    def test_point_beyond_threshold_is_unmatched(self) -> None:
        # 偏 300m，远超 100m 阈值
        res = matcher(cross_roads()).match([116.35], [39.90 + deg_lat(300)])
        self.assertFalse(bool(res.matched[0]))
        self.assertEqual(res.road_idx[0], -1)
        self.assertTrue(np.isinf(res.distance_m[0]))
        # 未匹配时坐标保持原值，前端直接画红点
        self.assertAlmostEqual(float(res.snap_lat[0]), 39.90 + deg_lat(300), places=6)

    def test_snap_respects_segment_endpoint(self) -> None:
        # 路端外侧的点：投影应夹在线段端点，而不是外推到延长线上
        res = matcher(cross_roads()).match([116.2999], [39.90], directions=[90.0])
        self.assertAlmostEqual(float(res.snap_lng[0]), 116.30, places=5)


class DirectionTest(unittest.TestCase):
    def test_direction_breaks_tie_at_intersection(self) -> None:
        res = matcher(cross_roads()).match([116.40001, 116.40001], [39.90001, 39.90001],
                                           directions=[90.0, 0.0])
        self.assertEqual(res.road_idx[0], 0, "向东行驶应匹配东西路")
        self.assertEqual(res.road_idx[1], 1, "向北行驶应匹配南北街")


class HmmTest(unittest.TestCase):
    def test_continuity_beats_nearest_segment(self) -> None:
        """中间一点横向漂移到离北侧路更近，但整条轨迹沿南侧路东行：维特比应保持在南侧。

        这种点用「最近距离」逐点贪心会被拽到北侧路上（20m vs 24m），
        维特比靠相邻点的行进距离把漂移吸收掉，这是 HMM 相对贪心的核心收益。
        """
        m = matcher(parallel_roads())
        # 每步东行 30m；y 正常贴南侧路（北偏 2m），第三点北偏 24m
        lngs = [116.36 + deg_lng(30 * i) for i in range(5)]
        lats = [39.9000 + deg_lat(2)] * 5
        lats[2] = 39.9000 + deg_lat(24)
        res = m.match(lngs, lats, directions=[90.0] * 5)
        self.assertEqual([int(r) for r in res.road_idx], [0, 0, 0, 0, 0])
        self.assertGreater(float(res.distance_m[2]), 20.0)  # 仍吸附到 24m 外的南侧路

    def test_trips_are_independent(self) -> None:
        """不同行程之间不做转移：两段相距很远的轨迹各自匹配各自的路。"""
        roads = cross_roads() + [Road("R_FAR", "远端路", "branch", [(116.90, 39.60), (117.00, 39.60)])]
        m = matcher(roads)
        res = m.match(
            [116.35, 116.36, 116.92, 116.93],
            [39.90, 39.90, 39.60, 39.60],
            directions=[90.0] * 4,
            trip_ids=[1, 1, 2, 2],
        )
        self.assertEqual(res.road_idx[0], res.road_idx[1])
        self.assertEqual(res.road_idx[2], res.road_idx[3])
        self.assertNotEqual(res.road_idx[0], res.road_idx[2])


class ThresholdTest(unittest.TestCase):
    def test_threshold_monotonic(self) -> None:
        """阈值越大，匹配数单调不减。"""
        roads = cross_roads()
        lngs = [116.35] * 4
        lats = [39.90 + deg_lat(d) for d in (5, 40, 90, 160)]
        counts = []
        for th in (10, 50, 100, 200):
            res = matcher(roads, threshold=th).match(lngs, lats, directions=[90.0] * 4)
            counts.append(int(res.matched.sum()))
        self.assertEqual(counts, sorted(counts))
        self.assertEqual(counts, [1, 2, 3, 4])

    def test_stats_shape(self) -> None:
        roads = cross_roads()
        res = matcher(roads).match([116.35, 116.36, 116.37], [39.90, 39.90, 39.90 + deg_lat(300)])
        stats = res.stats()
        self.assertEqual(stats["total"], 3)
        self.assertEqual(stats["matched"], 2)
        self.assertEqual(stats["unmatched"], 1)
        self.assertEqual(stats["roadCount"], 1)
        self.assertAlmostEqual(stats["matchRate"], 2 / 3, places=4)


if __name__ == "__main__":
    unittest.main()
