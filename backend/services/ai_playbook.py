"""Strategie-Playbook des KI-Traders: vielseitige Daytrading-Setups mit
automatischem Performance-Tracking, Sperren schwacher Setups und Re-Tests.

Die KI wählt pro Trade ein Setup (JSON-Feld "setup"), das Setup wird am Trade
gespeichert (auto_trades.setup). Aus den ECHTEN Trade-Ergebnissen entsteht pro
Setup eine Statistik (Trades, Winrate, PnL) mit Urteil:
  bewährt  – bevorzugen
  neutral  – weiter nutzen, beobachten
  test     – zu wenig Daten, bewusst (klein) antesten
  schwach  – wird automatisch GESPERRT (technisch erzwungen) und nach
             RETEST_DAYS wieder für einen Re-Test freigegeben.

Zusätzlich enthält das Modul die Diversifikations-Guards gegen das beobachtete
Fehlverhalten "viele gleichgerichtete Trades / mehrere Einstiege in derselben
Preiszone" (rein & testbar, technisch in ai_engine._emit_signal erzwungen).
"""
import logging
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

from services import setup_lifecycle as lifecycle

logger = logging.getLogger(__name__)

STATE_ID = "ai_playbook_state"
LOOKBACK_DAYS = 30
MIN_TRADES_FOR_VERDICT = 5
MIN_TRADES_FOR_BLOCK = 8
RETEST_DAYS = 14

# Kompakte Setup-Bibliothek (bewusst kurz gehalten – Token-Budget!).
SETUPS: Dict[str, str] = {
    "trend_follow": "Trendfolge-Scalp: Pullback an EMA/Struktur im laufenden Trend, SL hinter dem Pullback-Extrem.",
    "breakout": "Breakout: Ausbruch aus Range/Key-Level mit Momentum, Einstieg im Ausbruch/Retest, SL zurück im Level.",
    "squeeze_breakout": "Volatilitäts-Squeeze: enge Kompression (BB/Range), Einstieg beim Impuls-Ausbruch, weites Ziel.",
    "mean_reversion": "Mean-Reversion: überdehnter Move (RSI-Extrem/VWAP-Abstand), Gegenposition zurück zum Mittelwert, enges Ziel.",
    "range_fade": "Range-Fade: klare Seitwärtsrange, Einstieg an der Range-Kante gegen die Bewegung, SL knapp dahinter.",
    "liquidity_sweep": "Liquidity-Sweep: Stop-Jagd über markantes Hoch/Tief mit schneller Rückeroberung, Einstieg gegen den Sweep.",
    "momentum_news": "News-/Momentum: starker Impuls (News/Volumen), Einstieg in Impulsrichtung nach erster Konsolidierung.",
    "pullback": "Key-Level-Pullback: Einstieg an starkem Support/Resistance mit Bestätigung (Abweisung/Volumen).",
    "swing_trend": "Swing-Trendfolge (horizon=swing): übergeordneter HTF-Trend, weiter SL, gestaffelte Ziele/Runner.",
    "hedge": "Hedge: bewusste GEGENposition zur bestehenden Exposure zur Risikoreduktion (z.B. Short-Scalp gegen Swing-Long).",
    # Smart-Money-/Multi-Timeframe-Setups (Datenbasis: 'SMC-Zonen'-Zeile je Asset
    # aus services/smc_zones.py auf 5m/15m/1h). Starten ohne Historie -> laufen
    # über das Reife-Gate zuerst als Paper-Datensammlung (Shadow), Live erst nach
    # genug echten Ergebnissen ('bewährt'/'neutral').
    "order_block": "Order-Block (SMC): Rücklauf in einen frischen 15m/1h-Order-Block (letzte Gegenkerze vor Impuls), Einstieg bei 5m-Reaktion in der Zone, SL hinter dem Block, TP zum nächsten Liquiditäts-Level.",
    "fvg_fill": "Fair-Value-Gap (SMC): Rückkehr in eine unverfüllte 5m/15m-Imbalance in Trendrichtung, Einstieg bei Rejection im Gap, SL hinter dem Gap, TP am Ursprung des Impulses.",
    "htf_range": "HTF-Range-Trading: 1h/4h-Seitwärtsrange mit mehrfachen Touches, Einstieg an der Range-Kante nach 15m-Bestätigung (Rejection/Sweep), SL außerhalb der Range, TP Range-Mitte/Gegenseite.",
}

SETUP_ENUM = "|".join(SETUPS.keys())

