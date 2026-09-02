"""Tests: Risiko-basierte Positionsgröße (services/position_sizing.py), Playbook-
Erweiterungen (neue SMC-Setups, Paper/Live-Divergenz-Gate, KI-eigene Setups
mit Shadow-Test), SMC-Zonen-Text, Boot-Migration risk_sizing_v1 und die
Integration in bitunix_trade.execute_signal (Marge/Hebel aus dem Risiko-Budget).
Ohne Netzwerk (lokale Mongo, eigene Test-DB)."""
import asyncio
import os
import sys
from datetime import datetime, timezone, timedelta

sys.path.insert(0, "/app/backend")
from dotenv import load_dotenv
load_dotenv("/app/backend/.env")
from motor.motor_asyncio import AsyncIOMotorClient

TEST_DB = os.environ["DB_NAME"] + "_test_risk_sizing"


def iso(**delta):
    return (datetime.now(timezone.utc) - timedelta(**delta)).isoformat()


def approx(a, b, tol=0.02):
    return abs(a - b) <= tol * max(abs(b), 1e-9)


# ------------------------------------------------------------------ Sizing (rein)
def test_conviction_and_combined_scale():
    from services import position_sizing as ps
    assert ps.conviction_scale(100) == 1.0
    assert ps.conviction_scale(10) == 0.5
    assert approx(ps.conviction_scale(55), 0.75)
    assert ps.conviction_scale(None) == 1.0
    assert ps.conviction_scale(0) == 1.0
    assert ps.conviction_scale(10, floor=0.8) == 0.8
    # Minimum statt Produkt: capital_pct 20 (0.556) + ML 0.5 -> 0.5, nicht 0.278
    s, note = ps.combined_scale(ps.conviction_scale(20), 0.5)
    assert s == 0.5 and "nicht gestapelt" in note
    s, _ = ps.combined_scale(ps.conviction_scale(20), 0.5, stack=True)
    assert approx(s, 0.2778, 0.01)
    s, _ = ps.combined_scale(1.0, 1.0)
    assert s == 1.0


def test_compute_reproduces_prod_case_and_fixes_it():
    """Prod-Fall DOTUSDT 01.09.: Equity ~700, SL 0,46 %, alte Marge 4,5 USDT @7,2x
    (Risiko ~0,15 USDT). Risiko-Modus: 2 % = 14 USDT Risiko."""
    from services import position_sizing as ps
    cfg = {"leverage": 7.2, "auto_lev_value": 0.5, "auto_lev_mode": "liq_pct",
           "maintenance_margin_rate": 0.5}
    params = ps.build_params(dict(ps.DEFAULTS), {"capital_pct": 100}, is_swing=False, ml_scale=1.0)
    entry, sl = 0.8572, 0.8572 * (1 + 0.0046)   # SHORT, SL 0,46 % über Entry
    r = ps.compute(params, cfg, entry, sl, equity=700.0, coin_max_lev=125)
    assert r is not None
    assert approx(r["risk_usdt"], 14.0)
    # Hebel: 1/(0,46 % + 0,5 % Puffer + 0,5 % MMR) = 68x -> Deckel 50x
    assert r["leverage"] == 50.0
    assert approx(r["notional"], 14.0 / 0.0046)          # ~3043 USDT
    assert approx(r["margin"], 14.0 / 0.0046 / 50)        # ~60,9 USDT (< 15 % Deckel 105)
    assert r["capped"] is False
    # Enger SL -> größere Position bei gleichem Risiko; weiter SL -> kleiner
    r_wide = ps.compute(params, cfg, entry, entry * 1.02, equity=700.0, coin_max_lev=125)
    assert r_wide["notional"] < r["notional"] and approx(r_wide["risk_usdt"], 14.0)
    assert r_wide["leverage"] < 50.0  # 1/(2 %+0,5 %+0,5 %) = 33x


