"""Regressionstests für die Trade-Verbesserungen:
- MFE/Peak-Tracking (höchster Stand bei Long, tiefster bei Short)
- Voll-Close rundet die Menge AUF (Watchdog-Bug: XRP-Rest blieb offen)
- Intervall-Wahl für den On-Demand-Trade-Chart
"""
from core.utils import _enrich_trade
from routers.autotrade import _chart_interval
from services.bitunix_trade import BitunixTradeClient, update_peak


# ---------- update_peak (MFE) ----------

def test_update_peak_long_takes_highest():
    assert update_peak("LONG", 105.0, 100.0, 103.0) == 105.0
    assert update_peak("LONG", 105.0, 100.0, 110.0) == 110.0


def test_update_peak_short_takes_lowest():
    assert update_peak("SHORT", 95.0, 100.0, 97.0) == 95.0
    assert update_peak("SHORT", 95.0, 100.0, 92.0) == 92.0


def test_update_peak_handles_missing_values():
    assert update_peak("LONG", None, None) is None
    assert update_peak("LONG", None, 100.0) == 100.0
    assert update_peak("SHORT", None, "abc", 100.0) == 100.0


# ---------- _enrich_trade Peak-Felder ----------

def _base_trade(**kw):
    t = {"id": "t1", "symbol": "XRPUSDT", "side": "LONG", "entry": 100.0,
         "sl": 95.0, "initial_sl": 95.0, "tp1": 105.0, "tpf": 110.0,
         "qty": 10.0, "qty_remaining": 10.0, "risk": 5.0,
         "status": "open", "realized_pnl": 0.0, "mode": "paper",
         "opened_at": "2026-06-01T10:00:00+00:00"}
    t.update(kw)
    return t


def test_enrich_open_long_peak_uses_live_price_over_stored():
    t = _enrich_trade(_base_trade(peak_price=103.0), current_price=106.0)
    c = t["computed"]
    assert c["peak_price"] == 106.0
    assert c["mfe_pct"] == 6.0


def test_enrich_open_short_peak_keeps_stored_low():
    t = _enrich_trade(_base_trade(side="SHORT", peak_price=94.0),
                      current_price=97.0)
    c = t["computed"]
    assert c["peak_price"] == 94.0
    assert c["mfe_pct"] == 6.0  # 6% in Trade-Richtung (Short)


def test_enrich_closed_without_stored_peak_is_none():
    t = _enrich_trade(_base_trade(status="closed", exit_price=110.0,
                                  qty_remaining=0,
                                  closed_at="2026-06-01T12:00:00+00:00"))
    assert t["computed"]["peak_price"] is None
    assert t["computed"]["mfe_pct"] is None


def test_enrich_closed_with_stored_peak():
    t = _enrich_trade(_base_trade(status="closed", exit_price=110.0,
                                  peak_price=112.5, qty_remaining=0,
                                  closed_at="2026-06-01T12:00:00+00:00"))
    c = t["computed"]
    assert c["peak_price"] == 112.5
    assert c["mfe_pct"] == 12.5


# ---------- Voll-Close: Menge aufrunden (Watchdog-Fix) ----------

def test_fmt_qty_full_close_rounds_up():
    client = BitunixTradeClient()
    client._pairs_meta["XRPUSDT"] = {"qty_step": 0.1, "min_qty": 0.1}
    # Normal (Teil-Close/Entry): abrunden wie bisher
    assert client._fmt_qty("XRPUSDT", 12.34) == "12.3"
    # Voll-Close: aufrunden, damit kein Step-Rest an der Börse zurückbleibt
    assert client._fmt_qty("XRPUSDT", 12.34, round_up=True) == "12.4"
    # Exaktes Vielfaches bleibt unverändert
    assert client._fmt_qty("XRPUSDT", 12.3, round_up=True) == "12.3"


# ---------- Chart-Intervall-Wahl ----------

def test_chart_interval_short_trade_uses_1m():
    assert _chart_interval(30 * 60)[0] == "1m"


def test_chart_interval_scales_with_duration():
    assert _chart_interval(24 * 3600)[0] in ("15m", "30m")
    assert _chart_interval(30 * 86400)[0] in ("4h", "1d")


def test_chart_interval_caps_at_1d():
    assert _chart_interval(365 * 86400)[0] == "1d"
