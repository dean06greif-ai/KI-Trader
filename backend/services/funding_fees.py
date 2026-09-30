"""Funding-Gebühren: Datenquelle + Funding-Wächter.

1. Datenquelle: aktuelle Funding-Rate + Intervall pro Kontrakt über den
   öffentlichen Bitunix-Endpoint /api/v1/futures/market/funding_rate
   (10-min-Cache, fail-open: bei API-Fehlern wird 0 angenommen).

2. Entry-Seite (Fee-Wächter): `adverse_funding_pct()` projiziert die
   Funding-Kosten über die erwartete Haltedauer (Horizont scalp/swing) und
   fließt in `fee_guard_check()` ein – lange gehaltene Trades werden damit
   realistisch bewertet (Funding zahlt nur die Seite mit passendem Vorzeichen).

3. Positions-Seite (Funding-Wächter): `check_open_trades()` liest die ECHTEN
   kumulierten Funding-Zahlungen offener Live-Positionen aus der Bitunix-
   Positions-API (Feld `funding` in get_pending_positions; positiv = erhalten,
   negativ = gezahlt). Die frühere Hochrechnung (aktuelle Rate × Alter) war
   grob falsch: Raten wechseln Vorzeichen/Höhe pro Intervall, wodurch z.B.
   "97% der Marge" gemeldet wurde, obwohl die Position Funding EMPFANGEN hat.
   Der Wächter ist reine INFO: er speichert `funding_real_usdt` am Trade
   (sichtbar für den KI-Trade-Manager im Prompt) und warnt ab
   `warn_margin_pct` echter Kosten – es gibt KEINE Auto-Aktionen mehr.
   Konfiguration: settings['funding_guard'].
"""
import logging
import time
from datetime import datetime, timezone
from typing import Dict, Optional

import aiohttp

logger = logging.getLogger(__name__)

DEFAULT_CONFIG = {
    "enabled": True,
    "warn_margin_pct": 20.0,   # Warnung: ECHTE Funding-Kosten >= X% der Marge
}

# Erwartete Haltedauer je KI-Horizont für die Funding-Projektion beim Entry
HOLD_HOURS = {"scalp": 2.0, "swing": 24.0}

CACHE_TTL_S = 600
_cache: Dict[str, tuple] = {}


def hold_hours(horizon) -> float:
    return HOLD_HOURS.get(str(horizon or "scalp").lower(), 2.0)


def adverse_funding_pct(rate, side: str, hold_h: float,
                        interval_h: float = 8.0) -> float:
    """Projizierte Funding-Kosten in % des Notionals über `hold_h` Stunden.
    0.0 wenn die Seite Funding EMPFÄNGT (Long zahlt bei rate>0, Short bei rate<0)."""
    try:
        r = float(rate)
    except (TypeError, ValueError):
        return 0.0
    pays = r > 0 if str(side).upper() == "LONG" else r < 0
    if not pays or not hold_h or hold_h <= 0:
        return 0.0
    return abs(r) * 100.0 * (float(hold_h) / max(float(interval_h) or 8.0, 1.0))


def parse_funding_payload(payload) -> Optional[Dict]:
    """Bitunix funding_rate-Antwort -> {'rate', 'interval_h'} (rein, testbar)."""
    if not isinstance(payload, dict) or str(payload.get("code")) != "0":
        return None
    data = payload.get("data")
    row = data[0] if isinstance(data, list) and data else \
        (data if isinstance(data, dict) else None)
    if not isinstance(row, dict):
        return None
    try:
        rate = float(row.get("fundingRate"))
    except (TypeError, ValueError):
        return None
    try:
        interval_h = float(row.get("fundingInterval") or 8) or 8.0
    except (TypeError, ValueError):
        interval_h = 8.0
    return {"rate": rate, "interval_h": interval_h}


def positions_by_id(payload) -> Dict[str, Dict]:
    """get_pending_positions -> {positionId: {'funding', 'margin', 'fee'}}
    (rein, testbar). Bitunix-Konvention: funding > 0 = ERHALTEN, < 0 = gezahlt."""
    out: Dict[str, Dict] = {}
    data = (payload or {}).get("data") if isinstance(payload, dict) else None
    if not isinstance(data, list):
        return out
    for p in data:
        if not isinstance(p, dict) or not p.get("positionId"):
            continue

        def _num(k):
            try:
                return float(p.get(k))
            except (TypeError, ValueError):
                return 0.0
        out[str(p["positionId"])] = {"funding": _num("funding"),
                                     "margin": _num("margin"),
                                     "fee": _num("fee")}
    return out