def test_compute_margin_cap_and_floors():
    from services import position_sizing as ps
    cfg = {"leverage": 5, "auto_lev_value": 0.5, "maintenance_margin_rate": 0.5}
    params = ps.build_params({**ps.DEFAULTS, "risk_max_leverage": 5},
                             {"capital_pct": 100}, is_swing=False)
    # SL 0,3 %, Hebel-Deckel 5x -> Marge = 14/0,003/5 = 933 > 15 % von 700 (105) -> gedeckelt
    r = ps.compute(params, cfg, 100.0, 99.7, equity=700.0, coin_max_lev=200)
    assert r["capped"] is True and approx(r["margin"], 105.0)
    assert r["risk_usdt"] < 14.0
    # Swing-Cap deckelt den Hebel
    p_sw = ps.build_params({**ps.DEFAULTS, "swing_max_leverage": 8},
                           {"capital_pct": 100}, is_swing=True)
    r_sw = ps.compute(p_sw, cfg, 100.0, 99.0, equity=1000.0, coin_max_lev=200)
    assert r_sw["leverage"] == 8.0
    # Coin-Max deckelt
    r_cm = ps.compute(params, cfg, 100.0, 99.0, equity=1000.0, coin_max_lev=3)
    assert r_cm["leverage"] == 3.0
    # Ungültige Eingaben -> None (Legacy-Pfad)
    assert ps.compute(params, cfg, 100.0, 100.0, equity=700.0) is None
    assert ps.compute(params, cfg, 100.0, 99.0, equity=None) is None
    assert ps.compute(params, cfg, 100.0, 99.0, equity=0) is None
    # Sammel-Trades skaliert
    p_c = ps.build_params({**ps.DEFAULTS, "risk_collection_scale": 0.5},
                          {"capital_pct": 100}, is_swing=False, collection=True)
    r_c = ps.compute(p_c, cfg, 100.0, 99.0, equity=1000.0, coin_max_lev=200)
    assert approx(r_c["risk_usdt"], 10.0)


def test_clamp_updates():
    from services import position_sizing as ps
    cfg = dict(ps.DEFAULTS)
    ps.clamp_updates({"sizing_mode": "legacy"}, cfg)
    assert cfg["sizing_mode"] == "legacy"
    ps.clamp_updates({"sizing_mode": "risk", "risk_per_trade_pct": 50, "risk_max_leverage": 999,
                      "risk_conviction_floor": 0, "risk_stack_reductions": 1,
                      "risk_max_margin_pct": "abc"}, cfg)
    assert cfg["sizing_mode"] == "risk"
    assert cfg["risk_per_trade_pct"] == 10.0
    assert cfg["risk_max_leverage"] == 200 and isinstance(cfg["risk_max_leverage"], int)
    assert cfg["risk_conviction_floor"] == 0.1
    assert cfg["risk_stack_reductions"] is True
    assert cfg["risk_max_margin_pct"] == 15.0
    ps.clamp_updates({"sizing_mode": "bogus"}, cfg)
    assert cfg["sizing_mode"] == "risk"


def test_ai_engine_defaults_include_sizing_and_prompt_enum():
    from services.ai_engine import DEFAULT_AI_CONFIG, ANALYSIS_SYSTEM, ANALYSIS_SYSTEM_LEAN
    from services import ai_playbook
    assert DEFAULT_AI_CONFIG["sizing_mode"] == "risk"
    assert DEFAULT_AI_CONFIG["risk_per_trade_pct"] == 2.0
    for p in (ANALYSIS_SYSTEM, ANALYSIS_SYSTEM_LEAN):
        assert "order_block|fvg_fill|htf_range" in p
        assert '"new_setups"' in p
    assert ai_playbook.SETUP_ENUM.endswith("order_block|fvg_fill|htf_range")