_ALIASES = (
    ("hedge", "hedge"), ("sweep", "liquidity_sweep"), ("ict", "liquidity_sweep"),
    ("order_block", "order_block"), ("orderblock", "order_block"), ("block", "order_block"),
    ("fvg", "fvg_fill"), ("imbalance", "fvg_fill"), ("fair_value", "fvg_fill"), ("gap", "fvg_fill"),
    ("htf_range", "htf_range"), ("range_trading", "htf_range"),
    ("squeeze", "squeeze_breakout"), ("break", "breakout"),
    ("reversion", "mean_reversion"), ("revert", "mean_reversion"),
    ("range", "range_fade"), ("fade", "range_fade"),
    ("news", "momentum_news"), ("momentum", "momentum_news"),
    ("swing", "swing_trend"), ("pull", "pullback"),
    ("trend", "trend_follow"), ("scalp", "trend_follow"),
)

# Cache der gesperrten Setups (von refresh() aktualisiert – einmal pro Analyse)
_disabled_cache: Dict[str, Dict] = {}
# Live-Sperre bei Paper/Live-Divergenz (Setup läuft im Paper ok, live schwach)
_live_blocked_cache: Dict[str, Dict] = {}
# Von der KI selbst entdeckte Setups (Shadow-Test über das Reife-Gate)
_custom_cache: Dict[str, Dict] = {}
MAX_CUSTOM_SETUPS = 6
_CUSTOM_ID_RE = re.compile(r"^[a-z][a-z0-9_]{2,23}$")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def all_setups() -> Dict[str, str]:
    """Playbook-Bibliothek + aktive KI-eigene Setups (rein)."""
    out = dict(SETUPS)
    for sid, meta in _custom_cache.items():
        out[sid] = f"[KI-Setup] {meta.get('desc', '')}"
    return out


def set_custom_cache(custom: Dict[str, Dict]) -> None:
    global _custom_cache
    _custom_cache = dict(custom or {})


def normalize_setup(raw) -> Optional[str]:
    """Freitext der KI auf eine bekannte Setup-ID mappen (rein, testbar)."""
    s = str(raw or "").strip().lower().replace("-", "_").replace(" ", "_")
    if not s:
        return None
    if s in SETUPS or s in _custom_cache:
        return s
    for needle, target in _ALIASES:
        if needle in s:
            return target
    return "other"


def valid_custom_setup(sid: str, desc: str) -> Tuple[bool, str]:
    """Prüfung eines KI-Setup-Vorschlags (rein & testbar)."""
    sid = str(sid or "").strip().lower().replace("-", "_").replace(" ", "_")
    if not _CUSTOM_ID_RE.match(sid):
        return False, "id muss snake_case (3-24 Zeichen) sein"
    if sid in SETUPS or sid == "other":
        return False, "id kollidiert mit Playbook-Setup"
    if normalize_setup(sid) != "other" and sid not in _custom_cache:
        return False, "id ist nur ein Alias eines bestehenden Setups"
    if len(str(desc or "").strip()) < 20:
        return False, "Beschreibung zu kurz (mind. 20 Zeichen)"
    return True, sid


async def propose_custom_setup(db, sid: str, desc: str, source: str = "ki") -> Dict:
    """Neues KI-Setup anlegen (Shadow: kein Live bis Reife-Gate). Idempotent."""
    ok, res = valid_custom_setup(sid, desc)
    if not ok:
        return {"status": "rejected", "reason": res}
    sid = res
    doc = await db.settings.find_one({"_id": STATE_ID}) or {}
    custom = dict(doc.get("custom") or {})
    retired = dict(doc.get("custom_retired") or {})
    if sid in custom:
        return {"status": "exists", "id": sid}
    if sid in retired:
        return {"status": "rejected", "reason": "Setup wurde bereits getestet und ausgemustert"}
    if len(custom) >= MAX_CUSTOM_SETUPS:
        return {"status": "rejected", "reason": f"max. {MAX_CUSTOM_SETUPS} KI-Setups gleichzeitig"}
    custom[sid] = {"desc": str(desc).strip()[:300], "source": source, "created_at": _now_iso()}
    await db.settings.update_one({"_id": STATE_ID}, {"$set": {"custom": custom}}, upsert=True)
    set_custom_cache(custom)
    try:
        await db.ai_chat.insert_one({
            "id": str(uuid.uuid4()), "role": "playbook", "setup": sid,
            "text": (f"Neues KI-Setup '{sid}' angelegt (Shadow-Test als Paper-Datensammlung, "
                     f"Live erst nach {MIN_TRADES_FOR_VERDICT}+ Trades mit Urteil bewährt/neutral): "
                     f"{custom[sid]['desc']}"),
            "ts": _now_iso()})
    except Exception as e:  # noqa: BLE001
        logger.debug(f"Playbook: Feed-Meldung KI-Setup '{sid}' fehlgeschlagen: {e}")
    logger.info(f"Playbook: KI-Setup '{sid}' angelegt ({source})")
    return {"status": "ok", "id": sid}


