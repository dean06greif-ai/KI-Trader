"""Regressionstests: Retention, Aktivitäts-Wächter, Bewegungs-Scanner, Setup-Optimierer.

Nur pure Logik (keine DB/LLM) – schnell und deterministisch.
"""
from services import retention
from services import activity_guard as ag
from services import ai_move_scanner as ms
from services.setup_backtest import runner, detectors


# ---------------------------------------------------------------- Retention
def test_retention_policy_covers_top_collections():
    colls = {p["coll"] for p in retention.merged_policy({})}
    for c in ("ai_chat_archive", "ai_decisions", "ai_market_snapshots",
              "backtest_trades", "optimizer_trades", "ml_gate_models",
              "ai_move_events"):
        assert c in colls, f"{c} fehlt in der Retention-Policy"


def test_retention_policy_never_touches_critical_collections():
    colls = {p["coll"] for p in retention.merged_policy({})}
    for c in ("auto_trades", "ai_playbook", "settings", "ai_knowledge",
              "setup_backtest_state", "setup_backtest_trades", "candle_cache"):
        assert c not in colls, f"{c} darf NIE per Retention gelöscht werden"


def test_retention_override_days_and_floors():
    pol = {p["coll"]: p for p in retention.merged_policy(
        {"ai_decisions": {"days": 10}, "ai_market_snapshots": {"days": 5},
         "ml_gate_models": {"keep_last": 1}})}
    assert pol["ai_decisions"]["days"] == 10
    # Harte Untergrenzen greifen (ML-Lookback bzw. MIN_KEEP)
    assert pol["ai_market_snapshots"]["days"] == retention.MIN_DAYS["ai_market_snapshots"]
    assert pol["ml_gate_models"]["keep_last"] == retention.MIN_KEEP
    # Nicht überschriebene Regeln bleiben Default
    default_chat = next(p for p in retention.DEFAULT_POLICY if p["coll"] == "ai_chat_archive")["days"]
    assert pol["ai_chat_archive"]["days"] == default_chat


def test_retention_override_invalid_values_ignored():
    pol = {p["coll"]: p for p in retention.merged_policy({"ai_decisions": {"days": "quatsch"}})}
    default_dec = next(p for p in retention.DEFAULT_POLICY if p["coll"] == "ai_decisions")["days"]
    assert pol["ai_decisions"]["days"] == default_dec


# ------------------------------------------------------- Aktivitäts-Wächter
def test_guard_loosen_respects_floors():
    cfg = dict(ag.DEFAULTS)
    cur = {"min_confidence": 56, "collection_min_confidence": 50,
           "cooldown_min": 12, "collection_cooldown_min": 10}
    changes = ag.loosen_values(cur, cfg, tune_conf_min=55)
    assert changes.get("min_confidence", 55) >= 55
    assert changes.get("cooldown_min", 10) >= ag.COOLDOWN_FLOOR
    # Bereits am Floor -> kein Change-Eintrag
    assert "collection_min_confidence" not in changes
    assert "collection_cooldown_min" not in changes


def test_guard_tighten_never_exceeds_baseline():
    cfg = dict(ag.DEFAULTS)
    baseline = {"min_confidence": 65, "collection_min_confidence": 60,
                "cooldown_min": 45, "collection_cooldown_min": 30}
    cur = {"min_confidence": 64, "collection_min_confidence": 59,
           "cooldown_min": 44, "collection_cooldown_min": 29}
    changes = ag.tighten_values(cur, baseline, cfg)
    for k, v in changes.items():
        assert v <= baseline[k], f"{k} über Baseline gestrafft"


def test_guard_full_cycle_returns_to_baseline():
    cfg = dict(ag.DEFAULTS)
    baseline = {"min_confidence": 65, "collection_min_confidence": 60,
                "cooldown_min": 45, "collection_cooldown_min": 30}
    cur = dict(baseline)
    for _ in range(cfg["max_steps"]):
        cur.update(ag.loosen_values(cur, cfg, tune_conf_min=55))
    assert cur["min_confidence"] < baseline["min_confidence"]
    for _ in range(cfg["max_steps"] + 2):
        cur.update(ag.tighten_values(cur, baseline, cfg))
    assert cur == baseline


