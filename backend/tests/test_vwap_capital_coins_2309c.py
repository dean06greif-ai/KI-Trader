"""Regressionstests 23.09 (c): LINK/SUI im Universum, VWAP-Reclaim-Setup,
schrittweise Kapital-Drosselung je Setup × Asset inkl. Verlauf."""
import numpy as np

from core import instruments
from services import ai_playbook, setup_asset_class as ac, setup_capital as sc
from services.candles import CandleArray
from services.setup_backtest import detectors as det


def test_link_sui_in_universe():
    for s in ("LINKUSDT", "SUIUSDT"):
        assert s in instruments.TOP_10_COINS and s in instruments.BACKTEST_SYMBOLS
        assert instruments.is_tradable(s) and ac.asset_class_of(s) == "crypto"
    assert instruments.TOP_10_COINS[:3] == ["BTCUSDT", "ETHUSDT", "BNBUSDT"]


def test_vwap_setup_registered_everywhere():
    assert "vwap_reclaim" in ai_playbook.SETUPS and "vwap_reclaim" in det.DETECTORS
    assert len(det.VARIANTS["vwap_reclaim"]) == 3
    assert ai_playbook.normalize_setup("VWAP reclaim") == "vwap_reclaim"
    assert ac.setup_allowed("crypto", "vwap_reclaim") and not ac.setup_allowed("forex", "vwap_reclaim")


def _ca(closes, vols, start_ts=0):
    n = len(closes)
    cl = np.array(closes, float)
    op = np.r_[cl[0], cl[:-1]]
    return CandleArray(ts=np.arange(n, dtype=np.int64) * 60_000 + start_ts, op=op,
                       hi=np.maximum(op, cl) + 0.05, lo=np.minimum(op, cl) - 0.05, cl=cl,
                       vol=np.array(vols, float))


def test_session_vwap_resets_daily_and_first_partial_day_nan():
    c = _ca([100.0] * 10, [1.0] * 10)
    vw, sb = det.session_vwap(c)
    assert np.allclose(vw, 100.0) and list(sb[:3]) == [0, 1, 2]
    c2 = _ca([100.0] * 5, [1.0] * 5, start_ts=3_600_000)      # startet 01:00 UTC
    assert np.isnan(det.session_vwap(c2)[0]).all()


def test_vwap_reclaim_detector_long():
    # 1-Min-Daten: Abwärts unter VWAP, dann kräftige Rückeroberung mit Volumen
    n = 2000
    base = np.r_[np.full(n - 60, 100.0) + np.sin(np.arange(n - 60) / 7) * 0.3,
                 np.linspace(100.0, 99.5, 55), np.linspace(99.55, 100.1, 5)]
    vols = np.r_[np.ones(n - 5), np.full(5, 8.0)]
    f = det.Features(_ca(base, vols))
    f.trend60[:] = 0
    p = dict(det.VARIANTS["vwap_reclaim"][2], min_session_bars=0)
    sigs = det.detect_vwap_reclaim(f, p)
    last = [s for s in sigs if s.idx == f.n - 1]
    assert last and last[0].side == "LONG" and last[0].sl < last[0].entry < last[0].tpf


def test_asset_factor_graduated_steps():
    mk = lambda n, w, pnl, m=1000: {"trades": n, "wins": w, "pnl": pnl, "margin": m}
    assert sc.asset_factor(mk(3, 0, -50))[0] == 1.0                   # zu wenig Daten
    assert sc.asset_factor(mk(10, 6, 20))[0] == 1.0                   # positiv
    assert sc.asset_factor(mk(10, 5, -3))[0] == 1.0                   # WR 50 %, nur -0.3 % Margin
    assert sc.asset_factor(mk(10, 5, -6))[0] == 0.75                  # leicht reduziert
    assert sc.asset_factor(mk(10, 4, -3))[0] == 0.5                   # WR 40 %
    assert sc.asset_factor(mk(20, 7, -10))[0] == 0.25                 # WR 35 %
    assert sc.asset_factor(mk(10, 2, -10))[0] == 0.0                  # WR 20 % -> ausgesetzt


def test_track_asset_changes_logs_steps():
    per = {"range_fade": {"BNBUSDT": {"trades": 10, "wins": 4, "pnl": -20, "margin": 1000},
                          "BTCUSDT": {"trades": 10, "wins": 7, "pnl": 30, "margin": 1000}}}
    state, ev = sc.track_asset_changes({}, per, [], "2026-09-23T10:00:00+00:00")
    assert state == {"range_fade|BNBUSDT": 0.5} and len(ev) == 1 and ev[0]["direction"] == "down"
    per["range_fade"]["BNBUSDT"] = {"trades": 12, "wins": 7, "pnl": 5, "margin": 1000}
    state2, ev2 = sc.track_asset_changes(state, per, ev, "2026-09-24T10:00:00+00:00")
    assert state2 == {} and ev2[-1]["to"] == 1.0 and ev2[-1]["direction"] == "up"
    assert sc.track_asset_changes(state2, per, ev2, "x")[1] == ev2   # keine Änderung, kein Eintrag