# ------------------------------------------------------------------ Playbook (rein)
def test_normalize_setup_new_ids_and_aliases():
    from services import ai_playbook as pb
    pb.set_custom_cache({})
    assert pb.normalize_setup("order_block") == "order_block"
    assert pb.normalize_setup("Order Block Retest") == "order_block"
    assert pb.normalize_setup("fvg") == "fvg_fill"
    assert pb.normalize_setup("bullish imbalance") == "fvg_fill"
    assert pb.normalize_setup("htf_range") == "htf_range"
    assert pb.normalize_setup("range_fade") == "range_fade"   # bestehend unverändert
    assert pb.normalize_setup("liquidity sweep") == "liquidity_sweep"
    assert pb.normalize_setup("xyz") == "other"
    pb.set_custom_cache({"vwap_reclaim": {"desc": "x"}})
    assert pb.normalize_setup("VWAP Reclaim") == "vwap_reclaim"
    assert "vwap_reclaim" in pb.all_setups()
    pb.set_custom_cache({})


def test_valid_custom_setup():
    from services import ai_playbook as pb
    pb.set_custom_cache({})
    ok, sid = pb.valid_custom_setup("VWAP-Reclaim", "Rückeroberung des VWAP nach Sweep, SL unter Sweep-Low, TP POC")
    assert ok and sid == "vwap_reclaim"
    assert pb.valid_custom_setup("breakout", "lange Beschreibung hier drin......")[0] is False
    assert pb.valid_custom_setup("squeeze_v2", "lange Beschreibung hier drin......")[0] is False  # Alias
    assert pb.valid_custom_setup("ab", "lange Beschreibung hier drin......")[0] is False
    assert pb.valid_custom_setup("neu_setup", "kurz")[0] is False


def test_live_divergent():
    from services import ai_playbook as pb
    weak = {"trades": 15, "wins": 1, "pnl": -21.07, "verdict": "schwach"}
    good = {"trades": 79, "wins": 38, "pnl": 40.0, "verdict": "neutral"}
    assert pb.live_divergent(weak, good) is not None
    assert pb.live_divergent({"trades": 5, "wins": 0, "pnl": -3, "verdict": "test"}, good) is None
    assert pb.live_divergent(weak, {**good, "verdict": "schwach"}) is None
    assert pb.live_divergent(None, good) is None
    assert pb.live_divergent({"trades": 10, "wins": 6, "pnl": 3, "verdict": "bewährt"}, good) is None


def test_maturity_overview_live_columns():
    from services import ai_playbook as pb
    pb.set_custom_cache({"vwap_reclaim": {"desc": "x"}})
    stats = {"squeeze_breakout": {"trades": 79, "wins": 38, "pnl": 40.0, "verdict": "neutral"}}
    live = {"squeeze_breakout": {"trades": 15, "wins": 1, "pnl": -21.0, "verdict": "schwach"}}
    lb = {"squeeze_breakout": {"reason": "live 15 Trades"}}
    rows = {r["setup"]: r for r in pb.maturity_overview(stats, {}, lb, live)}
    r = rows["squeeze_breakout"]
    assert r["live_ready"] is False and r["reason"].startswith("live pausiert")
    assert r["live_trades"] == 15 and r["live_winrate"] == 7
    assert rows["vwap_reclaim"]["custom"] is True and rows["order_block"]["custom"] is False
    pb.set_custom_cache({})


# ------------------------------------------------------------------ SMC-Zonen
def _candles(n=400, start=100.0):
    import math
    out = []
    p = start
    for i in range(n):
        drift = 0.4 * math.sin(i / 25.0)
        o = p
        c = p + drift + (0.6 if i % 97 == 50 else 0) - (0.6 if i % 89 == 40 else 0)
        h, l = max(o, c) + 0.05, min(o, c) - 0.05
        out.append({"timestamp": 1_700_000_000_000 + i * 60_000, "open": o, "high": h,
                    "low": l, "close": c, "volume": 10 + (i % 7)})
        p = c
    return out


