"""IBKR-Forex-Live-Trading – Brücke zwischen AutoTradeManager und IBKRClient.

Design (gleiche Buchhaltung und gleicher Ablauf wie Bitunix-Live-Trades):
  * Entry: bis zu ZWEI OCA-Brackets in einem Request (Market + SL(STP) + TP(LMT),
    alle GTC), analog zum Bitunix-Partial-TP1:
        Leg "tp1":    tp1_close_percent der Menge, TP = TP1
        Leg "runner": Restmenge,                   TP = voller TP (tpf)
    Jedes Leg ist ein eigenes IBKR-Bracket (Kinder = OCA-Gruppe): füllt TP1,
    storniert IBKR den zugehörigen SL automatisch; der Runner bleibt jederzeit
    durch seinen eigenen SL abgesichert. Danach zieht sync_open_trades() den
    Runner-SL auf Break-Even (Order-Modify) – exakt wie beim Bitunix-Flow.
  * SL-Anpassungen (Break-Even, Trailing, Gewinnsicherung) aus _manage_trade()
    laufen über move_stop() -> Order-Modify an der Börse.
  * Monitoring: run_loop() hält die Gateway-Session am Leben (tickle) und
    gleicht offene broker='ibkr'-Trades gegen die echten Orders/Positionen ab
    (TP1-Fill -> Teilverbuchung + BE; Position flat -> Close mit Fill-Preis).
  * Gebühren: services/fee_model.py (IBKR-Kommission + Mindestkommission je
    Order – bei zwei Legs zählt die Mindestkommission je Leg).
"""
import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

from services import fee_model
from services.ibkr_client import ibkr_client, base_qty_for_notional

logger = logging.getLogger(__name__)

POLL_SEC = 30
_summary_cache = {"ts": 0.0, "data": None}


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


def notional_usd(symbol: str, price: float, qty_base: float) -> float:
    """USD-Notional einer FX-Menge (Basis-Einheiten)."""
    s = str(symbol or "").upper()
    return float(qty_base) if s.startswith("USD") else float(price) * float(qty_base)


def plan_legs(qty: int, tp1_close_percent: float) -> List[Dict]:
    """Menge auf die OCA-Legs verteilen (rein, testbar).
    Ein einzelnes Leg, wenn TP1 deaktiviert (0/100 %) oder die Menge zu klein ist."""
    qty = int(qty or 0)
    try:
        pct = max(0.0, min(100.0, float(tp1_close_percent or 0)))
    except (TypeError, ValueError):
        pct = 0.0
    q1 = int(round(qty * pct / 100.0))
    if qty <= 1 or q1 <= 0 or q1 >= qty:
        return [{"leg": "runner", "qty": qty}]
    return [{"leg": "tp1", "qty": q1}, {"leg": "runner", "qty": qty - q1}]


def be_price(side: str, entry: float, fee_pct: float) -> float:
    """Echtes Break-Even inkl. Gebühren (gleiche Formel wie Bitunix-Flow)."""
    fee = float(fee_pct or 0) / 100.0
    if str(side).upper() == "LONG":
        return round(float(entry) * (1 + fee) / (1 - fee), 6)
    return round(float(entry) * (1 - fee) / (1 + fee), 6)


def parse_order_status(st: Dict) -> Tuple[str, float, Optional[float]]:
    """(status_lower, filled_qty, avg_price) aus /iserver/account/order/status."""
    if not isinstance(st, dict):
        return "", 0.0, None
    status = str(st.get("order_status") or st.get("status") or "").lower()
    filled = 0.0
    for k in ("cum_fill", "filled_quantity", "filledQuantity"):
        try:
            filled = float(st.get(k) or 0)
            if filled:
                break
        except (TypeError, ValueError):
            continue
    avg = None
    for k in ("average_price", "avg_fill_price", "avgPrice"):
        try:
            v = float(st.get(k) or 0)
            if v > 0:
                avg = v
                break
        except (TypeError, ValueError):
            continue
    return status, filled, avg


def _child_side(side: str) -> str:
    return "SELL" if str(side).upper() == "LONG" else "BUY"


