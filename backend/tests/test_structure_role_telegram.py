"""Struktur-Stop, KI-Prüfer-Modus, Telegram-Nachtruhe/Presets/Zähler – reine Regressionstests."""
from datetime import datetime, timezone

from services import structure_stop as ss
from services import ai_role
from services import notifications as nf


def _htf(lows, highs):
    return [{"low": lo, "high": hi} for lo, hi in zip(lows, highs)]


def test_structure_stop_widens_to_swing_and_scales_tps():
    htf = _htf([98.5, 98.8, 99.0], [101, 101.5, 101.2])
    lv, why = ss.compute("LONG", 100.0, 99.5, 100.5, 101.0, htf, {})
    sl, tp1, tpf = lv
    assert round((100 - sl), 2) == 1.6          # Swing 98.5 − 0,1 % Puffer
    assert round(tp1 - 100, 2) == 1.6 and round(tpf - 100, 2) == 3.2   # CRV 1/2 bleibt
    assert "Struktur-Stop 15m" in why


def test_structure_stop_min_and_max():
    lv, _ = ss.compute("SHORT", 100.0, 100.3, 99.7, 99.4, _htf([99], [100.2]), {})
    assert round(lv[0], 4) == 101.0             # auf Minimum 1 % erweitert
    lv, why = ss.compute("LONG", 100.0, 99.5, 100.5, 101, _htf([95], [101]), {})
    assert lv is None and why.startswith("Struktur-Stop")
    lv, why = ss.compute("LONG", 100.0, 99.5, 100.5, 101, [], {})
    assert lv is None and not why.startswith("Struktur-Stop")   # fail-open, kein Block


def test_structure_clamp_updates():
    cfg = {}
    ss.clamp_updates({"structure_stop_enabled": 1, "structure_tf": "4h", "structure_min_pct": 9,
                      "structure_max_pct": 0.1, "structure_lookback": "x"}, cfg)
    assert cfg["structure_stop_enabled"] is True and "structure_tf" not in cfg
    assert cfg["structure_min_pct"] == 5.0 and cfg["structure_max_pct"] == 5.0


def test_ai_role_pruefer_requires_broker():
    assert ai_role.is_pruefer({"ai_role": "pruefer"}) is True            # Broker Default an
    assert ai_role.is_pruefer({"ai_role": "pruefer", "signal_broker_enabled": False}) is False
    assert ai_role.is_pruefer({}) is False
    assert ai_role.veto_free({"signal_source": "ki_frei"}, {"ai_role": "pruefer"}) is True
    assert ai_role.veto_free({"signal_source": "ki_geprueft"}, {"ai_role": "pruefer"}) is False
    assert ai_role.veto_free({"signal_source": "ki_frei"}, {"ai_role": "frei"}) is False
    cfg = {}
    ai_role.clamp_updates({"ai_role": "boss"}, cfg)
    assert cfg == {}


def test_new_defaults_registered():
    from services.ai_engine import DEFAULT_AI_CONFIG
    assert DEFAULT_AI_CONFIG["ai_role"] == "frei"
    assert DEFAULT_AI_CONFIG["structure_stop_enabled"] is False


def test_quiet_hours_window():
    q = {"enabled": True, "from_h": 23, "to_h": 7}
    assert nf.quiet_active(q, 23) and nf.quiet_active(q, 3) and not nf.quiet_active(q, 7)
    assert not nf.quiet_active({**q, "enabled": False}, 3)
    assert nf.quiet_active({"enabled": True, "from_h": 12, "to_h": 14}, 13)
    assert nf.allowed_in_quiet("kill_switch", None)
    assert nf.allowed_in_quiet("trade_opened", "trade_opened_live")
    assert not nf.allowed_in_quiet("signals", "signals_ai")


def test_presets():
    imp = nf.preset_config("wichtig")
    assert imp["kill_switch"] and imp["trade_opened_live"] and not imp["signals"] and not imp["backtest_done"]
    assert all(nf.preset_config("alles").values())
    assert nf.preset_config("standard") == nf.DEFAULT_CONFIG
    assert set(imp) == set(nf.DEFAULT_CONFIG)


def test_sum_counts_last_days():
    today = datetime(2026, 11, 3, tzinfo=timezone.utc)
    doc = {"2026-11-03": {"signals": 5}, "2026-10-28": {"signals": 2, "watchdog": 1}, "2026-10-01": {"signals": 99}}
    assert nf.sum_counts(doc, 7, today) == {"signals": 7, "watchdog": 1}