def test_smc_zones_text_and_limits():
    from services import smc_zones
    cs = _candles()
    z = smc_zones.zones(cs, cs[-1]["close"])
    for tf, rows in z.items():
        assert tf in smc_zones.TIMEFRAMES
        assert len(rows) <= smc_zones.MAX_ZONES_PER_TF
        for r in rows:
            assert abs(r["dist_pct"]) <= smc_zones.MAX_DISTANCE_PCT
            assert r["kind"] in ("OB", "FVG") and r["side"] in ("bull", "bear")
    txt = smc_zones.zones_text(cs, cs[-1]["close"])
    assert txt is None or txt.startswith("SMC-Zonen ")
    assert smc_zones.zones_text(cs[:20], 100.0) is None  # zu wenig Daten -> keine Zeile


# ------------------------------------------------------------------ DB-Tests
async def _db_tests():
    from services import ai_playbook as pb
    from services import boot_migrations as bm
    client = AsyncIOMotorClient(os.environ["MONGO_URL"], serverSelectionTimeoutMS=5000)
    db = client[TEST_DB]
    await client.drop_database(TEST_DB)

    # ---- Divergenz-Gate: Paper ok, live schwach -> live_blocked, weiter sammelbar
    rows = []
    for i in range(60):   # Sammel-Trades: 50 % Win
        rows.append({"id": f"c{i}", "strategy_id": "ai_trader", "status": "closed",
                     "mode": "paper", "data_collection": True, "setup": "squeeze_breakout",
                     "realized_pnl": 1.5 if i % 2 else -0.8, "opened_at": iso(days=2)})
    for i in range(15):   # Live: 1 Gewinner von 15
        rows.append({"id": f"l{i}", "strategy_id": "ai_trader", "status": "closed",
                     "mode": "live", "setup": "squeeze_breakout",
                     "realized_pnl": 0.5 if i == 0 else -1.5, "opened_at": iso(days=1)})
    await db.auto_trades.insert_many(rows)
    data = await pb.refresh(db)
    assert data["stats"]["squeeze_breakout"]["verdict"] in ("neutral", "bewährt")
    assert "squeeze_breakout" in data["live_blocked"], data["live_blocked"]
    assert data["live_ready"]["squeeze_breakout"] is False
    assert "squeeze_breakout" not in data["disabled"]
    assert pb.live_block_reason("squeeze_breakout") and "live-schwach" in pb.live_block_reason("squeeze_breakout")
    assert pb.disabled_reason("squeeze_breakout") is None   # Sammeln bleibt erlaubt
    feed = await db.ai_chat.find_one({"role": "playbook", "setup": "squeeze_breakout"})
    assert feed and "live deutlich schlechter" in feed["text"]
    # Idempotent: zweiter Lauf legt keine zweite Sperre/Meldung an
    await pb.refresh(db)
    assert await db.ai_chat.count_documents({"role": "playbook", "setup": "squeeze_breakout"}) == 1
    # Ablauf der Live-Sperre -> Re-Test freigegeben
    await db.settings.update_one({"_id": pb.STATE_ID}, {"$set": {
        "live_blocked.squeeze_breakout.retest_at": iso(days=1)}})
    await db.auto_trades.delete_many({"mode": "live"})
    data = await pb.refresh(db)
    assert "squeeze_breakout" not in data["live_blocked"]
    ctx = await pb.context_text(db)
    assert "order_block" in ctx and "new_setups" in ctx and "MULTI-TIMEFRAME" in ctx
    print("  ✓ Divergenz-Gate (live schwach, paper ok) + Re-Test + Prompt-Block")

    # ---- KI-eigene Setups: anlegen, Shadow (nicht live-reif), ausmustern
    res = await pb.propose_custom_setup(db, "VWAP Reclaim",
                                        "Rückeroberung des Session-VWAP nach Sweep, SL unter Sweep-Low, TP POC (5m).")
    assert res["status"] == "ok" and res["id"] == "vwap_reclaim"
    assert (await pb.propose_custom_setup(db, "vwap_reclaim", "nochmal eine lange Beschreibung dazu"))["status"] == "exists"
    assert (await pb.propose_custom_setup(db, "breakout", "kollidiert mit Playbook-Setup ....."))["status"] == "rejected"
    data = await pb.refresh(db)
    assert "vwap_reclaim" in data["custom"] and data["live_ready"]["vwap_reclaim"] is False
    assert pb.normalize_setup("vwap_reclaim") == "vwap_reclaim"
    ok, why = pb.live_ready(data["stats"].get("vwap_reclaim"))
    assert ok is False and "neues Setup" in why
    mat = {r["setup"]: r for r in pb.maturity_overview(data["stats"], data["disabled"],
                                                        data["live_blocked"], data["live_stats"])}
    assert mat["vwap_reclaim"]["custom"] is True
    # Max-Anzahl
    for i in range(pb.MAX_CUSTOM_SETUPS + 2):
        await pb.propose_custom_setup(db, f"idea_{i}", "eine ausreichend lange Beschreibung des Setups")
    doc = await db.settings.find_one({"_id": pb.STATE_ID})
    assert len(doc["custom"]) == pb.MAX_CUSTOM_SETUPS
    # schwaches KI-Setup -> gesperrt UND ausgemustert (kein Re-Test), Wiedervorschlag abgelehnt
    await db.auto_trades.insert_many([
        {"id": f"v{i}", "strategy_id": "ai_trader", "status": "closed", "mode": "paper",
         "data_collection": True, "setup": "vwap_reclaim", "realized_pnl": -1.0,
         "opened_at": iso(days=1)} for i in range(10)])
    data = await pb.refresh(db)
    assert "vwap_reclaim" not in data["custom"]
    doc = await db.settings.find_one({"_id": pb.STATE_ID})
    assert "vwap_reclaim" in doc["custom_retired"]
    assert (await pb.propose_custom_setup(db, "vwap_reclaim", "nochmal eine lange Beschreibung dazu"))["status"] == "rejected"
    pb.set_custom_cache({})
    print("  ✓ KI-Setups: Shadow-Test, Limit, Ausmusterung")

    # ---- Boot-Migration risk_sizing_v1: setzt risk, respektiert Trader-Wahl, idempotent
    class _Eng:
        config = {}
    await db.settings.update_one({"_id": "ai_trader_config"}, {"$set": {"enabled": True}}, upsert=True)
    eng = _Eng()
    await bm.migrate_risk_sizing(db, eng)
    doc = await db.settings.find_one({"_id": "ai_trader_config"})
    assert doc["sizing_mode"] == "risk" and doc["risk_per_trade_pct"] == 2.0
    assert eng.config["sizing_mode"] == "risk"
    await db.settings.update_one({"_id": "ai_trader_config"}, {"$set": {"sizing_mode": "legacy"}})
    await bm.migrate_risk_sizing(db, eng)   # Marker gesetzt -> keine Wiederholung
    assert (await db.settings.find_one({"_id": "ai_trader_config"}))["sizing_mode"] == "legacy"
    await client.drop_database(TEST_DB)
    await db.settings.update_one({"_id": "ai_trader_config"},
                                 {"$set": {"sizing_mode": "legacy"}}, upsert=True)
    await bm.migrate_risk_sizing(db, None)  # Trader hat selbst gewählt -> unangetastet
    assert (await db.settings.find_one({"_id": "ai_trader_config"}))["sizing_mode"] == "legacy"
    await client.drop_database(TEST_DB)
    print("  ✓ Boot-Migration risk_sizing_v1")


