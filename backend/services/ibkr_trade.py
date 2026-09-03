"""IBKR-Forex-Live-Trading – Brücke zwischen AutoTradeManager und IBKRClient.

Design (bewusst schlank, gleiche Buchhaltung wie Bitunix-Trades):
  * Entry: Market-Order + Börsen-Bracket (SL = STP, TP = LMT, beide GTC).
    Live-Forex nutzt v1 den VOLLEN TP (tpf) – kein Partial-TP1 (IBKR-Brackets
    tragen genau einen TP). Paper-Forex behält das komplette TP1/BE-Verhalten.
  * Monitoring: run_loop() hält die Gateway-Session am Leben (tickle) und
    gleicht offene broker='ibkr'-Trades gegen die echten IBKR-Positionen ab.
    Ist die Position an der Börse zu (SL/TP gefüllt oder manuell geschlossen),
    wird der Trade lokal mit echtem Fill-Preis verbucht.
  * Gebühren: services/fee_model.py (IBKR-Kommission + Mindestkommission).
"""
import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Dict, Optional

from services import fee_model
from services.ibkr_client import ibkr_client, base_qty_for_notional

logger = logging.getLogger(__name__)

POLL_SEC = 60
_auth_cache = {"ts": 0.0, "ok": None}


def ibkr_ready() -> bool:
    """Gateway konfiguriert und (soweit bekannt) authentifiziert. Ohne
    frischen Auth-Status fail-open – die Order-Antwort ist die Wahrheit."""
    if not ibkr_client.configured():
        return False
    ts, ok = ibkr_client.last_auth
    if ok is not None and time.time() - ts < 300:
        return bool(ok)
    return True


def pnl_usd(symbol: str, side: str, entry: float, exit_price: float,
            qty_base: float) -> float:
    """Realisierter Preis-PnL in USD. Quote-Währung != USD (USDJPY etc.):
    PnL fällt in der Quote-Währung an und wird über den Exit-Kurs in USD
    umgerechnet (USD ist bei allen unterstützten Paaren Basis ODER Quote)."""
    sign = 1.0 if str(side).upper() == "LONG" else -1.0
    pnl_quote = (float(exit_price) - float(entry)) * float(qty_base) * sign
    s = str(symbol or "").upper()
    if s.endswith("USD"):
        return pnl_quote
    return pnl_quote / float(exit_price) if exit_price else 0.0


async def open_live_forex(symbol: str, side: str, entry: float, sl: float,
                          tp: float, notional_usd: float) -> Dict:
    """Bracket-Order platzieren. Rückgabe: {ok, qty, fill_price, conid,
    order_id, sl_order_id, tp_order_id, error}."""
    qty = base_qty_for_notional(symbol, notional_usd, entry)
    res = await ibkr_client.place_forex_bracket(symbol, side, qty, sl, tp)
    if not res.get("ok"):
        return res
    fill = None
    try:
        fill = await ibkr_client.wait_fill(res["order_id"])
    except Exception as e:
        logger.debug(f"IBKR {symbol}: Fill-Abfrage fehlgeschlagen: {e}")
    return {**res, "qty": qty, "fill_price": fill}


async def close_live_forex(trade: Dict) -> Dict:
    """Manueller Close eines IBKR-Trades: Bracket-Kinder stornieren, dann
    Gegen-Market-Order. Der run_loop verbucht den Abschluss."""
    for key in ("ibkr_sl_order_id", "ibkr_tp_order_id"):
        oid = trade.get(key)
        if oid:
            try:
                await ibkr_client.cancel_order(oid)
            except Exception as e:
                logger.debug(f"IBKR cancel {key}: {e}")
    qty = float(trade.get("qty_remaining") or trade.get("qty") or 0)
    return await ibkr_client.close_forex_position(
        trade["symbol"], trade["side"], int(round(qty)))


def _exit_from_trades(trades: list, conid, side: str) -> Optional[float]:
    """Jüngsten Close-Fill (Gegenseite) für den Kontrakt aus den Executions."""
    want = "S" if str(side).upper() == "LONG" else "B"
    best_ts, best_px = 0, None
    for tr in trades or []:
        if not isinstance(tr, dict) or str(tr.get("conid")) != str(conid):
            continue
        if str(tr.get("side") or "").upper()[:1] != want:
            continue
        try:
            ts = int(tr.get("trade_time_r") or 0)
            px = float(tr.get("price") or 0)
        except (TypeError, ValueError):
            continue
        if px > 0 and ts >= best_ts:
            best_ts, best_px = ts, px
    return best_px