async def retire_custom_setup(db, sid: str, reason: str) -> bool:
    doc = await db.settings.find_one({"_id": STATE_ID}) or {}
    custom = dict(doc.get("custom") or {})
    if sid not in custom:
        return False
    retired = dict(doc.get("custom_retired") or {})
    retired[sid] = {**custom.pop(sid), "retired_at": _now_iso(), "reason": reason}
    await db.settings.update_one(
        {"_id": STATE_ID}, {"$set": {"custom": custom, "custom_retired": retired}}, upsert=True)
    set_custom_cache(custom)
    logger.warning(f"Playbook: KI-Setup '{sid}' ausgemustert ({reason})")
    return True


def live_block_reason(setup: Optional[str]) -> Optional[str]:
    """Live-Sperre wegen Paper/Live-Divergenz (Cache von refresh())."""
    if not setup:
        return None
    d = _live_blocked_cache.get(setup)
    if d:
        return (f"Setup '{setup}' live-schwach ({d.get('reason', '')}) – zurückgestuft in "
                f"die Paper-Datensammlung; Live wieder nach {lifecycle.MIN_TRADES_PROMOTE} "
                f"guten Paper-Trades seit Rückstufung (spätestens Re-Test ab "
                f"{str(d.get('retest_at', ''))[:10]})")
    return None


def verdict_for(trades: int, wins: int, pnl: float) -> str:
    """Urteil pro Setup aus echten Ergebnissen (rein, testbar)."""
    if trades < MIN_TRADES_FOR_VERDICT:
        return "test"
    wr = wins / trades * 100
    if wr >= 55 and pnl > 0:
        return "bewährt"
    if trades >= MIN_TRADES_FOR_BLOCK and (wr <= 35 or (pnl < 0 and wr < 45)):
        return "schwach"
    return "neutral"


def live_ready(stats: Optional[Dict]) -> Tuple[bool, str]:
    """Setup-Reife-Gate (rein & testbar): darf ein Setup LIVE gehandelt werden?

    Idee: Live-Trades nur für Setups, zu denen bereits genug echte Daten
    gesammelt wurden ('bewährt'/'neutral'). Neue/unreife Setups ('test' oder
    ohne Daten) laufen zuerst als Paper-Datensammlung weiter – auch bei hoher
    Konfidenz. 'schwach' ist ohnehin komplett gesperrt (disabled_reason)."""
    ok, why = lifecycle.promotion_ok(stats)
    if not ok:
        return False, why
    v = stats.get("verdict") or verdict_for(int(stats.get("trades") or 0),
                                            int(stats.get("wins") or 0),
                                            float(stats.get("pnl") or 0))
    if v == "schwach":
        return False, f"Setup-Urteil '{v}'"
    return True, v


_stats_cache: Dict = {"ts": 0.0, "stats": {}}
STATS_CACHE_SEC = 300


async def cached_setup_stats(db) -> Dict[str, Dict]:
    """setup_stats mit 5-Minuten-Cache (für das Live-Gate im Analyse-Loop)."""
    import time as _time
    if _time.time() - _stats_cache["ts"] > STATS_CACHE_SEC:
        _stats_cache["stats"] = await setup_stats(db)
        _stats_cache["ts"] = _time.time()
    return _stats_cache["stats"]


# Korrelierte Coins zählen als EIN Richtungs-Risiko (Basis-Symbol, ohne USDT)
CORRELATED_GROUPS = [("BTC", "ETH", "SOL")]


def _corr_group(symbol) -> Optional[tuple]:
    base = str(symbol or "").upper().replace("USDT", "").replace("USD", "").strip()
    for g in CORRELATED_GROUPS:
        if base in g:
            return g
    return None


