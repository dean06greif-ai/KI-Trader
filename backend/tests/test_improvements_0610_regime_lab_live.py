"""Regressionstests 06.10.2026: Regime-Anzahl im Autopilot, Regime-Champion je Asset,
KI-Trader-Lab Job-Steuerung, Lab->Live für Setups, Setup-Trigger-Nachholen,
Mindest-SL im Backtest (reine Funktionen, kein Backend/Netzwerk)."""
import asyncio
from types import SimpleNamespace

import numpy as np
import pytest

from services import regime_autopilot as ap
from services import regime_selection as sel
from services import setup_lab_live as sll
from services import setup_trigger as st
from services.setup_backtest import runner, simulator
from services.setup_backtest.detectors import Signal


# ---------------- 1) Regime-Anzahl bleibt fest (5 bleibt 5) ----------------
def test_requested_mode_defaults_to_ui_default_5():
    assert ap.requested_regime_mode({}) == 5
    assert ap.requested_regime_mode({"engine_config": {"regime_mode": 3}}) == 3
    assert ap.requested_regime_mode({"regime_mode": 5, "engine_config": {"regime_mode": 9}}) == 5


def test_pin_body_overrides_reference_and_seed_modes():
    body = {"engine_config": {"detector": "kombi", "regime_mode": 9},
            "seed_configs": [{"engine_config": {"detector": "ema", "regime_mode": 9}, "source": "ref"},
                             {"engine_config": {"detector": "reactive"}, "source": "alt"}]}
    out = ap.pin_body_regime_mode(body, 5)
    assert out["regime_mode"] == 5
    assert out["engine_config"]["regime_mode"] == 5
    assert all(s["engine_config"]["regime_mode"] == 5 for s in out["seed_configs"])
    assert body["engine_config"]["regime_mode"] == 9      # Eingabe unverändert (rein)
    assert ap.pin_body_regime_mode(out) == out             # idempotent


def test_warm_seeds_inherit_start_mode_and_dedupe():
    start = {"detector": "kombi", "regime_mode": 5}
    raw = [{"engine_config": {"detector": "kombi", "regime_mode": 9}},
           {"engine_config": {"detector": "kombi", "regime_mode": 3}}]
    seeds = ap.warm_seeds(raw, start, True)
    # beide Seeds werden nach dem Pin identisch mit dem Start -> kein Seed übrig
    assert seeds == []
    seeds = ap.warm_seeds([{"engine_config": {"detector": "ema", "regime_mode": 9}}], start, True)
    assert seeds[0]["engine_config"]["regime_mode"] == 5


def test_mutate_never_changes_regime_mode():
    import random
    rng = random.Random(1)
    cfg = {"detector": "kombi", "regime_mode": 5}
    for i in range(300):
        cfg = ap.mutate(cfg, rng, True, i % 40)
        assert cfg["regime_mode"] == 5


# ---------------- 2) Regime-Champion: robust statt überangepasst ----------------
def _row(aid, h, i, t, bars=2000, scope="combined", wf=None, kappa=20.0):
    return {"aid": aid, "name": aid, "scope": scope, "timeframe": "1h", "holdout_f1": h,
            "inner_f1": i, "train_f1": t, "holdout_bars": bars, "bars_per_day": 24.0,
            "holdout_kappa": kappa, "walkforward_passed": wf}


def test_evaluate_requires_oos_and_skill():
    assert sel.evaluate(_row("a", 60, 60, 60, bars=50))["eligible"] is False
    assert sel.evaluate(_row("a", 60, 60, 60, kappa=-1))["eligible"] is False
    assert sel.evaluate(_row("a", 60, 60, 60))["eligible"] is True


