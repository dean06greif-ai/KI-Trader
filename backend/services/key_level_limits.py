"""KI-Limit-Orders an Key-Levels (Order-Blocks / POC / Range-Grenzen).

Die KI wählt pro Entscheidung selbst die Entry-Art: entry_type 'market'
(sofort, wie bisher) oder 'limit' – dann wartet der Entry als LOKALE
(synthetische) Limit-Order am Key-Level in der Collection `ai_limit_orders`.
Erreicht der Preis das Level, läuft das gespeicherte Signal durch die ganz
NORMALE Pipeline (Guards, Kapital-Limits, Live/Paper-Modus) und füllt zum
Limit-Preis. Die Gültigkeitsdauer (limit_valid_min) bestimmt die KI selbst;
danach verfällt die Order automatisch. Jeder Analyse-Zyklus bewertet wartende
Orders neu: eine neue Limit-Entscheidung ersetzt die alte (Symbol+Richtung),
cancel_limit=true storniert, Gegenrichtungs-Signale räumen auf. Die KI sieht
alle wartenden Orders im Prompt-Kontext (WARTENDE LIMIT-ORDERS).

Bewusst lokal statt an der Börse platziert: identisches Verhalten für Paper
und Live, kein gebundenes Börsen-Margin und keine verwaisten Exchange-Orders
(vgl. Bug-Historie in services/entry_order_registry.py). Beim Fill entsteht
eine Market-Order am Level – im Paper-Modus rechnet services/paper_execution.py
dieselben Spread-/Slippage-Kosten an wie live.
"""
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

MIN_VALID_MIN = 5
MAX_VALID_MIN = 720
DEFAULT_VALID_MIN = 60
MAX_DIST_PCT_SCALP = 2.5
MAX_DIST_PCT_SWING = 8.0
MIN_DIST_PCT = 0.05
MAX_PENDING_TOTAL = 12
HISTORY_DAYS = 14


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def parse_decision_fields(d: Dict) -> Dict:
    """Limit-Felder einer KI-Entscheidung robust normalisieren (JSON darf fehlen)."""
    et = "limit" if str(d.get("entry_type") or "").strip().lower() == "limit" else "market"
    try:
        lp = float(d.get("limit_price") or 0)
    except (TypeError, ValueError):
        lp = 0.0
    try:
        vm = int(float(d.get("limit_valid_min") or 0))
    except (TypeError, ValueError):
        vm = 0
    vm = max(MIN_VALID_MIN, min(MAX_VALID_MIN, vm)) if vm > 0 else DEFAULT_VALID_MIN
    return {"entry_type": et, "limit_price": lp if lp > 0 else None,
            "limit_valid_min": vm, "cancel_limit": bool(d.get("cancel_limit"))}


def validate(action: str, limit_price, cur_price: float, horizon: Optional[str] = None):
    """(ok, grund): Limit muss auf der richtigen Seite und in Reichweite liegen."""
    try:
        lp = float(limit_price or 0)
    except (TypeError, ValueError):
        lp = 0.0
    if lp <= 0:
        return False, "limit_price fehlt/ungültig"
    if not cur_price or cur_price <= 0:
        return False, "kein aktueller Preis"
    if action == "LONG" and lp >= cur_price:
        return False, "LONG-Limit muss UNTER dem aktuellen Preis liegen"
    if action == "SHORT" and lp <= cur_price:
        return False, "SHORT-Limit muss ÜBER dem aktuellen Preis liegen"
    dist = abs(cur_price - lp) / cur_price * 100
    max_d = MAX_DIST_PCT_SWING if str(horizon or "") == "swing" else MAX_DIST_PCT_SCALP
    if dist > max_d:
        return False, f"Level zu weit entfernt ({dist:.2f}% > {max_d:g}%)"
    if dist < MIN_DIST_PCT:
        return False, f"Level zu nah am Preis ({dist:.3f}%) – Market sinnvoller"
    return True, ""


async def _chat(db, text: str):
    try:
        await db.ai_chat.insert_one({"id": str(uuid.uuid4()), "role": "governance",
                                     "text": text, "ts": _iso(_now())})
    except Exception as e:
        logger.debug(f"limit order chat note failed: {e}")


async def _cancel_rows(db, rows: List[Dict], reason: str) -> int:
    n = 0
    for row in rows:
        res = await db.ai_limit_orders.update_one(
            {"id": row["id"], "status": "pending"},
            {"$set": {"status": "cancelled", "cancel_reason": str(reason)[:160],
                      "closed_at": _iso(_now())}})
        if res.modified_count:
            n += 1
            logger.info(f"KI-Limit-Order storniert {row['symbol']} {row['side']} "
                        f"@ {row['limit_price']}: {reason}")
    return n


