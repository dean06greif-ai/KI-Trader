"""06/2026 E3–E6: Mehrstufige Regime, Nutzen-Note, Assistent, Schutzfilter."""
import asyncio

import numpy as np

from services import regime_assistant as ra
from services import regime_hierarchy as rh
from services import regime_usefulness as ru
from services import research_mode

H8 = 8 * 3_600_000


def _candles(n=900, seed=1, tf_ms=H8):
    """Synthetische Kerzen: Trend auf, ab, seitwärts im Wechsel (je 150 Kerzen)."""
    rng = np.random.default_rng(seed)
    drift = np.concatenate([np.full(150, d) for d in [0.004, -0.004, 0.0] * 3])[:n]
    lr = drift + rng.normal(0, 0.008, n)
    close = 100 * np.exp(np.cumsum(lr))
    out = []
    for i, c in enumerate(close):
        o = close[i - 1] if i else c
        out.append({"timestamp": 1_700_000_000_000 + i * tf_ms, "open": float(o), "close": float(c),
                    "high": float(max(o, c) * 1.002), "low": float(min(o, c) * 0.998), "volume": 1000.0})
    return out


# ---------------- Matrix ----------------
def test_combine_matrix():
    c = rh.combine("up", "up")
    assert c["allow_long"] and not c["allow_short"] and c["size_factor"] == 1.0
    k = rh.combine("up", "down")
    assert not k["allow_long"] and k["allow_short"] and k["size_factor"] == 0.5
    s = rh.combine("sideways", "sideways")
    assert s["allow_long"] and s["allow_short"]


def test_combine_unknown_fail_open_and_uncertain():
    u = rh.combine(None, "up")
    assert u["state"] == "unknown" and u["allow_long"] and u["allow_short"]
    c = rh.combine("up", "up", primary_prob=0.4)
    assert c["size_factor"] == 0.5 and c["notes"]


def test_leader_against_halves_size():
    c = rh.combine("up", "up", leader_macro="down", is_leader=False)
    assert c["size_factor"] == 0.5
    assert rh.combine("up", "up", leader_macro="down", is_leader=True)["size_factor"] == 1.0


def test_side_allowed():
    res = {"combined": rh.combine("down", "down")}
    assert rh.side_allowed(res, "SHORT") and not rh.side_allowed(res, "LONG")
    assert rh.side_allowed(None, "LONG") and rh.side_allowed(res, None)


def test_align_macro_uses_only_closed_bars():
    day = 86_400_000
    primary = [{"timestamp": i * H8} for i in range(9)]       # 3 Tage à 3 Kerzen
    macro = [{"timestamp": i * day} for i in range(3)]
    out = rh.align_macro(primary, macro, np.array([1, -1, 0]), H8, day)
    # Tag 0 ist erst mit der 3. Kerze (Ende 24h) abgeschlossen
    assert list(out[:2]) == [-9, -9] and out[2] == 1 and out[5] == -1 and out[8] == 0


def test_combined_series():
    m = np.array([1, 1, -1, -9])
    p = np.array([1, -1, -1, 1])
    assert list(rh.combined_series(m, p)) == [1, -1, -1, -9]


def test_classify_layer_detects_trends():
    cs = _candles()
    lay = rh.classify_layer(cs, rh.PRIMARY_JUMP, "8h")
    d = lay["series"]
    assert np.mean(d[120:150] == 1) > 0.6      # Ende 1. Aufwärtsphase
    assert np.mean(d[250:300] == -1) > 0.6     # Ende 1. Abwärtsphase
    assert lay["direction"] in ("up", "down", "sideways")


def test_analyze_candles_crypto():
    res = rh.analyze_candles("ETHUSDT", "crypto", _candles(), leader_macro="down")
    pub = rh.public(res)
    assert pub["recipe"]["primary_tf"] == "8h" and pub["recipe"]["macro_tf"] == "1d"
    assert "_primary_series" not in pub and pub["combined"]["state"] in ("ok", "unknown")
    assert len(res["_macro_series"]) == len(res["_primary_candles"])


# ---------------- Nutzen-Note ----------------
def test_usefulness_good_vs_random():
    cs = _candles()
    close = [c["close"] for c in cs]
    lay = rh.classify_layer(cs, rh.PRIMARY_JUMP, "8h")
    good = ru.evaluate(close, lay["series"], 3.0, start=60)
    rng = np.random.default_rng(3)
    rnd = ru.evaluate(close, rng.choice([-1, 0, 1], len(close)), 3.0, start=60)
    assert good["score"] is not None and rnd["score"] is not None
    assert good["score"] > rnd["score"] + 15
    assert rnd["stability"]["pingpong_pct"] > 20