def diversification_check(open_trades: List[Dict], symbol: str, side: str,
                          price: float, max_same_direction: int = 3,
                          min_dist_pct: float = 0.5,
                          setup: Optional[str] = None,
                          correlation_guard: bool = True) -> Tuple[bool, str]:
    """Guards gegen Richtungs-Klumpen und Entry-Cluster (rein & testbar).

    1. Korrelations-Guard: BTC/ETH/SOL zählen als EIN Richtungs-Risiko – ein
       zweiter gleichgerichteter Trade auf einem anderen Coin derselben Gruppe
       wird blockiert (verstecktes Klumpen-Risiko, umgeht sonst das Limit).
    2. Richtungs-Guard: max. N gleichzeitig offene KI-Trades in DIESELBE
       Richtung (0 = aus); korrelierte Coins zählen dabei zusammen nur 1x.
       Ein echter Hedge ist per Definition die Gegenrichtung und wird nie blockiert.
    3. Cluster-Guard: kein weiterer Einstieg auf demselben Symbol in dieselbe
       Richtung, wenn ein offener Entry näher als `min_dist_pct` % liegt.
    """
    side = str(side or "").upper()
    same = [t for t in open_trades
            if str(t.get("side") or "").upper() == side]
    if correlation_guard:
        new_group = _corr_group(symbol)
        if new_group:
            for t in same:
                t_sym = str(t.get("symbol") or "")
                if t_sym != str(symbol) and _corr_group(t_sym) == new_group:
                    return False, (f"Korrelations-Guard: {'/'.join(new_group)} zählen als "
                                   f"EIN Richtungs-Risiko – offener {side} auf {t_sym} "
                                   f"deckt dieses Risiko bereits ab")
    if max_same_direction:
        count = 0
        seen_groups = set()
        for t in same:
            g = _corr_group(t.get("symbol")) if correlation_guard else None
            if g:
                if g in seen_groups:
                    continue
                seen_groups.add(g)
            count += 1
        if count >= max_same_direction:
            syms = ", ".join(sorted({str(t.get("symbol")) for t in same})[:6])
            return False, (f"Richtungs-Guard: bereits {count} offene {side}-Risiken "
                           f"({syms}) – Limit {max_same_direction}, kein weiterer "
                           f"gleichgerichteter Trade")
    try:
        px = float(price or 0)
    except (TypeError, ValueError):
        px = 0.0
    if min_dist_pct and px > 0:
        for t in open_trades:
            if str(t.get("symbol") or "") != str(symbol) \
                    or str(t.get("side") or "").upper() != side:
                continue
            try:
                entry = float(t.get("entry") or 0)
            except (TypeError, ValueError):
                continue
            if entry <= 0:
                continue
            dist = abs(entry - px) / px * 100
            if dist < float(min_dist_pct):
                return False, (f"Cluster-Guard: offener {side} auf {symbol} @ {entry} "
                               f"liegt nur {dist:.2f}% vom neuen Entry entfernt "
                               f"(Mindestabstand {min_dist_pct}%)")
    return True, ""


def disabled_reason(setup: Optional[str]) -> Optional[str]:
    """Gesperrtes Setup? (nutzt den von refresh() gepflegten Cache)."""
    if not setup:
        return None
    d = _disabled_cache.get(setup)
    if d:
        return (f"Playbook: Setup '{setup}' ist gesperrt (schwache Performance: "
                f"{d.get('reason', '')}) – Re-Test ab {str(d.get('retest_at', ''))[:10]}")
    return None


async def setup_stats(db, days: int = LOOKBACK_DAYS, live_only: bool = False,
                      since: Optional[str] = None, paper_only: bool = False,
                      setup: Optional[str] = None) -> Dict[str, Dict]:
    """Echte Trade-Ergebnisse des KI-Traders pro Setup aggregieren.
    live_only=True: nur echte Live-Trades (mode=live, keine Sammel-Trades).
    paper_only=True: nur Paper-/Sammel-Trades. since: ISO-Zeitpunkt (>= cutoff)."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    if since and str(since) > cutoff:
        cutoff = str(since)
    match = {"strategy_id": "ai_trader", "status": "closed",
             "opened_at": {"$gte": cutoff}, "setup": {"$nin": [None, ""]}}
    if setup:
        match["setup"] = setup
    if live_only:
        match["mode"] = "live"
        match["data_collection"] = {"$ne": True}
    elif paper_only:
        match["$or"] = [{"mode": {"$ne": "live"}}, {"data_collection": True}]
    rows = await db.auto_trades.aggregate([
        {"$match": match},
        {"$group": {"_id": "$setup", "trades": {"$sum": 1},
                    "wins": {"$sum": {"$cond": [{"$gt": ["$realized_pnl", 0]}, 1, 0]}},
                    "pnl": {"$sum": "$realized_pnl"},
                    "margin": {"$sum": {"$ifNull": ["$margin_used", 0]}}}},
    ]).to_list(50)
    out = {}
    for r in rows:
        sid = str(r["_id"])
        out[sid] = {"trades": int(r["trades"]), "wins": int(r["wins"]),
                    "pnl": round(float(r.get("pnl") or 0), 2),
                    "margin": round(float(r.get("margin") or 0), 2),
                    "verdict": verdict_for(int(r["trades"]), int(r["wins"]),
                                           float(r.get("pnl") or 0))}
    return out


def live_divergent(live: Optional[Dict], overall: Optional[Dict]) -> Optional[str]:
    """Paper/Live-Divergenz (rein & testbar): Gesamtstatistik sagt 'ok', die
    echten Live-Trades sind aber für sich 'schwach' (Befund 02.09.: squeeze_
    breakout 48 % Win im Paper, 7 % live bei n=15). Rückgabe = Sperrgrund."""
    if overall and overall.get("verdict") == "schwach":
        return None  # ohnehin komplett gesperrt
    return lifecycle.demotion_reason(live)


async def tf_stats(db, days: int = LOOKBACK_DAYS) -> List[Dict]:
    """Performance je Setup × Timeframe (echte geschlossene KI-Trades)."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    rows = await db.auto_trades.aggregate([
        {"$match": {"strategy_id": "ai_trader", "status": "closed",
                    "opened_at": {"$gte": cutoff}, "setup": {"$nin": [None, ""]},
                    "timeframe": {"$nin": [None, ""]}}},
        {"$group": {"_id": {"setup": "$setup", "tf": "$timeframe"},
                    "trades": {"$sum": 1},
                    "wins": {"$sum": {"$cond": [{"$gt": ["$realized_pnl", 0]}, 1, 0]}},
                    "pnl": {"$sum": "$realized_pnl"}}},
    ]).to_list(200)
    return [{"setup": str(r["_id"].get("setup")), "timeframe": str(r["_id"].get("tf")),
             "trades": int(r["trades"]), "wins": int(r["wins"]),
             "pnl": round(float(r.get("pnl") or 0), 2)} for r in rows]


