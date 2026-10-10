"""Regressionstests 10/2026: Erwartungswert-Regel (R) im Setup-Lebenszyklus und
Retention-Verschlankung/Indizes (MongoDB-Speicher)."""
import asyncio

from core import indexes
from services import retention
from services import setup_lifecycle as lc
from services import setup_variant


# ---------------------------------------------------------------- Erwartungswert
def test_old_rule_without_risk_data_unchanged():
    assert lc.promotion_ok({"trades": 5, "wins": 3, "pnl": -1.0})[0] is True       # WR 60 %
    assert lc.promotion_ok({"trades": 5, "wins": 1, "pnl": 0.5})[0] is True        # PnL > 0
    assert lc.promotion_ok({"trades": 6, "wins": 2, "pnl": -1.0})[0] is False
    assert lc.expectancy_r({"trades": 5, "wins": 3, "pnl": -1.0}) is None


def test_high_winrate_negative_expectancy_is_not_promoted():
    st = {"trades": 20, "wins": 12, "pnl": -4.0, "risk_usdt": 20.0, "risk_n": 20, "pnl_r": -4.0}
    ok, why = lc.promotion_ok(st)
    assert ok is False and "R" in why
    assert round(lc.expectancy_r(st), 2) == -0.2


def test_positive_expectancy_needs_enough_trades_after_shrinkage():
    few = {"trades": 5, "wins": 2, "pnl": 1.0, "risk_usdt": 5.0, "risk_n": 5, "pnl_r": 1.0}
    assert round(lc.shrunk_expectancy_r(few), 3) == round(0.2 * 5 / 15, 3)
    assert lc.promotion_ok(few)[0] is True                    # 0.067 R >= 0.05 R
    tiny = {**few, "pnl": 0.5, "pnl_r": 0.5}                   # 0.1 R -> 0.033 R
    assert lc.promotion_ok(tiny)[0] is False


def test_low_risk_coverage_falls_back_to_old_rule():
    st = {"trades": 10, "wins": 6, "pnl": -1.0, "risk_usdt": 4.0, "risk_n": 5, "pnl_r": -1.0}
    assert lc.expectancy_r(st) is None
    assert lc.promotion_ok(st)[0] is True                      # Alt-Regel: WR 60 %


def test_demotion_by_negative_expectancy():
    live = {"trades": 12, "wins": 6, "pnl": -0.5, "margin": 1000.0,
            "risk_usdt": 12.0, "risk_n": 12, "pnl_r": -3.6}   # -0.3 R -> -0.164 R
    assert "Erwartungswert" in lc.demotion_reason(live)
    ok_live = {**live, "pnl_r": 1.2}
    assert lc.demotion_reason(ok_live) is None


def test_variant_merge_keeps_risk_fields():
    a = {"trades": 4, "wins": 2, "pnl": 1.0, "margin": 10, "risk_usdt": 4.0, "risk_n": 4, "pnl_r": 1.0}
    b = {"trades": 6, "wins": 3, "pnl": -2.0, "margin": 10, "risk_usdt": 6.0, "risk_n": 6, "pnl_r": -2.0}
    m = setup_variant.merge_stats([a, b])
    assert m["risk_n"] == 10 and m["risk_usdt"] == 10.0 and m["pnl_r"] == -1.0
    assert "risk_n" not in setup_variant.merge_stats([{"trades": 3, "wins": 1, "pnl": 1.0}])


# ---------------------------------------------------------------- Retention
def test_slim_filter_is_idempotent_and_aged():
    rule = retention.SLIM_POLICY[1]
    flt = retention.slim_filter(rule)
    assert flt["action"] == "HOLD" and flt["outcome"] == {"$nin": ["win", "loss"]}
    assert {"entry_market_snapshot": {"$exists": True}} in flt["$or"]
    assert "$lt" in flt["ts"]
    arch = retention.slim_filter(retention.SLIM_POLICY[0])
    assert arch["source"] == "ai_decisions" and "ts" not in arch


def test_slim_doc_drops_heavy_fields_only():
    d = {"id": "x", "action": "LONG", "entry_market_snapshot": {"a": 1}, "policy_version": {"h": 1},
         "prompt_version": "v", "reasoning": "r"}
    assert retention.slim_doc(d) == {"id": "x", "action": "LONG", "reasoning": "r"}
    assert "entry_market_snapshot" in d                      # Original unverändert


def test_trade_exports_capped():
    pol = {r["coll"]: r for r in retention.merged_policy({})}
    assert pol["backtest_trades"]["keep_last"] == 8 and pol["optimizer_trades"]["keep_last"] == 8


def test_keep_last_collections_have_sort_index():
    idx = {(c, tuple(k[0] for k in keys)) for c, keys, _ in indexes.INDEX_SPECS}
    for rule in retention.DEFAULT_POLICY:
        if rule.get("keep_last") and rule["coll"] not in ("guard_calibration_log",):
            assert (rule["coll"], (rule["ts"],)) in idx, rule["coll"]


class _Res:
    def __init__(self, n):
        self.modified_count = n
        self.deleted_count = 0


class _Coll:
    def __init__(self, log, name):
        self.log, self.name = log, name

    async def update_many(self, flt, upd):
        self.log.append((self.name, flt, upd))
        return _Res(3)

    async def delete_many(self, flt):
        return _Res(0)

    async def count_documents(self, flt):
        return 0

    async def distinct(self, field):
        return []

    async def find_one(self, *a, **k):
        return None

    async def update_one(self, *a, **k):
        return None


class _Db:
    def __init__(self):
        self.log = []
        self.settings = _Coll(self.log, "settings")

    def __getitem__(self, name):
        return _Coll(self.log, name)


def test_sweep_runs_slim_rules():
    db = _Db()
    out = asyncio.run(retention.run_sweep(db, trigger="manual"))
    slim = [x for x in db.log if x[0] in ("ai_chat_archive", "ai_decisions")]
    assert len(slim) == 2 and out["slimmed_total"] == 6
    assert slim[0][2] == {"$unset": {f: "" for f in retention.ARCHIVE_DROP_FIELDS}}


def test_champion_analyses_are_protected():
    champs = {"assign": {"BTCUSDT|swing": {"aid": "ra_a"}, "ETHUSDT|swing": {"aid": "ra_b"}, "X": {}}}
    assert retention.champion_analysis_ids(champs) == {"ra_a", "ra_b"}
    assert retention.champion_analysis_ids(None) == set()