async def open_live_forex(symbol: str, side: str, entry: float, sl: float,
                          tp: float, notional_usd: float, tp1: Optional[float] = None,
                          tp1_close_percent: float = 0.0) -> Dict:
    """Bracket-Orders (1-2 OCA-Legs) platzieren. Rückgabe: {ok, qty, fill_price,
    conid, legs, order_id, sl_order_id, tp_order_id, error}."""
    qty = base_qty_for_notional(symbol, notional_usd, entry)
    acct = await ibkr_client.ensure_account()
    if not acct:
        return {"ok": False, "error": "IBKR-Konto nicht ermittelbar (Gateway eingeloggt?)"}
    conid = await ibkr_client.forex_conid(symbol)
    if not conid:
        return {"ok": False, "error": f"Kein IBKR-Kontrakt (conid) für {symbol}"}
    legs = plan_legs(qty, tp1_close_percent if tp1 else 0)
    is_long = str(side).upper() == "LONG"
    stamp = int(time.time() * 1000) % 10_000_000_000
    orders: List[Dict] = []
    for leg in legs:
        tag = f"KIT-{symbol}-{stamp}-{leg['leg']}"
        leg["tp_price"] = float(tp1 if (leg["leg"] == "tp1" and tp1) else tp)
        leg["sl_price"] = float(sl)
        leg["ref"] = tag
        orders.append(ibkr_client.fx_order(acct, conid, "BUY" if is_long else "SELL",
                                           leg["qty"], "MKT", tag))
        if sl:
            orders.append(ibkr_client.fx_order(acct, conid, _child_side(side), leg["qty"],
                                               "STP", f"{tag}-SL", price=sl, parent=tag))
        if leg["tp_price"]:
            orders.append(ibkr_client.fx_order(acct, conid, _child_side(side), leg["qty"],
                                               "LMT", f"{tag}-TP", price=leg["tp_price"],
                                               parent=tag))
    res = await ibkr_client.place_orders(orders)
    if not res.get("ok"):
        return res
    by_ref = res.get("by_ref") or {}
    for leg in legs:
        ref = leg.pop("ref")
        leg["order_id"] = by_ref.get(ref)
        leg["sl_order_id"] = by_ref.get(f"{ref}-SL")
        leg["tp_order_id"] = by_ref.get(f"{ref}-TP")
        leg["closed"] = False
    fill = None
    try:
        if legs[0].get("order_id"):
            fill = await ibkr_client.wait_fill(legs[0]["order_id"])
    except Exception as e:
        logger.debug(f"IBKR {symbol}: Fill-Abfrage fehlgeschlagen: {e}")
    runner = legs[-1]
    return {"ok": True, "qty": qty, "fill_price": fill, "conid": conid, "legs": legs,
            "order_id": legs[0].get("order_id"), "sl_order_id": runner.get("sl_order_id"),
            "tp_order_id": runner.get("tp_order_id")}


def _open_legs(trade: Dict) -> List[Dict]:
    legs = trade.get("ibkr_legs")
    if isinstance(legs, list) and legs:
        return [l for l in legs if isinstance(l, dict) and not l.get("closed")]
    # Rückwärtskompatibel: Trades aus v1 (ein Bracket ohne Legs-Liste)
    return [{"leg": "runner", "qty": float(trade.get("qty_remaining") or trade.get("qty") or 0),
             "sl_order_id": trade.get("ibkr_sl_order_id"),
             "tp_order_id": trade.get("ibkr_tp_order_id"), "closed": False}]


async def move_stop(trade: Dict, new_sl: float) -> Dict:
    """SL aller offenen Legs an der Börse auf new_sl ändern (Order-Modify)."""
    acct = await ibkr_client.ensure_account()
    conid = trade.get("ibkr_conid") or await ibkr_client.forex_conid(trade["symbol"])
    if not acct or not conid:
        return {"ok": False, "error": "Konto/Kontrakt nicht verfügbar"}
    errors = []
    for leg in _open_legs(trade):
        oid = leg.get("sl_order_id")
        if not oid:
            errors.append(f"{leg.get('leg')}: keine SL-Order-ID")
            continue
        body = ibkr_client.fx_order(acct, conid, _child_side(trade["side"]),
                                    int(round(float(leg.get("qty") or 0))), "STP",
                                    f"KIT-MOD-{oid}", price=new_sl)
        res = await ibkr_client.modify_order(str(oid), body)
        if not res.get("ok"):
            errors.append(f"{leg.get('leg')}: {res.get('error')}")
        else:
            leg["sl_price"] = float(new_sl)
    if errors:
        return {"ok": False, "error": "; ".join(errors)[:200]}
    try:
        from core import state
        if state.db is not None and isinstance(trade.get("ibkr_legs"), list):
            await state.db.auto_trades.update_one(
                {"id": trade["id"]}, {"$set": {"ibkr_legs": trade["ibkr_legs"]}})
    except Exception as e:
        logger.debug(f"IBKR move_stop persist: {e}")
    return {"ok": True}


