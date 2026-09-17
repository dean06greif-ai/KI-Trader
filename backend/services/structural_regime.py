"""Struktur-Resolver (PLAN_REGIME_BRUECKE, Baustein 2): EINE Wahrheit für die Runtime.

Liefert je Symbol das strukturelle Marktregime aus der FREIGEGEBENEN Lab-Analyse
seiner Assetklasse (Stufe `shadow`/`active`) als MarketContext-Eintrag der Ebene
`structural`. Ohne Freigabe -> `unknown` (kein Fallback auf die Gate-Erkennung:
die bleibt Sache von `regime_gate` mit `source=own`). Fehler -> `stale` mit
letztem bekannten Stand, nie Exception nach außen.

Frische-Regel statt TTL-Feld: 2 Kerzen-Längen des Modell-Timeframes; Nicht-Krypto
außerhalb der Handelszeiten gilt der Stand der letzten Sitzung (`market_closed`).
"""
import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Dict, List, Optional

from services import market_context as mc
from services import setup_asset_class

logger = logging.getLogger(__name__)

CACHE_TTL_S = 900
REFRESH_EVERY_S = 600
DETECT_DAYS = 30
MAX_GAP_CANDLES = 3
CACHE_DOC_ID = "structural_regime_cache"
TF_SECONDS = {"1m": 60, "5m": 300, "15m": 900, "30m": 1800, "1h": 3600, "4h": 14400, "1d": 86400}
KURZFRIST_WORDS = ("trend_up", "trend_down", "range_ruhig", "range", "breakout", "drift")
DIRECTION_WORDS = {"down": "BÄR", "up": "BULLE", "sideways": "SEITWÄRTS"}

_cache: Dict[str, Dict] = {}
_release_cache: Dict[str, Optional[Dict]] = {}
_release_ts = 0.0
_locks: Dict[str, asyncio.Lock] = {}
_db = None


def setup(db):
    global _db
    _db = db


def invalidate():
    global _release_ts
    _release_ts = 0.0
    _release_cache.clear()
    _cache.clear()


# --------------------------------------------------------------------------
# Reine Bausteine
# --------------------------------------------------------------------------
def freshness_ttl_sec(symbol: str, timeframe: str, now: Optional[datetime] = None) -> Dict:
    """TTL aus Timeframe + Marktkalender (rein). market_closed=True: Stand der
    letzten Sitzung gilt bis zur Eröffnung (kein 'stale' am Wochenende)."""
    tf_s = TF_SECONDS.get(str(timeframe or "1h"), 3600)
    ttl = 2 * tf_s
    closed = False
    if setup_asset_class.asset_class_of(symbol) != setup_asset_class.CRYPTO:
        try:
            from core.market_hours import is_market_closed
            closed, _ = is_market_closed(symbol, now)
        except Exception:  # noqa: BLE001
            closed = False
    return {"ttl_sec": ttl, "market_closed": bool(closed), "tf_sec": tf_s}


def candle_gap_ok(candles: List[Dict], tf_sec: int) -> bool:
    if len(candles) < 2:
        return False
    ts = [int(c.get("timestamp") or 0) for c in candles[-50:]]
    return all((b - a) <= MAX_GAP_CANDLES * tf_sec * 1000 for a, b in zip(ts, ts[1:]))


def context_from_model(model: Dict, candles: List[Dict], timeframe: str, aid: str, stage: str,
                       kept_ids: List[int], symbol: str, now: Optional[datetime] = None) -> Dict:
    """Modell + Kerzen -> MarketContext(structural) inkl. since_days/kept/stage (rein)."""
    from services import regime as rg
    cur = rg.current_regime(model, candles, timeframe)
    rid = cur.get("regime")
    mode = (model.get("config") or {}).get("regime_mode", model.get("regime_mode"))
    direction = mc.direction_from_regime_id(rid, mode) if rid is not None else None
    fresh = freshness_ttl_sec(symbol, timeframe, now)
    last_ts = int(candles[-1].get("timestamp") or 0) if candles else 0
    now_dt = now or datetime.now(timezone.utc)
    age = max(0.0, now_dt.timestamp() - last_ts / 1000) if last_ts else None
    ttl = None if fresh["market_closed"] else fresh["ttl_sec"]
    ctx = mc.structural_context("regime_lab", direction=direction, label=cur.get("label"),
                                confidence=cur.get("confidence"), model_fp=mc.model_fingerprint(model),
                                age_sec=age, ttl_sec=ttl)
    if ctx["state"] == "ok" and not candle_gap_ok(candles, fresh["tf_sec"]):
        ctx["state"] = "stale"
    since_days = None
    sw = cur.get("last_switch")
    if sw:
        try:
            since_days = round((now_dt.timestamp() * 1000 - int(sw)) / 86400000, 1)
        except (TypeError, ValueError):
            since_days = None
    ctx.update({"aid": aid, "stage": stage, "regime_id": rid, "since_days": since_days,
                "kept": (int(rid) in set(kept_ids)) if rid is not None else False,
                "market_closed": fresh["market_closed"], "timeframe": timeframe,
                "refreshed_at": now_dt.isoformat()})
    return ctx


