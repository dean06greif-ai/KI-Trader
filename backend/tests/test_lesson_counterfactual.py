"""PLAN_LEKTIONS_BILANZ Baustein B – HOLD-Gegenprobe (rein + FakeDB)."""
import asyncio
import time

from services import lesson_counterfactual as cf

T0 = 1_760_000_000_000


def _candles(path, start=T0, step=60000):
    """path: Liste (high, low, close) je Minute."""
    return [{"timestamp": start + i * step, "open": c[2], "high": c[0], "low": c[1], "close": c[2]}
            for i, c in enumerate(path)]


def _dec(action="LONG", price=100.0, sl=1.0, tp=2.0, horizon="scalp", blocked=("les_a",), ts_ms=T0):
    from datetime import datetime, timezone
    return {"id": "d1", "symbol": "BTCUSDT", "action": "HOLD", "price": price, "horizon": horizon,
            "ts": datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).isoformat(),
            "applied_lessons": list(blocked) + ["les_x"],
            "would_be": {"action": action, "sl_pct": sl, "tp1_pct": tp, "blocked_by": list(blocked)}}


def test_frame_from_decision():
    fr = cf.frame_from_decision(_dec())
    assert fr["side"] == "LONG" and fr["sl"] == 99.0 and fr["tp"] == 102.0
    assert fr["end_ms"] == T0 + 240 * 60000
    assert cf.frame_from_decision({"action": "HOLD", "price": 100}) is None
    assert cf.frame_from_decision({**_dec(), "action": "LONG"}) is None
    assert cf.frame_from_decision(_dec(horizon="swing"))["end_ms"] == T0 + 1440 * 60000


def test_sl_first_tp_first_both_none():
    d = _dec()
    sl_first = _candles([(100.5, 99.5, 100), (100.2, 98.9, 99), (103, 99, 102)])
    r = cf.review_hold(d, sl_first, cost=0.0)
    assert r["exit_reason"] == "sl" and r["r_gross"] == -1.0 and r["status"] == "done"
    tp_first = _candles([(100.5, 99.5, 100), (102.5, 99.9, 102), (99, 98, 98.5)])
    r = cf.review_hold(d, tp_first, cost=0.0)
    assert r["exit_reason"] == "tp" and r["r_gross"] == 2.0
    both = _candles([(100.5, 99.5, 100), (102.5, 98.5, 100)])
    r = cf.review_hold(d, both, cost=0.0)
    assert r["exit_reason"] == "sl"          # konservativ
    none = _candles([(100.5, 99.5, 100), (100.8, 99.6, 100.5)])
    r = cf.review_hold(d, none, cost=0.0)
    assert r["exit_reason"] == "open" and r["r_gross"] == 0.5
    assert cf.review_hold(d, [], cost=0.0)["status"] == "no_data"


def test_costs_reduce_r_and_short_sign():
    d = _dec()
    tp_first = _candles([(100.5, 99.5, 100), (102.5, 99.9, 102)])
    gross = cf.review_hold(d, tp_first, cost=0.0)
    net = cf.review_hold(d, tp_first, cost=0.2)
    assert net["r"] < gross["r"] and net["pnl_pct_net"] == 1.8
    d_short = _dec(action="SHORT")
    down = _candles([(100.5, 99.5, 100), (100.1, 97.9, 98)])
    r = cf.review_hold(d_short, down, cost=0.0)
    assert r["exit_reason"] == "tp" and r["r_gross"] == 2.0
    assert cf.cost_pct("BTCUSDT") > 0.1


def test_aggregate_separates_avoided_and_missed():
    reviews = [
        {"status": "done", "r": -1.0, "blocked_by": ["les_a"], "exit_reason": "sl"},
        {"status": "done", "r": -0.8, "blocked_by": ["les_a"], "exit_reason": "sl"},
        {"status": "done", "r": 1.5, "blocked_by": ["les_a", "les_b"], "exit_reason": "tp"},
        {"status": "no_data", "blocked_by": ["les_a"]},
        {"status": "done", "r": 0.3, "blocked_by": [], "exit_reason": "open"},
    ]
    agg = cf.aggregate(reviews)
    a = agg["les_a"]
    assert a["n"] == 3 and a["would_loss"] == 2 and a["would_win"] == 1 and a["open"] == 1
    assert a["avoided_loss_r"] == 1.8 and a["missed_gain_r"] == 1.5
    assert a["net_r"] == 0.3 and a["sum_r"] == 0.3
    assert agg["les_b"]["net_r"] == -1.5
    assert "les_x" not in agg      # ohne blocked_by keiner Lektion zugerechnet


class _Cursor:
    def __init__(self, docs):
        self.docs = docs

    def sort(self, *_):
        return self

    def limit(self, *_):
        return self

    async def to_list(self, *_):
        return list(self.docs)


class _Coll:
    def __init__(self, docs=None):
        self.docs = docs or []
        self.updates = []

    def _match(self, d, q):
        for k, v in q.items():
            if isinstance(v, dict):
                if "$exists" in v and ((k in d) != v["$exists"]):
                    return False
                if "$gte" in v and not (str(d.get(k, "")) >= v["$gte"]):
                    return False
            elif d.get(k) != v:
                return False
        return True

    def find(self, q, *_):
        return _Cursor([d for d in self.docs if self._match(d, q)])

    async def count_documents(self, q):
        return len([d for d in self.docs if self._match(d, q)])

    async def update_one(self, q, u, upsert=False):
        self.updates.append((q, u))
        for d in self.docs:
            if self._match(d, q):
                d.update(u.get("$set", {}))
                return
        if upsert:
            self.docs.append({**q, **u.get("$set", {})})


class _DB:
    def __init__(self, decisions):
        self.ai_decisions = _Coll(decisions)
        self.ai_lesson_cf = _Coll()


def test_pending_only_due_hold_with_would_be_not_done():
    now_ms = int(time.time() * 1000)
    old = now_ms - 300 * 60000
    fresh = now_ms - 10 * 60000
    docs = [
        {**_dec(ts_ms=old), "id": "due"},
        {**_dec(ts_ms=fresh), "id": "too_fresh"},
        {**_dec(ts_ms=old), "id": "done", "lesson_cf": "done"},
        {"id": "no_wb", "action": "HOLD", "price": 1, "ts": _dec(ts_ms=old)["ts"]},
        {**_dec(ts_ms=old), "id": "long", "action": "LONG"},
    ]
    svc = cf.CounterfactualService()
    svc.setup(_DB(docs))
    pend = asyncio.run(svc._pending())
    assert [d["id"] for d in pend] == ["due"]
