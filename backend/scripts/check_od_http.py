"""验证真实 OD HTTP 查询；--recompute 显式允许更新独立重算快照。"""

import argparse
import hashlib
import json
import sqlite3
import urllib.request
from contextlib import closing
from pathlib import Path

from check_od_baseline import EXPECTED_ROWS


def source_hashes(db_path: Path) -> dict[str, str]:
    result = {}
    with closing(sqlite3.connect(db_path.resolve().as_uri() + "?mode=ro", uri=True)) as db:
        for table in EXPECTED_ROWS:
            digest = hashlib.sha256()
            for row in db.execute(f'SELECT * FROM "{table}" ORDER BY id'):
                digest.update(json.dumps(row, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
                digest.update(b"\n")
            result[table] = digest.hexdigest()
    return result


def verify(base: str, db_path: Path, recompute: bool) -> dict:
    def request(path, method="GET"):
        req = urllib.request.Request(base.rstrip("/") + path, method=method)
        with urllib.request.urlopen(req, timeout=180) as response:
            body = json.load(response)
        if body["code"] != 0:
            raise ValueError(body)
        return body["data"]

    def dataset(name):
        first = request(f"/api/od/list?dataset={name}&page_size=100")
        rows = list(first["items"])
        for page in range(2, first["pages"]+1):
            rows.extend(request(f"/api/od/list?dataset={name}&page_size=100&page={page}")["items"])
        keys = [(row["origin_road_id"], row["dest_road_id"]) for row in rows]
        assert len(rows) == len(set(keys)) == first["total"]
        assert rows == sorted(rows, key=lambda row: (-row["trip_count"], row["origin_road_id"], row["dest_road_id"]))
        top = request(f"/api/od/top?dataset={name}")
        assert top["items"] == rows[:20]
        matrix = request(f"/api/od/matrix?dataset={name}")
        total = sum(row["trip_count"] for row in rows)
        assert matrix["total_trip_count"] == total
        assert matrix["displayed_trip_count"] == sum(map(sum, matrix["counts"])) <= total
        assert len(matrix["origins"]) <= 20 and len(matrix["destinations"]) <= 20
        assert len(matrix["counts"]) == len(matrix["origins"])
        assert all(len(row) == len(matrix["destinations"]) for row in matrix["counts"])
        return {"pairs": len(rows), "trips": total}

    before = source_hashes(db_path)
    original = dataset("original")
    assert original == {"pairs": 1985, "trips": 2274}
    report = {"original": original, "recomputed": False}
    if recompute:
        first = request("/api/od/recompute", "POST")
        assert first["available"] and not first["running"]
        computed = dataset("computed")
        assert computed["trips"] == first["stats"]["trip_count"]
        second = request("/api/od/recompute", "POST")
        assert first["stats"] == second["stats"]
        assert dataset("computed") == computed
        assert request("/api/od/list?dataset=computed&time_period=transit")["total"] == 0
        report.update(recomputed=True, computed=computed, stats=second["stats"], idempotent=True)
    assert source_hashes(db_path) == before, "原始八表内容发生改变"
    report["source_tables_unchanged"] = True
    report["health"] = request("/api/health")["database"]
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--db", required=True, type=Path)
    parser.add_argument("--recompute", action="store_true")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    report = verify(args.base_url, args.db, args.recompute)
    if args.report:
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
