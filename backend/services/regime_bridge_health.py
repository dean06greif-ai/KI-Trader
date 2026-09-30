"""Gesundheit der Regime-Brücke (Lab -> dynamische Strategien -> KI-Trader).

Macht sichtbar, wenn die Brücke ins Leere läuft – bisher passierte das stumm
(verwaiste dynamische Strategie seit 30.07., 0 Freigaben bei 12 Analysen):
  * orphaned_dynamic  – dynamische Strategie zeigt auf gelöschte Lab-Analyse
  * dynamic_idle      – aktive dynamische Strategie ohne Auto-Check oder mit
                        letztem Check älter als STALE_DAYS
  * no_release        – Lab-Analysen vorhanden, aber keine Freigabe (Shadow/Aktiv)
  * observer_low_hit  – Kurzfrist-Erkennung eines Symbols ≤ Zufall (Cockpit-Cache)

Nur lesend; reine Funktionen oben (unit-testbar), IO unten. Meldung einmal
täglich per Telegram/Website (Toggle `regime_bridge` in den Meldungen).
API: GET /api/regime-cockpit/health.
"""
import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

STALE_DAYS = 7
LOW_HIT_PCT = 50.0
CHECK_EVERY_S = 6 * 3600
NOTIFY_COOLDOWN_MIN = 24 * 60
CACHE_TTL_S = 300
_cache: Dict = {"ts": 0.0, "data": None}


def _age_days(iso: Optional[str], now: Optional[datetime] = None) -> Optional[float]:
    if not iso:
        return None
    try:
        dt = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return ((now or datetime.now(timezone.utc)) - dt).total_seconds() / 86400
    except ValueError:
        return None


def evaluate(dyn_docs: List[Dict], existing_aids: set, n_analyses: int, n_released: int,
             cockpit_rows: List[Dict], now: Optional[datetime] = None,
             released_aid_by_dyn: Optional[Dict[str, str]] = None,
             structural_ctx: Optional[Dict[str, Dict]] = None) -> List[Dict]:
    """Rein: Rohdaten -> Checks [{name, level, count, detail, items}].
    released_aid_by_dyn: dyn-id -> freigegebene Analyse ihrer Anlageklasse (Kopplung).
    structural_ctx: symbol -> MarketContext(structural) aus dem Cache (Historien-Vertrag)."""
    checks: List[Dict] = []
    active = [d for d in dyn_docs if not d.get("archived")]

    orphans = [d for d in active if (d.get("settings") or {}).get("analysis_id")
               and (d.get("settings") or {}).get("analysis_id") not in existing_aids]
    checks.append({"name": "orphaned_dynamic", "level": "warn" if orphans else "ok", "count": len(orphans),
                   "items": [{"id": d.get("id"), "name": d.get("name"),
                              "analysis_id": (d.get("settings") or {}).get("analysis_id")} for d in orphans],
                   "detail": (f"{len(orphans)} dynamische Strategie(n) zeigen auf gelöschte Lab-Analysen – "
                              "Beweispaket weg, Brücke wirkungslos. Löschen oder auf freigegebene Analyse neu aufsetzen."
                              if orphans else "Alle dynamischen Strategien haben ihre Lab-Analyse")})

    idle = []
    for d in active:
        s = d.get("settings") or {}
        age = _age_days(((d.get("last_state") or {}).get("checked_at")), now)
        if not s.get("auto_check_enabled"):
            idle.append({"id": d.get("id"), "name": d.get("name"), "reason": "Auto-Check aus",
                         "checked_days_ago": round(age, 1) if age is not None else None})
        elif age is None or age > STALE_DAYS:
            idle.append({"id": d.get("id"), "name": d.get("name"),
                         "reason": f"letzter Check vor {round(age, 1) if age is not None else '∞'} Tagen",
                         "checked_days_ago": round(age, 1) if age is not None else None})
    checks.append({"name": "dynamic_idle", "level": "warn" if idle else "ok", "count": len(idle), "items": idle,
                   "detail": (f"{len(idle)} dynamische Strategie(n) laufen nicht (Auto-Check aus / Check veraltet)"
                              if idle else ("Dynamische Strategien werden regelmäßig geprüft" if active
                                            else "Keine aktiven dynamischen Strategien"))})

    no_rel = n_analyses > 0 and n_released == 0
    checks.append({"name": "no_release", "level": "warn" if no_rel else "ok",
                   "count": n_released, "items": [],
                   "detail": (f"{n_analyses} Lab-Analysen, aber keine Freigabe (Shadow/Aktiv) – Struktur-Ebene, "
                              "Reward-Split und Regime-Gate 'lab' bleiben ohne Wirkung. Erste Analyse als Shadow freigeben."
                              if no_rel else (f"{n_released} Lab-Freigabe(n) aktiv" if n_released
                                              else "Noch keine Lab-Analysen"))})

    rel_map = released_aid_by_dyn or {}
    mism = []
    for d in active:
        s = d.get("settings") or {}
        target = rel_map.get(d.get("id"))
        if s.get("follow_release_enabled") and target and target != s.get("analysis_id"):
            mism.append({"id": d.get("id"), "name": d.get("name"),
                         "analysis_id": s.get("analysis_id"), "released_aid": target})
    checks.append({"name": "release_mismatch", "level": "warn" if mism else "ok", "count": len(mism), "items": mism,
                   "detail": (f"{len(mism)} gekoppelte dynamische Strategie(n) basieren nicht auf der freigegebenen "
                              "Lab-Analyse ihrer Anlageklasse – aus der freigegebenen Analyse neu aufbauen "
                              "(Regime-Lab → Strategie zusammenstellen), alte danach archivieren."
                              if mism else "Gekoppelte dynamische Strategien passen zur Lab-Freigabe")})

    low = [r for r in cockpit_rows if not r.get("error") and r.get("observer_reliable")
           and r.get("observer_hit_pct") is not None and float(r["observer_hit_pct"]) < LOW_HIT_PCT]
    checks.append({"name": "observer_low_hit", "level": "info" if low else "ok", "count": len(low),
                   "items": [{"symbol": r.get("symbol"), "hit_pct": r.get("observer_hit_pct"),
                              "n": r.get("observer_n")} for r in low],
                   "detail": (f"Kurzfrist-Erkennung bei {len(low)} Symbol(en) unter {LOW_HIT_PCT:.0f} % Vorwärts-Trefferquote "
                              "(Zufallsniveau) – Labels dort nicht als Einstiegsbegründung nutzen"
                              if low else "Kurzfrist-Erkennung über Zufallsniveau (oder noch ohne Daten)")})

    # Historien-Vertrag: Struktur-Regime, dessen Kerzen nicht für den Detektor-
    # Warmup reichen (Live-Label wäre nicht das Lab-Label) -> Zustand stale.
    short = [{"symbol": s, "history_bars": c.get("history_bars"),
              "required": c.get("history_required_bars")}
             for s, c in sorted((structural_ctx or {}).items())
             if c and c.get("history_ok") is False]
    checks.append({"name": "structural_short_history", "level": "warn" if short else "ok",
                   "count": len(short), "items": short,
                   "detail": (f"Struktur-Regime bei {len(short)} Symbol(en) ohne ausreichende Historie für den "
                              "Detektor-Warmup – Live-Regime wäre nicht das Lab-Regime, Zustand 'stale' "
                              "(Gate/Prompt ohne Struktur). Historienquelle prüfen."
                              if short else "Struktur-Regime läuft auf ausreichender Historie (Lab = Live)")})
    return checks


