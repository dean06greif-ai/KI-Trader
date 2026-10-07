"""Setup-Trigger (services/setup_trigger.py) + Bewegungs-Scanner-Vor-Einordnung
(ai_move_scanner.classify): Backtest-Detektoren live, LLM-freie Paper-Sammel-
Trades, momentum_news-Impuls-Detektor, Token-Sparen im Bewegungs-Scanner."""
import asyncio
from datetime import datetime, timezone

import pytest

from services import setup_trigger as st
from services import ai_move_scanner as ms
from services.ai_engine import DEFAULT_AI_CONFIG
from services.setup_backtest.detectors import Signal, VARIANTS


def _candles(n=400, base=100.0, step=0.0, vol=10.0, start_ms=1_700_000_000_000):
    rows = []
    p = base
    for i in range(n):
        p = p + step
        rows.append({"timestamp": start_ms + i * 60_000, "open": p, "high": p + 0.05,
                     "low": p - 0.05, "close": p, "volume": vol})
    return rows


def test_defaults_present_in_ai_config():
    for k in st.DEFAULTS:
        assert k in DEFAULT_AI_CONFIG


def test_clamp_updates():
    cfg = dict(st.DEFAULTS)
    st.clamp_updates({"setup_trigger_ai_daily_cap": 999, "setup_trigger_paper": 0,
                      "setup_trigger_cooldown_min": 1}, cfg)
    assert cfg["setup_trigger_ai_daily_cap"] == 50
    assert cfg["setup_trigger_paper"] is False
    assert cfg["setup_trigger_cooldown_min"] == 5


def test_fresh_signals_only_last_bar():
    sigs = [Signal(10, "LONG", 1, 0.9, 1.1, 1.2), Signal(12, "SHORT", 1, 1.1, 0.9, 0.8)]
    assert [s.idx for s in st.fresh_signals(sigs, 12)] == [12]
    assert st.fresh_signals(sigs, 11) == []


def test_params_for_uses_edge_or_standard_variant():
    p = st.SetupTrigger.params_for("session_open", None)
    assert p["has_edge"] is False and p["vol_high"] == VARIANTS["session_open"][0]["vol_high"]
    p2 = st.SetupTrigger.params_for("session_open", {"status": "tuned",
                                                     "tuned": {"vol_high": 1.1, "vol_low": 0.9, "tp_r": 2.2}})
    assert p2["has_edge"] is True and p2["vol_high"] == 1.1
    p3 = st.SetupTrigger.params_for("session_open", {"status": "exhausted"})
    assert p3["has_edge"] is False


def test_to_decision_builds_pipeline_dict_with_clamped_levels():
    dec = st.to_decision("BTCUSDT", "session_open", "LONG", 100.0, 99.0, 101.0, 102.0,
                         60, "London-Open Breakout", "crypto")
    assert dec["setup"] == "session_open" and dec["action"] == "LONG"
    assert dec["source"] == "setup_trigger" and dec["price"] == 100.0
    assert abs(dec["sl_pct"] - 1.0) < 1e-6 and dec["tpf_pct"] >= dec["tp1_pct"] > 0
    assert st.to_decision("BTCUSDT", "x", "LONG", 100.0, 100.0, 0, 0, 60, "", "crypto") is None


def test_momentum_impulse_detects_spike_with_volume_and_consolidation():
    c = _candles(300, base=100.0, vol=10.0)
    # Impuls: letzte 15 Kerzen +1.5 % mit hohem Volumen, letzte 3 Kerzen eng
    for k, i in enumerate(range(285, 300)):
        p = 100.0 + 1.5 * min(1.0, (k + 1) / 12)
        c[i].update({"open": p - 0.05, "close": p, "high": p + 0.05, "low": p - 0.1, "volume": 40.0})
    imp = st.detect_momentum_impulse(c, "crypto", news_hit=False)
    assert imp and imp["side"] == "LONG" and imp["vol_ratio"] >= 2.0
    assert imp["sl"] < imp["entry"] < imp["tp1"] < imp["tpf"]


def test_momentum_impulse_ignores_low_volume_or_small_move():
    c = _candles(300, base=100.0, vol=10.0)
    for i in range(285, 300):
        c[i].update({"close": 101.5, "high": 101.55, "low": 101.4, "volume": 10.0})  # kein Volumen
    assert st.detect_momentum_impulse(c, "crypto") is None
    assert st.detect_momentum_impulse(_candles(300), "crypto") is None               # keine Bewegung
    assert st.detect_momentum_impulse(_candles(100), "crypto") is None               # zu wenig Daten


