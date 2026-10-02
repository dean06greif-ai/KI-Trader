"""Audit 2.3: R in Geld (risk_usdt = risk × qty). Ohne Netzwerk."""
import asyncio
import inspect

from services import ai_rewards, boot_migrations
from services import ml_gate as mg
from services.bitunix_trade import AutoTradeManager


def test_trade_doc_stores_risk_usdt():
    src = inspect.getsource(AutoTradeManager._on_signal_impl)
    assert '"risk_usdt": round(risk * qty, 6)' in src


def test_money_r_prefers_risk_usdt_with_fallback():
    assert mg.money_r({"realized_pnl": -5.0, "risk_usdt": 5.0}) == -1.0
    assert mg.money_r({"realized_pnl": 10.0, "risk_usdt": 5.0}) == 2.0
    # Fallback alte Trades: risk (Preisdistanz) × qty
    assert mg.money_r({"realized_pnl": 6.0, "risk": 2.0, "qty": 3.0}) == 1.0
    # keine Basis -> None (vorher: pnl / Preisdistanz = dimensional falsch)
    assert mg.money_r({"realized_pnl": 6.0, "risk": 2.0}) is None
    assert mg.money_r({"realized_pnl": 6.0}) is None


def test_compute_reward_uses_r_basis_when_available():
    t = {"realized_pnl": -8.0, "risk_usdt": 4.0, "max_capital": 100.0,
         "result": "loss", "opened_at": "2026-06-01T12:00:00+00:00",
         "closed_at": "2026-06-01T13:00:00+00:00"}
    r = ai_rewards.compute_reward(t)
    base = [c for c in r["components"] if c["label"].startswith("R-Basis")][0]
    assert base["value"] == -2.0  # -2R
    assert r["r_multiple"] == -2.0
    # Deckel ±4 bleibt
    t2 = {**t, "realized_pnl": 100.0, "result": "win"}
    r2 = ai_rewards.compute_reward(t2)
    assert [c for c in r2["components"] if c["label"].startswith("R-Basis")][0]["value"] == 4.0


def test_compute_reward_falls_back_to_pnl_basis_without_risk_usdt():
    t = {"realized_pnl": 5.0, "max_capital": 100.0, "result": "win",
         "opened_at": "2026-06-01T12:00:00+00:00",
         "closed_at": "2026-06-01T13:00:00+00:00"}
    r = ai_rewards.compute_reward(t)
    assert [c for c in r["components"] if c["label"] == "PnL-Basis"]
    assert r["r_multiple"] is None


class _Coll:
    def __init__(self):
        self.calls = []

    async def update_many(self, flt, upd):
        self.calls.append((flt, upd))

        class R:
            modified_count = 7
        return R()


class _Settings:
    def __init__(self):
        self.marks = {}

    async def find_one(self, flt):
        return self.marks.get("doc")

    async def update_one(self, flt, upd, upsert=False):
        self.marks["doc"] = {**(self.marks.get("doc") or {}),
                             **upd.get("$set", {})}


class _Db:
    def __init__(self):
        self.settings = _Settings()
        self.auto_trades = _Coll()


def test_migration_backfills_risk_usdt_once():
    db = _Db()
    asyncio.run(boot_migrations.migrate_risk_usdt_backfill(db))
    assert len(db.auto_trades.calls) == 1
    flt, upd = db.auto_trades.calls[0]
    assert flt == {"risk_usdt": {"$exists": False}, "risk": {"$gt": 0}, "qty": {"$gt": 0}}
    assert upd[0]["$set"]["risk_usdt"]["$round"][0] == {"$multiply": ["$risk", "$qty"]}
    # Zweiter Lauf: Marker verhindert erneutes Update (idempotent)
    asyncio.run(boot_migrations.migrate_risk_usdt_backfill(db))
    assert len(db.auto_trades.calls) == 1


def test_shadow_report_uses_money_r():
    src = inspect.getsource(mg.MLGate.shadow_report)
    assert "money_r(t)" in src and "(pnl / risk)" not in src
