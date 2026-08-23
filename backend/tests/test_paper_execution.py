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
    orig_book = pe._book_top
    orig_settings = pe._settings

    async def no_book(symbol):
        return None

    pe._book_top = no_book
    pe._settings = lambda: {}

    try:
        # ---------- 1) Fallback-Kosten + Richtung Entry ----------
        spread, slip, src, depth = await pe.get_costs("BTCUSDT")
        assert src == "fallback" and spread == 0.01 and slip == 0.01 and depth is None
        spread, slip, src, _ = await pe.get_costs("DOGEUSDT")
        assert spread == 0.04 and slip == 0.03
        # Yahoo-Instrument (Gold) -> nie Orderbuch, weiter gefasste Schätzung
        spread, slip, src, _ = await pe.get_costs("GOLD")
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
        spread, slip, src, _ = await pe.get_costs("DOGEUSDT")
        assert spread == 0.2 and slip == 0.1
        p, info = await pe.entry_fill("DOGEUSDT", "LONG", 1.0)
        assert abs(p - 1.002) < 1e-9  # 0.2/2 + 0.1 = 0.2%
        pe._settings = lambda: {"paper_realistic_fills": False}
        p_off, info_off = await pe.entry_fill("BTCUSDT", "LONG", ref)
        assert p_off == ref and info_off is None, "AUS-Schalter muss 1:1 füllen"
        print("PASS 4: Settings-Overrides + paper_realistic_fills=False")

        # ---------- 5) Live-Orderbuch bevorzugt ----------
        async def fake_book(symbol):
            return {"spread_pct": 0.008, "bid_usd": 500000.0, "ask_usd": 400000.0}

        pe._book_top = fake_book
        pe._settings = lambda: {}
        spread, slip, src, depth = await pe.get_costs("ETHUSDT")
        assert src == "orderbook" and spread == 0.008 and slip == 0.01
        assert depth == 400000.0  # Kauf konsumiert Asks
        _, _, _, depth_sell = await pe.get_costs("ETHUSDT", buy=False)
        assert depth_sell == 500000.0  # Verkauf konsumiert Bids
        print("PASS 5: Live-Orderbuch-Spread + seitenrichtige Tiefe")

        # ---------- 6) Kaputte Eingaben fail-safe ----------
        p, info = await pe.entry_fill("BTCUSDT", "LONG", 0)
        assert p == 0 and info is None
        r = pe._sig_round(0.000012345678912)
        assert 0 < r < 0.0001, "Micro-Alt-Preise dürfen nicht wegrunden"
        print("PASS 6: Fail-safe bei Preis 0 + Rundung für Micro-Alts")

        # ---------- 7) Größenabhängige Slippage (Sqrt-Impact) ----------
        pe._book_top = no_book
        pe._settings = lambda: {}
        # Kleine Order (<= Referenz 100k für BTC): Basis-Slippage, Faktor 1
        slip, mult = pe.size_scaled_slippage(0.01, 20_000, "major")
        assert slip == 0.01 and mult == 1.0
        # 400k auf BTC (Ref 100k): sqrt(4) = 2x
        slip, mult = pe.size_scaled_slippage(0.01, 400_000, "major")
        assert abs(slip - 0.02) < 1e-9 and mult == 2.0
        # 100k auf Alt (Ref 25k): sqrt(4) = 2x auf 0.03 -> 0.06
        slip, mult = pe.size_scaled_slippage(0.03, 100_000, "crypto")
        assert abs(slip - 0.06) < 1e-9 and mult == 2.0
        # Harte Kappung (Default 0.30%)
        slip, _ = pe.size_scaled_slippage(0.03, 50_000_000, "crypto")
        assert slip == pe.MAX_SLIPPAGE_PCT_DEFAULT
        # Tiefes Buch absorbiert: Depth 400k > Ref 100k -> Faktor 1 bei 400k
        slip, mult = pe.size_scaled_slippage(0.01, 400_000, "major", depth_usd=400_000)
        assert slip == 0.01 and mult == 1.0
        # Notional 0 / Feature aus -> Basis
        slip, mult = pe.size_scaled_slippage(0.01, 0, "major")
        assert slip == 0.01 and mult == 1.0
        pe._settings = lambda: {"paper_size_slippage_enabled": False}
        slip, mult = pe.size_scaled_slippage(0.01, 1_000_000, "major")
        assert slip == 0.01 and mult == 1.0
        # Settings-Overrides: eigene Referenz + eigener Deckel
        pe._settings = lambda: {"paper_size_ref_notional": 10_000,
                                "paper_max_slippage_pct": 0.05}
        slip, mult = pe.size_scaled_slippage(0.01, 90_000, "major")
        assert mult == 3.0 and slip == 0.03
        slip, _ = pe.size_scaled_slippage(0.01, 9_000_000, "major")
        assert slip == 0.05
        print("PASS 7: Größenabhängige Slippage (Sqrt, Tiefe, Kappung, Overrides)")

        # ---------- 8) Größen-Slippage fließt in den Fill ein ----------
        pe._settings = lambda: {}
        ref = 100.0
        p_small, i_small = await pe.entry_fill("BTCUSDT", "LONG", ref, notional_usdt=20_000)
        p_big, i_big = await pe.entry_fill("BTCUSDT", "LONG", ref, notional_usdt=400_000)
        assert p_big > p_small, "Große Order muss schlechter füllen als kleine"
        assert i_big["size_mult"] == 2.0 and i_small["size_mult"] == 1.0
        assert i_big["slippage_pct"] == 0.02 and i_big["base_slippage_pct"] == 0.01
        assert i_big["notional_usdt"] == 400_000
        x_big, xi = await pe.exit_fill("BTCUSDT", "LONG", ref, notional_usdt=400_000)
        assert x_big < ref and xi["size_mult"] == 2.0
        print("PASS 8: entry_fill/exit_fill skalieren mit der Ordergröße")
    finally:
        pe._book_top = orig_book
        pe._settings = orig_settings

    print("ALLE PAPER-EXECUTION-TESTS GRÜN")


def test_paper_execution():
    asyncio.run(main())


if __name__ == "__main__":
    asyncio.run(main())