def best_tf_per_setup(rows: List[Dict], min_trades: int = 3) -> Dict[str, Dict]:
    """Pro Setup den historisch besten Timeframe (höchster PnL, mind.
    min_trades Trades) bestimmen – rein & testbar."""
    best: Dict[str, Dict] = {}
    for r in rows:
        if int(r.get("trades") or 0) < min_trades:
            continue
        cur = best.get(r["setup"])
        if cur is None or float(r.get("pnl") or 0) > float(cur.get("pnl") or 0):
            best[r["setup"]] = r
    return best


def tf_context_lines(rows: List[Dict]) -> List[str]:
    """Kompakter Prompt-/UI-Block: bester Timeframe pro Setup (rein & testbar)."""
    best = best_tf_per_setup(rows)
    if not best:
        return []
    lines = ["TIMEFRAME-PERFORMANCE PRO SETUP (bester TF nach echtem PnL):"]
    for sid, r in sorted(best.items(), key=lambda x: -float(x[1].get("pnl") or 0)):
        wr = round(r["wins"] / r["trades"] * 100) if r.get("trades") else 0
        lines.append(f"- {sid}: bester TF {r['timeframe']} ({r['trades']} Trades, "
                     f"WR {wr}%, PnL {r['pnl']:+.2f} USDT)")
    return lines


