"""Regressionstests (09/2026):
  * Aktive Setup-Variante: Zeitfenster, Label, Zusammenführung, maturity_overview
  * Markt-Kalender: tägliche Handelspause + Wochenende (is_market_closed)
  * Diagnose: veraltete Kerzen nur bei OFFENEM Markt kritisch
  * Feed-Wächter: Warnung nur bei offenem Markt
  * Provider-Health: Backup-Key ist KEIN Fallback mehr
"""
from datetime import datetime, timezone

from core import market_hours, scheduler
from services import ai_diagnosis as diag
from services import ai_playbook, ai_providers, setup_variant


# ---------------- setup_variant (rein) ----------------
def test_variant_since_takes_latest_of_all_sources():
    scope = {
        "live_blocked": {"a": {"at": "2026-09-06T10:00:00+00:00"}},
        "eval_since": {"a": "2026-09-01T00:00:00+00:00", "b": "2026-09-03T00:00:00+00:00"},
        "revisions": {"b": {"version": 1, "since": "2026-09-08T00:00:00+00:00"}},
        "lifecycle": {"c": {"versions": [{"v": 1, "since": "2026-09-02T00:00:00+00:00"},
                                         {"v": 2, "since": "2026-09-10T00:00:00+00:00"}]}},
    }
    m = setup_variant.variant_since_map(scope, ["a", "b", "c", "d"])
    assert m["a"].startswith("2026-09-06")      # Rückstufung jünger als eval_since
    assert m["b"].startswith("2026-09-08")      # Revision jünger als eval_since
    assert m["c"].startswith("2026-09-10")      # aktive Profil-Version
    assert "d" not in m                          # keine Variante -> Gesamtstatistik


def test_variant_label_mentions_revision_profile_and_state():
    scope = {"live_blocked": {"a": {"at": "x"}},
             "revisions": {"a": {"version": 2, "since": "y"}},
             "lifecycle": {"a": {"versions": [{"v": 3, "since": "z"}]}}}
    lbl = setup_variant.variant_label(scope, "a")
    assert "Rev.2" in lbl and "Profil v3" in lbl and "Rückstufung" in lbl
    assert setup_variant.variant_label({}, "q") == "aktive Variante"


def test_merge_and_global_view():
    merged = setup_variant.merge_stats([{"trades": 3, "wins": 1, "pnl": -10},
                                        None, {"trades": 5, "wins": 4, "pnl": 30}])
    assert merged["trades"] == 8 and merged["wins"] == 5 and merged["pnl"] == 20.0
    results = {
        "crypto": {"variant_since": {"a": "2026-09-09"}, "variant_stats": {"a": {"trades": 2, "wins": 2, "pnl": 8}},
                   "stats": {"a": {"trades": 20, "wins": 5, "pnl": -300}, "b": {"trades": 4, "wins": 1, "pnl": -5}}},
        "forex": {"variant_since": {}, "variant_stats": {},
                  "stats": {"a": {"trades": 1, "wins": 0, "pnl": -2}}},
    }
    g = setup_variant.global_variant_view({}, ["a", "b"], results,
                                          {"a": {"trades": 21, "wins": 5, "pnl": -302}, "b": {"trades": 4, "wins": 1, "pnl": -5}})
    assert g["a"]["trades"] == 3 and g["a"]["pnl"] == 6.0 and g["a"]["variant_since"] == "2026-09-09"
    assert g["b"]["trades"] == 4 and g["b"]["variant_since"] is None


def test_maturity_overview_shows_variant_stats_and_keeps_totals():
    stats = {"range_fade": {"trades": 50, "wins": 20, "pnl": -270.93, "verdict": "schwach"}}
    rows = ai_playbook.maturity_overview(
        stats, {}, {}, {}, None, None,
        variant_since={"range_fade": "2026-09-11T00:00:00+00:00"},
        variant_stats={"range_fade": {"trades": 4, "wins": 3, "pnl": 12.5, "verdict": "test"}},
        variant_labels={"range_fade": "Profil v6"})
    r = next(x for x in rows if x["setup"] == "range_fade")
    assert r["trades"] == 4 and r["winrate"] == 75 and r["pnl"] == 12.5
    assert r["variant"]["since"] == "2026-09-11" and r["variant"]["label"] == "Profil v6"
    assert r["variant"]["total"] == {"trades": 50, "winrate": 40, "pnl": -270.93}
    other = next(x for x in rows if x["setup"] != "range_fade")
    assert other["variant"] is None                  # ohne Variante: Gesamt, unverändert


def test_maturity_overview_without_variants_is_backwards_compatible():
    stats = {"breakout": {"trades": 7, "wins": 3, "pnl": -25.66, "verdict": "test"}}
    rows = ai_playbook.maturity_overview(stats, {}, {}, {}, None, None)
    r = next(x for x in rows if x["setup"] == "breakout")
    assert r["trades"] == 7 and r["pnl"] == -25.66 and r["variant"] is None


