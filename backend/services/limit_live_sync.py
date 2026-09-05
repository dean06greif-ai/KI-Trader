"""Live-Sync für KI-Limit-Orders: echte Limit-Orders an der Börse (Bitunix).

Bisher waren die Key-Level-Limit-Orders (services/key_level_limits.py) rein
LOKAL: erst bei Preis-Berührung wurde eine Market-Order gesendet. Schnelle
Wicks konnten so nicht abgeholt werden. Dieses Modul spiegelt wartende
`ai_limit_orders` als ECHTE Limit-Orders (inkl. SL/TP) nach Bitunix:

  * Genug freies Kapital        -> Order sofort live an die Börse ("armed").
  * Kapital knapp ("scarce")    -> Order bleibt lokal und wird erst live
                                   gesetzt, wenn der Preis nahe genug am
                                   Limit ist (arm_dist_pct); entfernt er sich
                                   wieder (park_dist_pct) UND das Kapital ist
                                   weiterhin knapp, wird sie an der Börse
                                   storniert ("parked") – die lokale Order
                                   bleibt bestehen.
  * Fill an der Börse           -> das gespeicherte Signal läuft mit
                                   `_live_prefill` durch die NORMALE Pipeline
                                   (bitunix_trade._on_signal_impl bucht den
                                   bereits gefüllten Entry, setzt TP1/SL,
                                   Trade-Doc, Telegram). Lehnen Guards den
                                   Trade dennoch ab, wird die Börsen-Position
                                   sofort wieder geschlossen (Notfall-Close).
  * TEILFÜLLUNG                 -> wartet die Order nach der ersten Teilfüllung
                                   `partial_book_min` Minuten ohne Vollfill
                                   (bei knappem Kapital: sofort), wird der Rest
                                   storniert und der gefüllte Teil ANTEILIG als
                                   Position verbucht (Marge = qty×entry/Hebel
                                   über die normale Prefill-Pipeline) statt auf
                                   den Vollfill zu warten.
  * Lokal storniert/abgelaufen  -> Cleanup storniert die Börsen-Order; war
                                   sie inzwischen gefüllt, wird der Fill
                                   nachgebucht statt verworfen.

Paper-Modus und nicht-Bitunix-Instrumente bleiben unverändert lokal.
Konfiguration: settings['limit_live_sync'].
"""
import logging
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Dict, Optional, Tuple

logger = logging.getLogger(__name__)

DEFAULT_CONFIG = {
    "enabled": True,
    "arm_dist_pct": 1.5,          # scarce: live setzen, wenn Preis <= X% vom Limit
    "park_dist_pct": 3.5,         # scarce: an der Börse stornieren, wenn > X% entfernt
    "scarce_reserve_usdt": 25.0,  # so viel freies Kapital soll NACH dem Arm übrig bleiben
    "max_live_orders": 6,         # max. gleichzeitig echte Limit-Orders an der Börse
    "retry_min": 10,              # Wartezeit nach Börsen-Fehler, bevor erneut versucht wird
    "partial_book_min": 10,       # Teilfüllung: nach X min ohne Vollfill Rest stornieren
                                  # und den gefüllten Teil anteilig verbuchen (0 = aus)
}

INTERVAL_S = 10
_last_run = 0.0


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


async def get_config(db) -> Dict:
    cfg = dict(DEFAULT_CONFIG)
    try:
        doc = await db.settings.find_one({"_id": "limit_live_sync"}) or {}
        for k in DEFAULT_CONFIG:
            if k in doc:
                cfg[k] = doc[k]
    except Exception as e:
        logger.debug(f"limit_live_sync config: {e}")
    return cfg


def should_arm(free: Optional[float], margin: float, dist_pct: Optional[float],
               cfg: Dict) -> bool:
    """(rein, testbar) Live setzen? Immer, wenn genug Kapital frei bleibt;
    bei Knappheit nur nahe am Ziel und nur, wenn die Marge überhaupt passt."""
    if free is None or margin <= 0:
        return False
    scarce = (free - margin) < float(cfg.get("scarce_reserve_usdt", 25.0) or 0)
    if not scarce:
        return True
    if dist_pct is None:
        return False
    return (dist_pct <= float(cfg.get("arm_dist_pct", 1.5) or 0)
            and margin <= max(0.0, free - 2.0))


