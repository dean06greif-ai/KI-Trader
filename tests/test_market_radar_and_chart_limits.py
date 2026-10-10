"""Regressionstests: Markt-Radar (Wochen-Ultra-Scan) + Chart-Limit-Order-Daten.
Ohne Netzwerk/DB: reine Funktionen (digest/build_prompt/is_due/format_*)."""
import sys
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

sys.path.insert(0, "/app/backend")
from dotenv import load_dotenv
load_dotenv("/app/backend/.env")

BERLIN = ZoneInfo("Europe/Berlin")


def _candles(closes, spread=0.5):
    return [{"open": c, "high": c + spread, "low": c - spread,
             "close": c, "volume": 1.0} for c in closes]


def main():
    from services.ai_market_radar import (digest_symbol, zones_text, is_due,
                                          build_prompt, format_context,
                                          format_chat, RUN_DAY, RUN_HOUR)

    # ---------- 1) digest_symbol: gemessene Multi-TF-Statistik ----------
    closes = [100 + i * 0.5 for i in range(120)]        # stetiger Aufwärtstrend
    d1 = _candles(closes)
    h4 = _candles([c * 0.999 for c in closes])
    t = digest_symbol("BTCUSDT", d1, h4)
    assert t.startswith("BTCUSDT: Preis 159.5")
    assert "7d +" in t and "30d +" in t and "90d +" in t
    assert "EMA50-Dist +" in t and "EMA200-Dist +" in t
    assert "90d-Range-Pos 99%" in t
    assert digest_symbol("X", d1[:10], h4) == ""        # zu wenige Kerzen
    print("PASS 1: digest_symbol (7/30/90d, EMA-Distanzen, Range-Position)")

    # ---------- 2) is_due: Sonntag ab 18:00 Berlin, Überfällig-Nachholer ----
    sunday_19 = datetime(2026, 6, 7, 19, 0, tzinfo=BERLIN)       # Sonntag
    assert sunday_19.weekday() == RUN_DAY and 19 >= RUN_HOUR
    assert is_due(sunday_19, None) is True                        # nie gelaufen
    fresh = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    assert is_due(sunday_19, fresh) is False                      # erst gestern
    week_old = (datetime.now(timezone.utc) - timedelta(days=6.5)).isoformat()
    assert is_due(sunday_19, week_old) is True                    # Sonntag + alt genug
    sunday_10 = datetime(2026, 6, 7, 10, 0, tzinfo=BERLIN)
    assert is_due(sunday_10, week_old) is False                   # vor 18:00
    monday_12 = datetime(2026, 6, 8, 12, 0, tzinfo=BERLIN)
    assert is_due(monday_12, week_old) is False                   # falscher Tag
    overdue = (datetime.now(timezone.utc) - timedelta(days=9)).isoformat()
    assert is_due(monday_12, overdue) is True                     # Downtime-Nachholer
    print("PASS 2: is_due (Sonntag 18:00, Mindestabstand, Überfällig-Nachholer)")

    # ---------- 3) build_prompt: inkrementell + alle Datenblöcke ----------
    prev = {"ts": "2026-05-31T18:00:00+00:00", "summary": "BTC seitwärts in 60-64k",
            "mid_term": "Range hält", "long_term": "Aufwärts intakt",
            "coins": [{"symbol": "BTCUSDT", "bias": "neutral"}]}
    p = build_prompt(["BTCUSDT: Preis 78000 | 7d +2.0%"],
                     ["BTCUSDT: 1d SUP 74000-75000 (St 80, 3x)"],
                     "=== MARKT-BEOBACHTER ===\n- BTC trend_up",
                     "=== FORSCHUNG ===\nErkenntnis X", prev)
    assert "AKTUALISIEREN, nicht neu analysieren" in p
    assert "BTC seitwärts in 60-64k" in p and "BTCUSDT: neutral" in p
    assert "MULTI-TIMEFRAME-STATISTIK" in p and "HAUPT-S/R-ZONEN" in p
    assert "MARKT-BEOBACHTER" in p and "FORSCHUNG" in p
    assert "ÄNDERUNGEN" in p
    p0 = build_prompt(["x"], [], "", "", None)                    # erster Lauf
    assert "LETZTER MARKT-RADAR" not in p0
    print("PASS 3: build_prompt (inkrementelles Update statt Neu-Analyse)")

    # ---------- 4) format_context / format_chat / zones_text ----------
    report = {"ts": "2026-06-07T18:00:00+00:00", "summary": "Markt bullisch.",
              "short_term": "Aufwärts", "mid_term": "Range-Ausbruch möglich",
              "long_term": "Bullisch über EMA200", "changes": "BTC von neutral auf bullish",
              "coins": [{"symbol": "BTCUSDT", "bias": "bullish",
                         "note": "über EMA50", "key_zones": "74k SUP"}],
              "watchlist": ["BTCUSDT"]}
    ctx = format_context(report)
    assert ctx.startswith("=== WOCHEN-MARKTBILD (Markt-Radar vom 2026-06-07)")
    assert "Mittelfristig:" in ctx and "Langfristig:" in ctx
    assert "BTCUSDT bullish (74k SUP)" in ctx and "NUTZUNG:" in ctx
    assert format_context({}) == ""
    chat = format_chat(report)
    assert chat.startswith("📡 Markt-Radar") and "Δ Änderungen" in chat
    assert "Watchlist: BTCUSDT" in chat
    zt = zones_text("BTCUSDT", [{"tf": "1d", "kind": "support", "low": 74000.0,
                                 "high": 75000.0, "strength": 80, "touches": 3}])
    assert zt == "BTCUSDT: 1d SUP 74000-75000 (St 80, 3x)"
    print("PASS 4: format_context/format_chat/zones_text")

    # ---------- 5) Limit-Order-API-Form fürs Chart (expires_in_s vorhanden) ----
    import inspect
    from services import key_level_limits as kl
    src = inspect.getsource(kl.list_orders)
    assert "expires_in_s" in src, "Chart-Countdown braucht expires_in_s aus list_orders"
    print("PASS 5: /api/ai/limit-orders liefert expires_in_s (Chart-Countdown)")

    print("\nALLE TESTS BESTANDEN")


if __name__ == "__main__":
    main()
