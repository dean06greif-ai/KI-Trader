"""LIVE-Test (echte Börse, Minimalbetrag): Position öffnen -> SL wie der Trail
nachziehen (_live_move_sl, exakt der Pfad aus _manage_trade) -> Read-Back -> schließen.
Aufruf: cd backend && python ../scripts/live_trail_sl_check.py [SYMBOL]"""
import asyncio
import sys

sys.path.insert(0, ".")
from dotenv import load_dotenv  # noqa: E402

load_dotenv(".env")
from services.bitunix_trade import AutoTradeManager, BitunixTradeClient  # noqa: E402

CANDIDATES = ["DOGEUSDT", "XRPUSDT", "ADAUSDT", "TRXUSDT"]


async def main():
    c = BitunixTradeClient()
    await c.load_trading_pairs()
    print("balance", str(await c.get_balance())[:300])
    syms = sys.argv[1:] or CANDIDATES
    best = None
    for s in syms:
        px = await c.get_mark_price(s)
        mq = (c._pairs_meta.get(s) or {}).get("min_qty") or 0
        if px and mq and (best is None or px * mq < best[2]):
            best = (s, mq, px * mq, px)
    sym, qty, notional, px = best
    print("symbol", sym, "qty", qty, "notional", round(notional, 3))
    print("lev", await c.set_leverage(sym, 2))
    sl0 = px * 0.97
    print("open", await c.place_order(sym, "BUY", qty, sl_price=sl0))
    await asyncio.sleep(2)
    pid = await c.resolve_position_id(sym, "LONG")
    print("position", pid)
    mgr = AutoTradeManager(c)
    t = {"id": "livetest", "mode": "live", "symbol": sym, "side": "LONG", "qty": qty,
         "qty_remaining": qty, "bitunix_position_id": pid, "sl": sl0, "entry": px}
    try:
        for step, f in (("trail#1", 0.98), ("trail#2", 0.985)):
            new_sl = (await c.get_mark_price(sym)) * f
            ok = await mgr._live_move_sl(t, new_sl, qty)
            t["sl"] = new_sl
            pend = await c.get_pending_tpsl(sym, pid)
            print(step, "ok" if ok else "FAILED", round(new_sl, 6), "exchange:", str(pend)[:260])
    finally:
        print("close", await c.flash_close(sym, pid, "LONG", qty, full=True))
        await asyncio.sleep(2)
        print("positions after close", str(await c.get_positions(sym))[:200])


asyncio.run(main())