def should_park(free: Optional[float], dist_pct: Optional[float], cfg: Dict) -> bool:
    """(rein, testbar) Armierte Order wieder von der Börse nehmen?
    Nur wenn das Konto AKTUELL knapp ist UND der Preis weit weg ist."""
    if dist_pct is None:
        return False
    scarce = free is not None and free < float(cfg.get("scarce_reserve_usdt", 25.0) or 0)
    return scarce and dist_pct > float(cfg.get("park_dist_pct", 3.5) or 0)


def should_book_partial(filled_qty, planned_qty, partial_since_iso,
                        now_iso: str, free: Optional[float],
                        cfg: Dict) -> Tuple[bool, str]:
    """(rein, testbar) Teilgefüllte Börsen-Limit-Order anteilig als Position
    verbuchen statt auf den Vollfill zu warten?

    * Kapital knapp -> sofort (der Rest würde ohnehin nicht mehr finanzierbar
      sein; genau dieser Fall erzeugt die Teilfüllungen).
    * sonst nach `partial_book_min` Minuten seit der ersten Teilfüllung.
    """
    try:
        filled = float(filled_qty or 0)
        planned = float(planned_qty or 0)
    except (TypeError, ValueError):
        return False, ""
    if filled <= 0 or (planned > 0 and filled >= planned * 0.999):
        return False, ""
    if free is not None and float(free) < float(cfg.get("scarce_reserve_usdt", 25.0) or 0):
        return True, "Kapital knapp – gefüllter Teil wird sofort als Position verbucht"
    wait_min = float(cfg.get("partial_book_min", 10) or 0)
    if wait_min <= 0 or not partial_since_iso:
        return False, ""
    try:
        since = datetime.fromisoformat(str(partial_since_iso))
        now = datetime.fromisoformat(str(now_iso))
    except (TypeError, ValueError):
        return False, ""
    if now - since >= timedelta(minutes=wait_min):
        return True, f"Teilfüllung seit ≥{wait_min:g} min ohne Vollfill"
    return False, ""


def plan_size(autotrader, row: Dict, equity: Optional[float] = None) -> Optional[Dict]:
    """Marge/Hebel/Menge für die Börsen-Order planen – gleiche Kette wie die
    Entry-Pipeline (max_capital -> ai_max_capital -> ai_capital_pct ->
    ml_risk_scale bzw. Risiko-Sizing), damit Arm-Größe und Fill-Buchung passen."""
    from services.backtester import effective_leverage
    from services.bitunix_trade import ai_capital_base, ai_leverage_override
    sig = row.get("signal") or {}
    symbol = row["symbol"]
    try:
        entry = float(row["limit_price"])
    except (TypeError, ValueError, KeyError):
        return None
    if entry <= 0:
        return None
    cfg = autotrader.effective_cfg(symbol, "ai_trader")
    try:
        sl = float(sig.get("stop_loss") or 0)
    except (TypeError, ValueError):
        sl = 0.0
    lev = (effective_leverage(cfg, entry, sl)
           if cfg.get("auto_leverage_enabled") and sl > 0
           else float(cfg.get("leverage") or 1))
    try:
        lev = ai_leverage_override(cfg, lev, float(sig.get("ai_leverage") or 0))
    except (TypeError, ValueError):
        pass
    lev = max(1.0, min(lev, autotrader._coin_max_lev(symbol)))
    capital = float(cfg.get("max_capital") or 0)
    try:
        capital = ai_capital_base(capital, float(sig.get("ai_max_capital") or 0))
    except (TypeError, ValueError):
        pass
    try:
        pct = float(sig.get("ai_capital_pct") or 0)
        if 5.0 <= pct <= 100.0:
            capital = round(capital * pct / 100, 6)
    except (TypeError, ValueError):
        pass
    try:
        ml = float(sig.get("ml_risk_scale") or 0)
        if 0.1 <= ml < 1.0:
            capital = round(capital * ml, 6)
    except (TypeError, ValueError):
        pass
    sizing = sig.get("ai_sizing")
    if isinstance(sizing, dict) and sizing.get("mode") == "risk" and sl > 0:
        try:
            from services import position_sizing
            rs = position_sizing.compute(sizing, cfg, entry, sl, equity,
                                         coin_max_lev=autotrader._coin_max_lev(symbol))
            if rs:
                capital, lev = rs["margin"], rs["leverage"]
        except Exception as e:
            logger.debug(f"{symbol}: Risiko-Sizing für Limit-Arm übersprungen: {e}")
    if capital <= 0:
        return None
    qty = round(capital * lev / entry, 6)
    try:
        tp = float(sig.get("take_profit_full") or 0)
    except (TypeError, ValueError):
        tp = 0.0
    long_side = str(row.get("side")) == "LONG"
    sl_ok = sl > 0 and ((sl < entry) if long_side else (sl > entry))
    tp_ok = tp > 0 and ((tp > entry) if long_side else (tp < entry))
    return {"qty": qty, "leverage": lev, "margin": round(capital, 6),
            "sl": sl if sl_ok else None, "tp": tp if tp_ok else None}