def test_overfit_gap_and_short_oos_are_penalised():
    fair = sel.evaluate(_row("a", 60, 60, 62))["score"]
    overfit = sel.evaluate(_row("b", 60, 60, 85))["score"]
    short = sel.evaluate(_row("c", 60, 60, 62, bars=240))["score"]
    coin = sel.evaluate(_row("d", 60, 60, 62, scope="per_coin"))["score"]
    assert overfit < fair and short < fair and coin < fair


def test_challenger_must_win_every_window_and_margin():
    inc = _row("inc", 58, 57, 60)
    lucky = _row("lucky", 70, 50, 72)          # Holdout-Glückstreffer, inneres Fenster schlechter
    res = sel.choose([inc, lucky], {"aid": "inc", "scope": "combined"})
    assert res["decision"] == "keep"
    better = _row("better", 66, 65, 67)
    res = sel.choose([inc, better], {"aid": "inc", "scope": "combined"})
    assert res["decision"] == "switch" and res["champion"]["aid"] == "better"
    tiny = _row("tiny", 58.5, 57.5, 60)        # minimal besser -> unter der Marge
    assert sel.choose([inc, tiny], {"aid": "inc", "scope": "combined"})["decision"] == "keep"


def test_margin_grows_with_candidates():
    assert sel.margin_for(1) < sel.margin_for(10) < sel.margin_for(50)


def test_champion_doc_keeps_class_stage():
    from services import structural_regime as sr
    cls_doc = {"id": "ra_cls", "release": {"stage": "shadow", "scope": "combined", "asset_classes": ["crypto"]}}
    ana = {"id": "ra_btc", "timeframe": "4h"}
    out = sr.champion_doc_of(cls_doc, {"aid": "ra_btc", "scope": "per_coin"}, ana, "BTCUSDT", "swing")
    assert out["release"]["stage"] == "shadow" and out["release"]["symbol"] == "BTCUSDT"
    assert out["_asset_champion"] is True
    assert sr.champion_doc_of(cls_doc, {"aid": "ra_cls", "scope": "combined"}, cls_doc, "BTCUSDT", "swing") is None
    assert sr.champion_doc_of({"release": {"stage": "none"}}, {"aid": "x"}, ana, "BTCUSDT", "swing") is None


# ---------------- 3) KI-Trader-Lab Job-Steuerung ----------------
def test_lab_checkpoint_soft_stop_and_cancel():
    job = {"status": "running"}
    asyncio.run(runner.checkpoint(job))
    job["stop_explore"] = True
    with pytest.raises(runner.SoftStop):
        asyncio.run(runner.checkpoint(job))
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(runner.checkpoint({"status": "running", "cancel": True}))


# ---------------- 4) Lab -> Live ----------------
def _entry(**kw):
    e = {"status": "tuned", "name": "KI-Rev.17", "min_trades": {"oos": 10},
         "oos": {"trades": 278, "wins": 162, "pnl": 34.45, "winrate": 58,
                 "windows": [{"pnl": 14.2}, {"pnl": 13.6}, {"pnl": 6.7}], "windows_pos": 3},
         "is": {"trades": 537, "pnl": 51.07}}
    e.update(kw)
    return e


def test_lab_validated_strict():
    assert sll.lab_validated(_entry())[0] is True
    assert sll.lab_validated(_entry(status="exhausted"))[0] is False
    assert sll.lab_validated(_entry(oos={"trades": 12, "pnl": 1, "windows": [], "windows_pos": 0}))[0] is False
    bad_wf = _entry(oos={"trades": 100, "pnl": 1, "windows": [{"pnl": 1}, {"pnl": -1}, {"pnl": -1}],
                         "windows_pos": 1})
    assert sll.lab_validated(bad_wf)[0] is False
    assert sll.lab_validated(_entry(**{"is": {"pnl": -0.5}}))[0] is False
    assert sll.lab_validated(_entry(stale=1))[0] is False
    noise = _entry(oos={"trades": 128, "pnl": 0.44, "windows": [{"pnl": 1}, {"pnl": 1}, {"pnl": -1}],
                        "windows_pos": 2})
    assert sll.lab_validated(noise)[0] is False


