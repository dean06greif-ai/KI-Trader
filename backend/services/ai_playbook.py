"""Strategie-Playbook des KI-Traders: vielseitige Daytrading-Setups mit
automatischem Performance-Tracking, Rückstufung schwacher Setups und
automatischer Wieder-Freischaltung – seit 06/2026 GETRENNT JE ANLAGEKLASSE
(services/setup_asset_class.py: crypto / indices / resources / forex).

Die KI wählt pro Trade ein Setup (JSON-Feld "setup"), das Setup wird am Trade
gespeichert (auto_trades.setup). Aus den ECHTEN Trade-Ergebnissen entsteht pro
Setup UND Anlageklasse eine Statistik (Trades, Winrate, PnL) mit Urteil:
  bewährt  – bevorzugen
  neutral  – weiter nutzen, beobachten
  test     – zu wenig Daten, bewusst (klein) antesten
  schwach  – wird NICHT gesperrt, sondern in die Paper-Datensammlung
             ZURÜCKGESTUFT (kein Live-Einstieg in dieser Klasse, Paper läuft
             weiter). Wieder live, sobald seit der Rückstufung genug gute
             Paper-Trades vorliegen (setup_lifecycle.promotion_ok) – spätestens
             beim Re-Test nach RETEST_DAYS.

Reife-Gate, Rückstufung, Parameter-Profile und KI-Überarbeitungen (Revisionen)
gelten immer nur INNERHALB der Anlageklasse. Beim Wechsel (Migration) wurde
der bisherige globale Status in jede Klasse kopiert – live-reife Setups blieben
live-reif, bis sie sich in ihrer Klasse als schwach erweisen.

Ein einzelnes schlecht laufendes Asset stuft ein Setup NICHT zurück: dafür
reduziert services/setup_capital.py zuerst das Kapital für dieses Setup × Asset
und setzt es als letzte Instanz aus; die Rückstufung folgt erst, wenn das
Gesamtbild in der Klasse schlecht ist (setup_lifecycle.breadth_ok).

Historie: Bis 06/2026 wurden 'schwache' Setups 14 Tage hart gesperrt
(`disabled`). `disabled` bleibt aus Kompatibilität (API/UI) als leeres Feld
erhalten. Die globalen Felder (live_blocked/live_ready/...) im State-Dokument
werden weiter gepflegt (abgeleitet aus den Klassen) – für Alt-Aufrufer.

Zusätzlich enthält das Modul die Diversifikations-Guards gegen das beobachtete
Fehlverhalten "viele gleichgerichtete Trades / mehrere Einstiege in derselben
Preiszone" (rein & testbar, technisch in ai_engine._emit_signal erzwungen).
"""
import logging
import re
import time as _time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

from services import setup_asset_class as ac
from services import setup_capital
from services import setup_lifecycle as lifecycle

logger = logging.getLogger(__name__)

STATE_ID = "ai_playbook_state"
LOOKBACK_DAYS = 30
MIN_TRADES_FOR_VERDICT = 5
MIN_TRADES_FOR_BLOCK = 8
RETEST_DAYS = 14

# Kompakte Setup-Bibliothek (bewusst kurz gehalten – Token-Budget!).
SETUPS: Dict[str, str] = {
    "trend_follow": "Trendfolge: Pullback an EMA/Struktur im Trend, SL hinter Pullback-Extrem.",
    "breakout": "Breakout aus Range/Key-Level mit Momentum, Entry Ausbruch/Retest, SL im Level.",
    "squeeze_breakout": "Vol-Squeeze: enge Kompression (BB/Range), Entry beim Impuls, weites Ziel.",
    "mean_reversion": "Mean-Reversion: überdehnter Move (RSI-Extrem/VWAP-Abstand), Gegenposition, enges Ziel.",
    "range_fade": "Range-Fade: Seitwärtsrange, Entry an Range-Kante gegen die Bewegung, SL knapp dahinter.",
    "liquidity_sweep": "Liquidity-Sweep: Stop-Jagd über Hoch/Tief + schnelle Rückeroberung, Entry gegen den Sweep.",
    "momentum_news": "News-/Momentum: starker Impuls (News/Volumen), Entry in Impulsrichtung nach 1. Konsolidierung.",
    "pullback": "Key-Level-Pullback: Entry an starkem S/R mit Bestätigung (Abweisung/Volumen).",
    "swing_trend": "Swing (horizon=swing): HTF-Trend, weiter SL, gestaffelte Ziele/Runner.",
    "hedge": "Hedge: bewusste GEGENposition zur bestehenden Exposure (z.B. Short-Scalp gegen Swing-Long).",
    # Smart-Money-/Multi-Timeframe-Setups (Datenbasis: 'SMC-Zonen'-Zeile je Asset)
    "order_block": "Order-Block (SMC): Rücklauf in frischen 15m/1h-OB, Entry bei 5m-Reaktion, SL hinter Block, TP nächstes Liq-Level.",
    "fvg_fill": "FVG (SMC): Rückkehr in unverfüllte 5m/15m-Imbalance in Trendrichtung, Entry Rejection im Gap, SL hinter Gap, TP Impuls-Ursprung.",
    "htf_range": "HTF-Range: 1h/4h-Range mit mehreren Touches, Entry an Kante nach 15m-Bestätigung, SL außerhalb, TP Mitte/Gegenseite.",
    # Neue Setups 06/2026: funding_fade -> FUNDING-FADE-RADAR, session_open -> SESSION-OPEN-Block
    "funding_fade": "Funding-Fade: extreme Funding (FUNDING-FADE-RADAR) GEGEN die Crowd, Entry erst nach 15m-Strukturbruch, SL hinter Extrem, konservativ.",
    "session_open": "Session-Open: Opening-Range London/US (SESSION-OPEN-Block) – Vol hoch: Range-BREAKOUT (SL Range-Mitte), Vol niedrig: FADE des Fehlausbruchs.",
    "divergence": "Divergenz: RSI/Preis-Divergenz am Extrem, Entry nach Bestätigungskerze gegen den erschöpften Move, SL hinter Extrem, enges Ziel.",
}

SETUP_ENUM = "|".join(SETUPS.keys())

