"""用临时小样本检查基线核验器的失败检测与只读保证，不接触开发数据库。"""

import csv
import hashlib
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from check_od_baseline import EXPECTED_ROWS, check_baseline


class BaselineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.db_path = self.root / "fixture.db"
        self.csv_dir = self.root / "csv"
        self.csv_dir.mkdir()
        self.counts = {name: 1 for name in EXPECTED_ROWS}
        self.counts["od_matrix"] = 4
        self.periods = {name: (1, i) for i, name in enumerate(
            ("morning_peak", "evening_peak", "normal", "transit"), 1
        )}
        with closing(sqlite3.connect(self.db_path)) as db, db:
            for name in ("data_source", "hotzone"):
                db.execute(f'CREATE TABLE "{name}" (id INTEGER)')
                db.execute(f'INSERT INTO "{name}" VALUES (1)')
            for name in ("raw_trajectory", "clean_trajectory"):
                db.execute(f'CREATE TABLE "{name}" (id INTEGER, timestamp TEXT)')
                db.execute(f'INSERT INTO "{name}" VALUES (1, ?)', ("2024-03-04 07:00:00.123456",))
            db.execute("CREATE TABLE road_metrics (id INTEGER, time_window TEXT)")
            db.execute("INSERT INTO road_metrics VALUES (1, '2024-03-04 07:00:00')")
            db.execute("CREATE TABLE road_segment (road_id TEXT)")
            db.execute("INSERT INTO road_segment VALUES ('R1')")
            db.execute("CREATE TABLE match_result (id INTEGER, clean_trajectory_id INTEGER)")
            db.execute("INSERT INTO match_result VALUES (1, 1)")
            db.execute("""CREATE TABLE od_matrix (origin_road_id TEXT, dest_road_id TEXT,
                time_period TEXT, trip_count INTEGER, avg_duration_min REAL)""")
            db.executemany("INSERT INTO od_matrix VALUES ('R1', 'R1', ?, ?, 2.5)",
                           [(period, trips) for period, (_, trips) in self.periods.items()])
            for table in self.counts:
                cursor = db.execute(f'SELECT * FROM "{table}"')
                with (self.csv_dir / f"{table}.csv").open("w", encoding="utf-8", newline="") as stream:
                    writer = csv.writer(stream)
                    writer.writerow([col[0] for col in cursor.description])
                    writer.writerows(cursor)

    def check(self):
        return check_baseline(self.db_path, self.csv_dir,
                              expected_rows=self.counts, expected_periods=self.periods)

    def mutate(self, sql):
        with closing(sqlite3.connect(self.db_path)) as db, db:
            db.execute(sql)

    def test_valid_fixture_is_read_only(self):
        before = hashlib.sha256(self.db_path.read_bytes()).digest()
        self.assertEqual(self.check()["trip_count"], 10)
        self.assertEqual(hashlib.sha256(self.db_path.read_bytes()).digest(), before)

    def test_missing_database_is_not_created(self):
        missing = self.root / "missing.db"
        with self.assertRaisesRegex(ValueError, "数据库不存在"):
            check_baseline(missing, self.csv_dir)
        self.assertFalse(missing.exists())

    def test_database_row_loss(self):
        self.mutate("DELETE FROM hotzone")
        with self.assertRaisesRegex(ValueError, "hotzone 数据库行数"):
            self.check()

    def test_orphan_road(self):
        self.mutate("UPDATE od_matrix SET dest_road_id = 'missing' WHERE time_period = 'normal'")
        with self.assertRaisesRegex(ValueError, "缺少路段关联"):
            self.check()

    def test_changed_trip_count(self):
        self.mutate("UPDATE od_matrix SET trip_count = 99 WHERE time_period = 'normal'")
        with self.assertRaisesRegex(ValueError, "次数与 CSV 不一致"):
            self.check()

    def test_invalid_duration(self):
        self.mutate("UPDATE od_matrix SET avg_duration_min = -1 WHERE time_period = 'normal'")
        with self.assertRaisesRegex(ValueError, "时长无效"):
            self.check()

    def test_duplicate_od(self):
        self.mutate("UPDATE od_matrix SET time_period = 'normal' WHERE time_period = 'transit'")
        with self.assertRaisesRegex(ValueError, "重复 OD 三元组"):
            self.check()

    def test_invalid_trajectory_time(self):
        self.mutate("UPDATE clean_trajectory SET timestamp = 'invalid'")
        with self.assertRaisesRegex(ValueError, "无效时间"):
            self.check()

    def test_missing_clean_point(self):
        self.mutate("UPDATE match_result SET clean_trajectory_id = 999")
        with self.assertRaisesRegex(ValueError, "缺少清洗点关联"):
            self.check()

    def test_wrong_period_totals(self):
        self.periods["normal"] = (1, 100)
        with self.assertRaisesRegex(ValueError, "分类统计不符"):
            self.check()


if __name__ == "__main__":
    unittest.main()
