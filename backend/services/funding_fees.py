"""Funding-Gebühren: Datenquelle + Funding-Wächter.

1. Datenquelle: aktuelle Funding-Rate + Intervall pro Kontrakt über den
   öffentlichen Bitunix-Endpoint /api/v1/futures/market/funding_rate
   (10-min-Cache, fail-open: bei API-Fehlern wird 0 angenommen).

2. Entry-Seite (Fee-Wächter): `adverse_funding_pct()` projiziert die
   Funding-Kosten über die erwartete Haltedauer (Horizont scalp/swing) und
   fließt in `fee_guard_check()` ein – lange gehaltene Trades werden damit
   realistisch bewertet (Funding zahlt nur die Seite mit passendem Vorzeichen).

3. Positions-Seite (Funding-Wächter): `check_open_trades()` schätzt die bereits
   aufgelaufenen Funding-Kosten offener Live-Trades. Übersteigen sie
   `warn_margin_pct` der Marge, gibt es eine Warnung (Website + Telegram);
   optional (`close_enabled`, Default AUS) wird ab `close_margin_pct`
   automatisch geschlossen. Konfiguration: settings['funding_guard'].
"""
import logging
import time
from datetime import datetime, timezone
from typing import Dict, Optional

import aiohttp

logger = logging.getLogger(__name__)

DEFAULT_CONFIG = {
    "enabled": True,
    "warn_margin_pct": 20.0,   # Warnung: Funding-Kosten >= X% der Marge
    "close_enabled": False,    # aktives Schließen (bewusst Default AUS)
    "close_margin_pct": 40.0,  # Auto-Close: Funding-Kosten >= X% der Marge
    "min_age_hours": 4.0,      # jüngere Trades ignorieren (Scalps)
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
    """Funding-Wächter für offene Live-Trades (Aufruf periodisch, throttled)."""
    report = {"checked": 0, "warned": 0, "closed": 0}
    cfg = await get_config(db)
    if not cfg.get("enabled", True) or not (client and client.configured()):
        return report
    now = datetime.now(timezone.utc)
    trades = await db.auto_trades.find(
        {"status": "open", "mode": "live",
         "data_collection": {"$ne": True}}).to_list(200)
    for t in trades:
        try:
            opened = datetime.fromisoformat(str(t.get("opened_at")))
        except (TypeError, ValueError):
            continue
        age_h = (now - opened).total_seconds() / 3600.0
        if age_h < float(cfg.get("min_age_hours", 4.0) or 0):
            continue
        info = await get_funding_info(client, t["symbol"])
        if not info:
            continue
        report["checked"] += 1
        pct = adverse_funding_pct(info["rate"], t.get("side"), age_h,
                                  info["interval_h"])
        entry = float(t.get("entry") or 0)
        qty = float(t.get("qty_remaining") or t.get("qty") or 0)
        cost = round(entry * qty * pct / 100.0, 6)
        margin = float(t.get("max_capital") or 0) or \
            (entry * qty / max(float(t.get("leverage") or 1), 1.0))
        updates = {"funding_est_usdt": cost, "funding_rate": info["rate"],
                   "funding_est_at": now.isoformat()}
        close_at = margin * float(cfg.get("close_margin_pct", 40.0)) / 100.0
        warn_at = margin * float(cfg.get("warn_margin_pct", 20.0)) / 100.0
        if cfg.get("close_enabled") and close_at > 0 and cost >= close_at \
                and autotrader is not None:
            if await _close_for_funding(db, client, autotrader, telegram,
                                        t, cost, margin):
                report["closed"] += 1
                continue
        if warn_at > 0 and cost >= warn_at and not t.get("funding_warned"):
            updates["funding_warned"] = True
            report["warned"] += 1
            await _notify_warn(db, telegram, t, cost, margin, info["rate"])
        await db.auto_trades.update_one({"id": t["id"]}, {"$set": updates})
    return report


async def _notify_warn(db, telegram, t: Dict, cost: float, margin: float,
                       rate: float):
    from services import notifications
    text = (f"💸 *FUNDING-WÄCHTER*\n{t['symbol']} {t['side']}: geschätzte "
            f"Funding-Kosten bereits ~{cost:.2f} USDT "
            f"({cost / margin * 100:.0f}% der Marge {margin:.2f} USDT, "
            f"Rate {rate * 100:.4f}%/Intervall). Lange Haltedauer frisst den "
            f"Trade auf – bitte prüfen.")
    try:
        await notifications.website_notify(
            db, "funding_guard", "Funding-Wächter",
            f"{t['symbol']} {t['side']}: Funding-Kosten ~{cost:.2f} USDT "
            f"({cost / margin * 100:.0f}% der Marge)", cooldown_min=60)
        await notifications.telegram_notify(db, telegram, "funding_guard", text)
    except Exception as e:
        logger.warning(f"Funding-Wächter Notify fehlgeschlagen: {e}")


async def _close_for_funding(db, client, autotrader, telegram, t: Dict,
                             cost: float, margin: float) -> bool:
    pid = t.get("bitunix_position_id")
    qty = float(t.get("qty_remaining") or t.get("qty") or 0)
    if not pid or qty <= 0:
        return False
    try:
        res = await client.flash_close(t["symbol"], pid, t["side"], qty)
    except Exception as e:
        logger.warning(f"Funding-Close {t['symbol']} fehlgeschlagen: {e}")
        return False
    if not (isinstance(res, dict) and res.get("code") == 0):
        return False
    try:
        await autotrader._book_external_close(t)
    except Exception as e:
        logger.warning(f"Funding-Close verbuchen fehlgeschlagen: {e}")
    from services import notifications
    try:
        await notifications.telegram_notify(
            db, telegram, "funding_guard",
            f"💸 *FUNDING-WÄCHTER: POSITION GESCHLOSSEN*\n"
            f"{t['symbol']} {t['side']}: Funding-Kosten ~{cost:.2f} USDT "
            f"überschritten das Limit ({cost / margin * 100:.0f}% der Marge) – "
            f"Position wurde automatisch geschlossen.")
    except Exception as e:
        logger.warning(f"Funding-Close Notify fehlgeschlagen: {e}")
    logger.warning(f"Funding-Wächter: {t['symbol']} {t['side']} geschlossen "
                   f"(Funding ~{cost:.2f} USDT bei Marge {margin:.2f})")
    return True
