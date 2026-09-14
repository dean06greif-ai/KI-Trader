"""Audit 3.2: Policy-Labor (Champion vs. Kandidat, Shadow-Trials). Ohne Netzwerk."""
import asyncio

import pytest

from services import policy_lab as pl


def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


@pytest.fixture()
def loop():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    yield loop
    loop.close()


# ---------------- Fakes ----------------

class FakeCursor:
    def __init__(self, rows):
        self.rows = rows

    def limit(self, n):
        self.rows = self.rows[:n]
        return self

    def sort(self, *_):
        return self

    async def to_list(self, n):
        return self.rows[:n]


class FakeColl:
    def __init__(self):
        self.rows = []
        self.updates = []

    def find(self, q=None, proj=None):
        q = q or {}
        return FakeCursor([dict(r) for r in self.rows
                           if all(r.get(k) == v for k, v in q.items())])

    async def find_one(self, q=None, proj=None):
        for r in self.rows:
            if all(r.get(k) == v for k, v in (q or {}).items() if k != "_id"):
                return dict(r)
        return None

    async def insert_one(self, doc):
        self.rows.append(dict(doc))

    async def count_documents(self, q):
        return len([r for r in self.rows if all(r.get(k) == v for k, v in q.items())])

    async def update_one(self, q, u, upsert=False):
        self.updates.append((q, u))
        for r in self.rows:
            if all(r.get(k) == v for k, v in q.items() if k != "_id"):
                r.update(u.get("$set") or {})
                return
        if upsert:
            self.rows.append({**{k: v for k, v in q.items()}, **(u.get("$set") or {})})


class FakeDB:
    def __init__(self):
        self.colls = {}

    def __getitem__(self, name):
        return self.colls.setdefault(name, FakeColl())

    def __getattr__(self, name):
        return self[name]


class FakeScanner:
    def __init__(self, prices):
        self.prices = prices

    def current_price(self, sym):
        return self.prices.get(sym)


class FakeEngine:
    def __init__(self, db, decisions, prices=None):
        self.db = db
        self.config = {"min_confidence": 65}
        self.scanner = FakeScanner(prices or {})
        self._decisions = decisions

    async def _generate_json(self, prompt, system):
        return "raw", "test-model"

    def _parse_json(self, raw):
        return {"decisions": self._decisions}


def _lab(db, decisions=None, prices=None):
    lab = pl.PolicyLab()
    lab.setup(FakeEngine(db, decisions or [], prices))
    return lab


# ---------------- reine Funktionen ----------------

def test_validate_candidate():
    ok, why, _ = pl.validate_candidate({"name": "X"})
    assert not ok and "Änderung" in why
    ok, _, c = pl.validate_candidate({"name": "Enger SL", "system_suffix": "SL enger setzen"})
    assert ok and c["system_suffix"] == "SL enger setzen"
    ok, _, c = pl.validate_candidate({
        "name": "Konservativ", "config_overrides": {
            "min_confidence": 75, "risk_per_trade_pct": 1.0, "hack": "nein"}})
    assert ok and "hack" not in c["config_overrides"]
    assert c["config_overrides"]["min_confidence"] == 75
    assert not pl.validate_candidate({"system_suffix": "ohne Name"})[0]
    assert not pl.validate_candidate(None)[0]


def test_candidate_fingerprint_differs_from_champion():
    from services import policy_fingerprint as pf
    base_cfg = {"sizing_mode": "risk", "risk_per_trade_pct": 2.0}
    champ = pf.build(prompt_hash="lean-a+b", lessons_h="l", playbook_version="p",
                     model="m", gate_version=1, sizing_h=pf.sizing_hash(base_cfg))
    cand = {"system_suffix": "Neu", "config_overrides": {}}
    fp = pl.candidate_fingerprint(cand, "lean-a+b", "l", "p", "m", 1, base_cfg)
    assert fp["combined"] != champ["combined"]          # Suffix ändert die Policy
    cand2 = {"system_suffix": "", "config_overrides": {"risk_per_trade_pct": 1.0}}
    fp2 = pl.candidate_fingerprint(cand2, "lean-a+b", "l", "p", "m", 1, base_cfg)
    assert fp2["sizing_hash"] != champ["sizing_hash"]   # Override ändert Sizing
    assert fp2["combined"] != fp["combined"]


def test_net_pnl_pct_and_should_shadow():
    assert pl.net_pnl_pct("LONG", 100.0, 101.0, 0.05) == 0.9   # 1% brutto - 0.1% Fees
    assert pl.net_pnl_pct("SHORT", 100.0, 99.0, 0.05) == 0.9
    assert pl.net_pnl_pct("LONG", 100.0, 99.0, 0.0) == -1.0
    assert pl.net_pnl_pct("LONG", 0, 99.0, 0.1) == 0.0
    assert pl.should_shadow(3, 3) and not pl.should_shadow(4, 3)
    assert pl.should_shadow(1, 1)


def test_trial_stats():
    rows = [
        {"status": "closed", "result": "win", "net_pnl_pct": 0.9},
        {"status": "closed", "result": "loss", "net_pnl_pct": -0.7},
        {"status": "expired", "result": "expired"},
        {"status": "open"},
    ]
    st = pl.trial_stats(rows)
    assert st["trials"] == 4 and st["closed"] == 2 and st["open"] == 1
    assert st["wins"] == 1 and st["losses"] == 1 and st["expired"] == 1
    assert st["win_rate"] == 50.0 and st["net_pnl_pct"] == 0.2
    assert pl.trial_stats([])["win_rate"] == 0.0


# ---------------- Labor-Verhalten ----------------