_ALIASES = (
    # Neue Setups zuerst, damit z.B. "funding fade" nicht auf range_fade mappt
    ("funding", "funding_fade"), ("session", "session_open"),
    ("opening", "session_open"), ("orb", "session_open"),
    ("diverg", "divergence"),
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

# Cache der gesperrten Setups – seit 06/2026 immer leer (keine harten Sperren
# mehr, nur Rückstufung); bleibt für API-/UI-Kompatibilität erhalten.
_disabled_cache: Dict[str, Dict] = {}
# Globale (klassenübergreifend abgeleitete) Rückstufungen: nur Setups, die in
# JEDER erlaubten Klasse rückgestuft sind – für Alt-Aufrufer ohne Klasse.
_live_blocked_cache: Dict[str, Dict] = {}
# Globaler Reife-Status (irgendeine Klasse live-reif) für Alt-Aufrufer
_ready_cache: Dict[str, Tuple[bool, str]] = {}
# Je Anlageklasse: {"live_blocked": {...}, "ready": {sid: (ok, why)},
#                   "stats": {...}, "asset_stats": {sid: {symbol: st}}, "revisions": {...}}
_class_cache: Dict[str, Dict] = {}
# Von der KI selbst entdeckte Setups (Shadow-Test über das Reife-Gate)
_custom_cache: Dict[str, Dict] = {}
MAX_CUSTOM_SETUPS = 6
_CUSTOM_ID_RE = re.compile(r"^[a-z][a-z0-9_]{2,23}$")
# KI-Revisionen je Klasse: höchstens eine Überarbeitung pro Setup und RETEST_DAYS
REVISION_COOLDOWN_DAYS = RETEST_DAYS
MAX_REVISION_DESC = 300


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def all_setups() -> Dict[str, str]:
    """Playbook-Bibliothek + aktive KI-eigene Setups (rein)."""
    out = dict(SETUPS)
    for sid, meta in _custom_cache.items():
        out[sid] = f"[KI-Setup] {meta.get('desc', '')}"
    return out


def class_setups(asset_class: str) -> Dict[str, str]:
    """Bibliothek einer Anlageklasse: ohne ausgeschlossene Setups, mit den
    aktiven KI-Revisionen dieser Klasse (rein, Cache aus refresh())."""
    lib = ac.allowed_setups(asset_class, all_setups())
    revs = (_class_cache.get(asset_class) or {}).get("revisions") or {}
    for sid, r in revs.items():
        if sid in lib and r.get("desc"):
            lib[sid] = f"[Rev.{int(r.get('version') or 1)} für {ac.LABELS.get(asset_class, asset_class)}] {r['desc']}"
    return lib


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


async def _feed(db, sid: Optional[str], text: str, **extra) -> None:
    try:
        await db.ai_chat.insert_one({"id": str(uuid.uuid4()), "role": "playbook",
                                     "setup": sid, "text": text, "ts": _now_iso(), **extra})
    except Exception as e:  # noqa: BLE001
        logger.debug(f"Playbook: Feed-Meldung '{sid}' fehlgeschlagen: {e}")


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
    await _feed(db, sid, (f"Neues KI-Setup '{sid}' angelegt (Shadow-Test als Paper-Datensammlung in "
                          f"allen Anlageklassen, Live je Klasse erst nach {MIN_TRADES_FOR_VERDICT}+ "
                          f"Trades mit Urteil bewährt/neutral): {custom[sid]['desc']}"))
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


def revision_allowed(asset_class: str, sid: str, scope: Dict, library: Dict,
                     now: Optional[datetime] = None) -> Tuple[bool, str]:
    """Darf die KI ein Setup für diese Klasse überarbeiten? (rein & testbar)
    Nur rückgestufte Setups der Klasse, höchstens eine Revision je
    REVISION_COOLDOWN_DAYS."""
    now = now or datetime.now(timezone.utc)
    if sid not in library or not ac.setup_allowed(asset_class, sid):
        return False, "Setup in dieser Klasse unbekannt/ausgeschlossen"
    if sid not in (scope.get("live_blocked") or {}):
        return False, "nur rückgestufte Setups dürfen überarbeitet werden"
    prev = (scope.get("revisions") or {}).get(sid)
    if prev:
        try:
            since = datetime.fromisoformat(str(prev.get("since")))
            if now - since < timedelta(days=REVISION_COOLDOWN_DAYS):
                return False, f"letzte Revision erst {(now - since).days} Tage alt"
        except (TypeError, ValueError):
            pass
    return True, "ok"


async def revise_setup(db, asset_class: str, sid: str, desc: str, reason: str = "",
                       source: str = "ki") -> Dict:
    """KI-Überarbeitung eines rückgestuften Setups FÜR EINE ANLAGEKLASSE: neue
    Regelbeschreibung, Validierung startet neu (Paper-Zähler seit jetzt)."""
    sid = normalize_setup(sid) or ""
    desc = str(desc or "").strip()
    if asset_class not in ac.CLASSES:
        return {"status": "rejected", "reason": "unbekannte Anlageklasse"}
    if len(desc) < 20:
        return {"status": "rejected", "reason": "Beschreibung zu kurz (mind. 20 Zeichen)"}
    doc = await db.settings.find_one({"_id": STATE_ID}) or {}
    classes = dict(doc.get("classes") or {})
    scope = dict(classes.get(asset_class) or {})
    ok, why = revision_allowed(asset_class, sid, scope, all_setups())
    if not ok:
        return {"status": "rejected", "reason": why}
    revs = dict(scope.get("revisions") or {})
    version = int((revs.get(sid) or {}).get("version") or 0) + 1
    now = _now_iso()
    revs[sid] = {"desc": desc[:MAX_REVISION_DESC], "since": now, "version": version,
                 "reason": str(reason or "")[:200], "source": source}
    lb = dict(scope.get("live_blocked") or {})
    lb[sid] = {**(lb.get(sid) or {}), "at": now, "paper_since": {"trades": 0},
               "retest_at": (datetime.now(timezone.utc) + timedelta(days=RETEST_DAYS)).isoformat(),
               "reason": f"Revision v{version}: Validierung neu gestartet"}
    eval_since = dict(scope.get("eval_since") or {})
    eval_since[sid] = now
    scope.update({"revisions": revs, "live_blocked": lb, "eval_since": eval_since})
    classes[asset_class] = scope
    await db.settings.update_one({"_id": STATE_ID}, {"$set": {"classes": classes}}, upsert=True)
    _class_cache.setdefault(asset_class, {})["revisions"] = revs
    _class_cache[asset_class]["live_blocked"] = lb
    label = ac.LABELS.get(asset_class, asset_class)
    await _feed(db, sid, (f"Setup '{sid}' für {label} überarbeitet (Revision v{version}"
                          f"{', ' + reason[:120] if reason else ''}): {desc[:200]} – "
                          f"Validierung läuft neu: wieder live nach {lifecycle.MIN_TRADES_PROMOTE} "
                          f"guten Paper-Trades in {label}."), asset_class=asset_class)
    logger.info(f"Playbook: Setup '{sid}' [{label}] Revision v{version} ({source})")
    return {"status": "ok", "id": sid, "version": version, "asset_class": asset_class}


def _blocked_text(setup: str, d: Dict, label: str = "") -> str:
    where = f" in {label}" if label else ""
    return (f"Setup '{setup}' rückgestuft{where} ({d.get('reason', '')}) – läuft als "
            f"Paper-Datensammlung weiter; Live wieder nach {lifecycle.MIN_TRADES_PROMOTE} "
            f"guten Paper-Trades seit Rückstufung (spätestens Re-Test ab "
            f"{str(d.get('retest_at', ''))[:10]})")


def live_block_reason(setup: Optional[str], asset_class: Optional[str] = None) -> Optional[str]:
    """Rückstufung (schwaches Urteil oder Paper/Live-Divergenz) – Cache von
    refresh(). Mit asset_class: Status dieser Klasse; ohne: nur wenn in allen
    Klassen rückgestuft (Alt-Aufrufer)."""
    if not setup:
        return None
    if asset_class:
        d = ((_class_cache.get(asset_class) or {}).get("live_blocked") or {}).get(setup)
        return _blocked_text(setup, d, ac.LABELS.get(asset_class, asset_class)) if d else None
    d = _live_blocked_cache.get(setup)
    return _blocked_text(setup, d) if d else None


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
    Konfidenz. 'schwach' ist rückgestuft (Paper-Datensammlung, live_block_reason)."""
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
    """setup_stats (global) mit 5-Minuten-Cache (Alt-Aufrufer/Fallback)."""
    if _time.time() - _stats_cache["ts"] > STATS_CACHE_SEC:
        _stats_cache["stats"] = await setup_stats(db)
        _stats_cache["ts"] = _time.time()
    return _stats_cache["stats"]


def class_stats(asset_class: str, setup: Optional[str] = None) -> Optional[Dict]:
    """Setup-Statistik der Klasse aus dem letzten refresh() (rein)."""
    st = (_class_cache.get(asset_class) or {}).get("stats") or {}
    return st.get(setup) if setup else st


def asset_stats(asset_class: str, setup: Optional[str], symbol: Optional[str]) -> Optional[Dict]:
    """Setup × Symbol-Statistik der Klasse aus dem letzten refresh() (rein)."""
    return (((_class_cache.get(asset_class) or {}).get("asset_stats") or {})
            .get(setup or "") or {}).get(symbol or "")


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
    """Hart gesperrtes Setup? Seit 06/2026 gibt es keine harten Sperren mehr
    (schwache Setups werden rückgestuft, siehe live_block_reason) – die
    Funktion bleibt für Aufrufer (ai_engine, ai_diagnosis) erhalten und liefert
    nur noch für Alt-Einträge im Cache einen Grund."""
    if not setup:
        return None
    d = _disabled_cache.get(setup)
    if d:
        return (f"Playbook: Setup '{setup}' ist gesperrt (schwache Performance: "
                f"{d.get('reason', '')}) – Re-Test ab {str(d.get('retest_at', ''))[:10]}")
    return None


def _stats_row(r: Dict) -> Dict:
    return {"trades": int(r["trades"]), "wins": int(r["wins"]),
            "pnl": round(float(r.get("pnl") or 0), 2),
            "margin": round(float(r.get("margin") or 0), 2),
            "verdict": verdict_for(int(r["trades"]), int(r["wins"]), float(r.get("pnl") or 0))}


def _stats_match(days: int, live_only: bool, since: Optional[str], paper_only: bool,
                 setup: Optional[str], asset_class: Optional[str]) -> Dict:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    if since and str(since) > cutoff:
        cutoff = str(since)
    match = {"strategy_id": "ai_trader", "status": "closed",
             "opened_at": {"$gte": cutoff}, "setup": {"$nin": [None, ""]}}
    if setup:
        match["setup"] = setup
    if asset_class:
        match["symbol"] = {"$in": ac.symbols_of(asset_class)}
    if live_only:
        match["mode"] = "live"
        match["data_collection"] = {"$ne": True}
    elif paper_only:
        match["$or"] = [{"mode": {"$ne": "live"}}, {"data_collection": True}]
    return match


_GROUP_FIELDS = {"trades": {"$sum": 1},
                 "wins": {"$sum": {"$cond": [{"$gt": ["$realized_pnl", 0]}, 1, 0]}},
                 "pnl": {"$sum": "$realized_pnl"},
                 "margin": {"$sum": {"$ifNull": ["$margin_used", 0]}}}


async def setup_stats(db, days: int = LOOKBACK_DAYS, live_only: bool = False,
                      since: Optional[str] = None, paper_only: bool = False,
                      setup: Optional[str] = None,
                      asset_class: Optional[str] = None) -> Dict[str, Dict]:
    """Echte Trade-Ergebnisse des KI-Traders pro Setup aggregieren.
    live_only=True: nur echte Live-Trades (mode=live, keine Sammel-Trades).
    paper_only=True: nur Paper-/Sammel-Trades. since: ISO-Zeitpunkt (>= cutoff).
    asset_class: nur Symbole dieser Anlageklasse (None = alle)."""
    rows = await db.auto_trades.aggregate([
        {"$match": _stats_match(days, live_only, since, paper_only, setup, asset_class)},
        {"$group": {"_id": "$setup", **_GROUP_FIELDS}},
    ]).to_list(50)
    return {str(r["_id"]): _stats_row(r) for r in rows}


async def setup_symbol_stats(db, asset_class: Optional[str] = None, days: int = LOOKBACK_DAYS,
                             live_only: bool = False) -> Dict[str, Dict[str, Dict]]:
    """Setup × Symbol: {setup: {symbol: stats}} – Basis für die Kapital-
    Zuweisung je Asset (setup_capital) und die Gesamtbild-Regel (breadth_ok)."""
    rows = await db.auto_trades.aggregate([
        {"$match": _stats_match(days, live_only, None, False, None, asset_class)},
        {"$group": {"_id": {"setup": "$setup", "symbol": "$symbol"}, **_GROUP_FIELDS}},
    ]).to_list(500)
    out: Dict[str, Dict[str, Dict]] = {}
    for r in rows:
        key = r.get("_id")
        if not isinstance(key, dict):
            continue
        out.setdefault(str(key.get("setup")), {})[str(key.get("symbol"))] = _stats_row(r)
    return out


def live_divergent(live: Optional[Dict], overall: Optional[Dict]) -> Optional[str]:
    """Rückstufungs-Grund (rein & testbar) – zwei Fälle:
      * Gesamt-Urteil 'schwach' (Paper+Live): früher harte Sperre, jetzt
        Rückstufung in die Paper-Datensammlung.
      * Paper/Live-Divergenz: Gesamtstatistik sagt 'ok', die echten Live-Trades
        sind aber für sich 'schwach' (Befund 02.09.: squeeze_breakout 48 % Win
        im Paper, 7 % live bei n=15).
    None = kein Grund."""
    if overall and overall.get("verdict") == "schwach":
        n = int(overall.get("trades") or 0)
        wr = round(int(overall.get("wins") or 0) / n * 100) if n else 0
        return (f"Gesamt-Urteil schwach: {n} Trades, Winrate {wr}%, "
                f"PnL {float(overall.get('pnl') or 0):+.2f} USDT")
    return lifecycle.demotion_reason(live)


def demotion_candidates(stats: Dict[str, Dict], live_stats: Dict[str, Dict],
                        library, per_symbol: Optional[Dict[str, Dict[str, Dict]]] = None) -> Dict[str, str]:
    """Alle Setups mit Rückstufungs-Grund (rein & testbar): setup -> Grund.
    Mit `per_symbol` (Setup × Symbol) greift die Gesamtbild-Regel: ein einzelnes
    schlechtes Asset stuft nicht zurück (setup_lifecycle.breadth_ok)."""
    out: Dict[str, str] = {}
    for sid in set(stats) | set(live_stats or {}):
        if sid not in library:
            continue
        why = live_divergent((live_stats or {}).get(sid), stats.get(sid))
        if not why:
            continue
        if per_symbol is not None:
            ok, bwhy = lifecycle.breadth_ok(per_symbol.get(sid))
            if not ok:
                logger.info(f"Playbook: '{sid}' bleibt live – {bwhy}")
                continue
            why = f"{why}; {bwhy}"
        out[sid] = why
    return out


def migrate_disabled(disabled: Dict[str, Dict], live_blocked: Dict[str, Dict],
                     now_iso: Optional[str] = None) -> Tuple[Dict[str, Dict], Dict[str, Dict], bool]:
    """Alt-Sperren (`disabled`) in Rückstufungen (`live_blocked`) überführen
    (rein & testbar). Rückgabe: (disabled={}, live_blocked, changed)."""
    if not disabled:
        return {}, dict(live_blocked or {}), False
    lb = dict(live_blocked or {})
    for sid, d in disabled.items():
        if sid in lb:
            continue
        lb[sid] = {"at": d.get("at") or now_iso or _now_iso(),
                   "reason": f"migriert aus Sperre: {d.get('reason', '')}",
                   "retest_at": d.get("retest_at")
                   or (datetime.now(timezone.utc) + timedelta(days=RETEST_DAYS)).isoformat(),
                   "paper_since": {"trades": 0}}
    return {}, lb, True


_LEGACY_CLASS_KEYS = {"krypto": ac.CRYPTO, "indizes": ac.INDICES, "rohstoffe": ac.RESOURCES,
                      "forex": ac.FOREX}


def migrate_to_classes(doc: Dict, now_iso: Optional[str] = None) -> Tuple[Dict[str, Dict], bool]:
    """Einmalige Migration (rein & testbar): den bisherigen globalen Status in
    jede Anlageklasse KOPIEREN (live-reife Setups bleiben live-reif, Rück-
    stufungen bleiben bestehen), ausgeschlossene Setups je Klasse entfallen.
    Alt-Scopes eines früheren Zwischenstands (deutsche Schlüssel) werden auf
    die kanonischen Klassen-IDs übernommen."""
    raw = dict(doc.get("classes") or {})
    classes: Dict[str, Dict] = {}
    changed = any(k not in ac.CLASSES for k in raw)
    for cls in ac.CLASSES:
        cands = [dict(raw[k]) for k, canon in _LEGACY_CLASS_KEYS.items()
                 if canon == cls and k != cls and k in raw]
        if cls in raw:
            cands.append(dict(raw[cls]))
        if not cands:
            continue
        # umfangreichsten Stand als Basis, Rückstufungen aller Stände vereinen
        base = max(cands, key=lambda s: len(s.get("live_blocked") or {}))
        merged_blocked: Dict[str, Dict] = {}
        for s in cands:
            for sid, d in (s.get("live_blocked") or {}).items():
                merged_blocked.setdefault(sid, d)
        classes[cls] = {**base, "live_blocked": merged_blocked}
    if len(classes) == len(ac.CLASSES) and not changed:
        return classes, False
    now_iso = now_iso or _now_iso()
    for cls in ac.CLASSES:
        if cls in classes:
            continue
        def _f(d: Optional[Dict]) -> Dict:
            return {k: v for k, v in dict(d or {}).items() if ac.setup_allowed(cls, k)}
        classes[cls] = {"live_blocked": _f(doc.get("live_blocked")),
                        "live_ready": _f(doc.get("live_ready")),
                        "live_since": _f(doc.get("live_since")),
                        "eval_since": _f(doc.get("eval_since")),
                        lifecycle.STATE_KEY: _f(doc.get(lifecycle.STATE_KEY)),
                        "revisions": {}, "migrated_at": now_iso}
        changed = True
    return classes, changed


async def tf_stats(db, days: int = LOOKBACK_DAYS, asset_class: Optional[str] = None) -> List[Dict]:
    """Performance je Setup × Timeframe (echte geschlossene KI-Trades)."""
    match = _stats_match(days, False, None, False, None, asset_class)
    match["timeframe"] = {"$nin": [None, ""]}
    rows = await db.auto_trades.aggregate([
        {"$match": match},
        {"$group": {"_id": {"setup": "$setup", "tf": "$timeframe"},
                    "trades": {"$sum": 1},
                    "wins": {"$sum": {"$cond": [{"$gt": ["$realized_pnl", 0]}, 1, 0]}},
                    "pnl": {"$sum": "$realized_pnl"}}},
    ]).to_list(200)
    return [{"setup": str(r["_id"].get("setup")), "timeframe": str(r["_id"].get("tf")),
             "trades": int(r["trades"]), "wins": int(r["wins"]),
             "pnl": round(float(r.get("pnl") or 0), 2)} for r in rows
            if isinstance(r.get("_id"), dict)]


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
    lines = ["BESTER TF je Setup (echter PnL):"]
    for sid, r in sorted(best.items(), key=lambda x: -float(x[1].get("pnl") or 0)):
        wr = round(r["wins"] / r["trades"] * 100) if r.get("trades") else 0
        lines.append(f"- {sid}: {r['timeframe']} ({r['trades']}T, WR {wr}%, {r['pnl']:+.2f})")
    return lines


def _custom_unproven(asset_class: str, sid: str) -> bool:
    """KI-Setup in dieser Klasse rückgestuft, schwach oder ohne belastbare Daten?
    (rein; Basis: Klassen-Cache des letzten refresh())"""
    cc = _class_cache.get(asset_class) or {}
    if sid in (cc.get("live_blocked") or {}):
        return True
    st = (cc.get("stats") or {}).get(sid) or {}
    return int(st.get("trades") or 0) < MIN_TRADES_FOR_VERDICT or st.get("verdict") == "schwach"


async def _refresh_scope(db, scope: Dict, library: Dict[str, str], cls: str) -> Tuple[Dict, Dict]:
    """Lebenszyklus EINER Anlageklasse fortschreiben (Rückstufung, Wieder-
    Freischaltung, Profile, Reife-Status). Rückgabe (neuer Scope, Ergebnis)."""
    label = ac.LABELS.get(cls, cls)
    now = datetime.now(timezone.utc)
    stats = await setup_stats(db, asset_class=cls)
    per_symbol = await setup_symbol_stats(db, asset_class=cls)
    changed = False
    live_blocked: Dict[str, Dict] = {k: v for k, v in dict(scope.get("live_blocked") or {}).items()
                                     if k in library}
    live_since: Dict[str, str] = dict(scope.get("live_since") or {})
    # Bewertungs-Startpunkt je Setup: nach einer Rückstufung zählt für Reife
    # UND erneute Rückstufung nur noch die Statistik seit der Rückstufung.
    eval_since: Dict[str, str] = dict(scope.get("eval_since") or {})
    lifted: set = set()
    for sid in list(live_blocked.keys()):
        since_at = str(live_blocked[sid].get("at") or "")
        try:
            paper_since = (await setup_stats(db, paper_only=True, since=since_at,
                                             setup=sid, asset_class=cls)).get(sid)
        except Exception as e:  # noqa: BLE001
            logger.debug(f"Playbook[{label}]: Paper-Statistik seit Rückstufung '{sid}': {e}")
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
            eval_since[sid] = since_at or _now_iso()
            lifted.add(sid)
            changed = True
            how = f"seit Rückstufung {why_paper}" if ok_paper else "Re-Test-Datum erreicht"
            logger.info(f"Playbook[{label}]: Rückstufung '{sid}' aufgehoben ({how})")
            await _feed(db, sid, (f"Setup '{sid}' in {label} wieder LIVE-freigeschaltet ({how}). "
                                  f"Live-Trades werden weiter überwacht."), asset_class=cls)
        elif retest <= now and sid in _custom_cache \
                and (stats.get(sid) or {}).get("verdict") == "schwach" \
                and all(_custom_unproven(c, sid) for c in ac.CLASSES
                        if c != cls and ac.setup_allowed(c, sid)):
            # KI-eigenes Setup bleibt nach dem Re-Test schwach und hat sich in
            # keiner anderen Klasse bewährt -> ausmustern (Platz für die
            # nächste Idee, Historie in custom_retired)
            live_blocked.pop(sid)
            changed = True
            await retire_custom_setup(
                db, sid, f"nach Re-Test in allen Klassen weiterhin schwach: "
                         f"{live_divergent(None, stats.get(sid)) or ''}")
    # Live-Statistik nur seit der letzten Freischaltung
    live_stats: Dict[str, Dict] = {}
    try:
        live_stats = await setup_stats(db, live_only=True, asset_class=cls)
        for sid in list(live_stats.keys()):
            if live_since.get(sid):
                part = await setup_stats(db, live_only=True, since=live_since[sid],
                                         setup=sid, asset_class=cls)
                live_stats[sid] = part.get(sid) or {"trades": 0, "wins": 0, "pnl": 0.0,
                                                    "margin": 0.0, "verdict": "test"}
    except Exception as e:  # noqa: BLE001
        logger.debug(f"Playbook[{label}]: Live-Statistik übersprungen: {e}")
        live_stats = {}
    judge_stats: Dict[str, Dict] = dict(stats)
    for sid, since_iso in eval_since.items():
        if sid not in library or sid in live_blocked:
            continue
        try:
            part = await setup_stats(db, since=since_iso, setup=sid, asset_class=cls)
            judge_stats[sid] = part.get(sid) or {"trades": 0, "wins": 0, "pnl": 0.0,
                                                 "margin": 0.0, "verdict": "test"}
        except Exception as e:  # noqa: BLE001
            logger.debug(f"Playbook[{label}]: Statistik seit Rückstufung '{sid}': {e}")
    for sid, why in demotion_candidates(judge_stats, live_stats, library, per_symbol).items():
        if sid in live_blocked or sid in lifted:
            continue
        live_blocked[sid] = {"at": _now_iso(), "reason": why,
                             "retest_at": (now + timedelta(days=RETEST_DAYS)).isoformat(),
                             "paper_since": {"trades": 0}}
        changed = True
        logger.warning(f"Playbook[{label}]: Setup '{sid}' rückgestuft in die Paper-Datensammlung ({why})")
        await _feed(db, sid, (f"Setup '{sid}' läuft in {label} schwach ({why}) – Live-Einstiege in "
                              f"{label} pausiert, zurückgestuft in die Paper-Datensammlung (keine Sperre). Wieder "
                              f"live nach {lifecycle.MIN_TRADES_PROMOTE} guten Paper-Trades (spätestens "
                              f"Re-Test ab {live_blocked[sid]['retest_at'][:10]}). Die KI darf die "
                              f"Setup-Regeln für {label} überarbeiten (setup_revisions)."),
                    asset_class=cls)
    # ---- Parameter-Profile je Setup (versioniert, Auto-Rollback) – je Klasse ----
    try:
        lc_state, lc_changed = await lifecycle.refresh_profiles(
            db, scope, list(library), LOOKBACK_DAYS, symbols=ac.symbols_of(cls), scope_label=label)
        changed = changed or lc_changed
    except Exception as e:  # noqa: BLE001
        logger.debug(f"Playbook[{label}]: Setup-Profile übersprungen: {e}")
        lc_state = dict(scope.get(lifecycle.STATE_KEY) or {})
    # ---- Reife-Status (Live-Freischaltung) + KI-Feed-Meldung bei Übergang ----
    prev_ready: Dict[str, bool] = dict(scope.get("live_ready") or {})
    ready_now: Dict[str, bool] = {}
    ready_why: Dict[str, str] = {}
    for sid in library:
        ok, _why = live_ready(judge_stats.get(sid) if sid in eval_since else stats.get(sid))
        ready_now[sid] = bool(ok and sid not in live_blocked)
        ready_why[sid] = (str(live_blocked[sid].get("reason") or "rückgestuft")
                          if sid in live_blocked else _why)
    for sid, ok in ready_now.items():
        if ok and not prev_ready.get(sid) and sid not in live_since:
            live_since[sid] = _now_iso()
            changed = True
        if ok and prev_ready.get(sid) is False and sid not in lifted:
            st = stats.get(sid) or {}
            wr = round(st["wins"] / st["trades"] * 100) if st.get("trades") else 0
            await _feed(db, sid, (f"Setup '{sid}' hat in {label} genug Daten gesammelt und ist "
                                  f"jetzt LIVE-freigeschaltet: {st.get('trades', 0)} Trades, Winrate "
                                  f"{wr}%, PnL {st.get('pnl', 0):+.2f} USDT (Urteil: {st.get('verdict', '?')})."),
                        asset_class=cls,
                        stats={"trades": st.get("trades", 0), "winrate": wr,
                               "pnl": st.get("pnl", 0), "verdict": st.get("verdict")})
            logger.info(f"Playbook[{label}]: Setup '{sid}' live-freigeschaltet "
                        f"({st.get('trades', 0)} Trades, WR {wr}%)")
    new_scope = {**scope, "live_blocked": live_blocked, "live_ready": ready_now,
                 "live_since": live_since, "eval_since": eval_since,
                 lifecycle.STATE_KEY: lc_state,
                 "revisions": dict(scope.get("revisions") or {})}
    result = {"stats": stats, "live_stats": live_stats, "judge_stats": judge_stats,
              "per_symbol": per_symbol, "ready_why": ready_why,
              "changed": changed or ready_now != prev_ready}
    return new_scope, result


_refresh_cache: Dict = {"ts": 0.0, "db": None, "data": None}
REFRESH_CACHE_SEC = 60


async def refresh(db, force: bool = False) -> Dict:
    """Statistik je Anlageklasse neu berechnen, schwache Setups zurückstufen
    (Paper-Datensammlung), Wieder-Freischaltungen prüfen.

    Läuft einmal pro Analyse-Zyklus (über context_text) und hält die Klassen-
    Caches für das Live-Gate (ai_engine._setup_live_gate) aktuell. `force`
    ist für Aufrufer reserviert, die den context_text-Cache umgehen wollen."""
    global _disabled_cache, _live_blocked_cache
    doc = await db.settings.find_one({"_id": STATE_ID}) or {}
    set_custom_cache(doc.get("custom") or {})
    library = all_setups()
    changed = False
    # Migration 1: harte Alt-Sperren -> globale Rückstufung (keine Sperren mehr)
    disabled, g_blocked, migrated = migrate_disabled(
        dict(doc.get("disabled") or {}), dict(doc.get("live_blocked") or {}), _now_iso())
    if migrated:
        doc = {**doc, "disabled": {}, "live_blocked": g_blocked}
        changed = True
        logger.info("Playbook: Alt-Sperren in Rückstufungen migriert")
    # Migration 2: globaler Status -> je Anlageklasse kopiert
    classes, cls_migrated = migrate_to_classes(doc, _now_iso())
    if cls_migrated:
        changed = True
        logger.info("Playbook: Setup-Status in die 4 Anlageklassen kopiert "
                    f"(live-reif: {[k for k, v in (doc.get('live_ready') or {}).items() if v]})")
    if cls_migrated and (doc.get("live_ready") or doc.get("live_blocked")) and not doc.get("classes"):
        # Feed-Hinweis nur bei echter Umstellung eines bestehenden Status
        await _feed(db, None, ("Playbook auf Anlageklassen umgestellt: alle Setups wurden in Krypto, "
                               "Indizes, Rohstoffe und Forex kopiert (funding_fade nur Krypto). Reife-Gate, "
                               "Rückstufung und Parameter-Profile gelten ab jetzt je Anlageklasse; "
                               "bisher live-reife Setups bleiben live, bis sie sich in ihrer Klasse als "
                               "schwach erweisen."))
    results: Dict[str, Dict] = {}
    for cls in ac.CLASSES:
        lib_c = ac.allowed_setups(cls, library)
        scope, res = await _refresh_scope(db, dict(classes.get(cls) or {}), lib_c, cls)
        classes[cls] = scope
        results[cls] = res
        changed = changed or res["changed"]
        _class_cache[cls] = {"live_blocked": scope["live_blocked"],
                             "ready": {sid: (scope["live_ready"].get(sid, False), res["ready_why"].get(sid, ""))
                                       for sid in lib_c},
                             "stats": res["stats"], "asset_stats": res["per_symbol"],
                             "revisions": scope.get("revisions") or {},
                             "lifecycle": scope.get(lifecycle.STATE_KEY) or {}}
    # Globale (abgeleitete) Sicht für Alt-Aufrufer/UI: Gesamtstatistik, live-reif
    # sobald in EINER Klasse reif, rückgestuft nur wenn in ALLEN erlaubten Klassen.
    stats = await setup_stats(db)
    live_stats = await setup_stats(db, live_only=True)
    ready_now: Dict[str, bool] = {}
    ready_why: Dict[str, str] = {}
    live_blocked_all: Dict[str, Dict] = {}
    for sid in library:
        allowed = [c for c in ac.CLASSES if ac.setup_allowed(c, sid)]
        ready_c = [c for c in allowed if classes[c]["live_ready"].get(sid)]
        ready_now[sid] = bool(ready_c)
        ready_why[sid] = (f"live in {', '.join(ac.LABELS[c] for c in ready_c)}" if ready_c
                          else "; ".join(f"{ac.LABELS[c]}: {results[c]['ready_why'].get(sid, '')}"
                                         for c in allowed)[:300])
        blocked_c = [c for c in allowed if sid in classes[c]["live_blocked"]]
        if allowed and len(blocked_c) == len(allowed):
            first = classes[blocked_c[0]]["live_blocked"][sid]
            live_blocked_all[sid] = {**first, "classes": blocked_c,
                                     "reason": "in allen Klassen: " + str(first.get("reason", ""))}
    live_since = {sid: min(classes[c]["live_since"][sid] for c in ac.CLASSES
                           if sid in classes[c].get("live_since", {}))
                  for sid in library
                  if any(sid in classes[c].get("live_since", {}) for c in ac.CLASSES)}
    prev_ready = dict(doc.get("live_ready") or {})
    if changed or ready_now != prev_ready or "classes" not in doc:
        await db.settings.update_one(
            {"_id": STATE_ID},
            {"$set": {"disabled": {}, "live_blocked": live_blocked_all,
                      "live_ready": ready_now, "live_since": live_since,
                      "classes": classes, "updated_at": _now_iso()}}, upsert=True)
    _disabled_cache = {}
    _live_blocked_cache = live_blocked_all
    _ready_cache.clear()
    _ready_cache.update({sid: (ready_now[sid], ready_why.get(sid, "")) for sid in ready_now})
    data = {"stats": stats, "disabled": {}, "live_ready": ready_now,
            "live_blocked": live_blocked_all, "live_stats": live_stats,
            "live_since": live_since, "eval_since": dict(doc.get("eval_since") or {}),
            "judge_stats": stats, "lifecycle": dict(doc.get(lifecycle.STATE_KEY) or {}),
            "custom": dict(_custom_cache),
            "classes": {cls: {**classes[cls], "stats": results[cls]["stats"],
                              "live_stats": results[cls]["live_stats"],
                              "judge_stats": results[cls]["judge_stats"],
                              "per_symbol": results[cls]["per_symbol"],
                              "ready_why": results[cls]["ready_why"]}
                        for cls in ac.CLASSES}}
    _refresh_cache.update({"data": data, "ts": _time.time(), "db": id(db)})
    return data


async def _refresh_cached(db) -> Dict:
    """refresh() mit 60-s-Cache: die Gruppen-Läufe EINES Analyse-Zyklus
    brauchen denselben Stand – spart pro Zyklus zwei komplette Neuberechnungen."""
    if _refresh_cache["data"] is not None and _refresh_cache["db"] == id(db) \
            and _time.time() - _refresh_cache["ts"] < REFRESH_CACHE_SEC:
        return _refresh_cache["data"]
    return await refresh(db)


def live_ready_for(setup: Optional[str], stats: Optional[Dict],
                   asset_class: Optional[str] = None) -> Tuple[bool, str]:
    """Live-Gate-Entscheidung für den Analyse-Loop: bevorzugt den von refresh()
    berechneten Reife-Status der Anlageklasse (berücksichtigt Rückstufungen und
    die Statistik seit der Rückstufung); ohne Klasse der globale Status;
    Fallback: live_ready(stats)."""
    if setup and asset_class:
        if not ac.setup_allowed(asset_class, setup):
            return False, ac.excluded_reason(asset_class, setup) or "ausgeschlossen"
        ready = (_class_cache.get(asset_class) or {}).get("ready") or {}
        if setup in ready:
            return ready[setup]
    if setup and setup in _ready_cache:
        return _ready_cache[setup]
    return live_ready(stats)


def _perf_lines(label: str, stats: Dict[str, Dict], live_blocked: Dict[str, Dict],
                per_symbol: Dict[str, Dict[str, Dict]]) -> List[str]:
    """Kompakte Performance-Zeilen einer Klasse inkl. ausgesetzter Assets (rein)."""
    lines = [f"PERFORMANCE {label} (echte KI-Trades, {LOOKBACK_DAYS}T):"]
    if not stats:
        lines.append("- (noch keine Daten in dieser Klasse)")
        return lines
    for sid, st in sorted(stats.items(), key=lambda x: -x[1]["pnl"]):
        wr = round(st["wins"] / st["trades"] * 100) if st["trades"] else 0
        mark = st["verdict"].upper() if st["verdict"] in ("bewährt", "schwach") else st["verdict"]
        if sid in live_blocked:
            ps = (live_blocked[sid].get("paper_since") or {}).get("trades", 0)
            mark += f" – RÜCKGESTUFT (nur Paper; {ps}/{lifecycle.MIN_TRADES_PROMOTE} gute Paper-Trades)"
        weak = [sym for sym, a in (per_symbol.get(sid) or {}).items()
                if setup_capital.asset_factor(a)[0] < 1.0]
        if weak:
            mark += f"; Kapital reduziert/ausgesetzt: {', '.join(sorted(weak))}"
        lines.append(f"- {sid}: {st['trades']}T, WR {wr}%, {st['pnl']:+.2f} → {mark}")
    return lines


async def context_text(db, classes: Optional[List[str]] = None) -> str:
    """Kompakter Prompt-Block: Playbook + echte Performance pro Setup – für die
    übergebenen Anlageklassen (None = alle; der Gruppen-Lauf übergibt nur seine
    Klassen -> spart Tokens und zeigt nur dort erlaubte Setups)."""
    data = await _refresh_cached(db)
    classes = [c for c in (classes or ac.CLASSES) if c in ac.CLASSES] or list(ac.CLASSES)
    labels = "/".join(ac.LABELS[c] for c in classes)
    lib: Dict[str, str] = {}
    for c in classes:
        lib.update(class_setups(c))
    lines = [f"=== STRATEGIE-PLAYBOOK {labels} (Feld \"setup\" – Pflicht bei LONG/SHORT) ==="]
    for c in classes:
        lines.append(f"KLASSE {ac.LABELS[c]}: {ac.HINTS[c]}")
    for sid, desc in lib.items():
        only = [c for c in classes if ac.setup_allowed(c, sid)]
        tag = f" [nur {'/'.join(ac.LABELS[c] for c in only)}]" if len(only) < len(classes) else ""
        lines.append(f"- {sid}{tag}: {desc}")
    demoted: List[str] = []
    for c in classes:
        cd = data["classes"][c]
        lines.extend(_perf_lines(ac.LABELS[c], cd["stats"], cd["live_blocked"], cd.get("per_symbol") or {}))
        try:
            lines.extend(tf_context_lines(await tf_stats(db, asset_class=c)))
        except Exception as e:  # noqa: BLE001
            logger.debug(f"Playbook TF-Statistik übersprungen: {e}")
        lines.extend(lifecycle.context_lines(cd.get(lifecycle.STATE_KEY) or {}))
        demoted += [f"{s}@{c}" for s in cd["live_blocked"]]
    if demoted:
        lines.append("RÜCKGESTUFT (nur Paper-Datensammlung, KEINE Sperre – bewusst weiter für Paper "
                     "nutzen): " + ", ".join(demoted)
                     + ". Du darfst die Regeln eines rückgestuften Setups FÜR DIESE KLASSE neu fassen: "
                       "'setup_revisions': [{\"setup\": id, \"asset_class\": \"crypto|indices|resources|forex\", "
                       "\"desc\": Einstieg/SL/TP/TF in 1-2 Sätzen, \"reason\": kurz}] (max. 1 je Lauf, "
                       "nur bei klarem Befund; Validierung startet dann neu).")
    special = []
    if ac.CRYPTO in classes:
        special.append("funding_fade NUR bei FUNDING-FADE-RADAR-Meldung")
    special.append("session_open NUR im SESSION-OPEN-Fenster")
    lines.append("REGELN: Setup passend zur Marktphase und Klasse; bewährte bevorzugen; Setups ohne "
                 "Daten klein antesten (capital_pct niedrig). REGIME: breakout/squeeze_breakout/"
                 "momentum_news NUR im Trend-/High-Vol-Regime, im Chop range_fade/mean_reversion/"
                 f"htf_range. {', '.join(special)}. MTF: Zone 15m/1h, Trigger 5m, 1m nur Timing. "
                 f"NEUE SETUPS: klar regelbasiertes Muster ohne Playbook-Entsprechung -> 'new_setups' "
                 f"(id snake_case, desc 1-2 Sätze; max. {MAX_CUSTOM_SETUPS} aktiv, meist []). Läuft als "
                 f"Paper-Shadow, live je Klasse nach {lifecycle.MIN_TRADES_PROMOTE}+ Trades mit PnL>0 "
                 f"oder WR≥{lifecycle.PROMOTE_MIN_WINRATE:.0f}%. Kapital je Setup×Asset wird automatisch "
                 "nach Historie skaliert – ein einzelnes schlechtes Asset stuft das Setup nicht zurück.")
    return "\n".join(lines)


def maturity_overview(stats: Dict[str, Dict], disabled: Dict[str, Dict],
                      live_blocked: Optional[Dict[str, Dict]] = None,
                      live_stats: Optional[Dict[str, Dict]] = None,
                      lifecycle_state: Optional[Dict[str, Dict]] = None,
                      judge_stats: Optional[Dict[str, Dict]] = None,
                      asset_class: Optional[str] = None,
                      ready: Optional[Dict[str, bool]] = None,
                      ready_why: Optional[Dict[str, str]] = None,
                      per_symbol: Optional[Dict[str, Dict[str, Dict]]] = None,
                      revisions: Optional[Dict[str, Dict]] = None) -> List[Dict]:
    """Reife-Status pro Setup für die UI (rein & testbar): gesammelte Trades,
    Winrate, PnL, Urteil und ob das Setup live-reif ist. `judge_stats` = die
    für die Reife maßgebliche Statistik (seit Rückstufung), Default = stats.
    Mit asset_class: nur dort erlaubte Setups, `ready`/`ready_why` aus refresh()."""
    out: List[Dict] = []
    live_blocked = live_blocked or {}
    live_stats = live_stats or {}
    lifecycle_state = lifecycle_state or {}
    judge_stats = judge_stats or stats
    per_symbol = per_symbol or {}
    revisions = revisions or {}
    library = ac.allowed_setups(asset_class, all_setups()) if asset_class else all_setups()
    for sid in library:
        st = stats.get(sid)
        if ready is not None and sid in ready:
            ok, why = bool(ready[sid]), (ready_why or {}).get(sid, "")
        else:
            ok, why = live_ready(judge_stats.get(sid, st))
        blocked = sid in (disabled or {})
        lb = sid in live_blocked
        versions = (lifecycle_state.get(sid) or {}).get("versions") or []
        active_v = versions[-1] if versions else None
        is_live = bool(ok and not blocked and not lb)
        trades = int((st or {}).get("trades") or 0)
        wins = int((st or {}).get("wins") or 0)
        ls = live_stats.get(sid) or {}
        assets = []
        for sym, a in sorted((per_symbol.get(sid) or {}).items()):
            f, note = setup_capital.asset_factor(a)
            assets.append({"symbol": sym, "trades": int(a.get("trades") or 0),
                           "winrate": round(int(a.get("wins") or 0) / int(a["trades"]) * 100) if a.get("trades") else 0,
                           "pnl": round(float(a.get("pnl") or 0), 2), "factor": f,
                           "state": "ausgesetzt" if f <= 0 else "reduziert" if f < 1 else "ok"})
        rev = revisions.get(sid)
        out.append({
            "setup": sid,
            "asset_class": asset_class,
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
            "assets": assets,
            "revision": ({"version": rev.get("version"), "since": str(rev.get("since", ""))[:10],
                          "desc": rev.get("desc"), "reason": rev.get("reason")} if rev else None),
            "reason": (f"gesperrt: {disabled[sid].get('reason', '')}" if blocked
                       else f"rückgestuft (Paper-Datensammlung): {live_blocked[sid].get('reason', '')}" if lb
                       else why),
        })
    out.sort(key=lambda r: (-int(r["live_ready"]), -r["trades"]))
    return out


async def status(db) -> Dict:
    """Für API/UI: Playbook, Statistik, Sperren und Reife-Status – global
    (abgeleitet, kompatibel) und je Anlageklasse (`classes`)."""
    data = await refresh(db)
    tf_rows = await tf_stats(db)
    classes_out = {}
    for cls in ac.CLASSES:
        cd = data["classes"][cls]
        classes_out[cls] = {
            "label": ac.LABELS[cls], "symbols": ac.symbols_of(cls),
            "excluded": sorted(ac.EXCLUDED.get(cls, set())),
            "stats": cd["stats"], "live_stats": cd["live_stats"],
            "live_blocked": cd["live_blocked"], "live_ready": cd["live_ready"],
            "revisions": cd.get("revisions") or {},
            "lifecycle": cd.get(lifecycle.STATE_KEY) or {},
            "maturity": maturity_overview(
                cd["stats"], {}, cd["live_blocked"], cd["live_stats"],
                cd.get(lifecycle.STATE_KEY), cd["judge_stats"], asset_class=cls,
                ready=cd["live_ready"], ready_why=cd.get("ready_why"),
                per_symbol=cd.get("per_symbol"), revisions=cd.get("revisions")),
        }
    return {"setups": all_setups(), "stats": data["stats"], "disabled": data["disabled"],
            "live_blocked": data.get("live_blocked") or {},
            "live_stats": data.get("live_stats") or {},
            "custom": data.get("custom") or {},
            "lifecycle": data.get("lifecycle") or {},
            "maturity": maturity_overview(data["stats"], data["disabled"],
                                          data.get("live_blocked"), data.get("live_stats"),
                                          data.get("lifecycle"), data.get("judge_stats"),
                                          ready=data["live_ready"],
                                          ready_why={sid: v[1] for sid, v in _ready_cache.items()}),
            "classes": classes_out, "class_order": list(ac.CLASSES),
            "tf_stats": tf_rows, "best_tf": best_tf_per_setup(tf_rows),
            "lookback_days": LOOKBACK_DAYS, "retest_days": RETEST_DAYS,
            "hard_locks": False,  # seit 06/2026: nur Rückstufung, keine Sperren
            "rules": {"promote_min_trades": lifecycle.MIN_TRADES_PROMOTE,
                      "promote_min_winrate": lifecycle.PROMOTE_MIN_WINRATE,
                      "demote_min_live_trades": lifecycle.DEMOTE_MIN_LIVE_TRADES,
                      "demote_pnl_pct": lifecycle.DEMOTE_PNL_PCT,
                      "demote_max_winrate": lifecycle.DEMOTE_MAX_WINRATE,
                      "breadth_share": round(lifecycle.BREADTH_SHARE, 3),
                      "asset_reduce_factor": setup_capital.ASSET_REDUCE_FACTOR,
                      "asset_suspend_trades": setup_capital.ASSET_SUSPEND_TRADES}}
