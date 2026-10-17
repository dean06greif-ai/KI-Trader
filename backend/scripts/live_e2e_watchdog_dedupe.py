"""Live-E2E (Bitunix, ECHTE Mini-Order!): Doppel-Trade-Schutz + PnL-Abgleich.

Platziert eine minimale Market-Order (Standard XRPUSDT, Börsen-Minimum) und
prüft gegen die echte Bitunix-API:
  1. Watchdog übernimmt die Position NICHT, solange der Entry in-flight ist
  2. Watchdog übernimmt die Position NICHT innerhalb der Karenz (Börsen-ctime)
  3. nach der Karenz: Übernahme als 'Manuell (Bitunix)' mit Position-ID
  4. Dedupe: KI-Trade an derselben Position-ID -> Manuell-Duplikat entfernt
  5. Close an der Börse -> Bitunix-Sync verbucht den ECHTEN PnL (Historie),
     pnl_reconcile findet nichts Offenes mehr
Nur gegen eine DEV-Datenbank ausführen (DB_NAME in backend/.env)! Testdaten
werden am Ende wieder gelöscht.

    cd backend && python scripts/live_e2e_watchdog_dedupe.py [SYMBOL]
"""
import asyncio
import os
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

from motor.motor_asyncio import AsyncIOMotorClient  # noqa: E402

from services import entry_inflight, pnl_reconcile  # noqa: E402
from services.bitunix_trade import AutoTradeManager, BitunixTradeClient  # noqa: E402
from services.position_watchdog import PositionWatchdog, parse_positions  # noqa: E402

SYMBOL = (sys.argv[1] if len(sys.argv) > 1 else "XRPUSDT").upper()
SIDE = "LONG"


def ok(cond, msg):
    print(("PASS " if cond else "FAIL ") + msg)
    if not cond:
        raise SystemExit(1)


