"""Regressionstests Runner-Policy (News-Runner, Rausch-/Liq-sicheres Trailing,
Datensammel-Gewinnschutz) und Nachanalyse -> Setup-Version."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services import runner_policy as rp  # noqa: E402
from services import setup_lifecycle as lc  # noqa: E402
from services import trade_postmortem as pm  # noqa: E402


def _c(prices, spread=0.5):
    return [{"timestamp": i, "open": p, "high": p + spread, "low": p - spread, "close": p}
            for i, p in enumerate(prices)]


# ---------------- runner_allowed ----------------
def test_runner_allowed_swing_always():
    assert rp.runner_allowed({"runner": True, "horizon": "swing"}, {})
    assert not rp.runner_allowed({"runner": False, "horizon": "swing"}, {})


def test_runner_allowed_scalp_news_only_default():
    cfg = {}
    assert rp.runner_allowed({"runner": True, "horizon": "scalp", "news_impact": "positive"}, cfg)
    assert not rp.runner_allowed({"runner": True, "horizon": "scalp", "news_impact": "neutral"}, cfg)
    assert not rp.runner_allowed({"runner": True, "horizon": "scalp"}, cfg)
    # Schalter aus -> nie
    assert not rp.runner_allowed({"runner": True, "horizon": "scalp", "news_impact": "negative"},
                                 {"runner_scalp_enabled": False})
    # news_only aus -> jeder Scalp mit runner
    assert rp.runner_allowed({"runner": True, "horizon": "scalp"}, {"runner_scalp_news_only": False})


def test_scalp_runner_tpf_far_but_capped():
    assert rp.scalp_runner_tpf(0.008, 0.02) == 0.064          # 8R
    assert rp.scalp_runner_tpf(0.03, 0.05) == 0.15            # Deckel 15 %
    assert rp.scalp_runner_tpf(0.001, 0.05) == 0.05           # nie enger als vorhanden


# ---------------- noise_safe_sl ----------------
def test_noise_safe_sl_long_keeps_min_distance():
    # Kurs 100, ATR 0.4 -> Mindestabstand 0.4; Vorschlag 99.9 zu eng -> 99.6
    assert rp.noise_safe_sl("LONG", 100.0, 99.9, 99.0, 0.4) == 99.6
    # Vorschlag weit genug -> unverändert
    assert rp.noise_safe_sl("LONG", 100.0, 99.2, 99.0, 0.4) == 99.2
    # sicherer SL wäre keine Verbesserung -> None
    assert rp.noise_safe_sl("LONG", 100.0, 99.9, 99.7, 0.4) is None
    # ohne ATR gilt min_pct (0.1 % -> 0.1)
    assert rp.noise_safe_sl("LONG", 100.0, 99.95, 99.0, 0.0) == 99.9


def test_noise_safe_sl_short_mirror():
    assert rp.noise_safe_sl("SHORT", 100.0, 100.1, 101.0, 0.4) == 100.4
    assert rp.noise_safe_sl("SHORT", 100.0, 100.1, 100.3, 0.4) is None


def test_liq_safe_sl_after_margin_release():
    # Hebel 100 -> Liq bei ~99.5 (LONG, Entry 100); SL 99.2 läge HINTER der Liq
    fixed = rp.liq_safe_sl("LONG", 100.0, 100.0, 101.5, 99.2, buffer_pct=0.3)
    assert fixed is not None and fixed > 99.5
    # SL bereits sicher vor der Liq -> unverändert
    assert rp.liq_safe_sl("LONG", 100.0, 100.0, 101.5, 100.5) == 100.5
    # Kurs zu nah an der nötigen SL-Marke -> nicht ausführbar
    assert rp.liq_safe_sl("LONG", 100.0, 100.0, 99.85, 99.2) is None


def test_trail_candidate_combines_noise_and_liq():
    candles = _c([100 + i * 0.1 for i in range(30)], spread=0.2)   # ATR ~0.4
    t = {"side": "LONG", "entry": 100.0, "leverage": 100.0, "profit_margin_released": True,
         "profit_secure_sl_liq_buffer_pct": 0.3}
    price = 103.0
    # Vorschlag 102.9 (zu eng) -> Rausch-Abstand 102.6 -> liegt vor der Liq (99.5) -> ok
    assert rp.trail_candidate(t, candles, price, 102.9, 101.0) == 102.6
    # Vorschlag ohne Verbesserung -> None
    assert rp.trail_candidate(t, candles, price, 100.5, 101.0) is None
    # Swing: größerer Mindestabstand (0.3 %)
    ts = {"side": "LONG", "entry": 100.0, "ai_horizon": "swing"}
    assert rp.trail_candidate(ts, [], price, 102.95, 101.0) == round(103.0 * 0.997, 8)


def test_atr_basic():
    assert rp.atr(_c([100, 100, 100], spread=0.5)) == 1.0
    assert rp.atr([]) == 0.0


# ---------------- Nachanalyse -> Version ----------------
def _setup(best, kind, step, robust=True, bt=True):
    return {"setup": "breakout", "best_variant": best, "backtest_confirmed": bt,
            "variants": {best: {"kind": kind, "factor": 1.5, "step": step, "robust": robust,
                                "median_delta_r": 0.4, "consistency": 0.7, "n": 10}}}


def test_proposals_from_setups():
    p = pm.proposals_from_setups([_setup("tp_x1.5", "tp", 1.2)])
    assert p["breakout"]["param"] == "tp_ratio" and p["breakout"]["factor"] == 1.2
    assert "TP ×1.2" in p["breakout"]["note"]
    p2 = pm.proposals_from_setups([_setup("sl_x0.75", "sl", 0.8, bt=False)])
    assert p2["breakout"]["param"] == "sl_pct" and "ohne Backtest" in p2["breakout"]["note"]
    assert pm.proposals_from_setups([_setup("runner", "runner", None)]) == {}
    assert pm.proposals_from_setups([_setup("tp_x1.5", "tp", 1.2, robust=False)]) == {}
    assert pm.proposals_from_setups([{"setup": "x", "best_variant": None, "variants": {}}]) == {}


def _trades(n, since="2026-05-01T00:00:00+00:00", pnl=1.0):
    return [{"setup": "breakout", "entry": 100, "initial_sl": 99, "sl": 99, "tpf": 102,
             "leverage": 10, "timeframe": "5m", "realized_pnl": pnl,
             "opened_at": f"2026-05-{(i % 27) + 2:02d}T00:00:00+00:00"} for i in range(n)]


def test_evolve_versions_applies_hint_once_and_clamped():
    entry = {"versions": [{"v": 1, "since": "2026-05-01T00:00:00+00:00",
                           "params": {"sl_pct": 1.0, "tp_ratio": 2.0, "timeframe": "5m"},
                           "note": "initiales Profil"}], "active": 1}
    trades = _trades(6)
    hint = {"param": "tp_ratio", "factor": 1.5, "note": "TP ×1.5"}
    new, ev = lc.evolve_versions(entry, trades, now_iso="2026-06-01T00:00:00+00:00", hint=hint)
    assert ev and "Nachanalyse" in ev
    assert new["versions"][-1]["params"]["tp_ratio"] == 2.4          # ±20 % gedeckelt
    assert new["versions"][-1]["params"]["sl_pct"] == 1.0            # nur EIN Parameter
    assert new["versions"][-1].get("postmortem") is True
    # zweiter Durchlauf: gleicher Hint wird nicht erneut angewandt
    new2, ev2 = lc.evolve_versions(new, trades, now_iso="2026-06-02T00:00:00+00:00", hint=hint)
    assert ev2 is None and len(new2["versions"]) == len(new["versions"])


def test_evolve_versions_hint_needs_enough_trades():
    entry = {"versions": [{"v": 1, "since": "2026-05-01T00:00:00+00:00",
                           "params": {"sl_pct": 1.0, "tp_ratio": 2.0}, "note": "initiales Profil"}],
             "active": 1}
    new, ev = lc.evolve_versions(entry, _trades(3), now_iso="2026-06-01T00:00:00+00:00",
                                 hint={"param": "sl_pct", "factor": 0.8})
    assert ev is None and len(new["versions"]) == 1
    # ohne Hint unverändertes Verhalten
    new3, ev3 = lc.evolve_versions(entry, _trades(3), now_iso="2026-06-01T00:00:00+00:00")
    assert ev3 is None