async def refresh(db) -> Dict:
    """Statistik neu berechnen, schwache Setups sperren, Re-Tests freigeben.

    Läuft einmal pro Analyse-Zyklus (über context_text) und hält den
    Sperr-Cache für _emit_signal aktuell."""
    global _disabled_cache, _live_blocked_cache
    stats = await setup_stats(db)
    doc = await db.settings.find_one({"_id": STATE_ID}) or {}
    disabled: Dict[str, Dict] = dict(doc.get("disabled") or {})
    set_custom_cache(doc.get("custom") or {})
    changed = False
    now = datetime.now(timezone.utc)
    # Re-Test: Sperre nach RETEST_DAYS automatisch aufheben
    for sid in list(disabled.keys()):
        try:
            retest = datetime.fromisoformat(str(disabled[sid].get("retest_at")))
        except (TypeError, ValueError):
            retest = now
        if retest <= now:
            disabled.pop(sid)
            changed = True
            logger.info(f"Playbook: Setup '{sid}' wieder freigegeben (Re-Test)")
    # Schwache Setups sperren
    library = all_setups()
    for sid, st in stats.items():
        if st["verdict"] == "schwach" and sid not in disabled and sid in library:
            wr = round(st["wins"] / st["trades"] * 100) if st["trades"] else 0
            disabled[sid] = {
                "at": _now_iso(),
                "retest_at": (now + timedelta(days=RETEST_DAYS)).isoformat(),
                "reason": f"{st['trades']} Trades, Winrate {wr}%, PnL {st['pnl']:+.2f} USDT",
            }
            changed = True
            logger.warning(f"Playbook: Setup '{sid}' gesperrt ({disabled[sid]['reason']})")
            # KI-eigenes Setup mit Urteil 'schwach' -> ausmustern (kein Re-Test:
            # Platz für die nächste Idee, Historie bleibt in custom_retired)
            if sid in _custom_cache:
                await retire_custom_setup(db, sid, disabled[sid]["reason"])
    # ---- Lebenszyklus: Live-Rückstufung & Wieder-Freischaltung (setup_lifecycle) ----
    live_blocked: Dict[str, Dict] = dict(doc.get("live_blocked") or {})
    live_since: Dict[str, str] = dict(doc.get("live_since") or {})
    lifted: set = set()
    # Rückgestufte Setups: wieder live, sobald SEIT der Rückstufung genug gute
    # Paper-Trades gesammelt wurden – spätestens beim Re-Test-Datum, wenn die
    # Gesamtstatistik weiterhin die Freischaltung rechtfertigt.
    for sid in list(live_blocked.keys()):
        since_at = str(live_blocked[sid].get("at") or "")
        try:
            paper_since = (await setup_stats(db, paper_only=True, since=since_at,
                                             setup=sid)).get(sid)
        except Exception as e:  # noqa: BLE001
            logger.debug(f"Playbook: Paper-Statistik seit Rückstufung '{sid}': {e}")
            paper_since = None
        try:
            retest = datetime.fromisoformat(str(live_blocked[sid].get("retest_at")))
        except (TypeError, ValueError):
            retest = now
        ok_paper, why_paper = lifecycle.promotion_ok(paper_since)
        ok_retest = retest <= now and lifecycle.promotion_ok(stats.get(sid))[0]
        live_blocked[sid]["paper_since"] = paper_since or {"trades": 0}
        if ok_paper or ok_retest:
            live_blocked.pop(sid)
            live_since[sid] = _now_iso()
            lifted.add(sid)
            changed = True
            how = f"seit Rückstufung {why_paper}" if ok_paper else "Re-Test-Datum erreicht"
            logger.info(f"Playbook: Live-Sperre '{sid}' aufgehoben ({how})")
            try:
                await db.ai_chat.insert_one({
                    "id": str(uuid.uuid4()), "role": "playbook", "setup": sid,
                    "text": (f"Setup '{sid}' wieder LIVE-freigeschaltet ({how}). Live-Trades "
                             f"werden weiter überwacht – bei erneuter Schwäche folgt die Rückstufung."),
                    "ts": _now_iso()})
            except Exception:  # noqa: BLE001
                pass
    # Live-Statistik nur seit der letzten Freischaltung (alte Verluste vor einer
    # Rückstufung dürfen ein frisch freigeschaltetes Setup nicht sofort erneut sperren)
    live_stats: Dict[str, Dict] = {}
    try:
        live_stats = await setup_stats(db, live_only=True)
        for sid in list(live_stats.keys()):
            if live_since.get(sid):
                part = await setup_stats(db, live_only=True, since=live_since[sid], setup=sid)
                live_stats[sid] = part.get(sid) or {"trades": 0, "wins": 0, "pnl": 0.0,
                                                    "margin": 0.0, "verdict": "test"}
    except Exception as e:  # noqa: BLE001
        logger.debug(f"Playbook: Live-Statistik übersprungen: {e}")
        live_stats = {}
    for sid, lst in live_stats.items():
        why = live_divergent(lst, stats.get(sid))
        if why and sid not in live_blocked and sid in library:
            live_blocked[sid] = {"at": _now_iso(), "reason": why,
                                 "retest_at": (now + timedelta(days=RETEST_DAYS)).isoformat(),
                                 "paper_since": {"trades": 0}}
            changed = True
            logger.warning(f"Playbook: Setup '{sid}' LIVE-gesperrt (Rückstufung: {why})")
            try:
                await db.ai_chat.insert_one({
                    "id": str(uuid.uuid4()), "role": "playbook", "setup": sid,
                    "text": (f"Setup '{sid}' läuft live deutlich schlechter als im Paper "
                             f"({why}) – Live-Einstiege pausiert, zurückgestuft in die "
                             f"Paper-Datensammlung. Wieder live nach "
                             f"{lifecycle.MIN_TRADES_PROMOTE} guten Paper-Trades "
                             f"(spätestens Re-Test ab {live_blocked[sid]['retest_at'][:10]})."),
                    "ts": _now_iso()})
            except Exception:  # noqa: BLE001
                pass
    # ---- Parameter-Profile je Setup (versioniert, Auto-Rollback) ----
    try:
        lc_state, lc_changed = await lifecycle.refresh_profiles(db, doc, list(library), LOOKBACK_DAYS)
        changed = changed or lc_changed
    except Exception as e:  # noqa: BLE001
        logger.debug(f"Playbook: Setup-Profile übersprungen: {e}")
        lc_state = dict(doc.get(lifecycle.STATE_KEY) or {})
    # ---- Reife-Status (Live-Freischaltung) + KI-Feed-Meldung bei Übergang ----
    prev_ready: Dict[str, bool] = dict(doc.get("live_ready") or {})
    ready_now: Dict[str, bool] = {}
    for sid in library:
        ok, _why = live_ready(stats.get(sid))
        ready_now[sid] = bool(ok and sid not in disabled and sid not in live_blocked)
    # Meldung nur bei echtem Übergang (vorher explizit NICHT reif) –
    # beim allerersten Lauf wird der Status still initialisiert (kein Spam).
    for sid, ok in ready_now.items():
        if ok and not prev_ready.get(sid) and sid not in live_since:
            live_since[sid] = _now_iso()
            changed = True
        if ok and prev_ready.get(sid) is False and sid not in lifted:
            st = stats.get(sid) or {}
            wr = round(st["wins"] / st["trades"] * 100) if st.get("trades") else 0
            try:
                await db.ai_chat.insert_one({
                    "id": str(uuid.uuid4()), "role": "playbook", "setup": sid,
                    "text": (f"Setup '{sid}' hat genug Daten gesammelt und ist "
                             f"jetzt LIVE-freigeschaltet: {st.get('trades', 0)} "
                             f"Trades, Winrate {wr}%, PnL {st.get('pnl', 0):+.2f} "
                             f"USDT (Urteil: {st.get('verdict', '?')})."),
                    "stats": {"trades": st.get("trades", 0), "winrate": wr,
                              "pnl": st.get("pnl", 0),
                              "verdict": st.get("verdict")},
                    "ts": _now_iso(),
                })
                logger.info(f"Playbook: Setup '{sid}' live-freigeschaltet "
                            f"({st.get('trades', 0)} Trades, WR {wr}%)")
            except Exception as e:
                logger.warning(f"Playbook: Feed-Meldung '{sid}' fehlgeschlagen: {e}")
    if changed or ready_now != prev_ready or "disabled" not in doc:
        await db.settings.update_one(
            {"_id": STATE_ID},
            {"$set": {"disabled": disabled, "live_blocked": live_blocked,
                      "live_ready": ready_now, "live_since": live_since,
                      lifecycle.STATE_KEY: lc_state,
                      "updated_at": _now_iso()}}, upsert=True)
    _disabled_cache = disabled
    _live_blocked_cache = live_blocked
    return {"stats": stats, "disabled": disabled, "live_ready": ready_now,
            "live_blocked": live_blocked, "live_stats": live_stats,
            "live_since": live_since, "lifecycle": lc_state,
            "custom": dict(_custom_cache)}