async def _chat(db, text: str):
    try:
        await db.ai_chat.insert_one({"id": str(uuid.uuid4()), "role": "governance",
                                     "text": text, "ts": _iso(_now())})
    except Exception as e:
        logger.debug(f"limit_live_sync chat note failed: {e}")


def _eligible(autotrader, row: Dict) -> bool:
    from core import instruments as _instruments
    sig = row.get("signal") or {}
    if sig.get("data_collection") or sig.get("force_paper"):
        return False
    if not _instruments.is_tradable(row["symbol"]):
        return False
    return autotrader.effective_mode("ai_trader", row["symbol"]) == "live"


async def _arm(db, autotrader, row: Dict, plan: Dict) -> bool:
    """Echte Limit-Order an der Börse platzieren + am Row vermerken."""
    from services import entry_order_registry
    from services.bitunix_trade import _extract_order_id, make_client_id
    client = autotrader.client
    symbol, side = row["symbol"], row["side"]
    lp = float(row["limit_price"])
    cfg = autotrader.effective_cfg(symbol, "ai_trader")
    client_id = make_client_id("ai_trader")
    try:
        await client.set_leverage(symbol, max(int(round(plan["leverage"])), 1),
                                  cfg.get("margin_mode", "ISOLATION"))
        res = await client.place_order(
            symbol, "BUY" if side == "LONG" else "SELL", plan["qty"],
            order_type="LIMIT", price=lp, tp_price=plan.get("tp"),
            sl_price=plan.get("sl"), effect="GTC", client_id=client_id)
    except Exception as e:
        res = {"code": -1, "msg": str(e)[:160]}
    order_id = _extract_order_id(res)
    ok = isinstance(res, dict) and res.get("code") == 0 and order_id
    now = _now()
    if not ok:
        msg = (isinstance(res, dict) and (res.get("msg") or str(res))) or "?"
        logger.warning(f"Limit-Live-Arm fehlgeschlagen {symbol} {side} @ {lp}: {msg}")
        cfg_sync = await get_config(db)
        await db.ai_limit_orders.update_one(
            {"id": row["id"]},
            {"$set": {"live_error": str(msg)[:160],
                      "live_retry_at": _iso(now + timedelta(
                          minutes=float(cfg_sync.get("retry_min", 10) or 10)))}})
        return False
    try:
        sig = row.get("signal") or {}
        await entry_order_registry.register(
            db, order_id=order_id, symbol=symbol, side=side,
            qty=plan["qty"], price=lp,
            meta={"strategy_id": "ai_trader",
                  "strategy_name": sig.get("strategy_name") or "KI Trader",
                  "mode": "live", "leverage": round(plan["leverage"], 2),
                  "capital": plan["margin"], "sl": plan.get("sl"),
                  "tpf": plan.get("tp"), "client_id": client_id,
                  "horizon": row.get("horizon") or "scalp",
                  "signal_id": sig.get("id"),
                  "decision_id": row.get("decision_id")},
            kind="entry")
    except Exception as e:
        logger.debug(f"{symbol}: Registry-Eintrag für Limit-Arm fehlgeschlagen: {e}")
    await db.ai_limit_orders.update_one(
        {"id": row["id"], "status": "pending"},
        {"$set": {"live_order_id": str(order_id), "live_status": "live",
                  "live_qty": plan["qty"], "live_leverage": plan["leverage"],
                  "live_margin": plan["margin"], "live_client_id": client_id,
                  "live_armed_at": _iso(now)},
         "$unset": {"live_error": "", "live_retry_at": ""}})
    logger.info(f"Limit-Order LIVE an Börse: {side} {symbol} @ {lp} "
                f"(qty {plan['qty']}, {plan['leverage']:g}x, Marge ~{plan['margin']} USDT)")
    await _chat(db, f"📌 Limit-Order LIVE an Bitunix gesetzt: {side} {symbol} @ {lp} "
                    f"(Marge ~{plan['margin']} USDT, {plan['leverage']:g}x) – "
                    f"Wicks können jetzt direkt füllen")
    return True