async def main():
    db_name = os.environ["DB_NAME"]
    ok("dev" in db_name.lower() or "test" in db_name.lower(),
       f"DB_NAME={db_name} ist eine DEV-/TEST-Datenbank")
    db = AsyncIOMotorClient(os.environ["MONGO_URL"])[db_name]
    client = BitunixTradeClient()
    ok(client.configured(), "Bitunix-Keys konfiguriert")
    await client.load_trading_pairs()
    at = AutoTradeManager(client)
    at.set_db(db)
    wd = PositionWatchdog()
    wd.setup(db, client, at, telegram=None)

    async def _noop(text):
        print("   [notify]", text.replace("\n", " ")[:120])
    wd._notify = _noop

    before = {p["position_id"] for p in parse_positions(await client.get_positions())}
    ok(not any(p["bitunix_symbol"] == client.to_bitunix_symbol(SYMBOL) and p["side"] == SIDE
               for p in parse_positions(await client.get_positions())),
       f"keine offene {SYMBOL} {SIDE}-Position vor dem Test")
    meta = client.contract_meta(client.to_bitunix_symbol(SYMBOL)) or {}
    qty = float(meta.get("min_qty") or 0)
    mark = float(await client.get_mark_price(SYMBOL) or 0)
    ok(qty > 0 and mark > 0, f"Kontraktdaten {SYMBOL}: min_qty={qty} mark={mark}")
    await client.set_leverage(SYMBOL, 5, "ISOLATION")

    # ---- 1) Entry in-flight, Order platzieren ----
    key = entry_inflight.begin(SYMBOL, SIDE, "e2e")
    res = await client.place_order(SYMBOL, "BUY", qty, order_type="MARKET",
                                   sl_price=round(mark * 0.95, 4))
    ok(isinstance(res, dict) and res.get("code") == 0, f"Mini-Order platziert: {res}")
    await asyncio.sleep(2.5)
    positions = parse_positions(await client.get_positions())
    new = [p for p in positions if p["position_id"] not in before
           and p["bitunix_symbol"] == client.to_bitunix_symbol(SYMBOL)]
    ok(len(new) == 1, f"neue Börsen-Position gefunden: {new}")
    pos = new[0]
    ok(pos["opened_ms"] > 0, f"Börse liefert ctime (opened_ms={pos['opened_ms']})")
    pid = pos["position_id"]
    try:
        st = await wd.check()
        ok(st["adopt_deferred"] >= 1 and not await db.auto_trades.find_one(
            {"bitunix_position_id": pid}), "in-flight: Position NICHT übernommen")
        # ---- 2) Entry beendet, aber Position frisch -> Karenz ----
        entry_inflight.end(key)
        st = await wd.check()
        ok(st["adopt_deferred"] >= 1 and not await db.auto_trades.find_one(
            {"bitunix_position_id": pid}), "Karenz (ctime): Position NICHT übernommen")
        # ---- 3) Karenz abgelaufen -> Manuell (Bitunix) ----
        wd.settings["adopt_grace_sec"] = 0
        st = await wd.check()
        manual = await db.auto_trades.find_one({"bitunix_position_id": pid, "status": "open"})
        ok(manual is not None and manual["strategy_name"] == "Manuell (Bitunix)",
           f"nach Karenz übernommen als {manual and manual['strategy_name']} ({manual and manual['id']})")
        # ---- 4) KI-Trade an derselben Position -> Duplikat weg ----
        ki_id = f"{SYMBOL}-e2e-{int(time.time() * 1000)}"
        await db.auto_trades.insert_one({
            "id": ki_id, "symbol": SYMBOL, "side": SIDE, "mode": "live", "status": "open",
            "strategy_id": "ai_trader", "strategy_name": "KI-Trader", "entry": pos["entry"],
            "sl": round(mark * 0.95, 4), "tp1": mark * 1.01, "tpf": mark * 1.02,
            "qty": qty, "qty_remaining": qty, "leverage": 5, "max_capital": qty * mark / 5,
            "realized_pnl": 0.0, "fees_paid": 0.0, "fee_percent": 0.06,
            "bitunix_position_id": pid, "external_adopted": False,
            "opened_at": datetime.now(timezone.utc).isoformat(), "events": []})
        st = await wd.check()
        ok(st["deduped"] == 1, f"Dedupe: {st['deduped']} Duplikat entfernt")
        ok(await db.auto_trades.find_one({"id": manual["id"]}) is None, "Manuell-Duplikat gelöscht")
        ki = await db.auto_trades.find_one({"id": ki_id})
        ok(ki["status"] == "open", "KI-Trade bleibt führend")
    finally:
        entry_inflight.clear()
        # ---- 5) Position schließen -> echter PnL ----
        cres = await client.flash_close(SYMBOL, pid, SIDE, qty, full=True)
        print("   close:", cres)
    await asyncio.sleep(3)
    ok(not any(p["position_id"] == pid for p in parse_positions(await client.get_positions())),
       "Position an der Börse geschlossen")
    synced = await at.sync_live_positions()
    ki = await db.auto_trades.find_one({"id": ki_id})
    print(f"   sync={synced} status={ki['status']} pnl={ki.get('realized_pnl')} "
          f"exact={ki.get('pnl_exchange_exact')} events={ki.get('events')[-2:]}")
    ok(ki["status"] == "closed", "Bitunix-Sync hat den Trade lokal geschlossen")
    if not ki.get("pnl_exchange_exact"):
        # Historie läuft nach -> Abgleich-Loop-Logik
        for _ in range(6):
            await asyncio.sleep(5)
            out = await pnl_reconcile.reconcile_pending(at)
            ki = await db.auto_trades.find_one({"id": ki_id})
            if ki.get("pnl_exchange_exact"):
                break
        print("   reconcile:", out, ki.get("realized_pnl"), ki.get("events")[-1:])
    ok(ki.get("pnl_exchange_exact") is True, f"echter Börsen-PnL übernommen: {ki.get('realized_pnl')} USDT "
                                              f"(Fees {ki.get('fees_paid')})")
    hist = await client.get_history_positions(position_id=pid)
    row = next((r for r in ((hist.get("data") or {}).get("positionList") or [])
                if str(r.get("positionId")) == pid), None)
    print("   Bitunix-Historie:", {k: row.get(k) for k in ("realizedPNL", "fee", "funding", "closePrice")} if row else None)
    await db.auto_trades.delete_many({"id": ki_id})
    await client._http().close()
    print("ALLE LIVE-CHECKS BESTANDEN")


if __name__ == "__main__":
    asyncio.run(main())
