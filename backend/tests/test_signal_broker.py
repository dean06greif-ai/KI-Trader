"""Regressionstests „Detektor schlägt vor, KI entscheidet“ (10/2026).

Abgedeckt (rein bzw. mit Fake-DB, ohne Netzwerk):
  * Signal-Fenster je Setup, Overrides, Gültigkeit (SL/Hinterherlaufen), Matching
  * KI-Entscheidung -> ki_geprueft / ki_frei, Re-Label auf das Detektor-Setup, Timing
  * Live-Gate: kein Setup = kein Live (Gate-Lücke), KI-frei = Paper, erste 5 KI-geprüfte
    Trades = Paper, danach Erwartungswert-Regel, Rückstufung bei schlechtem Live-Ergebnis
  * Altsystem-Sperren (Mischstatistik/Bypass) blockieren im Broker-Modus nicht
  * Statistik nach Quelle, Konfidenz-Kalibrierung, KI-früh-Erkennung
  * Regel-Detektor für KI-eigene Setups, Fee-Wächter-Erweiterung für Paper
"""
import asyncio
from datetime import datetime, timedelta, timezone

import numpy as np
import pytest

from services import confidence_calibration as cc
from services import custom_detector as cd
from services import fee_guard_widen
from services import signal_broker as sb
from services import source_stats as ss
from services.ai_engine import DEFAULT_AI_CONFIG, AIEngine

NOW = datetime.now(timezone.utc)


def _hit(setup="divergence", side="SHORT", entry=100.0, sl=101.0, minutes_ago=5):
    bar_ts = int((NOW - timedelta(minutes=minutes_ago + 5)).timestamp() * 1000)
    return {"setup": setup, "side": side, "entry": entry, "sl": sl, "tp1": 99.0, "tpf": 98.0,
            "note": "x", "has_edge": False, "bar_ts": bar_ts}


# ---------------- Signal-Fenster ----------------
def test_defaults_in_ai_config():
    for k in sb.DEFAULTS:
        assert k in DEFAULT_AI_CONFIG
    assert DEFAULT_AI_CONFIG["fee_guard_widen_collection"] is True


def test_window_per_setup_and_override():
    assert sb.window_for("momentum_news") < sb.window_for("divergence") < sb.window_for("htf_range")
    assert sb.window_for("unbekannt") == sb.DEFAULT_WINDOW
    assert sb.window_for("divergence", {"signal_window_overrides": {"divergence": 90}}) == 90
    cfg = {}
    sb.clamp_updates({"signal_window_overrides": {"breakout": 9999}, "signal_min_ki_trades": 0,
                      "signal_broker_enabled": 0, "signal_max_chase_r": 9}, cfg)
    assert cfg["signal_window_overrides"]["breakout"] == sb.WINDOW_BOUNDS[1]
    assert cfg["signal_min_ki_trades"] == 1 and cfg["signal_broker_enabled"] is False
    assert cfg["signal_max_chase_r"] == 2.0


def test_build_signal_window_starts_at_bar_close():
    sig = sb.build_signal("BTCUSDT", "crypto", _hit(minutes_ago=5), {}, now=NOW)
    assert sig["window_min"] == 45 and sig["status"] == "open"
    assert sb.is_open(sig, NOW) and not sb.is_open(sig, NOW + timedelta(minutes=41))
    assert sb.entry_delay_min(sig, NOW) == pytest.approx(5.0)


def test_price_state_stop_and_chase():
    sig = {"side": "SHORT", "entry": 100.0, "sl": 101.0}
    assert sb.price_state(sig, 99.7, 0.6) == "valid"
    assert sb.price_state(sig, 101.2, 0.6) == "stopped"
    assert sb.price_state(sig, 99.3, 0.6) == "chased"
    assert sb.r_from_entry(sig, 99.5) == 0.5


def test_find_match_by_id_or_setup():
    s1 = sb.build_signal("BTCUSDT", "crypto", _hit(), {}, now=NOW)
    s2 = sb.build_signal("BTCUSDT", "crypto", _hit(setup="range_fade"), {}, now=NOW)
    sigs = [s1, s2]
    assert sb.find_match(sigs, "BTCUSDT", "SHORT", "divergence", now=NOW) is s1
    assert sb.find_match(sigs, "BTCUSDT", "SHORT", "trend_follow", f"sig:{s2['id']}", now=NOW) is s2
    assert sb.find_match(sigs, "BTCUSDT", "LONG", "divergence", now=NOW) is None
    assert sb.find_match(sigs, "ETHUSDT", "SHORT", "divergence", now=NOW) is None
    assert sb.find_match(sigs, "BTCUSDT", "SHORT", "trend_follow", now=NOW) is None


