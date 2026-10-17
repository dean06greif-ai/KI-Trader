"""Seed für die Preview/Test-Umgebung: ein Autopilot-Lauf mit gefallenem Holdout
(96,4 -> 95,7) – der vom Nutzer gemeldete Fall. Idempotent."""
import os
from pymongo import MongoClient
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))
db = MongoClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]
db.regime_lab_runs.replace_one({"id": "aptest001"}, {
    "id": "aptest001", "created_at": "2026-09-21T18:30:00+00:00", "result": {
        "kind": "autopilot", "improved": True, "improvements": 3, "tested": 40, "days": 1080, "train_pct": 75,
        "symbols": ["XAUUSDT", "XAGUSDT"], "timeframe": "1d", "holdout_regressed": True,
        "adopt_recommended": False, "stop_reason": "rounds_limit", "elapsed_seconds": 120,
        "best_engine_config": {"detector": "ema", "ema_regime_days": 20, "confidence_min": 0.6},
        "best": {"detector": "ema", "score": 97.2,
                 "engine_config": {"detector": "ema", "ema_regime_days": 20, "confidence_min": 0.6},
                 "changes": {"ema_regime_days": 20}, "baseline_score": 96.0,
                 "metrics": {"holdout_direction_pct": 95.7, "inner_direction_pct": 97.0,
                             "avg_live_phase_days": 7.0, "trend_hit_pct": 80}},
        "baseline": {"score": 96.0, "engine_config": {"detector": "ema"},
                     "metrics": {"holdout_direction_pct": 96.4, "inner_direction_pct": 96.0}},
        "created_at": "2026-09-21T18:30:00+00:00"}}, upsert=True)
print("analyses:", db.regime_analyses.count_documents({}))
for a in db.regime_analyses.find({}, {"id": 1, "name": 1, "symbols": 1, "scope": 1, "symbols_skipped": 1}).limit(5):
    print(a)
