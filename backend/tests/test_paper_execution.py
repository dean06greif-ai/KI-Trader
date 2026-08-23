"""Tests Ehrliches Paper-Trading (services/paper_execution.py):
Spread + Slippage werden bei Paper-Fills adversarial angerechnet –
Entry LONG teurer / SHORT billiger, Exit umgekehrt. Live-Orderbuch
bevorzugt, Fallback Schätzwerte, Settings-Overrides, An/Aus-Schalter."""
import asyncio
import sys

sys.path.insert(0, "/app/backend")
from dotenv import load_dotenv
load_dotenv("/app/backend/.env")


async def main():
    from services import paper_execution as pe

    # Netzwerk aus dem Test raushalten: Orderbuch-Abfrage deterministisch mocken
    orig_book = pe._book_spread_pct
    orig_settings = pe._settings

    async def no_book(symbol):
        return None

    pe._book_spread_pct = no_book
    pe._settings = lambda: {}

    try:
        # ---------- 1) Fallback-Kosten + Richtung Entry ----------
        spread, slip, src = await pe.get_costs("BTCUSDT")
        assert src == "fallback" and spread == 0.01 and slip == 0.01
        spread, slip, src = await pe.get_costs("DOGEUSDT")
        assert spread == 0.04 and slip == 0.03
        # Yahoo-Instrument (Gold) -> nie Orderbuch, weiter gefasste Schätzung
        spread, slip, src = await pe.get_costs("GOLD")
        assert src == "fallback" and spread == 0.06 and slip == 0.05
        print("PASS 1: Fallback-Kosten je Tier (Major/Crypto/Other)")

        # ---------- 2) Entry-Fill adversarial ----------
        ref = 100.0
        p_long, info = await pe.entry_fill("BTCUSDT", "LONG", ref)
        assert p_long > ref, "LONG-Entry muss TEURER als Referenz füllen"
        assert info["source"] == "fallback" and info["ref_price"] == ref
        # BTC: 0.01/2 + 0.01 = 0.015% -> 100.015
        assert abs(p_long - 100.015) < 1e-9
        p_short, _ = await pe.entry_fill("BTCUSDT", "SHORT", ref)
        assert p_short < ref, "SHORT-Entry muss BILLIGER als Referenz füllen"
        assert abs(p_short - 99.985) < 1e-9
        print("PASS 2: Entry-Fill LONG über / SHORT unter Referenzpreis")

        # ---------- 3) Exit-Fill adversarial (gespiegelt) ----------
        x_long, xi = await pe.exit_fill("BTCUSDT", "LONG", ref)
        assert x_long < ref, "LONG-Exit (Verkauf) muss UNTER dem Level füllen"
        x_short, _ = await pe.exit_fill("BTCUSDT", "SHORT", ref)
        assert x_short > ref, "SHORT-Exit (Kauf) muss ÜBER dem Level füllen"
        assert xi["cost_pct"] == 0.015
        print("PASS 3: Exit-Fill immer zu Ungunsten des Trades")

        # ---------- 4) Settings-Overrides + Abschalten ----------
        pe._settings = lambda: {"paper_fallback_spread_pct": 0.2,
                                "paper_slippage_pct": 0.1}
        spread, slip, src = await pe.get_costs("DOGEUSDT")
        assert spread == 0.2 and slip == 0.1
        p, info = await pe.entry_fill("DOGEUSDT", "LONG", 1.0)
        assert abs(p - 1.002) < 1e-9  # 0.2/2 + 0.1 = 0.2%
        pe._settings = lambda: {"paper_realistic_fills": False}
        p_off, info_off = await pe.entry_fill("BTCUSDT", "LONG", ref)
        assert p_off == ref and info_off is None, "AUS-Schalter muss 1:1 füllen"
        print("PASS 4: Settings-Overrides + paper_realistic_fills=False")

        # ---------- 5) Live-Orderbuch bevorzugt ----------
        async def fake_book(symbol):
            return 0.008

        pe._book_spread_pct = fake_book
        pe._settings = lambda: {}
        spread, slip, src = await pe.get_costs("ETHUSDT")
        assert src == "orderbook" and spread == 0.008 and slip == 0.01
        print("PASS 5: Live-Orderbuch-Spread wird bevorzugt genutzt")

        # ---------- 6) Kaputte Eingaben fail-safe ----------
        p, info = await pe.entry_fill("BTCUSDT", "LONG", 0)
        assert p == 0 and info is None
        r = pe._sig_round(0.000012345678912)
        assert 0 < r < 0.0001, "Micro-Alt-Preise dürfen nicht wegrunden"
        print("PASS 6: Fail-safe bei Preis 0 + Rundung für Micro-Alts")
    finally:
        pe._book_spread_pct = orig_book
        pe._settings = orig_settings

    print("ALLE PAPER-EXECUTION-TESTS GRÜN")


def test_paper_execution():
    asyncio.run(main())


if __name__ == "__main__":
    asyncio.run(main())