def unknown_context(reason: str = "keine freigegebene Analyse") -> Dict:
    ctx = mc.structural_context("regime_lab")
    ctx.update({"stage": "none", "reason": reason})
    return ctx


def prompt_line(ctx: Optional[Dict]) -> str:
    """Bewusst andere Wortwahl als das Kurzfrist-Regime der Symbolzeilen."""
    if not ctx or ctx.get("state") == "unknown" or not ctx.get("direction"):
        return "Struktur: unbekannt (keine freigegebene Analyse)"
    if ctx.get("state") == "stale":
        return "Struktur: unbekannt (Lab-Stand veraltet)"
    word = DIRECTION_WORDS.get(ctx.get("direction"), "UNKLAR")
    since = f" seit {ctx['since_days']:g} Tagen" if ctx.get("since_days") is not None else ""
    conf = ctx.get("confidence")
    conf_s = f" · Sicherheit {float(conf) / 100:.2f} (heuristisch)" if conf is not None else ""
    fp = (ctx.get("model_fingerprint") or "")[:6]
    kept = "" if ctx.get("kept", True) else " · Regime im Lab verworfen"
    return f"Struktur (Lab-Modell {fp}, {ctx.get('timeframe') or '1h'}): {word}{since}{conf_s}{kept}"


def artifact_of(release: Optional[Dict], aid: Optional[str]) -> Optional[str]:
    """Stabiles Fingerprint-Artefakt je Freigabe (nicht je Regime-Zustand)."""
    if not release or release.get("stage") not in ("shadow", "active") or not aid:
        return None
    return f"lab:{aid}:{str(release.get('model_fingerprint') or '')[:8]}"


# --------------------------------------------------------------------------
# Runtime (Cache + Kerzen)
# --------------------------------------------------------------------------
async def _release_for(asset_class: str) -> Optional[Dict]:
    global _release_ts
    if _db is None:
        return None
    if time.time() - _release_ts > CACHE_TTL_S:
        _release_cache.clear()
        _release_ts = time.time()
    if asset_class not in _release_cache:
        from services import regime_release
        try:
            _release_cache[asset_class] = await regime_release.released_for_class(_db, asset_class)
        except Exception as e:  # noqa: BLE001
            logger.debug(f"structural_regime release lookup {asset_class}: {e}")
            return None
    return _release_cache.get(asset_class)


def stage_of_class_cached(asset_class: str) -> str:
    doc = _release_cache.get(asset_class)
    return str(((doc or {}).get("release") or {}).get("stage") or "none")


async def stage_of(symbol: str) -> str:
    doc = await _release_for(setup_asset_class.asset_class_of(symbol))
    return str(((doc or {}).get("release") or {}).get("stage") or "none")


async def _compute(symbol: str, doc: Dict) -> Dict:
    from services import regime_lab as lab
    from services import regime_release
    from services.backtester import fetch_history
    from services.timeframes import aggregate_candles
    import aiohttp
    rel = doc.get("release") or {}
    scope = rel.get("scope") or doc.get("scope") or "combined"
    sym_for_model = rel.get("symbol") if scope == "per_coin" else None
    model = lab.model_for(doc, scope, sym_for_model or symbol)
    if not model:
        return unknown_context("Modell fehlt")
    tf = str(doc.get("timeframe") or "1h")
    async with aiohttp.ClientSession() as session:
        raw = await fetch_history(session, symbol, DETECT_DAYS)
    candles = aggregate_candles(raw, tf, drop_partial=True)
    del raw
    kept = regime_release.kept_regime_ids(doc, scope, sym_for_model)
    return context_from_model(model, candles, tf, doc["id"], rel.get("stage") or "none", kept, symbol)