async def get_funding_info(client, symbol: str) -> Optional[Dict]:
    """Aktuelle Funding-Rate (gecacht). None bei API-Fehler (fail-open)."""
    b_symbol = client.to_bitunix_symbol(symbol)
    now = time.time()
    hit = _cache.get(b_symbol)
    if hit and now - hit[0] < CACHE_TTL_S:
        return hit[1]
    url = f"{client.base}/api/v1/futures/market/funding_rate"
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(url, params={"symbol": b_symbol},
                             timeout=aiohttp.ClientTimeout(total=8)) as r:
                payload = await r.json(content_type=None)
    except Exception as e:
        logger.debug(f"funding_rate({b_symbol}) fehlgeschlagen: {e}")
        return None
    info = parse_funding_payload(payload)
    if info:
        _cache[b_symbol] = (now, info)
    return info


async def get_config(db) -> Dict:
    cfg = dict(DEFAULT_CONFIG)
    try:
        doc = await db.settings.find_one({"_id": "funding_guard"}) or {}
        for k in DEFAULT_CONFIG:
            if k in doc:
                cfg[k] = doc[k]
    except Exception as e:
        logger.debug(f"funding_guard-Config: {e}")
    return cfg


async def check_open_trades(db, client, telegram=None, autotrader=None) -> Dict:
    """Funding-Wächter (INFO-only): echte Funding-Zahlungen offener Live-Trades
    aus der Positions-API übernehmen. `autotrader` bleibt für die Aufrufer-
    Signatur erhalten, wird aber nicht mehr genutzt (kein Auto-Close)."""
    report = {"checked": 0, "warned": 0, "skipped": 0}
    cfg = await get_config(db)
    if not cfg.get("enabled", True) or not (client and client.configured()):
        return report
    trades = await db.auto_trades.find(
        {"status": "open", "mode": "live",
         "data_collection": {"$ne": True}}).to_list(200)
    if not trades:
        return report
    try:
        pos_map = positions_by_id(await client.get_positions())
    except Exception as e:
        logger.warning(f"Funding-Wächter: Positions-Abruf fehlgeschlagen: {e}")
        return report
    now_iso = datetime.now(timezone.utc).isoformat()
    warn_pct = float(cfg.get("warn_margin_pct", 20.0) or 0)
    for t in trades:
        pos = pos_map.get(str(t.get("bitunix_position_id") or ""))
        if pos is None:
            # Position (noch) nicht auffindbar -> lieber KEINE Aussage als eine falsche
            report["skipped"] += 1
            continue
        report["checked"] += 1
        funding = pos["funding"]
        margin = pos["margin"] or float(t.get("max_capital") or 0)
        cost = -funding if funding < 0 else 0.0
        updates = {"funding_real_usdt": round(funding, 6),
                   "funding_checked_at": now_iso}
        warn_at = margin * warn_pct / 100.0 if margin > 0 else 0.0
        if warn_at > 0 and cost >= warn_at and not t.get("funding_warned"):
            updates["funding_warned"] = True
            report["warned"] += 1
            await _notify_warn(db, telegram, t, cost, margin)
        elif t.get("funding_warned") and warn_at > 0 and cost < warn_at * 0.5:
            updates["funding_warned"] = False   # Entwarnung (Rate gedreht)
        await db.auto_trades.update_one(
            {"id": t["id"]},
            {"$set": updates, "$unset": {"funding_est_usdt": ""}})
    return report


async def _notify_warn(db, telegram, t: Dict, cost: float, margin: float):
    from services import notifications
    pct = cost / margin * 100 if margin else 0.0
    text = (f"💸 *FUNDING-WÄCHTER (Info)*\n{t['symbol']} {t['side']}: bereits "
            f"~{cost:.2f} USDT Funding GEZAHLT laut Börse "
            f"({pct:.0f}% der Marge {margin:.2f} USDT). Der KI-Trade-Manager "
            f"sieht diese Kosten und entscheidet über das Positions-Management – "
            f"keine automatische Aktion.")
    try:
        await notifications.website_notify(
            db, "funding_guard", "Funding-Wächter",
            f"{t['symbol']} {t['side']}: echte Funding-Kosten ~{cost:.2f} USDT "
            f"({pct:.0f}% der Marge)", cooldown_min=60)
        await notifications.telegram_notify(db, telegram, "funding_guard", text)
    except Exception as e:
        logger.warning(f"Funding-Wächter Notify fehlgeschlagen: {e}")