async def context_text(db) -> str:
    """Kompakter Prompt-Block: Playbook + echte Performance pro Setup."""
    data = await refresh(db)
    stats, disabled = data["stats"], data["disabled"]
    live_blocked = data.get("live_blocked") or {}
    lines = ["=== STRATEGIE-PLAYBOOK (Feld \"setup\" – Pflicht bei LONG/SHORT) ==="]
    for sid, desc in all_setups().items():
        lines.append(f"- {sid}: {desc}")
    lines.append("PERFORMANCE PRO SETUP (echte KI-Trades, letzte "
                 f"{LOOKBACK_DAYS} Tage):")
    if stats:
        for sid, st in sorted(stats.items(), key=lambda x: -x[1]["pnl"]):
            wr = round(st["wins"] / st["trades"] * 100) if st["trades"] else 0
            mark = st["verdict"].upper() if st["verdict"] in ("bewährt", "schwach") else st["verdict"]
            if sid in disabled:
                mark = "GESPERRT – nicht nutzen"
            elif sid in live_blocked:
                ps = (live_blocked[sid].get("paper_since") or {}).get("trades", 0)
                mark += (f" – RÜCKGESTUFT (live schwächer als Paper), nur Datensammlung; "
                         f"wieder live nach {lifecycle.MIN_TRADES_PROMOTE} guten Paper-Trades "
                         f"({ps}/{lifecycle.MIN_TRADES_PROMOTE} seit Rückstufung)")
            lines.append(f"- {sid}: {st['trades']} Trades, WR {wr}%, "
                         f"PnL {st['pnl']:+.2f} USDT → {mark}")
    else:
        lines.append("- (noch keine Setup-Daten – Statistik entsteht mit jedem Trade)")
    try:
        lines.extend(tf_context_lines(await tf_stats(db)))
    except Exception as e:
        logger.debug(f"Playbook TF-Statistik übersprungen: {e}")
    lines.extend(lifecycle.context_lines(data.get("lifecycle") or {}))
    if disabled:
        lines.append("GESPERRTE SETUPS (technisch blockiert): "
                     + ", ".join(f"{s} (Re-Test ab {str(d.get('retest_at', ''))[:10]})"
                                 for s, d in disabled.items()))
    lines.append("REGELN: Wähle das Setup passend zur Marktphase. Bevorzuge BEWÄHRTE "
                 "Setups, meide gesperrte. Setups ohne Daten bewusst klein antesten "
                 "(capital_pct niedrig), damit die Statistik wachsen kann. Nutze die "
                 "ganze Bandbreite (auch swing_trend, hedge, order_block, fvg_fill, htf_range) "
                 "statt immer dasselbe Muster. MULTI-TIMEFRAME: Zone/Struktur auf 15m/1h "
                 "(SMC-Zonen, Range-Check), Trigger auf 5m, 1m nur Timing. "
                 f"SETUP-ENTDECKUNG: Erkennst du in den Daten ein wiederkehrendes, klar "
                 f"regelbasiertes Muster, das KEINEM Playbook-Setup entspricht, schlage es "
                 f"unter 'new_setups' vor (id snake_case, desc = Einstieg/SL/TP/Timeframe in "
                 f"1-2 Sätzen; max. {MAX_CUSTOM_SETUPS} aktive KI-Setups, meist leere Liste). Es "
                 "läuft dann automatisch als Shadow-Test (Paper-Datensammlung) und wird "
                 f"nach {lifecycle.MIN_TRADES_PROMOTE}+ Trades mit positivem PnL oder WR ≥ "
                 f"{lifecycle.PROMOTE_MIN_WINRATE:.0f} % live freigeschaltet oder ausgemustert. "
                 "LEBENSZYKLUS: Live-Setups werden weiter gemessen; läuft ein Setup live "
                 f"nach ≥{lifecycle.DEMOTE_MIN_LIVE_TRADES} Trades stark ins Minus, geht es "
                 "zurück in die Paper-Datensammlung und wird dort weiter optimiert.")
    return "\n".join(lines)