async def resolve(symbol: str) -> Dict:
    """MarketContext(structural) für ein Symbol – gecacht, fail-open."""
    ent = _cache.get(symbol)
    if ent and time.time() - ent["_at"] < CACHE_TTL_S:
        return ent["ctx"]
    doc = await _release_for(setup_asset_class.asset_class_of(symbol))
    if not doc:
        return unknown_context()
    lock = _locks.setdefault(symbol, asyncio.Lock())
    async with lock:
        ent = _cache.get(symbol)
        if ent and time.time() - ent["_at"] < CACHE_TTL_S:
            return ent["ctx"]
        try:
            ctx = await _compute(symbol, doc)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"structural_regime {symbol}: {e} – letzter Stand als stale")
            if ent:
                ctx = {**ent["ctx"], "state": "stale"}
            else:
                ctx = unknown_context(f"Fehler: {str(e)[:80]}")
                ctx["state"] = "stale"
        prev = (ent or {}).get("ctx") or {}
        ctx["direction_changed"] = bool(prev.get("direction") and ctx.get("direction")
                                        and prev["direction"] != ctx["direction"])
        _cache[symbol] = {"_at": time.time(), "ctx": ctx}
        return ctx


async def resolve_if_released(symbol: str) -> Optional[Dict]:
    """Nur ab Stufe shadow (für Snapshot/Fingerprint) – sonst None (Schema wie heute)."""
    if await stage_of(symbol) == "none":
        return None
    return await resolve(symbol)


async def artifact(symbol: str) -> Optional[str]:
    doc = await _release_for(setup_asset_class.asset_class_of(symbol))
    return artifact_of((doc or {}).get("release"), (doc or {}).get("id")) if doc else None


async def phase(symbol: str) -> Optional[str]:
    """Gate-Quelle `lab`: Phase nur bei Stufe active und Zustand ok."""
    if await stage_of(symbol) != "active":
        return None
    ctx = await resolve(symbol)
    return ctx.get("phase") if ctx.get("state") == "ok" else None


async def prompt_block(symbols: List[str]) -> str:
    """Prompt-Block für Klassen mit Stufe `active` (leer sonst)."""
    lines = []
    for s in symbols:
        if await stage_of(s) != "active":
            continue
        lines.append(f"{s}: {prompt_line(await resolve(s))}")
    if not lines:
        return ""
    return ("=== STRUKTURELLES MARKTREGIME (freigegebenes Lab-Modell – NICHT das "
            "Kurzfrist-Regime der Symbolzeilen) ===\n" + "\n".join(lines) + "\n"
            "Struktur = Großwetterlage über Wochen (Lab), Kurzfrist-Regime = Zustand der "
            "letzten Stunden (Symbolzeile). Lektionen zur Struktur ausdrücklich mit "
            "'strukturell' benennen.")


async def snapshot_all() -> Dict[str, Dict]:
    return {s: e["ctx"] for s, e in _cache.items()}


async def _persist_cache():
    if _db is None or not _cache:
        return
    try:
        await _db.settings.update_one(
            {"_id": CACHE_DOC_ID},
            {"$set": {"symbols": {s: e["ctx"] for s, e in _cache.items()},
                      "updated_at": datetime.now(timezone.utc).isoformat()}}, upsert=True)
    except Exception as e:  # noqa: BLE001
        logger.debug(f"structural_regime cache persist: {e}")


async def load_cache():
    """Boot: persistierten Stand laden (kein Download-Burst nach Render-Neustart)."""
    if _db is None:
        return
    try:
        doc = await _db.settings.find_one({"_id": CACHE_DOC_ID}) or {}
        for s, ctx in (doc.get("symbols") or {}).items():
            _cache[s] = {"_at": time.time() - CACHE_TTL_S + 120, "ctx": {**ctx, "state": "stale"}}
    except Exception as e:  # noqa: BLE001
        logger.debug(f"structural_regime cache load: {e}")


async def run_loop(symbols_fn):
    """Hintergrund-Refresh (alle 10 min) für Symbole mit Freigabe; respektiert
    AI_TRADER_LOCAL_DISABLE. Vollzieht zudem KI-Auto-Freigaben nach Karenz."""
    from core.config import local_engine_disabled
    await load_cache()
    await asyncio.sleep(120)
    while True:
        try:
            if not local_engine_disabled():
                for s in list(symbols_fn() or []):
                    if await stage_of(s) != "none":
                        await resolve(s)
                await _persist_cache()
                if _db is not None:
                    from services import regime_release
                    await regime_release.apply_due_auto_proposals(_db)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            logger.warning(f"structural_regime loop: {e}")
        await asyncio.sleep(REFRESH_EVERY_S)