def test_db_flows():
    asyncio.run(_db_tests())


# ------------------------------------------------------------------ Integration bitunix_trade
async def _exec_signal_sizing():
    """execute_signal mit ai_sizing -> Marge/Hebel aus dem Risiko-Budget (Paper,
    kein Netzwerk); ohne ai_sizing bleibt der Legacy-Pfad exakt erhalten."""
    from services import bitunix_trade as bt
    client = AsyncIOMotorClient(os.environ["MONGO_URL"], serverSelectionTimeoutMS=5000)
    db = client[TEST_DB + "_exec"]
    await client.drop_database(db.name)
    mgr = bt.AutoTradeManager(bt.BitunixTradeClient())
    mgr.db = db
    mgr.config = {"enabled": True, "coins": {}, "strategy_coin_configs": {},
                  "capital_allocation": {"paper": {"mode": "full", "value": 0, "base_balance": 1000.0},
                                         "live": {"mode": "full", "value": 0}}}
    cfg = dict(bt.DEFAULT_COIN_CFG)
    cfg.update({"enabled": True, "mode": "paper", "max_capital": 30.0, "leverage": 7.2})
    mgr.config["coins"]["DOTUSDT"] = cfg
    mgr.config["strategy_coin_configs"]["ai_trader_DOTUSDT"] = dict(cfg)
    candles = _candles(200, start=0.85)
    entry = candles[-1]["close"]
    await db.settings.update_one({"_id": "ai_trader_config"}, {"$set": {
        "fee_guard_enabled": False, "max_trades_per_coin": 1}}, upsert=True)
    base = {"symbol": "DOTUSDT", "type": "SHORT", "entry_price": entry,
            "strategy_id": "ai_trader", "strategy_name": "KI Trader", "timeframe": "1m",
            "use_ai_levels": True, "stop_loss": entry * 1.005, "take_profit_1": entry * 0.99,
            "take_profit_full": entry * 0.98, "ai_max_capital": 100.0, "ai_capital_pct": 30,
            "ml_risk_scale": 0.5, "candles": candles}
    return mgr, db, client, mgr.on_signal, base, entry