async def _unarm(db, autotrader, row: Dict, reason: str) -> None:
    """Börsen-Order stornieren, lokale Order bleibt pending ("parked")."""
    from services import entry_order_registry
    client = autotrader.client
    try:
        await client.cancel_orders(row["symbol"], [row["live_order_id"]])
    except Exception as e:
        logger.warning(f"Limit-Live-Park cancel fehlgeschlagen {row['symbol']}: {e}")
        return
    try:
        await entry_order_registry.resolve(db, row["live_order_id"])
    except Exception:
        pass
    await db.ai_limit_orders.update_one(
        {"id": row["id"]},
        {"$set": {"live_status": "parked", "live_parked_at": _iso(_now())},
         "$unset": {"live_order_id": "", "live_qty": "", "live_client_id": ""}})
    logger.info(f"Limit-Order von Börse genommen (geparkt): {row['side']} "
                f"{row['symbol']} @ {row['limit_price']} – {reason}")
    await _chat(db, f"📥 Limit-Order geparkt (von Börse genommen): {row['side']} "
                    f"{row['symbol']} @ {row['limit_price']} – {reason}")


async def _book_fill(db, autotrader, row: Dict, qty: float, price: float) -> bool:
    """Börsen-Fill durch die normale Pipeline verbuchen (`_live_prefill`)."""
    from core.pipeline import emit_ai_signal
    now_iso = _iso(_now())
    sig = dict(row.get("signal") or {})
    sig.pop("_id", None)
    sig.pop("id", None)
    sig["ai_limit_fill"] = True
    sig["ai_limit_order_id"] = row["id"]
    sig["ai_limit_price"] = row.get("limit_price")
    sig["timestamp"] = now_iso
    sig["_live_prefill"] = {"order_id": row["live_order_id"], "qty": qty,
                            "price": price, "leverage": row.get("live_leverage")}
    try:
        from core.state import scanner
        sig["trade_date"] = scanner.berlin_date()
        now_b = scanner.berlin_now()
        sig["hour"], sig["weekday"] = now_b.hour, now_b.weekday()
        sig["session"] = scanner.get_current_session()
    except Exception as e:
        logger.debug(f"limit live fill time refresh failed: {e}")
    try:
        await emit_ai_signal(sig)
        opened = bool(sig.get("_trade_opened"))
        reject = sig.get("_reject_reason")
    except Exception as e:
        logger.error(f"Limit-Live-Fill {row['symbol']} Buchung fehlgeschlagen: {e}")
        opened, reject = False, str(e)[:160]
    upd = {"status": "filled" if opened else "rejected", "closed_at": _iso(_now()),
           "fill_price": float(price), "live_status": "filled" if opened else "orphan",
           "live_cleaned": True}
    if not opened:
        upd["reject_reason"] = str(reject or "Pipeline hat Fill abgelehnt")[:200]
    await db.ai_limit_orders.update_one({"id": row["id"]}, {"$set": upd})
    if opened:
        logger.info(f"Limit-Live-Order GEFÜLLT (Börse): {row['side']} "
                    f"{row['symbol']} @ {price}")
        try:
            from services import key_level_limits
            await key_level_limits._tg_notify(db, key_level_limits._tg_fill_text(row, True))
        except Exception as e:
            logger.debug(f"limit live fill notify failed: {e}")
        return True
    # Guards haben abgelehnt, aber die Position EXISTIERT an der Börse ->
    # sofort wieder schließen, sonst läuft sie unüberwacht.
    await _close_orphan(db, autotrader, row, qty, reject)
    return False