async def close_live_forex(trade: Dict) -> Dict:
    """Manueller/KI-Close: offene Bracket-Kinder stornieren, echte Restposition
    lesen und exakt diese per Gegen-Market-Order schließen (kein Überschließen).
    Rückgabe: {ok, flat, order_id, exit_price, error}."""
    for leg in _open_legs(trade):
        for key in ("sl_order_id", "tp_order_id"):
            oid = leg.get(key)
            if oid:
                try:
                    await ibkr_client.cancel_order(str(oid))
                except Exception as e:
                    logger.debug(f"IBKR cancel {key}: {e}")
    await asyncio.sleep(1.0)
    conid = trade.get("ibkr_conid") or await ibkr_client.forex_conid(trade["symbol"])
    pos = await ibkr_client.position_qty(conid) if conid else None
    qty = abs(pos) if pos is not None else float(trade.get("qty_remaining") or trade.get("qty") or 0)
    if qty < 1:
        return {"ok": True, "flat": True, "detail": "Position an der Börse bereits flat"}
    res = await ibkr_client.close_forex_position(trade["symbol"], trade["side"], int(round(qty)))
    if not res.get("ok"):
        return res
    fill = None
    try:
        fill = await ibkr_client.wait_fill(res["order_id"], timeout=8.0)
    except Exception:
        pass
    return {"ok": True, "flat": False, "order_id": res["order_id"], "exit_price": fill}


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


def _exit_fee(t: Dict, exit_price: float, qty: float) -> float:
    fee_pct = float(t.get("fee_percent") or fee_model.DEFAULT_FOREX_COMMISSION_PCT)
    return round(notional_usd(t["symbol"], exit_price, qty) * fee_pct / 100.0, 6)


async def _book_partial(db, t: Dict, leg: Dict, exit_price: float, via: str,
                        manager=None) -> Dict:
    """TP1-Leg an der Börse geschlossen (TP1- oder SL-Fill): Teilverbuchung +
    ggf. Break-Even für den Runner (wie Bitunix-Flow)."""
    qty = float(leg.get("qty") or 0)
    gross = pnl_usd(t["symbol"], t["side"], float(t["entry"]), exit_price, qty)
    fee = _exit_fee(t, exit_price, qty)
    realized = round(float(t.get("realized_pnl") or 0) + gross - fee, 6)
    qty_rem = round(float(t.get("qty_remaining", t["qty"]) or 0) - qty, 6)
    now = datetime.now(timezone.utc).isoformat()
    events = list(t.get("events", []))
    legs = t.get("ibkr_legs") or []
    for l in legs:
        if l.get("leg") == leg.get("leg"):
            l["closed"] = True
            l["exit_price"] = exit_price
    updates = {"realized_pnl": realized, "fees_paid": round(float(t.get("fees_paid") or 0) + fee, 6),
               "qty_remaining": max(qty_rem, 0), "ibkr_legs": legs}
    if via == "tp":
        updates["tp1_hit"] = True
        events.append(f"TP1 hit @ {exit_price} closed {t.get('tp1_close_percent')}% "
                      f"(IBKR-Fill, Fee {fee})")
        be_mode = t.get("be_mode") or ("tp1" if t.get("breakeven_enabled") else "off")
        if be_mode in ("tp1", "smart") and t.get("breakeven_enabled") is not False \
                and not t.get("breakeven_moved") and qty_rem > 0:
            be = be_price(t["side"], float(t["entry"]), float(t.get("fee_percent") or 0))
            updates["sl"] = be
            updates["breakeven_moved"] = True
            events.append(f"SL -> Break-Even @ {be} ({be_mode})")
            mv = await move_stop({**t, **updates}, be)
            events.append("Exchange SL -> BE synced" if mv.get("ok")
                          else f"Exchange SL move FAILED ({mv.get('error')})")
    else:
        events.append(f"TP1-Leg per STOP geschlossen @ {exit_price} (IBKR-Fill, Fee {fee})")
    updates["events"] = events[-20:]
    await db.auto_trades.update_one({"id": t["id"]}, {"$set": updates})
    logger.info(f"IBKR-Sync: {t['symbol']} TP1-Leg ({via}) @ {exit_price} -> PnL {realized}")
    t.update(updates)
    return updates


