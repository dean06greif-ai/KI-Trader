"""In-Flight-Registry für laufende Entry-Prozesse (Doppel-Trade-Schutz).

Bug-Report: Bei KI-Limit-Orders erschien auf der Website EIN KI-Trade und
zusätzlich derselbe Trade nochmal als 'Manuell (Bitunix)'. Ursache ist ein
Zeitfenster in AutoTradeManager.on_signal: Die Börsen-Position existiert ab
dem Fill, der lokale Trade wird aber erst nach TP1-/SL-Platzierung (Maker-
Modus: nach bis zu 45 s Warten auf den Fill) in die DB geschrieben. Läuft in
diesem Fenster der Positions-Watchdog, findet er eine Position OHNE lokalen
Trade und übernimmt sie – als Duplikat.

Lösung (zwei Ebenen, KI-Trade bleibt führend):
  1. on_signal meldet Symbol+Seite hier als "in Arbeit" an (Prozess-lokal,
     asyncio läuft im selben Prozess wie der Watchdog). Der Watchdog übernimmt
     solche Positionen NICHT, solange der Entry läuft.
  2. Sollte trotzdem ein Duplikat entstanden sein (z.B. Watchdog-Lauf exakt
     vor dem Insert), räumt on_signal nach dem Insert alle in der Zwischenzeit
     vom Watchdog übernommenen Extern-Trades derselben Position wieder ab.

Nach einem Backend-Neustart ist die Registry leer -> der Watchdog übernimmt
Positionen wie bisher (Registry-Abgleich über entry_order_registry).
"""
import logging
import time
from datetime import datetime, timezone
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

# Sicherheitsnetz: hängt ein Entry-Prozess (z.B. Task abgebrochen ohne
# finally), gilt die Markierung höchstens so lange.
MAX_AGE_SEC = 900.0

_inflight: Dict[str, Dict] = {}


def _key(symbol: str, side: str) -> str:
    return f"{str(symbol).upper()}:{str(side).upper()}"


def begin(symbol: str, side: str, strategy_id: Optional[str] = None) -> str:
    """Entry-Prozess anmelden (referenzgezählt – parallele Entries auf
    Symbol+Seite halten die Markierung, bis der letzte fertig ist).
    Rückgabe: Schlüssel für end()."""
    key = _key(symbol, side)
    row = _inflight.get(key)
    if row:
        row["count"] = int(row.get("count") or 1) + 1
        return key
    _inflight[key] = {"since": time.time(),
                      "since_iso": datetime.now(timezone.utc).isoformat(),
                      "strategy_id": strategy_id, "count": 1}
    return key


def end(key: str) -> None:
    row = _inflight.get(key)
    if not row:
        return
    row["count"] = int(row.get("count") or 1) - 1
    if row["count"] <= 0:
        _inflight.pop(key, None)


def started_iso(symbol: str, side: str) -> Optional[str]:
    row = _inflight.get(_key(symbol, side))
    return row.get("since_iso") if row else None


def is_inflight(symbol: str, side: str, max_age_sec: float = MAX_AGE_SEC) -> bool:
    """Läuft gerade ein Entry-Prozess für Symbol+Seite?"""
    key = _key(symbol, side)
    row = _inflight.get(key)
    if not row:
        return False
    if time.time() - float(row.get("since") or 0) > max_age_sec:
        _inflight.pop(key, None)  # verwaiste Markierung
        return False
    return True


def active() -> List[Dict]:
    """Für Status/Debug: alle aktiven Markierungen."""
    return [{"key": k, **v} for k, v in _inflight.items()]


def clear() -> None:
    _inflight.clear()


def is_watchdog_adoption(t: Dict) -> bool:
    """Trade wurde vom Watchdog aus einer Börsen-Position erzeugt (rein)."""
    return bool(t.get("external_adopted") or t.get("adopted_from_limit")
                or t.get("strategy_id") == "external")


def duplicate_of(trade: Dict, other: Dict, started_iso_: Optional[str]) -> bool:
    """Ist `other` eine Watchdog-Übernahme derselben Börsen-Position wie der
    frisch eröffnete KI-/Website-Trade `trade`? (rein & testbar)

    Kriterien: gleiches Symbol+Seite, offen, Live, vom Watchdog übernommen und
    entweder an dieselbe Bitunix-Position-ID gebunden ODER erst NACH dem Start
    des Entry-Prozesses eröffnet (Position-ID unbekannt/noch nicht gebunden)."""
    if other.get("id") == trade.get("id"):
        return False
    if other.get("status") != "open" or other.get("mode") != "live":
        return False
    if other.get("symbol") != trade.get("symbol") or other.get("side") != trade.get("side"):
        return False
    if not is_watchdog_adoption(other):
        return False
    pid = trade.get("bitunix_position_id")
    opid = other.get("bitunix_position_id")
    if pid and opid and str(pid) == str(opid):
        return True
    if started_iso_ and str(other.get("opened_at") or "") >= str(started_iso_):
        # Übernahme entstand während unser Entry lief -> gehört zu uns
        return not opid or not pid
    return False


async def remove_duplicate_adoptions(db, trade: Dict, started_iso_: Optional[str]) -> int:
    """Nach dem Insert eines Live-Trades: in der Zwischenzeit vom Watchdog
    übernommene Duplikate derselben Position entfernen (KI-Trade bleibt)."""
    if trade.get("mode") != "live":
        return 0
    try:
        rows = await db.auto_trades.find(
            {"status": "open", "mode": "live", "symbol": trade["symbol"],
             "side": trade["side"]}).to_list(50)
    except Exception as e:
        logger.debug(f"Inflight-Dedupe {trade.get('symbol')}: Abfrage fehlgeschlagen: {e}")
        return 0
    removed = 0
    for other in rows:
        if not duplicate_of(trade, other, started_iso_):
            continue
        try:
            await db.auto_trades.delete_one({"id": other["id"]})
            removed += 1
            logger.warning(
                f"{trade['symbol']} {trade['side']}: Watchdog-Duplikat "
                f"'{other.get('strategy_name')}' ({other.get('id')}) entfernt – "
                f"KI-/Website-Trade {trade.get('id')} bleibt führend")
        except Exception as e:
            logger.warning(f"Inflight-Dedupe: Löschen {other.get('id')} fehlgeschlagen: {e}")
    if removed:
        try:
            await db.auto_trades.update_one(
                {"id": trade["id"]},
                {"$push": {"events": {
                    "$each": [f"DEDUPE: {removed} doppelte Watchdog-Übernahme(n) "
                              f"derselben Position entfernt"],
                    "$slice": -20}}})
        except Exception as e:
            logger.debug(f"Inflight-Dedupe: Event-Update fehlgeschlagen: {e}")
    return removed