def test_context_text_lists_recent_hits_only():
    trig = st.SetupTrigger()
    assert trig.context_text() == ""
    trig.recent.append({"ts": datetime.now(timezone.utc).isoformat(), "symbol": "BTCUSDT",
                        "setup": "session_open", "side": "LONG", "entry": 100.0, "sl": 99.0,
                        "tpf": 102.0, "has_edge": True, "paper": True, "note": "x"})
    txt = trig.context_text()
    assert "SETUP-TRIGGER" in txt and "session_open" in txt and "Backtest-Edge" in txt


class _Engine:
    def __init__(self):
        self.config = {**DEFAULT_AI_CONFIG, "enabled": True, "collection_enabled": True}
        self.db = None
        self.key = None
        self._analyzing = False
        self.emitted = []

    async def _emit_signal(self, dec, collection=False):
        self.emitted.append((dec, collection))
        return True


class _Coll:
    async def insert_one(self, doc):
        return None


def test_handle_hit_emits_paper_trade_for_edge_setup_without_llm():
    trig = st.SetupTrigger()
    eng = _Engine()
    eng.db = type("DB", (), {"ai_decisions": _Coll()})()
    trig.setup(eng)
    hit = {"setup": "session_open", "side": "LONG", "entry": 100.0, "sl": 99.0, "tp1": 101.0,
           "tpf": 102.0, "note": "London-Open Breakout", "has_edge": True}
    res = asyncio.run(trig._handle_hit("BTCUSDT", "crypto", hit))
    assert res["paper"] is True and res["ai"] is False
    dec, collection = eng.emitted[0]
    assert collection is True and dec["setup"] == "session_open"
    # Cooldown je Symbol×Setup
    assert asyncio.run(trig._handle_hit("BTCUSDT", "crypto", hit))["status"] == "cooldown"


def test_handle_hit_without_edge_needs_paper_all():
    trig = st.SetupTrigger()
    eng = _Engine()
    eng.db = type("DB", (), {"ai_decisions": _Coll()})()
    trig.setup(eng)
    hit = {"setup": "breakout", "side": "SHORT", "entry": 100.0, "sl": 101.0, "tp1": 99.0,
           "tpf": 98.0, "note": "x", "has_edge": False}
    res = asyncio.run(trig._handle_hit("ETHUSDT", "crypto", hit))
    assert res["paper"] is False and not eng.emitted
    eng.config["setup_trigger_paper_all"] = True
    res = asyncio.run(trig._handle_hit("SOLUSDT", "crypto", hit))
    assert res["paper"] is True


# ---------------- Bewegungs-Scanner: regelbasierte Vor-Einordnung ----------------
def test_classify_caught_needs_no_llm():
    r = ms.classify(True, [], False, "LONG")
    assert r["needs_llm"] is False and r["missed"] is False and r["kind"] == "caught"


def test_classify_detector_hit_is_execution_gap():
    r = ms.classify(False, [{"setup": "session_open", "side": "LONG"}], False, "LONG")
    assert r["needs_llm"] is False and r["kind"] == "execution_gap"
    assert r["setup_match"] == "session_open" and "session_open" in r["missed_reason"]


def test_classify_detector_hit_other_direction_does_not_count():
    r = ms.classify(False, [{"setup": "breakout", "side": "SHORT"}], False, "LONG")
    assert r["kind"] == "setup_gap" and r["needs_llm"] is True


def test_classify_news_is_momentum_case():
    r = ms.classify(False, [], True, "SHORT")
    assert r["needs_llm"] is False and r["setup_match"] == "momentum_news"


def test_classify_setup_gap_needs_llm():
    r = ms.classify(False, [], False, "LONG")
    assert r["needs_llm"] is True and r["kind"] == "setup_gap"


@pytest.mark.parametrize("cls,chg,vola,expected", [
    ("crypto", 1.5, 0.2, True), ("crypto", 0.9, 0.1, False), ("forex", 0.4, 0.05, True)])
def test_is_strong_move_unchanged(cls, chg, vola, expected):
    assert ms.is_strong_move({"change_60m_pct": chg, "volatility_pct": vola}, cls, 4.0) is expected