async def place(db, signal: Dict, dec: Dict) -> Optional[str]:
    """Wartende Limit-Order speichern; ersetzt eine bestehende (Symbol+Richtung)."""
    symbol, side = signal["symbol"], signal["type"]
    limit_price = float(signal["entry_price"])
    valid_min = int(dec.get("limit_valid_min") or DEFAULT_VALID_MIN)
    valid_min = max(MIN_VALID_MIN, min(MAX_VALID_MIN, valid_min))
    pending = await db.ai_limit_orders.find(
        {"status": "pending"}, {"_id": 0, "signal": 0}).to_list(MAX_PENDING_TOTAL * 2)
    same = [r for r in pending if r["symbol"] == symbol and r["side"] == side]
    if same:
        await _cancel_rows(db, same, "Ersetzt durch neue Analyse (Neu-Bewertung)")
    elif len(pending) >= MAX_PENDING_TOTAL:
        logger.info(f"KI-Limit-Order {symbol} {side} abgelehnt: "
                    f"Maximum von {MAX_PENDING_TOTAL} wartenden Orders erreicht")
        return None
    now = _now()
    sig = dict(signal)
    sig.pop("_id", None)
    doc = {
        "id": str(uuid.uuid4()), "symbol": symbol, "side": side,
        "limit_price": limit_price,
        "ref_price": float(dec.get("price") or 0),
        "dist_pct": round(abs(float(dec.get("price") or limit_price) - limit_price)
                          / float(dec.get("price") or limit_price) * 100, 3),
        "valid_min": valid_min,
        "created_at": _iso(now), "expires_at": _iso(now + timedelta(minutes=valid_min)),
        "status": "pending",
        "confidence": dec.get("confidence"),
        "horizon": dec.get("horizon"),
        "setup": dec.get("setup"),
        "reason": str(dec.get("levels_reason") or dec.get("reasoning") or "")[:200],
        "decision_id": dec.get("id"),
        "signal": sig,
    }
    await db.ai_limit_orders.insert_one(dict(doc))
    logger.info(f"KI-Limit-Order platziert: {side} {symbol} @ {limit_price} "
                f"(gültig {valid_min}min, {doc['dist_pct']}% vom Preis)")
    await _chat(db, f"⏳ Limit-Order platziert: {side} {symbol} @ {limit_price} "
                    f"({doc['dist_pct']}% vom Preis entfernt, gültig {valid_min} Min) – "
                    f"{doc['reason'] or 'Key-Level-Entry'}")
    return doc["id"]


async def check_fills(db, prices: Dict[str, float]) -> Dict:
    """Pro Scanner-Tick: Abläufe verfallen lassen, Berührungen zum Limit füllen."""
    rows = await db.ai_limit_orders.find({"status": "pending"}).to_list(50)
    if not rows:
        return {"filled": 0, "expired": 0}
    now_iso = _iso(_now())
    filled = expired = 0
    for row in rows:
        if str(row.get("expires_at") or "") < now_iso:
            res = await db.ai_limit_orders.update_one(
                {"id": row["id"], "status": "pending"},
                {"$set": {"status": "expired", "closed_at": now_iso}})
            if res.modified_count:
                expired += 1
                logger.info(f"KI-Limit-Order abgelaufen: {row['side']} {row['symbol']} "
                            f"@ {row['limit_price']}")
                await _chat(db, f"⌛ Limit-Order abgelaufen (nicht gefüllt): "
                                f"{row['side']} {row['symbol']} @ {row['limit_price']} – "
                                f"wird im nächsten Zyklus neu bewertet")
            continue
        price = prices.get(row["symbol"])
        if not price:
            continue
        lp = float(row["limit_price"])
        touched = (price <= lp) if row["side"] == "LONG" else (price >= lp)
        if not touched:
            continue
        # Atomar pending -> filling, damit kein doppelter Fill möglich ist
        res = await db.ai_limit_orders.update_one(
            {"id": row["id"], "status": "pending"}, {"$set": {"status": "filling"}})
        if not res.modified_count:
            continue
        sig = dict(row.get("signal") or {})
        sig.pop("_id", None)
        sig.pop("id", None)
        sig["ai_limit_fill"] = True
        sig["ai_limit_order_id"] = row["id"]
        sig["timestamp"] = now_iso
        try:
            from core.state import scanner
            sig["trade_date"] = scanner.berlin_date()
            now_b = scanner.berlin_now()
            sig["hour"], sig["weekday"] = now_b.hour, now_b.weekday()
            sig["session"] = scanner.get_current_session()
        except Exception as e:
            logger.debug(f"limit fill time refresh failed: {e}")
        try:
            from core.pipeline import emit_ai_signal
            await emit_ai_signal(sig)
            opened = bool(sig.get("_trade_opened"))
            reject = sig.get("_reject_reason")
        except Exception as e:
            logger.error(f"KI-Limit-Order-Fill {row['symbol']} fehlgeschlagen: {e}")
            opened, reject = False, str(e)[:160]
        upd = {"status": "filled" if opened else "rejected",
               "closed_at": _iso(_now()), "fill_price": lp,
               "touch_price": float(price)}
        if not opened:
            upd["reject_reason"] = str(reject or "Guards/Kapital haben abgelehnt")[:200]
        await db.ai_limit_orders.update_one({"id": row["id"]}, {"$set": upd})
        if opened:
            filled += 1
            logger.info(f"KI-Limit-Order GEFÜLLT: {row['side']} {row['symbol']} @ {lp}")
        else:
            logger.info(f"KI-Limit-Order bei Fill abgelehnt: {row['side']} "
                        f"{row['symbol']} @ {lp}: {reject}")
            await _chat(db, f"⚠️ Limit-Order {row['side']} {row['symbol']} @ {lp} "
                            f"erreicht, aber Trade abgelehnt: {reject}")
    # History-Hygiene (Render/Mongo klein halten)
    try:
        cutoff = _iso(_now() - timedelta(days=HISTORY_DAYS))
        await db.ai_limit_orders.delete_many(
            {"status": {"$ne": "pending"}, "created_at": {"$lt": cutoff}})
    except Exception as e:
        logger.debug(f"limit order cleanup failed: {e}")
    return {"filled": filled, "expired": expired}