def test_execute_signal_uses_risk_sizing():
    async def run():
        from services import position_sizing as ps
        mgr, db, client, fn, base, entry = await _exec_signal_sizing()
        # Legacy: 30 × 0.3 × 0.5 = 4.5 USDT @ 7.2x
        await fn(dict(base), base["candles"])
        t_legacy = await db.auto_trades.find_one({"symbol": "DOTUSDT"}, sort=[("opened_at", -1)])
        assert t_legacy, "Legacy-Trade wurde nicht angelegt"
        assert approx(float(t_legacy["max_capital"]), 4.5), t_legacy["max_capital"]
        assert float(t_legacy["leverage"]) == 7.2
        assert t_legacy.get("sizing") is None
        await db.auto_trades.delete_many({})
        # Risiko-Modus: Equity 1000 (Paper-Basis) × 2 % = 20 USDT Risiko, aber
        # Skalierung min(conv 0.611, ML 0.5) = 0.5 -> 10 USDT Risiko, SL 0,5 %
        sig2 = dict(base)
        sig2["ai_sizing"] = ps.build_params(dict(ps.DEFAULTS), {"capital_pct": 30},
                                            is_swing=False, ml_scale=0.5)
        await fn(sig2, base["candles"])
        t = await db.auto_trades.find_one({"symbol": "DOTUSDT"}, sort=[("opened_at", -1)])
        assert t, "Risiko-Trade wurde nicht angelegt"
        s = t.get("sizing") or {}
        assert s.get("risk_usdt") and approx(s["risk_usdt"], 10.0, 0.05), s
        assert float(t["leverage"]) == 50.0, t["leverage"]        # 1/(0,5+0,5+0,5 %) = 66 -> 50
        assert approx(float(t["max_capital"]), 10.0 / 0.005 / 50, 0.05), t["max_capital"]  # ~40 USDT
        assert float(t["max_capital"]) > 4.5 * 5
        await client.drop_database(db.name)
        print("  ✓ execute_signal: Legacy unverändert, Risiko-Modus Marge 40 USDT @ 50x")
    asyncio.run(run())


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            print(f"- {name}")
            fn()
    print("ALLE TESTS GRÜN")