def test_attach_marks_ki_geprueft_relabels_and_times():
    broker = sb.SignalBroker()
    sig = sb.build_signal("BTCUSDT", "crypto", _hit(minutes_ago=0), {})
    broker.signals.append(sig)
    dec = {"symbol": "BTCUSDT", "action": "SHORT", "setup": "range_fade", "price": 99.9}
    assert broker.attach(dec, sig["id"], {}) is sig
    assert dec["signal_source"] == "ki_geprueft" and dec["signal_ref"] == sig["id"]
    assert dec["setup"] == "divergence" and dec["setup_label_ki"] == "range_fade"
    assert dec["ki_timing"] == "sofort"
    free = {"symbol": "BTCUSDT", "action": "LONG", "setup": "divergence", "price": 99.9}
    assert broker.attach(free, None, {}) is None and free["signal_source"] == "ki_frei"
    chased = {"symbol": "BTCUSDT", "action": "SHORT", "setup": "divergence", "price": 98.0}
    assert broker.attach(chased, sig["id"], {}) is None
    assert chased["signal_source"] == "ki_frei" and chased["signal_invalid"] == "chased"


def test_expire_counts_reviews_and_taken():
    broker = sb.SignalBroker()
    old = sb.build_signal("BTCUSDT", "crypto", _hit(minutes_ago=200), {})
    old["reviews"] = 2
    taken = sb.build_signal("ETHUSDT", "crypto", _hit(minutes_ago=1), {})
    broker.signals += [old, taken]
    asyncio.run(broker.mark_taken({"signal_ref": taken["id"], "id": "d1", "ki_timing": "sofort"}))
    asyncio.run(broker.expire())
    assert {r["id"]: r["status"] for r in broker.recent} == {taken["id"]: "genommen", old["id"]: "verworfen"}
    assert broker.open_signals() == []
    summ = sb.summarize_status(broker.recent)
    assert summ["divergence"]["genommen"] == 1 and summ["divergence"]["take_rate"] == 50


def test_prompt_block_lists_open_signals_with_instructions():
    broker = sb.SignalBroker()
    sig = sb.build_signal("BTCUSDT", "crypto", _hit(minutes_ago=1), {})
    broker.signals.append(sig)
    txt = broker.prompt_block({"BTCUSDT": 99.8}, {})
    assert f"[sig:{sig['id']}]" in txt and "signal_id" in txt and "KI-frei" in txt
    assert broker.prompt_block({}, {}, symbols=["ETHUSDT"]) == ""


def test_early_candidates_only_ki_free_same_setup_and_side():
    trades = [{"id": "a", "symbol": "BTCUSDT", "side": "SHORT", "setup": "divergence",
               "signal_source": "ki_frei", "opened_at": (NOW - timedelta(minutes=10)).isoformat()},
              {"id": "b", "symbol": "BTCUSDT", "side": "LONG", "setup": "divergence",
               "signal_source": "ki_frei", "opened_at": (NOW - timedelta(minutes=10)).isoformat()},
              {"id": "c", "symbol": "BTCUSDT", "side": "SHORT", "setup": "divergence",
               "signal_source": "ki_frei", "opened_at": (NOW - timedelta(minutes=90)).isoformat()}]
    out = sb.early_candidates(trades, {"setup": "divergence", "side": "SHORT"}, "BTCUSDT", 45, NOW)
    assert [t["id"] for t in out] == ["a"]


# ---------------- Statistik nach Quelle + Reife ----------------
def _trade(src, r, mode="paper", coll=True, setup="divergence", sym="BTCUSDT", conf=70, delay=None):
    return {"symbol": sym, "setup": setup, "signal_source": src, "mode": mode, "data_collection": coll,
            "realized_pnl": r * 10, "risk_usdt": 10, "ai_confidence": conf, "entry_delay_min": delay}


def test_source_of_legacy_trades():
    assert ss.source_of({"ai_reasoning": "Setup-Trigger (regelbasierter Detektor …"}) == "regel"
    assert ss.source_of({"ai_reasoning": "Bärische Struktur"}) == "ki_frei"
    assert ss.source_of({"signal_source": "regel_live"}) == "regel"


def test_aggregate_separates_sources_and_timing():
    data = ss.aggregate([_trade("regel", -0.2), _trade("ki_geprueft", 0.5, delay=3),
                         _trade("ki_geprueft", 0.3, delay=12), _trade("ki_frei", -1.0)])
    assert data[("regel", "crypto", "divergence")]["n"] == 1
    ki = data[("ki_geprueft", "crypto", "divergence")]
    assert ki["n"] == 2 and ki["avg_r"] == 0.4 and set(ki["timing"]) == {"sofort", "6-20 min"}
    rows = ss.rows_for_ui(data, {})
    assert rows[0]["ki_value_r"] == pytest.approx(0.6)