async def _book_partial(db, autotrader, row: Dict, reason: str) -> None:
    """Teilfüllung buchen: Rest-Order an der Börse stornieren, dann den
    tatsächlich gefüllten Teil anteilig über die normale Pipeline verbuchen.
    Erst NACH erfolgreichem Cancel wird gebucht – sonst droht Doppel-Buchung,
    falls der Rest während der Buchung noch füllt."""
    from services import entry_order_registry
    from services.bitunix_trade import parse_order_fill
    client = autotrader.client
    symbol = row["symbol"]
    try:
        await client.cancel_orders(symbol, [row["live_order_id"]])
    except Exception as e:
        logger.warning(f"Teilfill-Cancel {symbol} fehlgeschlagen (nächster Tick): {e}")
        return
    # Fill-Stand NACH dem Cancel maßgeblich (Rest kann bis dahin gefüllt haben)
    try:
        fi = parse_order_fill(await client.get_order_detail(row["live_order_id"]))
    except Exception as e:
        logger.debug(f"{symbol}: Teilfill-Status nach Cancel nicht abrufbar: {e}")
        fi = {"filled_qty": 0.0, "avg_price": 0.0}
    qty = float(fi.get("filled_qty") or 0) or float(row.get("live_partial_qty") or 0)
    try:
        await entry_order_registry.resolve(db, row["live_order_id"])
    except Exception:
        pass
    if qty <= 0:
        # Race: doch nichts gefüllt -> wie geparkt behandeln, lokal bleibt pending
        await db.ai_limit_orders.update_one(
            {"id": row["id"]},
            {"$set": {"live_status": "parked", "live_parked_at": _iso(_now())},
             "$unset": {"live_order_id": "", "live_qty": "", "live_client_id": "",
                        "live_partial_at": "", "live_partial_qty": ""}})
        return
    res = await db.ai_limit_orders.update_one(
        {"id": row["id"], "status": "pending"},
        {"$set": {"status": "filling", "partial_fill": True}})
    if not res.modified_count:
        return
    price = float(fi.get("avg_price") or 0) or float(row["limit_price"])
    planned = float(row.get("live_qty") or 0)
    logger.info(f"Limit-Order TEILFÜLLUNG wird verbucht: {row['side']} {symbol} "
                f"{qty:g}/{planned:g} @ {price} ({reason})")
    await _chat(db, f"🧩 Teilfüllung verbucht: {row['side']} {symbol} {qty:g}/{planned:g} "
                    f"@ {price} – Rest-Order storniert ({reason})")
    await _book_fill(db, autotrader, row, qty, price)