def test_guard_normalize_clamps_config():
    cfg = ag.normalize({"target_trades_per_day": 999, "window_hours": 1,
                        "max_steps": 0, "enabled": False})
    assert cfg["target_trades_per_day"] == 50
    assert cfg["window_hours"] == 12
    assert cfg["max_steps"] == 1
    assert cfg["enabled"] is False


# ------------------------------------------------------- Bewegungs-Scanner
def test_move_detection_thresholds_per_class():
    vm = ms.DEFAULTS["vol_mult"]
    assert ms.is_strong_move({"change_60m_pct": 1.5, "volatility_pct": 0.1}, "crypto", vm)
    assert not ms.is_strong_move({"change_60m_pct": 0.8, "volatility_pct": 0.1}, "crypto", vm)
    assert ms.is_strong_move({"change_60m_pct": -0.5, "volatility_pct": 0.05}, "forex", vm)
    assert not ms.is_strong_move({"change_60m_pct": -0.2, "volatility_pct": 0.05}, "forex", vm)


def test_move_detection_scales_with_volatility():
    vm = ms.DEFAULTS["vol_mult"]
    # Hohe Grundvola hebt die Schwelle: 1.5% bei 0.6% 60m-Vola => kein Alarm
    assert not ms.is_strong_move({"change_60m_pct": 1.5, "volatility_pct": 0.6}, "crypto", vm)
    assert ms.is_strong_move({"change_60m_pct": 3.0, "volatility_pct": 0.6}, "crypto", vm)


def test_move_detection_skips_closed_and_empty():
    vm = ms.DEFAULTS["vol_mult"]
    assert not ms.is_strong_move({"change_60m_pct": 5.0, "market_closed": True}, "indices", vm)
    assert not ms.is_strong_move({}, "crypto", vm)


def test_move_scanner_normalize_clamps():
    cfg = ms.normalize({"vol_mult": 100, "cooldown_min": 1, "max_llm_per_day": 0})
    assert cfg["vol_mult"] == 10.0
    assert cfg["cooldown_min"] == 30
    assert cfg["max_llm_per_day"] == 1


# --------------------------------------------------------- Setup-Optimierer
def test_normalize_mode_accepts_optimize():
    assert runner.normalize_mode("optimize") == "optimize"
    assert runner.normalize_mode("quatsch") == "single"


def _res(passed, trades, winrate, pnl):
    return {"passed": passed, "oos": {"trades": trades, "winrate": winrate, "pnl": pnl}}


def test_optimize_better_requires_pnl_hold():
    base = _res(True, 10, 50, 100.0)
    assert runner.optimize_better(base, _res(True, 14, 50, 100.0))        # mehr Trades
    assert runner.optimize_better(base, _res(True, 10, 55, 120.0))       # gleiche Trades, mehr WR
    assert not runner.optimize_better(base, _res(True, 14, 60, 80.0))    # PnL-Verlust -> nein
    assert not runner.optimize_better(base, _res(False, 20, 70, 200.0))  # Edge-Kriterium verfehlt
    assert not runner.optimize_better(base, _res(True, 10, 50, 100.0))   # keine Verbesserung


def test_optimize_score_orders_by_trades_first():
    a, b = _res(True, 12, 50, 50.0), _res(True, 10, 60, 500.0)
    assert runner.optimize_score(a) > runner.optimize_score(b)


def test_optimize_candidates_bounded_named_and_backtestable():
    for sid in detectors.VARIANTS:
        base = dict(detectors.VARIANTS[sid][0])
        cands = runner.optimize_candidates(sid, base)
        assert 0 < len(cands) <= 16, sid
        for c in cands:
            assert isinstance(c, dict) and c.get("name"), sid


def test_optimize_candidates_within_allowed_ranges():
    from services.setup_backtest import revise
    for sid in list(detectors.VARIANTS)[:4]:
        base = dict(detectors.VARIANTS[sid][0])
        ranges = revise.param_ranges(sid)
        for c in runner.optimize_candidates(sid, base):
            for k, v in c.items():
                if k in ranges and isinstance(v, (int, float)) and not isinstance(v, bool):
                    lo, hi = ranges[k]
                    assert lo <= v <= hi, f"{sid}.{k}={v} außerhalb [{lo},{hi}]"