async def _book_close(db, t: Dict, exit_price: float, manager=None) -> None:
    qty_rem = float(t.get("qty_remaining", t.get("qty")) or 0)
    gross = pnl_usd(t["symbol"], t["side"], float(t.get("entry") or 0), exit_price, qty_rem)
    exit_fee = _exit_fee(t, exit_price, qty_rem)
    fees_total = round(float(t.get("fees_paid") or 0) + exit_fee, 6)
    realized = round(float(t.get("realized_pnl") or 0) + gross - exit_fee, 6)
    result = "win" if realized > 1e-6 else ("loss" if realized < -1e-6 else "breakeven")
    closed_at = datetime.now(timezone.utc).isoformat()
    legs = t.get("ibkr_legs") or []
    for l in legs:
        l["closed"] = True
    updates = {"status": "closed", "exit_price": exit_price, "result": result,
               "realized_pnl": realized, "qty_remaining": 0,
               "fees_paid": fees_total, "closed_by": "ibkr_sync",
               "closed_at": closed_at, "ibkr_legs": legs,
               "events": (t.get("events", []) + [
                   f"IBKR-Abgleich: Position geschlossen @ {exit_price} (Fee {exit_fee})"])[-20:]}
    if manager is not None and hasattr(manager, "_finalize_close"):
        if not await manager._finalize_close(t["id"], updates):
            return None
    else:
        await db.auto_trades.update_one({"id": t["id"]}, {"$set": updates})
    logger.info(f"IBKR-Sync: {t['symbol']} {t['side']} geschlossen -> "
                f"PnL {realized} USD @ {exit_price}")
    if manager is not None:
        try:
            await manager._after_close({**t, **updates})
        except Exception as e:
            logger.debug(f"IBKR _after_close: {e}")


async def _check_tp1_leg(db, t: Dict, manager=None) -> bool:
    """TP1-Leg an der Börse gefüllt (TP oder SL)? Dann Teilverbuchung."""
    leg = next((l for l in _open_legs(t) if l.get("leg") == "tp1"), None)
    if not leg or t.get("tp1_hit"):
        return False
    for key, via in (("tp_order_id", "tp"), ("sl_order_id", "sl")):
        oid = leg.get(key)
        if not oid:
            continue
        status, filled, avg = parse_order_status(await ibkr_client.get_order_status(str(oid)))
        if status == "filled" or (filled > 0 and filled >= float(leg.get("qty") or 0)):
            await _book_partial(db, t, leg, avg or float(leg.get("tp_price" if via == "tp"
                                                                 else "sl_price") or 0),
                                via, manager)
            return True
    return False


async def sync_open_trades(db, manager=None) -> int:
    """Offene IBKR-Live-Trades gegen echte Orders/Positionen abgleichen."""
    open_trades = await db.auto_trades.find(
        {"status": "open", "mode": "live", "broker": "ibkr"}).to_list(50)
    if not open_trades:
        return 0
    positions = await ibkr_client.get_positions()
    pos_qty: Dict[str, float] = {}
    for p in positions or []:
        if isinstance(p, dict):
            try:
                pos_qty[str(p.get("conid"))] = abs(float(p.get("position") or 0))
            except (TypeError, ValueError):
                continue
    trades_hist = None
    synced = 0
    for t in open_trades:
        conid = str(t.get("ibkr_conid") or "")
        if not conid:
            continue
        try:
            if await _check_tp1_leg(db, t, manager):
                synced += 1
        except Exception as e:
            logger.warning(f"IBKR-Sync TP1 {t['symbol']}: {e}")
        if pos_qty.get(conid, 0.0) >= 1:
            continue
        if trades_hist is None:
            trades_hist = await ibkr_client.get_trades()
        exit_price = _exit_from_trades(trades_hist, conid, t["side"])
        if not exit_price:
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


