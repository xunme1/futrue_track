"""Local UI fixture: python -m tests.preview_rankings [--legacy] [--port 8012].

Serves the built frontend with synthetic data; never changes production data.
"""
import argparse
import copy

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from backend.core.config import PROJECT_ROOT
from backend.pipeline.screen import BUCKETS, _add_trend_rankings, _sort_results, screen_payload
from tests.test_trend_rankings import DATES, history


def create_app(legacy=False):
    data = {
        "UP": history([100, 110, 108, 115, 110, 105, 120]),
        "DOWN": history([100, 105, 110, 108, 115, 110, 110]),
        "FLAT": history([100, 101, 102, 101, 102, 103, 103]),
        "NEW": history([100] * 7, [0] * 6 + [1]),
        "GAP": history([100, 101, 101, 101, 101, 101], dates=DATES[-7:-2] + DATES[-1:]),
        "SHORTUP": history([100, 90, 92, 89, 88, 95, 80], [-1] * 7),
        "SHORTDOWN": history([100, 95, 90, 92, 85, 90, 90], [-1] * 7),
        "STALE": history([100, 110], [0, 0], dates=DATES[-3:-1]),
    }
    names = {"UP": "测试上升", "DOWN": "测试下降", "FLAT": "测试持平", "NEW": "测试新入榜",
             "GAP": "测试缺口", "SHORTUP": "测试空头上升", "SHORTDOWN": "测试空头下降", "STALE": "测试旧数据"}
    contracts = [{"symbol": f"{key}.TEST", "key": key, "name": names[key], "category": "交互测试数据",
                  "exchange": "TEST", "source": "fixture", "has_data": True, "extra": False} for key in data]
    results = {bucket: [] for bucket in BUCKETS}
    loaded = {}
    for contract in contracts:
        key = contract["key"]
        value = data[key]
        value["symbol"] = contract["symbol"]
        loaded[key] = (value, contract)
        screened = screen_payload(key, value, contract)
        for bucket in BUCKETS:
            results[bucket].extend(screened[bucket])
    _sort_results(results)
    metadata = _add_trend_rankings(results, loaded, contracts, 14)
    report = {"timeframe": "1d", "generated_at": "2026-08-17T16:00:00+08:00", "rules": {},
              "scanned_symbols": len(data), "skipped_symbols": [], "trend_ranking": metadata,
              "summary": {bucket: len(items) for bucket, items in results.items()}, "buckets": results}
    if legacy:
        report.pop("trend_ranking")
        for items in results.values():
            for item in items:
                for field in ("rank", "rank_change", "rank_status", "rank_history", "previous_rank"):
                    item.pop(field, None)
    app = FastAPI(title="Rank UI test fixtures")

    @app.get("/api/contracts")
    def get_contracts():
        return contracts

    @app.get("/api/screening")
    def get_report():
        return report

    @app.get("/api/signals/{key}")
    def get_signals(key: str):
        value = copy.deepcopy(data[key])
        size = len(value["dates"])
        value.update(symbol=f"{key}.TEST", timeframe="1d", signals=[], volume=[10000] * size,
                     opi=[20000] * size, ZD=[100] * size,
                     SB=[False] * size, DSB=[False] * size, DSBE=[False] * size,
                     AA1=[True] * size, ZZ1=[True] * size, TT1=[True] * size)
        return value

    app.mount("/", StaticFiles(directory=PROJECT_ROOT / "frontend/dist", html=True))
    return app


if __name__ == "__main__":
    import uvicorn
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--legacy", action="store_true")
    parser.add_argument("--port", type=int, default=8012)
    args = parser.parse_args()
    uvicorn.run(create_app(args.legacy), host="127.0.0.1", port=args.port)