async def reevaluate(db, symbol: str, dec: Dict) -> int:
    """Neu-Bewertung je Analyse-Zyklus: stornieren, was nicht mehr passt.

    - cancel_limit=true            -> alle wartenden Orders des Symbols weg
    - Gegenrichtungs-Entscheidung  -> Orders der Gegenseite weg
    - gleicher Richtung als Market bereits ERÖFFNET -> Limit-Order obsolet
    (Ersetzen gleicher Richtung übernimmt place() automatisch.)
    """
    rows = await db.ai_limit_orders.find(
        {"symbol": symbol, "status": "pending"}, {"_id": 0, "signal": 0}).to_list(10)
    if not rows:
        return 0
    if dec.get("cancel_limit"):
        return await _cancel_rows(db, rows, "KI: cancel_limit (Neu-Bewertung)")
    action = str(dec.get("action") or "HOLD")
    n = 0
    if action in ("LONG", "SHORT"):
        opposite = "SHORT" if action == "LONG" else "LONG"
        opp = [r for r in rows if r["side"] == opposite]
        if opp:
            n += await _cancel_rows(db, opp, f"KI signalisiert Gegenrichtung ({action})")
        if dec.get("signaled") and str(dec.get("entry_type") or "market") == "market":
            same = [r for r in rows if r["side"] == action]
            if same:
                n += await _cancel_rows(db, same, "Durch sofortigen Market-Entry ersetzt")
    return n


async def pending_context(db) -> str:
    """Prompt-Block für die KI: alle wartenden Limit-Orders + Steuerungshinweis."""
    rows = await db.ai_limit_orders.find(
        {"status": "pending"}, {"_id": 0, "signal": 0}).sort("created_at", 1).to_list(20)
    if not rows:
        return ""
    lines = ["=== WARTENDE LIMIT-ORDERS (Key-Level-Entries) ==="]
    for r in rows:
        lines.append(
            f"- {r['side']} {r['symbol']} LIMIT @ {r['limit_price']} "
            f"({r.get('dist_pct')}% vom damaligen Preis, Setup {r.get('setup') or '-'}, "
            f"Konfidenz {r.get('confidence')}) · verfällt {str(r.get('expires_at'))[:16]}Z"
            + (f" · Grund: {r['reason']}" if r.get("reason") else ""))
    lines.append(
        "NUTZUNG: Diese Entries warten am Key-Level auf ihren Fill. Passt eine Order nicht mehr "
        "zur Marktlage, setze in deiner Entscheidung für das Symbol cancel_limit=true (auch bei "
        "HOLD). Eine neue Limit-Entscheidung gleicher Richtung ersetzt die alte automatisch; "
        "eine Gegenrichtungs-Entscheidung storniert sie.")
    return "\n".join(lines)


async def list_orders(db, limit: int = 50) -> Dict:
    """API: wartende Orders + jüngste Historie (ohne schweres signal-Feld)."""
    limit = max(1, min(200, int(limit)))
    pending = await db.ai_limit_orders.find(
        {"status": "pending"}, {"_id": 0, "signal": 0}).sort("created_at", -1).to_list(limit)
    now = _now()
    for r in pending:
        try:
            exp = datetime.fromisoformat(str(r["expires_at"]))
            r["expires_in_s"] = max(0, int((exp - now).total_seconds()))
        except (ValueError, TypeError, KeyError):
            r["expires_in_s"] = None
    history = await db.ai_limit_orders.find(
        {"status": {"$nin": ["pending", "filling"]}}, {"_id": 0, "signal": 0}
    ).sort("created_at", -1).to_list(limit)
    return {"orders": pending, "history": history}


async def cancel(db, order_id: str, reason: str) -> bool:
    row = await db.ai_limit_orders.find_one({"id": order_id, "status": "pending"},
                                            {"_id": 0, "signal": 0})
    if not row:
        return False
    n = await _cancel_rows(db, [row], reason)
    if n:
        await _chat(db, f"🚫 Limit-Order storniert: {row['side']} {row['symbol']} "
                        f"@ {row['limit_price']} – {reason}")
    return n > 0
