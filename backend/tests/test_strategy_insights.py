"""Regressionstests: Cross-Strategie-Lesezugriff fürs KI-Trader-Training und
autonome Setup-Entwicklung aus dem Trainingslager – ohne DB, LLM oder Netzwerk."""
import asyncio
import json

import pytest

from services import strategy_insights
from services.ai_strategy_lab import StrategyLab, DEVELOP_SYSTEM


# ---------------- Fakes (Motor-ähnlich, rein in-memory) ----------------
class FakeCursor:
    def __init__(self, rows):
        self._rows = rows

    def sort(self, *a, **k):
        return self

    def limit(self, *a, **k):
        return self

    async def to_list(self, n):
        return self._rows[:n]


class FakeColl:
    def __init__(self, rows=None, agg_rows=None):
        self.rows = rows or []
        self.agg_rows = agg_rows or []
        self.inserted = []

    def aggregate(self, pipeline):
        return FakeCursor(self.agg_rows)

    def find(self, *a, **k):
        return FakeCursor(self.rows)

    async def find_one(self, q, *a, **k):
        for d in self.rows:
            if all(d.get(kk) == vv for kk, vv in q.items()):
                return dict(d)
        return None

    async def update_one(self, q, u, upsert=False, **k):
        for d in self.rows:
            if all(d.get(kk) == vv for kk, vv in q.items()):
                d.update(u.get("$set", {}))
                return
        if upsert:
            self.rows.append({**q, **u.get("$set", {})})

    async def count_documents(self, q):
        return len(self.rows)

    async def insert_one(self, doc):
        self.inserted.append(doc)
        self.rows.append(doc)


class FakeDB:
    def __init__(self, **colls):
        self._colls = colls

    def __getattr__(self, name):
        return self._colls.setdefault(name, FakeColl())

    def __getitem__(self, name):
        return self._colls.setdefault(name, FakeColl())


def _db():
    return FakeDB(
        auto_trades=FakeColl(agg_rows=[
            {"_id": {"sid": "scalping_4_rules", "mode": "paper"},
             "trades": 10, "wins": 6, "pnl": 12.5},
            {"_id": {"sid": "trend_surfer", "mode": "live"},
             "trades": 4, "wins": 3, "pnl": 22.0},
        ]),
        optimizer_runs=FakeColl(rows=[
            {"params": {"strategy_id": "trend_surfer"},
             "result": {"symbol": "BTCUSDT", "timeframe": "15m",
                        "best": {"metrics": {"trades": 40, "win_rate": 61.0,
                                             "total_pnl_pct": 18.4}}},
             "created_at": "2026-06-01T00:00:00+00:00"},
        ]),
        learning_memory=FakeColl(rows=[
            {"strategy_id": "trend_surfer", "strategy_name": "Trend Surfer",
             "timeframe": "15m", "metrics": {"trades": 40, "win_rate": 61.0,
                                             "total_pnl_pct": 18.4},
             "at": "2026-06-01T00:00:00+00:00"},
        ]),
        ai_strategy_candidates=FakeColl(rows=[
            {"id": "cand_x", "name": "Range Fade", "stage": "ghost",
             "last_assist": {"feedback": "SL zu eng bei hoher Volatilität.",
                             "data_findings": ["Winrate 48% bei ATR>1%"]}},
        ]),
    )


# ---------------- strategy_insights ----------------
def test_trade_stats_aggregation():
    stats = asyncio.run(strategy_insights.strategy_trade_stats(_db()))
    assert stats["scalping_4_rules"]["paper"]["trades"] == 10
    assert stats["scalping_4_rules"]["paper"]["win_rate"] == 60.0
    assert stats["trend_surfer"]["live"]["pnl"] == 22.0


def test_optimizer_best_latest_per_strategy():
    best = asyncio.run(strategy_insights.optimizer_best(_db()))
    assert best["trend_surfer"]["win_rate"] == 61.0
    assert best["trend_surfer"]["symbol"] == "BTCUSDT"


def test_context_text_contains_strategies_and_notes():
    txt = asyncio.run(strategy_insights.context_text(_db(), scanner_settings={}))
    assert "ANDERE STRATEGIEN IM VERGLEICH" in txt
    assert "NUR LESE-/LERNKONTEXT" in txt
    # Registry-Strategien mit Parametern + Ergebnissen
    assert "scalping_4_rules" in txt and "trend_surfer" in txt
    assert "WR 60.0%" in txt and "+22.00 USDT" in txt
    # KI-Trader selbst wird NICHT als Vergleich gelistet
    assert "[ai_trader]" not in txt
    # Optimizer-Erkenntnisse + Labor-Notizen
    assert "Optimizer-Best" in txt
    assert "Range Fade" in txt and "SL zu eng" in txt


def test_context_text_respects_max_chars():
    txt = asyncio.run(strategy_insights.context_text(_db(), scanner_settings={},
                                                     max_chars=400))
    assert len(txt) <= 400


def test_context_text_survives_empty_db():
    txt = asyncio.run(strategy_insights.context_text(FakeDB(), scanner_settings={}))
    assert "ANDERE STRATEGIEN IM VERGLEICH" in txt
    assert "noch keine geschlossenen Trades" in txt