class GatewayWatch:
    """Zustandsautomat für den Gateway-Alarm (rein, testbar).

    Alarm-Politik (bewusst sparsam, User-Wunsch 06/2026 – weniger Meldungen):
    * Ein Ausfall (Logout ODER readyz rot > RED_AFTER_SEC) erzeugt genau EINEN
      Alarm; solange der Ausfall anhält, folgt nichts weiter.
    * Entwarnung genau einmal, sobald das Gateway wieder da ist.
    * Ein NEUER Alarm wird erst scharf, nachdem das Gateway mindestens
      STABLE_OK_SEC am Stück gesund war – Flattern (rot/grün im Wechsel)
      erzeugt so keine Nachrichtenflut.
    * Logout- und readyz-Alarm überlappen nicht: während der Logout-Alarm
      aktiv ist, wird kein zusätzlicher readyz-Alarm gesendet."""
    RED_AFTER_SEC = 300
    STABLE_OK_SEC = 600

    def __init__(self):
        self.auth = None
        self.red_since = None
        self.red_alerted = False
        self.logout_alerted = False
        self.rearm_pending = False   # Entwarnung gesendet, Alarm noch nicht wieder scharf
        self.ok_since = None         # seit wann readyz durchgehend grün

    def evaluate(self, now: float, authenticated: Optional[bool],
                 readyz: Optional[int]) -> List[str]:
        events: List[str] = []
        if authenticated is not None:
            if self.auth is True and not authenticated:
                if not self.logout_alerted and not self.rearm_pending:
                    events.append("logout")
                    self.logout_alerted = True
            elif authenticated and self.logout_alerted:
                events.append("login")
                self.logout_alerted = False
                self.rearm_pending = True
            self.auth = bool(authenticated)
        if readyz == 200:
            if self.ok_since is None:
                self.ok_since = now
            self.red_since = None
            if self.red_alerted:
                events.append("readyz_green")
                self.red_alerted = False
                self.rearm_pending = True
            if (self.rearm_pending and not self.logout_alerted
                    and now - self.ok_since >= self.STABLE_OK_SEC):
                self.rearm_pending = False
        else:
            self.ok_since = None
            if self.red_since is None:
                self.red_since = now
            elif (not self.red_alerted and not self.rearm_pending
                  and not self.logout_alerted
                  and now - self.red_since >= self.RED_AFTER_SEC):
                events.append("readyz_red")
                self.red_alerted = True
        return events


gateway_watch = GatewayWatch()

_GW_TEXT = {
    "logout": ("⚠️ *IBKR-GATEWAY AUSGELOGGT*\n\nIBeam ist nicht mehr bei IBKR authentifiziert. "
               "Forex-Live-Orders sind bis zum Re-Login nicht möglich (offene Brackets "
               "bleiben an der Börse aktiv).\n`{detail}`", "IBKR-Gateway ausgeloggt"),
    "login": ("✅ *IBKR-GATEWAY WIEDER EINGELOGGT*\n\nForex-Live ist wieder handelbar.",
              "IBKR-Gateway wieder eingeloggt"),
    "readyz_red": ("🔴 *IBKR-GATEWAY NICHT BEREIT*\n\n`/readyz` ist seit über 5 Minuten rot "
                   "({readyz}) – IBeam ist nicht eingeloggt oder hängt. Bitte Render-Logs "
                   "des IBeam-Service prüfen (2FA/Passwort).\n`{detail}`",
                   "IBKR-Gateway seit 5 Min nicht bereit"),
    "readyz_green": ("🟢 *IBKR-GATEWAY WIEDER BEREIT*\n\n`/readyz` liefert wieder OK.",
                     "IBKR-Gateway wieder bereit"),
}