async def _close_orphan(db, autotrader, row: Dict, qty: float, reject) -> None:
    client = autotrader.client
    symbol, side = row["symbol"], row["side"]
    closed = False
    try:
        pid = await client.resolve_position_id(symbol, side)
        if pid:
            res = await client.flash_close(symbol, pid, side, qty, full=True)
            closed = isinstance(res, dict) and res.get("code") == 0
    except Exception as e:
        logger.error(f"Limit-Live-Orphan-Close {symbol} fehlgeschlagen: {e}")
    logger.error(f"Limit-Live-Fill {symbol} {side} abgelehnt ({reject}) – "
                 f"Notfall-Close {'OK' if closed else 'FEHLGESCHLAGEN'}")
    try:
        from core import state
        from services import notifications
        await notifications.telegram_notify(
            db, state.telegram, "limit_orders",
            f"⚠️ *LIMIT-ORDER (Börse) GEFÜLLT, aber Trade abgelehnt*\n"
            f"{symbol} {side} @ {row.get('limit_price')}: {str(reject)[:160]}\n"
            + ("Position wurde zur Sicherheit sofort geschlossen."
               if closed else
               "❗ NOTFALL-CLOSE FEHLGESCHLAGEN – bitte SOFORT in Bitunix prüfen!"))
    except Exception as e:
        logger.warning(f"orphan notify failed: {e}")


async def _cleanup_closed_rows(db, autotrader) -> None:
    """Lokal stornierte/abgelaufene Orders: Börsen-Order nachziehen.
    War sie inzwischen gefüllt, wird der Fill nachgebucht statt verworfen."""
    from services import entry_order_registry
    from services.bitunix_trade import parse_order_fill
    client = autotrader.client
    rows = await db.ai_limit_orders.find(
        {"status": {"$in": ["cancelled", "expired", "rejected"]},
         "live_order_id": {"$exists": True}, "live_cleaned": {"$ne": True}}
    ).to_list(20)
    for row in rows:
        try:
            fi = parse_order_fill(await client.get_order_detail(row["live_order_id"]))
        except Exception as e:
            logger.warning(f"cleanup order detail {row['symbol']}: {e}")
            continue
        if fi["filled_qty"] > 0:
            await _book_fill(db, autotrader, row,
                             fi["filled_qty"],
                             fi["avg_price"] or float(row["limit_price"]))
            continue
        if fi["status"] not in ("CANCELED", "CANCELLED", "FILLED", "EXPIRED"):
            try:
                await client.cancel_orders(row["symbol"], [row["live_order_id"]])
            except Exception as e:
                logger.warning(f"cleanup cancel {row['symbol']}: {e}")
                continue
        try:
            await entry_order_registry.resolve(db, row["live_order_id"])
        except Exception:
            pass
        await db.ai_limit_orders.update_one(
            {"id": row["id"]}, {"$set": {"live_cleaned": True,
                                         "live_status": "cancelled"}})
        logger.info(f"Limit-Live-Order an Börse storniert (lokal {row['status']}): "
                    f"{row['side']} {row['symbol']} @ {row['limit_price']}")