# ---------------- Trainingslager: develop() ----------------
class FakeEngine:
    def __init__(self, db, answer):
        self.db = db
        self.key = "test-key"
        self.learning = None
        self._answer = answer
        self.last_prompt = None
        self.last_system = None

    async def generate_for_role(self, role, prompt, system, temperature=0.4,
                                json_mode=True):
        self.last_prompt = prompt
        self.last_system = system
        return json.dumps(self._answer), "groq", "openai/gpt-oss-120b"

    def _parse_json(self, text):
        return json.loads(text)


def _lab(db, answer):
    lab = StrategyLab()
    lab.setup(FakeEngine(db, answer))
    return lab


def test_develop_creates_ghost_candidate_with_insights_context():
    db = _db()
    answer = {"should_create": True, "reason": "Trendfolge-Parameter liefern 61% WR",
              "name": "Trend-Pullback 15m", "thesis": "Pullback im Trend",
              "rules_text": "Long wenn ema_fast > ema_slow und RSI < 45",
              "symbols": ["BTCUSDT"], "timeframe": "15m",
              "learned_from": "trend_surfer Optimizer-Best",
              "rule_definition": None}
    lab = _lab(db, answer)
    res = asyncio.run(lab.develop())
    assert res["status"] == "ok"
    cand = res["candidate"]
    assert cand["stage"] == "ghost" and cand["source"] == "ki"
    assert cand["learned_from"] == "trend_surfer Optimizer-Best"
    # Cross-Strategie-Vergleich war Teil des Trainings-Prompts
    assert "ANDERE STRATEGIEN IM VERGLEICH" in lab.engine.last_prompt
    assert lab.engine.last_system == DEVELOP_SYSTEM


def test_develop_respects_should_create_false():
    lab = _lab(_db(), {"should_create": False, "reason": "Datenbasis zu dünn"})
    res = asyncio.run(lab.develop())
    assert res["status"] == "skipped"
    assert "Datenbasis" in res["reason"]


def test_develop_blocked_when_ai_create_disabled():
    lab = _lab(_db(), {"should_create": True})
    lab.settings["allow_ai_create"] = False
    res = asyncio.run(lab.develop())
    assert res["status"] == "blocked"


def test_develop_blocked_when_too_many_active_candidates():
    db = _db()
    db._colls["ai_strategy_candidates"].rows = [
        {"id": f"c{i}", "name": f"S{i}", "stage": "ghost"} for i in range(6)]
    lab = _lab(db, {"should_create": True})
    res = asyncio.run(lab.develop())
    assert res["status"] == "blocked"


def test_develop_invalid_rule_definition_is_dropped():
    answer = {"should_create": True, "reason": "ok", "name": "X1",
              "thesis": "t", "rules_text": "r", "symbols": [],
              "rule_definition": {"kaputt": True}}
    lab = _lab(_db(), answer)
    res = asyncio.run(lab.develop())
    assert res["status"] == "ok"
    assert res["candidate"]["rule_definition"] is None
    assert res["backtestable"] is False


# ---------------- Setup-Autopilot (wöchentliches Auto-Develop) ----------------
def test_auto_develop_runs_once_and_respects_interval():
    db = _db()
    answer = {"should_create": False, "reason": "Datenlage zu dünn"}
    lab = _lab(db, answer)
    asyncio.run(lab._auto_develop_tick())
    st = db._colls["settings"].rows if hasattr(db._colls.get("settings"), "rows") else None
    doc = asyncio.run(db.settings.find_one({"_id": "strategy_lab_autodevelop"}))
    assert doc and doc.get("last_ts"), "Autopilot-Zeitstempel fehlt"
    assert lab.engine.last_prompt is not None  # develop() wurde aufgerufen
    lab.engine.last_prompt = None
    asyncio.run(lab._auto_develop_tick())  # innerhalb des Intervalls: kein 2. Lauf
    assert lab.engine.last_prompt is None


def test_auto_develop_disabled():
    lab = _lab(_db(), {"should_create": False, "reason": "x"})
    lab.settings["auto_develop_enabled"] = False
    asyncio.run(lab._auto_develop_tick())
    assert lab.engine.last_prompt is None


# ---------------- Tages-Quota-Cooldown (Cerebras & Co.) ----------------
def test_daily_quota_cooldown_until_utc_midnight():
    from services.ai_providers import _quota_cooldown_s, KEY_LIMIT_COOLDOWN_S
    cd = _quota_cooldown_s("429 rate_limit: tokens_per_day quota exceeded")
    assert cd > KEY_LIMIT_COOLDOWN_S  # deutlich länger als 10 min
    assert cd <= 86400 + 60
    # Minuten-Limit bleibt beim kurzen Standard-Cooldown
    assert _quota_cooldown_s("429 requests per minute") == KEY_LIMIT_COOLDOWN_S


def test_daily_quota_key_skipped_long(monkeypatch):
    from services import ai_providers as ap
    ap._key_limited.pop("testprov", None)
    ap.mark_key_limited("testprov", 0, "tokens_per_day limit reached")
    ap.mark_key_limited("testprov", 1, "requests per minute")
    # Nach 11 min: Tages-Key weiter gesperrt, Minuten-Key wieder nutzbar
    future = ap._now() + 11 * 60
    idxs = ap.usable_key_indices("testprov", 3, now=future)
    assert 0 not in idxs and 1 in idxs and 2 in idxs
    ap._key_limited.pop("testprov", None)
