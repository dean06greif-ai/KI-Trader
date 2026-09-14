"""PnL-Abgleich geschlossener Live-Trades mit der Bitunix-Positions-Historie.

Bug-Report: SL stand auf Break-Even, die Kerze schoss aber durch – Bitunix
buchte z.B. −3 USDT, die Website zeigte weiter PnL 0. Ursache: Der lokale
Monitor rechnet Trigger-Exits (SL/TP) zum LEVEL-Preis ab, die Börse füllt
aber zum tatsächlichen Marktpreis (Slippage, Gap, Fees, Funding).

Dieses Modul übernimmt NACH dem Schließen den echten realisierten PnL
(inkl. Fees/Funding) aus `get_history_positions` – für JEDEN Schließ-Weg
(Monitor, manueller Close, KI-Trade-Manager, externer Close):
  * sofort in AutoTradeManager._after_close (mit kurzem Retry, weil die
    Historie einige Sekunden nachläuft) – damit Telegram, Rewards und
    Kill-Switch bereits den echten Wert sehen,
  * periodisch (run_loop) für Trades, deren Historie beim Close noch nicht
    verfügbar war (max. MAX_ATTEMPTS Versuche innerhalb LOOKBACK_H Stunden).

Die Zuordnung läuft ausschließlich über die Bitunix-Position-ID; Trades ohne
ID, Paper-Trades oder manuell aufgestockte Positionen (Mengen-Abweichung >5 %)
werden NICHT überschrieben (siehe AutoTradeManager._exchange_close_truth).
Der lokal geschätzte Wert bleibt als `pnl_local_estimate` erhalten.
"""
import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Dict, Optional

logger = logging.getLogger(__name__)

INTERVAL_SEC = 300
LOOKBACK_H = 48
MAX_ATTEMPTS = 6
IMMEDIATE_RETRY_DELAY_SEC = 2.5
# Unterhalb dieser Abweichung gilt der lokale Wert als korrekt (kein Event-Spam)
MIN_DIFF_USDT = 0.005


def _f(v) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def result_for(pnl: float, eps: float = 1e-6) -> str:
    if pnl > eps:
        return "win"
    if pnl < -eps:
        return "loss"
    return "breakeven"


def build_updates(t: Dict, exact: Dict) -> Optional[Dict]:
    """$set-Dokument für den Abgleich (rein & testbar). None = nichts zu tun."""
    if not exact:
        return None
    local_pnl = _f(t.get("realized_pnl"))
    real_pnl = _f(exact.get("net_pnl"))
    updates: Dict = {"pnl_exchange_exact": True,
                     "pnl_reconciled_at": datetime.now(timezone.utc).isoformat(),
                     "realized_pnl": round(real_pnl, 6),
                     "fees_paid": round(_f(exact.get("fee")), 6),
                     "funding_paid": round(_f(exact.get("funding")), 6),
                     "result": result_for(real_pnl)}
    if exact.get("exit_price"):
        updates["exit_price"] = exact["exit_price"]
    diff = real_pnl - local_pnl
    if abs(diff) >= MIN_DIFF_USDT:
        updates["pnl_local_estimate"] = round(local_pnl, 6)
        updates["pnl_reconcile_diff"] = round(diff, 6)
        updates["events"] = (list(t.get("events") or []) + [
            f"PNL-ABGLEICH (Bitunix): {local_pnl:+.4f} -> {real_pnl:+.4f} USDT "
            f"(echter Börsen-PnL inkl. Fees/Funding, Δ {diff:+.4f})"])[-20:]
    return updates


async def reconcile_trade(autotrader, t: Dict, retries: int = 1,
                          delay: float = IMMEDIATE_RETRY_DELAY_SEC) -> Optional[Dict]:
    """Einen geschlossenen Live-Trade mit der Börse abgleichen.
    Rückgabe: angewendete Updates (oder None). `t` wird in-place aktualisiert."""
    if t.get("mode") != "live" or not t.get("bitunix_position_id"):
        return None
    if t.get("pnl_exchange_exact"):
        return None
    exact = None
    for i in range(max(1, retries + 1)):
        try:
            exact = await autotrader._exchange_close_truth(t)
        except Exception as e:
            logger.debug(f"PnL-Abgleich {t.get('symbol')}: {e}")
            exact = None
        if exact or i >= retries:
            break
        await asyncio.sleep(delay)
    attempts = int(t.get("pnl_reconcile_attempts") or 0) + 1
    if not exact:
        try:
            await autotrader.db.auto_trades.update_one(
                {"id": t["id"]}, {"$set": {"pnl_reconcile_attempts": attempts}})
        except Exception as e:
            logger.debug(f"PnL-Abgleich: Zähler {t.get('id')} nicht gespeichert: {e}")
        t["pnl_reconcile_attempts"] = attempts
        return None
    updates = build_updates(t, exact)
    if not updates:
        return None
    updates["pnl_reconcile_attempts"] = attempts
    await autotrader.db.auto_trades.update_one({"id": t["id"]}, {"$set": updates})
    if "pnl_local_estimate" in updates:
        logger.info(f"PnL-Abgleich {t.get('symbol')} {t.get('side')}: "
                    f"{updates['pnl_local_estimate']:+} -> {updates['realized_pnl']:+} USDT")
    t.update(updates)
    return updates


async def reconcile_pending(autotrader, limit: int = 40) -> Dict:
    """Alle kürzlich geschlossenen Live-Trades ohne Börsen-Abgleich nachziehen."""
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=LOOKBACK_H)).isoformat()
    out = {"checked": 0, "reconciled": 0, "changed": 0}
    if autotrader.db is None or not autotrader.client.configured():
        return out
    rows = await autotrader.db.auto_trades.find(
        {"status": "closed", "mode": "live",
         "bitunix_position_id": {"$nin": [None, ""]},
         "pnl_exchange_exact": {"$ne": True},
         "closed_at": {"$gte": cutoff},
         "$or": [{"pnl_reconcile_attempts": {"$exists": False}},
                 {"pnl_reconcile_attempts": {"$lt": MAX_ATTEMPTS}}]}
    ).sort("closed_at", -1).to_list(limit)
    for t in rows:
        out["checked"] += 1
        try:
            upd = await reconcile_trade(autotrader, t, retries=0)
        except Exception as e:
            logger.warning(f"PnL-Abgleich {t.get('id')} fehlgeschlagen: {e}")
            continue
        if upd:
            out["reconciled"] += 1
            if "pnl_local_estimate" in upd:
                out["changed"] += 1
    if out["reconciled"]:
        logger.info(f"PnL-Abgleich: {out['reconciled']}/{out['checked']} Trades "
                    f"abgeglichen, {out['changed']} korrigiert")
    try:
        await autotrader.db.settings.update_one(
            {"_id": "pnl_reconcile_status"},
            {"$set": {**out, "last_run_at": datetime.now(timezone.utc).isoformat()}},
            upsert=True)
    except Exception as e:
        logger.debug(f"PnL-Abgleich: Status nicht gespeichert: {e}")
    return out


async def run_loop(autotrader, interval_sec: int = INTERVAL_SEC):
    logger.info(f"PnL-Abgleich gestartet (alle {interval_sec}s)")
    await asyncio.sleep(45)
    while True:
        try:
            await reconcile_pending(autotrader)
        except Exception as e:
            logger.error(f"PnL-Abgleich Fehler: {e}")
        await asyncio.sleep(interval_sec)