def overall_level(checks: List[Dict]) -> str:
    levels = {c.get("level") for c in checks}
    if "warn" in levels:
        return "warn"
    if "info" in levels:
        return "info"
    return "ok"


def notify_text(checks: List[Dict]) -> str:
    """Rein: Telegram-/Website-Text nur für Warnungen (Markdown-arm)."""
    warns = [c for c in checks if c.get("level") == "warn"]
    if not warns:
        return ""
    lines = ["⚠️ Regime-Brücke läuft ins Leere:"]
    for c in warns:
        lines.append(f"• {c.get('detail')}")
    lines.append("→ KI-Trader · Analyse · Regime-Cockpit")
    return "\n".join(lines)


# --------------------------------------------------------------------------
async def status(db, force: bool = False) -> Dict:
    now_mono = time.monotonic()
    if not force and _cache["data"] is not None and now_mono - _cache["ts"] < CACHE_TTL_S:
        return _cache["data"]
    from services import regime_cockpit
    dyn = await db.dynamic_strategies.find({}, {"_id": 0, "id": 1, "name": 1, "archived": 1, "symbols": 1,
                                                "settings": 1, "last_state.checked_at": 1}).to_list(100)
    aids = {(d.get("settings") or {}).get("analysis_id") for d in dyn}
    aids.discard(None)
    existing = {a["id"] for a in await db.regime_analyses.find(
        {"id": {"$in": list(aids)}}, {"_id": 0, "id": 1}).to_list(len(aids) or 1)} if aids else set()
    n_analyses = await db.regime_analyses.count_documents({})
    n_released = await db.regime_analyses.count_documents({"release.stage": {"$in": ["shadow", "active"]}})
    rows = [r for r in regime_cockpit._overview_cache.values() if r.get("row")]
    rows = [r["row"] for r in rows if r["row"].get("days") == 14]
    rel_by_dyn: Dict[str, str] = {}
    try:
        from services import regime_release
        from services.setup_asset_class import asset_class_of
        cache: Dict[str, Optional[str]] = {}
        for d in dyn:
            if d.get("archived") or not (d.get("settings") or {}).get("follow_release_enabled"):
                continue
            cls = asset_class_of((d.get("symbols") or ["BTCUSDT"])[0])
            if cls not in cache:
                doc = await regime_release.released_for_class(db, cls)
                cache[cls] = doc.get("id") if doc else None
            if cache[cls]:
                rel_by_dyn[d["id"]] = cache[cls]
    except Exception as e:  # noqa: BLE001
        logger.debug(f"regime_bridge_health release map: {e}")
    structural_ctx: Dict[str, Dict] = {}
    try:
        from services import structural_regime
        structural_ctx = await structural_regime.snapshot_all()
    except Exception as e:  # noqa: BLE001
        logger.debug(f"regime_bridge_health structural snapshot: {e}")
    checks = evaluate(dyn, existing, n_analyses, n_released, rows, released_aid_by_dyn=rel_by_dyn,
                      structural_ctx=structural_ctx)
    data = {"level": overall_level(checks), "checks": checks,
            "checked_at": datetime.now(timezone.utc).isoformat()}
    _cache.update(ts=now_mono, data=data)
    return data


async def run_loop(db, telegram) -> None:
    """Alle 6 h prüfen; Warnungen max. 1×/Tag melden (Website + Telegram)."""
    from services import notifications
    await asyncio.sleep(600)
    while True:
        try:
            data = await status(db, force=True)
            text = notify_text(data["checks"])
            if text:
                await notifications.website_notify(
                    db, "regime_bridge", "Regime-Brücke ohne Wirkung", text,
                    cooldown_min=NOTIFY_COOLDOWN_MIN, source="regime_bridge_health")
                await notifications.telegram_notify(db, telegram, "regime_bridge", text,
                                                    cooldown_min=NOTIFY_COOLDOWN_MIN)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            logger.warning(f"regime_bridge_health loop: {e}")
        await asyncio.sleep(CHECK_EVERY_S)