def test_usefulness_few_phases_capped_and_no_data():
    close = list(np.linspace(100, 200, 300))
    d = [1] * 300
    r = ru.evaluate(close, d, 3.0)
    assert r["few_phases"] is True or r["score"] is None
    assert ru.evaluate([1.0] * 10, [None] * 10, 3.0)["grade"] == "zu wenig Daten"


# ---------------- Assistent ----------------
def _row(c, p, m):
    return {"symbol": "X", "eval": {"variants": {"combined": {"score": c}, "primary": {"score": p},
                                                 "macro": {"score": m}}}}


def test_summarize_prefers_combined_within_margin():
    s = ra.summarize([_row(62, 64, 40), _row(60, 63, 42)])
    assert s["best"] == "combined" and s["light"] == "grün"
    s2 = ra.summarize([_row(40, 70, 30)])
    assert s2["best"] == "primary"
    assert ra.summarize([{"symbol": "X", "error": "x"}])["light"] == "grau"


def test_evaluate_symbol_variants():
    res = rh.analyze_candles("BTCUSDT", "crypto", _candles())
    ev = ra.evaluate_symbol(res, 3.0)
    assert set(ev["variants"]) == {"combined", "primary", "macro"}
    assert ev["days_evaluated"] > 0


def test_fallback_default_off():
    assert ra.config()["fallback_enabled"] is False
    assert ra.fallback_allowed("crypto") is False
    assert asyncio.run(ra.structural_fallback("BTCUSDT")) is None


def test_research_mode_defaults_and_body_override():
    assert research_mode.evidence_mode({}) == "robust"
    assert research_mode.evidence_mode({"evidence_mode": "classic"}) == "classic"
    assert research_mode.regime_val_mode({"regime_val_mode": "bogus"}) == "time"
    assert research_mode.clean({"evidence_mode": "x", "regime_val_mode": "phase"}) == {"regime_val_mode": "phase"}


def test_structural_prompt_line_assistant():
    from services import structural_regime
    ctx = {"state": "ok", "direction": "up", "source": "regime_assistant", "timeframe": "8h",
           "since_days": 4.0, "macro": {"direction": "up", "tf": "1d"}, "label": "Starker Aufwärtstrend",
           "size_factor": 1.0}
    line = structural_regime.prompt_line(ctx)
    assert "Regime-Assistent" in line and "BULLE" in line and "1d" in line


# ---------------- Schutzfilter in der dynamischen Strategie ----------------
def _strategy(settings):
    from strategies.dynamic_regime_strategy import DynamicRegimeStrategy
    return DynamicRegimeStrategy({"id": "dyn_t", "name": "T", "settings": settings, "model": {}})


def test_guards_off_by_default():
    res, info = {"signal_type": "LONG"}, {}
    _strategy({})._apply_guards(res, info, "BTCUSDT", {"confidence": 10})
    assert res["signal_type"] == "LONG" and "guard" not in info


def test_transition_guard_blocks_uncertain():
    res, info = {"signal_type": "LONG"}, {}
    _strategy({"transition_guard_min_conf": 60})._apply_guards(res, info, "BTCUSDT", {"confidence": 40})
    assert res["signal_type"] is None and "Übergangsschutz" in info["guard"]


def test_macro_filter_blocks_opposite_side():
    import time
    rh._cache["TESTUSDT"] = {"_at": time.time(), "res": {
        "combined": rh.combine("down", "down"), "macro": {"direction": "down"},
        "primary": {"direction": "down"}, "checked_at": time.time()}}
    st = _strategy({"macro_filter_enabled": True})
    res, info = {"signal_type": "LONG"}, {}
    st._apply_guards(res, info, "TESTUSDT", {"confidence": 90})
    assert res["signal_type"] is None and "Makro-Filter" in info["guard"]
    res2, info2 = {"signal_type": "SHORT"}, {}
    st._apply_guards(res2, info2, "TESTUSDT", {"confidence": 90})
    assert res2["signal_type"] == "SHORT" and info2["hierarchy"]["macro"] == "down"
    rh._cache.pop("TESTUSDT", None)


def test_macro_filter_fail_open_without_data():
    res, info = {"signal_type": "LONG"}, {}
    _strategy({"macro_filter_enabled": True})._apply_guards(res, info, "NODATAUSDT", {"confidence": 90})
    assert res["signal_type"] == "LONG"