def test_maturity_first_five_are_paper_then_expectancy():
    cfg = dict(sb.DEFAULTS)
    four = ss.aggregate([_trade("ki_geprueft", 1.0)] * 4)[("ki_geprueft", "crypto", "divergence")]
    ok, why, phase = ss.maturity(four, cfg)
    assert not ok and phase == "sammeln" and "4/5" in why
    good = ss.aggregate([_trade("ki_geprueft", 0.6)] * 6)[("ki_geprueft", "crypto", "divergence")]
    assert ss.maturity(good, cfg)[0] is True
    bad = ss.aggregate([_trade("ki_geprueft", -0.3)] * 8)[("ki_geprueft", "crypto", "divergence")]
    assert ss.maturity(bad, cfg)[2] == "nicht_validiert"
    live_bad = ss.aggregate([_trade("ki_geprueft", 2.0)] * 10
                            + [_trade("ki_geprueft", -1.0, mode="live", coll=False)] * 10)
    assert ss.maturity(live_bad[("ki_geprueft", "crypto", "divergence")], cfg)[2] == "zurueckgestuft"
    assert ss.maturity(None, cfg)[2] == "sammeln"


# ---------------- Konfidenz-Kalibrierung ----------------
def test_calibration_bins_shrink_and_informative_flag():
    trades = ([_trade("ki_frei", -0.4, conf=80)] * 20 + [_trade("ki_frei", 0.1, conf=60)] * 20
              + [_trade("ki_frei", 0.0, conf=70)] * 20 + [_trade("regel", 5.0, conf=90)] * 50)
    t = cc.build(trades)
    assert t["n"] == 60                                  # Regel-Trades zählen nicht
    b80 = cc.lookup(t, 80)
    assert b80["n"] == 20 and b80["avg_r"] == -0.4 and b80["cal_r"] > -0.4   # geschrumpft
    assert t["informative"] is False
    assert "KEIN Zusammenhang" in cc.prompt_line(t)
    assert cc.lookup(t, None) is None and cc.prompt_line({"n": 0}) == ""


# ---------------- Live-Gate im Broker-Modus ----------------
class _FakeAT:
    def __init__(self, mode):
        self.mode = mode

    def effective_mode(self, *_a):
        return self.mode


@pytest.fixture
def engine(monkeypatch):
    from core import state
    eng = AIEngine()
    eng.db = object()
    monkeypatch.setattr(state, "autotrader", _FakeAT("live"))
    ss._cache.update(ts=1e18, data=ss.aggregate([_trade("ki_geprueft", 0.8)] * 6))
    yield eng
    ss.invalidate()


def test_gate_gap_closed_without_setup(engine):
    why = asyncio.run(engine._setup_live_gate({"symbol": "BTCUSDT", "setup": None}))
    assert why and "Kein Setup" in why


def test_gate_ki_free_goes_paper(engine):
    why = asyncio.run(engine._setup_live_gate({"symbol": "BTCUSDT", "setup": "divergence",
                                               "signal_source": "ki_frei"}))
    assert why and "KI-frei" in why


def test_gate_mature_ki_checked_goes_live_despite_legacy_demotion(engine, monkeypatch):
    from services import ai_playbook
    monkeypatch.setattr(ai_playbook, "live_block_reason", lambda *a, **k: "rückgestuft (alt)")
    dec = {"symbol": "BTCUSDT", "setup": "divergence", "signal_source": "ki_geprueft",
           "confidence": 60}
    assert asyncio.run(engine._setup_live_gate(dec)) is None
    assert dec["maturity"]["phase"] == "live"


def test_gate_immature_ki_checked_collects(engine):
    dec = {"symbol": "ETHUSDT", "setup": "range_fade", "signal_source": "ki_geprueft"}
    why = asyncio.run(engine._setup_live_gate(dec))
    assert "Datensammlung 0/5" in why and dec["maturity"]["phase"] == "sammeln"


def test_gate_paper_mode_unchanged(engine, monkeypatch):
    from core import state
    monkeypatch.setattr(state, "autotrader", _FakeAT("paper"))
    assert asyncio.run(engine._setup_live_gate({"symbol": "BTCUSDT", "setup": None})) is None


def test_collection_key_separates_sources(engine):
    assert engine._collection_key("BTCUSDT", "divergence", "regel") != \
        engine._collection_key("BTCUSDT", "divergence", "ki_geprueft")
    engine.config["signal_broker_enabled"] = False
    assert engine._collection_key("BTCUSDT", "divergence", "regel") == "BTCUSDT:divergence"