async def _book_close(db, t: Dict, exit_price: float, manager=None) -> None:
    entry = float(t.get("entry") or 0)
    qty = float(t.get("qty") or 0)
    gross = pnl_usd(t["symbol"], t["side"], entry, exit_price, qty)
    notional = entry * qty if not str(t["symbol"]).upper().startswith("USD") else qty
    exit_fee = notional * fee_model.forex_fee_percent(notional) / 100.0
    fees_total = round(float(t.get("fees_paid") or 0) + exit_fee, 6)
    realized = round(gross - fees_total, 6)
    result = "win" if realized > 0 else ("breakeven" if realized == 0 else "loss")
    closed_at = datetime.now(timezone.utc).isoformat()
    updates = {"status": "closed", "exit_price": exit_price, "result": result,
               "realized_pnl": realized, "qty_remaining": 0,
               "fees_paid": fees_total, "closed_by": "ibkr_sync",
               "closed_at": closed_at,
               "events": (t.get("events", []) + [{
                   "ts": closed_at, "type": "ibkr_close",
                   "detail": f"IBKR-Abgleich: Position geschlossen @ {exit_price}"}])[-20:]}
    await db.auto_trades.update_one({"id": t["id"]}, {"$set": updates})
    logger.info(f"IBKR-Sync: {t['symbol']} {t['side']} geschlossen -> "
                f"PnL {realized} USD @ {exit_price}")
    if manager is not None:
        try:
            await manager._after_close({**t, **updates})
        except Exception as e:
            logger.debug(f"IBKR _after_close: {e}")


async def sync_open_trades(db, manager=None) -> int:
    """Offene IBKR-Live-Trades gegen die echten Positionen abgleichen."""
    open_trades = await db.auto_trades.find(
        {"status": "open", "mode": "live", "broker": "ibkr"}).to_list(50)
    if not open_trades:
        return 0
    positions = await ibkr_client.get_positions()
    open_conids = set()
    for p in positions or []:
        if isinstance(p, dict):
            try:
                if abs(float(p.get("position") or 0)) > 0:
                    open_conids.add(str(p.get("conid")))
            except (TypeError, ValueError):
                continue
    trades_hist = None
    synced = 0
    for t in open_trades:
        conid = str(t.get("ibkr_conid") or "")
        if not conid or conid in open_conids:
            continue
        if trades_hist is None:
            trades_hist = await ibkr_client.get_trades()
        exit_price = _exit_from_trades(trades_hist, conid, t["side"])
        if not exit_price:
            # Fallback: letzter bekannter Kurs (Yahoo-Feed) statt Fill
            try:
                from core import instruments
                candles = await instruments.fetch_live_candles(t["symbol"], limit=2)
                exit_price = float(candles[-1]["close"]) if candles else None
            except Exception:
                exit_price = None
        if not exit_price:
            logger.warning(f"IBKR-Sync: kein Exit-Preis für {t['symbol']} – nächster Lauf")
            continue
        await _book_close(db, t, float(exit_price), manager)
        synced += 1
    return synced


async def run_loop(manager=None):
    """Hintergrund-Loop: Session-Keepalive + Positions-Abgleich."""
    logger.info("IBKR-Forex-Monitor gestartet"
                if ibkr_client.configured() else
                "IBKR-Forex-Monitor inaktiv (IBKR_GATEWAY_URL nicht gesetzt)")
    while True:
        try:
            if not ibkr_client.configured():
                await asyncio.sleep(300)
                continue
            await ibkr_client.tickle()
            st = await ibkr_client.auth_status()
            if not st.get("authenticated"):
                logger.warning(f"IBKR: Gateway nicht authentifiziert "
                               f"({st.get('error') or 'IBeam re-authentifiziert automatisch'})")
                await asyncio.sleep(POLL_SEC)
                continue
            from core import state
            if state.db is not None:
                await sync_open_trades(state.db, manager)
        except Exception as e:
            logger.warning(f"IBKR-Monitor-Fehler: {e}")
        await asyncio.sleep(POLL_SEC)


async def status() -> Dict:
    """Status fürs Master-Panel (/api/ibkr/status)."""
    out = {"configured": ibkr_client.configured(),
           "gateway_url_set": bool(ibkr_client.gateway),
           "account_id": ibkr_client.account_id or None,
           "authenticated": None, "connected": None, "error": None}
    if not out["configured"]:
        out["error"] = "IBKR_GATEWAY_URL nicht gesetzt (siehe IBKR_SETUP_ANLEITUNG.md)"
        return out
    st = await ibkr_client.auth_status()
    out.update({"authenticated": st.get("authenticated"),
                "connected": st.get("connected"), "error": st.get("error")})
    if st.get("authenticated") and not ibkr_client.account_id:
        out["account_id"] = await ibkr_client.ensure_account()
    return out