# ---------------- Markt-Kalender ----------------
def test_market_closed_weekend_and_daily_break_forex():
    sat = datetime(2026, 9, 12, 8, 40, tzinfo=timezone.utc)          # Samstag (Bug-Report)
    assert market_hours.is_market_closed("EURUSD", sat)[0]
    assert not market_hours.is_market_closed("BTCUSDT", sat)[0]
    tue_break = datetime(2026, 9, 15, 21, 30, tzinfo=timezone.utc)   # FX-Rollover
    closed, why = market_hours.is_market_closed("USDJPY", tue_break)
    assert closed and "Handelspause" in why
    tue_open = datetime(2026, 9, 15, 9, 0, tzinfo=timezone.utc)
    assert not market_hours.is_market_closed("USDJPY", tue_open)[0]
    assert not market_hours.is_market_closed("GOLD", tue_open)[0]
    # Wochenend-Guard unverändert (tägliche Pause zählt dort NICHT als Wochenende)
    assert not market_hours.is_weekend_closed("USDJPY", tue_break)[0]


# ---------------- Diagnose ----------------
def _findings(dq):
    return " | ".join(x["text"] for x in diag.build_findings(
        {"trades": 0, "win_rate": None}, {"trades": 0, "win_rate": None},
        {}, {}, {}, {"measured_trades": 0}, [], [], dq, 0, 0))


def test_diagnosis_closed_market_is_info_not_critical():
    dq = [{"symbol": s, "candles": 500, "last_candle_age_min": 660.0, "orderflow_real": False,
           "has_pauses": True, "market_closed": True} for s in ("USDJPY", "EURUSD", "GBPUSD")]
    texts = _findings(dq)
    assert "Veraltete Kursdaten" not in texts
    assert "Markt geschlossen" in texts and "USDJPY" in texts


def test_diagnosis_open_market_stale_is_critical_with_market_threshold():
    dq = [{"symbol": "EURUSD", "candles": 500, "last_candle_age_min": 45.0,
           "has_pauses": True, "market_closed": False},
          {"symbol": "GOLD", "candles": 500, "last_candle_age_min": 8.0,     # Yahoo-Lag: unter 10 min ok
           "has_pauses": True, "market_closed": False},
          {"symbol": "BTCUSDT", "candles": 500, "last_candle_age_min": 6.0,   # Krypto: 5 min
           "has_pauses": False, "market_closed": False}]
    texts = _findings(dq)
    assert "Veraltete Kursdaten bei 2 Symbol(en) trotz geöffnetem Markt" in texts
    assert "EURUSD" in texts and "BTCUSDT" in texts and "GOLD (" not in texts


def test_data_quality_rows_carry_market_flags():
    class _Scanner:
        candle_buffer = {"EURUSD": [{"timestamp": 1}], "BTCUSDT": [{"timestamp": 1}]}
    rows = {r["symbol"]: r for r in diag.data_quality_rows(_Scanner())}
    assert rows["EURUSD"]["has_pauses"] is True and "market_closed" in rows["EURUSD"]
    assert rows["BTCUSDT"]["has_pauses"] is False and rows["BTCUSDT"]["market_closed"] is False


# ---------------- Feed-Wächter (Scheduler) ----------------
def test_feed_lag_warning_only_when_market_open():
    open_ts = int(datetime(2026, 9, 15, 9, 0, tzinfo=timezone.utc).timestamp() * 1000)
    old = open_ts - 40 * 60 * 1000
    assert "Datenfeed" in scheduler.feed_lag_warning("EURUSD", old, open_ts)
    assert scheduler.feed_lag_warning("EURUSD", open_ts - 5 * 60 * 1000, open_ts) is None
    sat_ts = int(datetime(2026, 9, 12, 8, 40, tzinfo=timezone.utc).timestamp() * 1000)
    assert scheduler.feed_lag_warning("EURUSD", sat_ts - 660 * 60 * 1000, sat_ts) is None
    assert scheduler.feed_lag_warning("BTCUSDT", old, open_ts) is None   # Krypto: eigener Guard
    # SL/TP-Frische-Logik unverändert: Märkte mit Pausen gelten nie als stale
    assert scheduler._is_stale("EURUSD", old, open_ts) is False


# ---------------- Provider-Health ----------------
def test_backup_key_is_not_a_fallback():
    ai_providers._health.clear()
    ai_providers._last_call.clear()
    ai_providers._role_fallbacks.clear()
    ai_providers.record_result("openrouter", "m", "ok", key_index=3, role="analyst", requested="m")
    h = ai_providers.health_status()
    assert h["fallback_active"] is False
    assert h["active_fallbacks"] == []
    assert h["last_call"]["key_index"] == 3
    # echtes Ersatz-Modell bleibt ein Fallback
    ai_providers.record_result("openrouter", "ersatz", "ok", key_index=0, role="analyst", requested="m")
    assert ai_providers.health_status()["fallback_active"] is True
    ai_providers._health.clear()
    ai_providers._last_call.clear()
    ai_providers._role_fallbacks.clear()
