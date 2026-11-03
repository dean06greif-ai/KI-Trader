"""Regressionstests: SL/TP-Grenzen je Anlageklasse (setup_asset_class.clamp_levels)
und regelbasierte Fehlerdiagnose (setup_diagnosis.diagnose). Rein, ohne Backend."""
import sys

sys.path.insert(0, "/app/backend")

from services import setup_asset_class as ac  # noqa: E402
from services import setup_diagnosis as dg  # noqa: E402


def test_clamp_forex_tightens_sl_and_keeps_crv():
    r = ac.clamp_levels(ac.FOREX, 0.6, 0.9, 1.8)
    assert r["sl_pct"] == 0.5 and r["levels_clamp"] and "Forex" in r["levels_clamp"]
    # CRV bleibt: 0.9/0.6 = 1.5 -> tp1 = 0.75, tpf = 1.5 (== tpf_max, kein zweiter Clamp)
    assert r["tp1_pct"] == 0.75 and r["tpf_pct"] == 1.5


def test_clamp_no_change_inside_limits():
    r = ac.clamp_levels(ac.CRYPTO, 0.6, 0.9, 1.8)
    assert r == {"sl_pct": 0.6, "tp1_pct": 0.9, "tpf_pct": 1.8, "levels_clamp": None}


def test_clamp_min_sl_and_tpf_cap_and_swing():
    r = ac.clamp_levels(ac.CRYPTO, 0.05, 0.1, 0.2)       # zu enger SL -> 0.2, TPs ×4
    assert r["sl_pct"] == 0.2 and r["tp1_pct"] == 0.4 and r["tpf_pct"] == 0.8
    r = ac.clamp_levels(ac.INDICES, 1.0, 3.0, 9.0)       # tpf über Klassen-Deckel
    assert r["sl_pct"] == 1.0 and r["tpf_pct"] == 4.0 and r["tp1_pct"] == 3.0 and "TPf" in r["levels_clamp"]
    r = ac.clamp_levels(ac.INDICES, 2.5, 3.0, 6.0, is_swing=True)   # Swing: Obergrenzen ×2
    assert r["sl_pct"] == 2.5 and r["levels_clamp"] is None
    assert ac.clamp_levels("unbekannt", 0.6, 0.9, 1.8)["levels_clamp"] is None  # Fallback Krypto
    assert ac.clamp_levels(ac.FOREX, 0, 0, 0)["sl_pct"] == 0.05                   # kaputte Werte


def _t(pnl, entry=100.0, sl=99.0, exit_=None, side="LONG", opened="2026-09-01T08:00:00+00:00",
       closed="2026-09-01T09:00:00+00:00", tf="5m", peak=None, trough=None):
    return {"realized_pnl": pnl, "entry": entry, "initial_sl": sl, "exit_price": exit_ if exit_ is not None else (sl if pnl < 0 else 102),
            "side": side, "opened_at": opened, "closed_at": closed, "timeframe": tf,
            "peak_price": peak, "trough_price": trough}


def test_diagnose_needs_data_and_losers():
    assert dg.diagnose([]) == []
    assert dg.diagnose([_t(1), _t(1), _t(1)]) == []                 # < MIN_TRADES
    assert dg.diagnose([_t(1), _t(2), _t(1), _t(3)]) == []           # keine Verlierer


def test_diagnose_sl_hits_and_quick_losses():
    rows = [_t(1), _t(1)] + [_t(-1, closed="2026-09-01T08:02:00+00:00") for _ in range(4)]
    out = dg.diagnose(rows)
    assert any("SL-Hit" in l for l in out)
    assert any("<5 Min" in l for l in out)
    assert len(out) <= 5


def test_diagnose_session_tf_side_and_mfe():
    # Verluste alle in Asia-Session (02:00 UTC), TF 1m, SHORT, vorher im Plus (trough weit unter Entry)
    losers = [_t(-1, side="SHORT", sl=101.0, exit_=101.0, opened="2026-09-01T02:00:00+00:00",
                 closed="2026-09-01T03:00:00+00:00", tf="1m", trough=99.0) for _ in range(5)]
    winners = [_t(1, side="LONG", opened="2026-09-01T14:00:00+00:00", tf="5m") for _ in range(3)]
    out = dg.diagnose(winners + losers)
    joined = " ".join(out)
    assert "Asia-Session" in joined and "TF 1m" in joined and "SHORT" in joined and "im Plus" in joined


def test_diagnose_fallback_line():
    rows = [_t(1), _t(1), _t(1), _t(-1, exit_=99.5), _t(-1, exit_=99.6)]  # kein SL-Hit, langsam, gemischt
    out = dg.diagnose(rows)
    assert len(out) >= 1 and all(isinstance(l, str) for l in out)