# ---------------- Regel-Detektor für KI-eigene Setups ----------------
def test_custom_rule_validation_clamps_and_rejects():
    ok, _, rule = cd.validate({"side": "short", "conditions": [{"f": "rsi", "op": "<", "v": "25"}],
                               "sl_atr": 99, "tp_r": 0.1})
    assert ok and rule["side"] == "SHORT" and rule["sl_atr"] == 4.0 and rule["tp_r"] == 1.0
    assert cd.validate({"side": "LONG", "conditions": [{"f": "magic", "op": "<", "v": 1}]})[0] is False
    assert cd.validate({"side": None, "conditions": []})[0] is False
    assert cd.validate(None)[0] is False


def _features(n_min=900, drop_at=None):
    from services.candles import CandleArray
    from services.setup_backtest.detectors import Features
    ts = 1_760_000_000_000 - (1_760_000_000_000 % 300_000) + np.arange(n_min) * 60_000
    rng = np.random.default_rng(1)
    cl = 100 + np.cumsum(rng.normal(0, 0.05, n_min))
    if drop_at:
        cl[drop_at:] -= np.linspace(0, 3, n_min - drop_at)
    op = np.r_[cl[0], cl[:-1]]
    return Features(CandleArray(ts, op, np.maximum(op, cl) + 0.02, np.minimum(op, cl) - 0.02, cl,
                                np.full(n_min, 10.0)), "crypto")


def test_custom_detector_fires_on_rule_and_builds_levels():
    f = _features(drop_at=700)
    _, _, rule = cd.validate({"side": "SHORT", "conditions": [{"f": "chg_1h_pct", "op": "<", "v": -0.5}],
                              "confirm": True, "sl_atr": 1.0, "tp_r": 2.0})
    sigs = cd.detect(f, rule)
    assert sigs, "Abverkauf muss die Regel auslösen"
    s = sigs[-1]
    assert s.side == "SHORT" and s.sl > s.entry > s.tp1 > s.tpf
    assert cd.detect(f, {**rule, "conditions": [{"f": "rsi", "op": ">", "v": 101}]}) == []


# ---------------- Fee-Wächter: Paper erweitern statt verwerfen ----------------
def test_fee_guard_widen_smallest_step_keeps_crv():
    out = fee_guard_widen.widen(lambda sl, tp: abs(100 - sl) >= 0.5, "LONG", 100.0, 99.6, 100.8, 101.6)
    sl, tp1, tpf, note = out
    assert sl == pytest.approx(99.48) and tp1 == pytest.approx(101.04) and "×1.3" in note
    assert (tp1 - 100) / (100 - sl) == pytest.approx(2.0)
    assert fee_guard_widen.widen(lambda sl, tp: False, "SHORT", 100.0, 100.4, 99.2, 98.4) is None


# ---------------- Getrennte Welten (lokale MongoDB, falls erreichbar) ----------------
def _local_db():
    try:
        from pymongo import MongoClient
        MongoClient("mongodb://localhost:27017", serverSelectionTimeoutMS=500).admin.command("ping")
    except Exception:  # noqa: BLE001
        pytest.skip("keine lokale MongoDB")
    from motor.motor_asyncio import AsyncIOMotorClient
    return AsyncIOMotorClient("mongodb://localhost:27017")["test_signal_broker_worlds"]


def test_rule_paper_does_not_block_ki_paper_dup_and_direction():
    async def run():
        db = _local_db()
        await db.auto_trades.delete_many({})
        now = datetime.now(timezone.utc).isoformat()
        rule = {"id": "r1", "status": "open", "strategy_id": "ai_trader", "symbol": "BTCUSDT",
                "side": "SHORT", "opened_at": now, "data_collection": True, "signal_source": "regel",
                "entry": 100.0}
        await db.auto_trades.insert_many([dict(rule)] + [
            {**rule, "id": f"r{i}", "symbol": s} for i, s in enumerate(
                ["ADAUSDT", "DOTUSDT", "LINKUSDT", "SUIUSDT", "XRPUSDT"], 2)])
        eng = AIEngine()
        eng.db = db
        dec = {"symbol": "BTCUSDT", "action": "SHORT", "price": 98.0, "setup": "divergence",
               "signal_source": "ki_geprueft"}
        assert await eng._dup_entry_block("BTCUSDT", "SHORT", True, "ki_geprueft") is None
        assert await eng._dup_entry_block("BTCUSDT", "SHORT", True, "regel")
        ok, why = await eng._diversification_gate("BTCUSDT", dec, collection=True)
        assert ok, why
        ok_r, why_r = await eng._diversification_gate("BTCUSDT", {**dec, "signal_source": "regel"},
                                                      collection=True)
        assert not ok_r and "Richtungs-Guard" in why_r
        eng.config["signal_broker_enabled"] = False          # Alt-Modus unverändert
        assert await eng._dup_entry_block("BTCUSDT", "SHORT", True, "ki_geprueft")
        await db.client.drop_database("test_signal_broker_worlds")
    asyncio.run(run())