def maturity_overview(stats: Dict[str, Dict], disabled: Dict[str, Dict],
                      live_blocked: Optional[Dict[str, Dict]] = None,
                      live_stats: Optional[Dict[str, Dict]] = None,
                      lifecycle_state: Optional[Dict[str, Dict]] = None) -> List[Dict]:
    """Reife-Status pro Setup für die UI (rein & testbar): gesammelte Trades,
    Winrate, PnL, Urteil und ob das Setup live-reif ist."""
    out: List[Dict] = []
    live_blocked = live_blocked or {}
    live_stats = live_stats or {}
    lifecycle_state = lifecycle_state or {}
    for sid in all_setups():
        st = stats.get(sid)
        ok, why = live_ready(st)
        blocked = sid in (disabled or {})
        lb = sid in live_blocked
        versions = (lifecycle_state.get(sid) or {}).get("versions") or []
        active_v = versions[-1] if versions else None
        is_live = bool(ok and not blocked and not lb)
        trades = int((st or {}).get("trades") or 0)
        wins = int((st or {}).get("wins") or 0)
        ls = live_stats.get(sid) or {}
        out.append({
            "setup": sid,
            "custom": sid in _custom_cache,
            "trades": trades,
            "winrate": round(wins / trades * 100) if trades else 0,
            "pnl": round(float((st or {}).get("pnl") or 0), 2),
            "live_trades": int(ls.get("trades") or 0),
            "live_winrate": (round(int(ls.get("wins") or 0) / int(ls["trades"]) * 100)
                             if ls.get("trades") else 0),
            "live_pnl": round(float(ls.get("pnl") or 0), 2),
            "verdict": (st or {}).get("verdict") or ("test" if trades else "keine Daten"),
            "live_ready": is_live,
            "phase": lifecycle.phase_of(sid, is_live, live_blocked, disabled),
            "paper_since_demotion": int(((live_blocked.get(sid) or {}).get("paper_since") or {})
                                        .get("trades") or 0) if lb else None,
            "profile": ({"version": active_v.get("v"), "note": active_v.get("note"),
                         "since": str(active_v.get("since", ""))[:10],
                         "params": active_v.get("params"),
                         "stats": active_v.get("stats"), "versions": len(versions)}
                        if active_v else None),
            "reason": (f"gesperrt: {disabled[sid].get('reason', '')}" if blocked
                       else f"live pausiert: {live_blocked[sid].get('reason', '')}" if lb
                       else why),
        })
    out.sort(key=lambda r: (-int(r["live_ready"]), -r["trades"]))
    return out


async def status(db) -> Dict:
    """Für API/UI: Playbook, Statistik, Sperren und Reife-Status."""
    data = await refresh(db)
    tf_rows = await tf_stats(db)
    return {"setups": all_setups(), "stats": data["stats"], "disabled": data["disabled"],
            "live_blocked": data.get("live_blocked") or {},
            "live_stats": data.get("live_stats") or {},
            "custom": data.get("custom") or {},
            "lifecycle": data.get("lifecycle") or {},
            "maturity": maturity_overview(data["stats"], data["disabled"],
                                          data.get("live_blocked"), data.get("live_stats"),
                                          data.get("lifecycle")),
            "tf_stats": tf_rows, "best_tf": best_tf_per_setup(tf_rows),
            "lookback_days": LOOKBACK_DAYS, "retest_days": RETEST_DAYS,
            "rules": {"promote_min_trades": lifecycle.MIN_TRADES_PROMOTE,
                      "promote_min_winrate": lifecycle.PROMOTE_MIN_WINRATE,
                      "demote_min_live_trades": lifecycle.DEMOTE_MIN_LIVE_TRADES,
                      "demote_pnl_pct": lifecycle.DEMOTE_PNL_PCT,
                      "demote_max_winrate": lifecycle.DEMOTE_MAX_WINRATE}}