def test_candidate_lifecycle_and_begin_cycle(loop):
    db = FakeDB()
    lab = _lab(db)
    assert lab.begin_cycle() is False  # kein Kandidat -> nie Shadow
    res = loop.run_until_complete(lab.create_candidate(
        {"name": "Test", "system_suffix": "Nur A-Setups"}))
    assert res["status"] == "ok"
    dup = loop.run_until_complete(lab.create_candidate(
        {"name": "Zweiter", "system_suffix": "x"}))
    assert dup["status"] == "rejected"  # nur EIN Kandidat
    lab.settings["shadow_every_k"] = 2
    assert [lab.begin_cycle() for _ in range(4)] == [False, True, False, True]
    res = loop.run_until_complete(lab.discard_candidate("Test vorbei"))
    assert res["status"] == "ok" and lab.candidate is None
    row = db[pl.CAND_COLL].rows[0]
    assert row["status"] == "discarded" and row["discard_reason"] == "Test vorbei"


def test_shadow_group_creates_costed_trials(loop, monkeypatch):
    async def fake_entry_fill(sym, side, ref, notional_usdt=0.0):
        return ref * 1.001, {"cost_pct": 0.1, "source": "test"}
    monkeypatch.setattr(pl.paper_execution, "entry_fill", fake_entry_fill)
    db = FakeDB()
    decisions = [
        {"symbol": "BTCUSDT", "action": "LONG", "confidence": 80,
         "sl_pct": 0.6, "tp1_pct": 0.9, "tpf_pct": 1.8, "reasoning": "Test"},
        {"symbol": "BTCUSDT", "action": "SHORT", "confidence": 50},   # unter min_conf
        {"symbol": "XXX", "action": "LONG", "confidence": 90},        # nicht im Snapshot
        {"symbol": "ETHUSDT", "action": "HOLD", "confidence": 90},    # HOLD
    ]
    lab = _lab(db, decisions)
    loop.run_until_complete(lab.create_candidate(
        {"name": "T", "system_suffix": "Nur A-Setups"}))
    snaps = {"BTCUSDT": {"price": 100.0}, "ETHUSDT": {"price": 10.0}}
    n = loop.run_until_complete(lab.shadow_group(
        lab.engine, "Krypto", "prompt", "sys", snaps, ["BTCUSDT", "ETHUSDT"],
        champion_prompt_hash="lean-a+b", lessons_h="l", playbook_version="p"))
    assert n == 1
    t = db[pl.TRIALS_COLL].rows[0]
    assert t["symbol"] == "BTCUSDT" and t["side"] == "LONG" and t["status"] == "open"
    assert t["entry"] == 100.0 and t["entry_eff"] == pytest.approx(100.1)
    assert t["sl"] < 100.0 < t["tp"]
    assert t["policy_version"]["combined"] and t["model"] == "test-model"
    assert t["paper_exec"]["source"] == "test"


def test_shadow_group_respects_open_cap(loop, monkeypatch):
    db = FakeDB()
    lab = _lab(db, [{"symbol": "BTCUSDT", "action": "LONG", "confidence": 80}])
    loop.run_until_complete(lab.create_candidate({"name": "T", "system_suffix": "x"}))
    lab.settings["max_open_trials"] = 1
    db[pl.TRIALS_COLL].rows.append({"status": "open", "id": "t1"})
    n = loop.run_until_complete(lab.shadow_group(
        lab.engine, "Krypto", "p", "s", {"BTCUSDT": {"price": 100.0}}, ["BTCUSDT"],
        champion_prompt_hash="h"))
    assert n == 0 and len(db[pl.TRIALS_COLL].rows) == 1


def test_evaluate_trials_win_loss_timeout(loop, monkeypatch):
    async def fake_exit_fill(sym, side, ref, notional_usdt=0.0):
        return ref, None
    monkeypatch.setattr(pl.paper_execution, "exit_fill", fake_exit_fill)
    db = FakeDB()
    lab = _lab(db, prices={"BTCUSDT": 101.0, "ETHUSDT": 9.0})
    now = pl._now_iso()
    db[pl.TRIALS_COLL].rows += [
        {"id": "t_win", "symbol": "BTCUSDT", "side": "LONG", "entry": 100.0,
         "entry_eff": 100.0, "sl": 99.4, "tp": 100.9, "status": "open", "opened_at": now},
        {"id": "t_loss", "symbol": "ETHUSDT", "side": "LONG", "entry": 10.0,
         "entry_eff": 10.0, "sl": 9.94, "tp": 10.09, "status": "open", "opened_at": now},
        {"id": "t_old", "symbol": "BTCUSDT", "side": "SHORT", "entry": 100.0,
         "entry_eff": 100.0, "sl": 101.0, "tp": 99.0, "status": "open",
         "opened_at": "2020-01-01T00:00:00+00:00"},
    ]
    lab.settings["fee_pct"] = 0.05
    loop.run_until_complete(lab._evaluate_trials())
    rows = {r["id"]: r for r in db[pl.TRIALS_COLL].rows}
    assert rows["t_win"]["status"] == "closed" and rows["t_win"]["result"] == "win"
    assert rows["t_win"]["net_pnl_pct"] == pytest.approx(0.8)   # 0.9% - 0.1% Fees
    assert rows["t_loss"]["result"] == "loss" and rows["t_loss"]["net_pnl_pct"] < 0
    assert rows["t_old"]["status"] == "expired"


def test_engine_wiring():
    """Shadow-Hook + Ökosystem-Tick sind in der Engine verdrahtet (Quelltext)."""
    import inspect

    from services import ai_engine
    src = inspect.getsource(ai_engine)
    assert "policy_lab.begin_cycle()" in src
    assert "await policy_lab.shadow_group(" in src
    assert '"policy_lab.policy_lab"' in src