async def _gateway_alerts(events: List[str], detail: str, readyz) -> None:
    """Telegram (Toggle 'ibkr_gateway') + Website-Glocke für Gateway-Ereignisse."""
    if not events:
        return
    from core import state
    from services.notifications import telegram_notify, website_notify
    for ev in events:
        tmpl, title = _GW_TEXT[ev]
        text = tmpl.format(detail=(detail or "-")[:200], readyz=readyz if readyz else "keine Antwort")
        logger.warning(f"IBKR-Gateway-Alarm: {title}")
        try:
            if state.db is not None:
                await telegram_notify(state.db, getattr(state, "telegram", None),
                                      "ibkr_gateway", text)
                await website_notify(state.db, "ibkr_gateway", title,
                                     text.replace("*", "").replace("`", ""),
                                     cooldown_min=15, source="IBKR-Monitor")
        except Exception as e:
            logger.debug(f"IBKR-Gateway-Alarm senden fehlgeschlagen: {e}")


async def run_loop(manager=None):
    """Hintergrund-Loop: Session-Keepalive + Gateway-Alarm + Orders-/Positions-Abgleich."""
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
            probe = await ibkr_client.probe()
            events = gateway_watch.evaluate(time.time(), st.get("authenticated"),
                                            probe.get("readyz"))
            await _gateway_alerts(events, st.get("error") or ibkr_client.last_diag or "",
                                  probe.get("readyz"))
            if not st.get("authenticated"):
                logger.warning(f"IBKR: Gateway nicht authentifiziert "
                               f"({st.get('error') or 'IBeam re-authentifiziert automatisch'})")
                await asyncio.sleep(60)
                continue
            from core import state
            if state.db is not None:
                await sync_open_trades(state.db, manager)
        except Exception as e:
            logger.warning(f"IBKR-Monitor-Fehler: {e}")
        await asyncio.sleep(POLL_SEC)


async def account_summary(max_age: float = 15.0) -> Dict:
    """Kontostand (gecacht) fürs Header-Badge / /api/autotrade/balance."""
    now = time.time()
    if _summary_cache["data"] is not None and now - _summary_cache["ts"] < max_age:
        return _summary_cache["data"]
    out = {"configured": ibkr_client.configured(), "authenticated": None,
           "net_liquidation": None, "available_funds": None, "currency": "USD",
           "account_id": ibkr_client.account_id or None, "error": None}
    if out["configured"]:
        ts, ok = ibkr_client.last_auth
        if ok is None or now - ts > 120:
            ok = (await ibkr_client.auth_status()).get("authenticated")
        out["authenticated"] = bool(ok)
        if ok:
            s = await ibkr_client.account_summary()
            out.update({k: s.get(k) for k in ("net_liquidation", "available_funds",
                                              "currency", "account_id") if k in s})
            out["error"] = s.get("error")
        else:
            out["error"] = ibkr_client.last_diag or "Gateway nicht eingeloggt"
    _summary_cache.update(ts=now, data=out)
    return out


async def status() -> Dict:
    """Status fürs Master-Panel (/api/ibkr/status) inkl. Diagnose-Hinweis."""
    out = {"configured": ibkr_client.configured(),
           "gateway_url_set": bool(ibkr_client.gateway),
           "token_set": bool(ibkr_client.token),
           "account_id": ibkr_client.account_id or None,
           "authenticated": None, "connected": None, "error": None,
           "hint": None, "probe": None}
    if not out["configured"]:
        out["error"] = "IBKR_GATEWAY_URL nicht gesetzt (siehe IBKR_SETUP_ANLEITUNG.md)"
        return out
    st = await ibkr_client.auth_status()
    out.update({"authenticated": st.get("authenticated"),
                "connected": st.get("connected"), "error": st.get("error")})
    out["hint"] = ibkr_client.last_diag
    try:
        out["probe"] = await ibkr_client.probe()
        if out["probe"].get("readyz") == 503 and not out["hint"]:
            out["hint"] = ("IBeam läuft, ist aber NICHT bei IBKR eingeloggt (readyz 503) – "
                           "IBeam-Logs auf Render prüfen: meist 2FA-Abfrage oder falsches Passwort.")
    except Exception:
        pass
    if st.get("authenticated") and not ibkr_client.account_id:
        out["account_id"] = await ibkr_client.ensure_account()
    return out