async def sync(db, autotrader, prices: Dict[str, float]) -> None:
    """Pro Scanner-Tick (gedrosselt): Fills prüfen, armieren, parken, aufräumen."""
    global _last_run
    now = time.time()
    if now - _last_run < INTERVAL_S:
        return
    _last_run = now
    client = getattr(autotrader, "client", None)
    if client is None or not client.configured() or db is None:
        return
    cfg = await get_config(db)
    if not cfg.get("enabled", True):
        return
    from core.state import control_state
    from services.bitunix_trade import parse_order_fill
    paused = bool(control_state.get("trades_paused"))

    await _cleanup_closed_rows(db, autotrader)

    rows = await db.ai_limit_orders.find({"status": "pending"}).to_list(50)
    if not rows:
        return
    fc = None
    try:
        fc = await autotrader.free_capital("live")
    except Exception as e:
        logger.debug(f"limit_live_sync free_capital: {e}")
    free = (fc or {}).get("free")
    armed_count = sum(1 for r in rows if r.get("live_order_id"))
    now_iso = _iso(_now())

    for row in rows:
        symbol = row["symbol"]
        try:
            lp = float(row["limit_price"])
        except (TypeError, ValueError):
            continue
        price = prices.get(symbol)
        dist_pct = (abs(float(price) - lp) / float(price) * 100) if price else None

        if row.get("live_order_id"):
            # ---- armiert: Fill / externe Stornierung / Parken prüfen ----
            try:
                fi = parse_order_fill(await client.get_order_detail(row["live_order_id"]))
            except Exception as e:
                logger.debug(f"{symbol}: Limit-Live-Status nicht abrufbar: {e}")
                continue
            if fi["status"] == "FILLED" or (
                    fi["status"] in ("CANCELED", "CANCELLED") and fi["filled_qty"] > 0):
                res = await db.ai_limit_orders.update_one(
                    {"id": row["id"], "status": "pending"},
                    {"$set": {"status": "filling"}})
                if res.modified_count:
                    await _book_fill(db, autotrader, row,
                                     fi["filled_qty"] or float(row.get("live_qty") or 0),
                                     fi["avg_price"] or lp)
                continue
            if fi["status"] in ("CANCELED", "CANCELLED", "EXPIRED"):
                # extern (z.B. manuell in Bitunix) storniert
                await db.ai_limit_orders.update_one(
                    {"id": row["id"], "status": "pending"},
                    {"$set": {"status": "cancelled", "live_cleaned": True,
                              "cancel_reason": "An der Börse storniert (extern)",
                              "closed_at": now_iso}})
                await _chat(db, f"🚫 Limit-Order {row['side']} {symbol} @ {lp} wurde "
                                f"direkt an der Börse storniert – lokal entfernt")
                continue
            if fi["filled_qty"] > 0:
                # ---- Teilfüllung: erfassen, ggf. anteilig verbuchen ----
                if not row.get("live_partial_at"):
                    row["live_partial_at"] = now_iso
                    await db.ai_limit_orders.update_one(
                        {"id": row["id"]},
                        {"$set": {"live_partial_at": now_iso,
                                  "live_partial_qty": fi["filled_qty"]}})
                    await _chat(db, f"⏳ Limit-Order TEILGEFÜLLT: {row['side']} {symbol} "
                                    f"@ {lp} – {fi['filled_qty']:g}/"
                                    f"{float(row.get('live_qty') or 0):g} gefüllt; ohne "
                                    f"Vollfill wird der Teil nach "
                                    f"{float(cfg.get('partial_book_min', 10) or 0):g} min "
                                    f"anteilig als Position verbucht")
                elif float(row.get("live_partial_qty") or 0) < fi["filled_qty"]:
                    await db.ai_limit_orders.update_one(
                        {"id": row["id"]},
                        {"$set": {"live_partial_qty": fi["filled_qty"]}})
                book, why = should_book_partial(
                    fi["filled_qty"], row.get("live_qty"),
                    row.get("live_partial_at"), now_iso, free, cfg)
                if paused and not book:
                    book, why = True, ("Master-Schalter 'Stop All Trades' – Rest "
                                       "storniert, gefüllter Teil wird verbucht")
                if book:
                    await _book_partial(db, autotrader, row, why)
                continue
            if paused or (fi["filled_qty"] == 0 and should_park(free, dist_pct, cfg)):
                reason = ("Master-Schalter 'Stop All Trades'" if paused else
                          f"Kapital knapp, Preis {dist_pct:.2f}% entfernt")
                await _unarm(db, autotrader, row, reason)
                armed_count -= 1
            continue

        # ---- lokal wartend: armieren? ----
        if paused or not _eligible(autotrader, row):
            continue
        if armed_count >= int(cfg.get("max_live_orders", 6) or 6):
            continue
        retry_at = row.get("live_retry_at")
        if retry_at and str(retry_at) > now_iso:
            continue
        plan = plan_size(autotrader, row, equity=(fc or {}).get("allocated"))
        if not plan:
            continue
        if should_arm(free, plan["margin"], dist_pct, cfg):
            if await _arm(db, autotrader, row, plan):
                armed_count += 1
                if free is not None:
                    free = max(0.0, free - plan["margin"])