def test_event_auto_live_and_opt_out():
    assert sll.event_live_allowed({}) is True
    assert sll.event_live_allowed({"live_opt_out": True}) is False
    assert sll.event_live_allowed({"live_enabled": True, "live_opt_out": True}) is True


def test_override_needs_optin_and_revision_only_bypass(monkeypatch):
    monkeypatch.setattr(sll, "_state", {"optin": {"crypto": ["session_open"]}, "auto_all": False})
    monkeypatch.setattr(sll, "_cache", {"ts": 9e18, "classes": {"crypto": {"session_open": _entry()}}})
    assert sll.override_for("session_open", "crypto")[0] is True
    assert sll.override_for("session_open", "forex") is None
    assert sll.override_for("breakout", "crypto") is None
    rev = {"reason": "Revision v1: Validierung neu gestartet"}
    weak = {"reason": "live 8 Trades, Winrate 20%"}
    assert sll.block_bypassed("session_open", "crypto", rev) is True
    assert sll.block_bypassed("session_open", "crypto", weak) is False
    assert sll.block_bypassed("breakout", "crypto", rev) is False


def test_fomc_override_auto_when_validated(monkeypatch):
    from services import fomc_event
    monkeypatch.setattr(fomc_event, "_state", {"live_enabled": False, "validation": {"crypto": {"validated": True}}})
    assert fomc_event.live_override("crypto")[0] is True
    monkeypatch.setattr(fomc_event, "_state", {"live_enabled": False, "live_opt_out": True,
                                               "validation": {"crypto": {"validated": True}}})
    assert fomc_event.live_override("crypto") is None


# ---------------- 5) Setup-Trigger holt verpasste Kerzen nach ----------------
def test_catchup_signals_window():
    ts = np.array([i * 300_000 for i in range(10)])
    sigs = [SimpleNamespace(idx=i) for i in (5, 7, 8, 9)]
    assert [s.idx for s in st.catchup_signals(sigs, ts, 9, None)] == [9]
    assert [s.idx for s in st.catchup_signals(sigs, ts, 9, int(ts[6]))] == [7, 8, 9]
    assert [s.idx for s in st.catchup_signals(sigs, ts, 9, int(ts[8]))] == [9]
    assert [s.idx for s in st.catchup_signals(sigs, ts, 9, int(ts[2]))] == [7, 8, 9]  # max 3 Kerzen


def test_late_entry_guard():
    assert st.late_entry_ok("LONG", 100.0, 99.0, 100.3) is True
    assert st.late_entry_ok("LONG", 100.0, 99.0, 100.8) is False   # > 0,5 R davongelaufen
    assert st.late_entry_ok("LONG", 100.0, 99.0, 98.9) is False    # hinter dem SL
    assert st.late_entry_ok("SHORT", 100.0, 101.0, 99.7) is True
    assert st.late_entry_ok("SHORT", 100.0, 101.0, 101.2) is False


# ---------------- 6) Backtest simuliert den Live-Mindest-SL ----------------
def test_simulator_widens_tight_sl_like_live():
    simulator.set_min_sl_config(None)
    sig = Signal(10, "LONG", 100.0, 99.75, 100.25, 100.5, "test")      # 0,25 % SL Krypto
    out = simulator.clamp_signal("crypto", sig)
    sl_pct = (100.0 - out.sl) / 100.0 * 100
    assert sl_pct == pytest.approx(0.42, abs=1e-6)                     # 0,4 % x 1,05 Puffer
    assert (out.tpf - 100.0) / (100.0 - out.sl) == pytest.approx(2.0, rel=1e-6)   # CRV bleibt
    simulator.set_min_sl_config({"min_sl_rule_enabled": False})
    assert simulator.clamp_signal("crypto", sig).sl == pytest.approx(99.75)
    simulator.set_min_sl_config(None)
