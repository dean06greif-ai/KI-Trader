"""FOMC-Event-Setup des KI-Traders (rein & testbar, dünne DB-Anbindung).

Zinsentscheid: 2. Meeting-Tag 14:00 ET (Statement), 14:30 ET Pressekonferenz.
Kalender: services/structure_indicators.FOMC_MEETINGS (Fed-Terminplan 2024-2026).

Phasen (alle Zeiten relativ zur Entscheidung T, ET -> UTC via zoneinfo):
  pre   T-90min..T      Vorbereitung: Pre-Range beobachten, keine neuen Positionen
                        in die Entscheidung hinein (außer bewusste fomc_event-Planung).
  lock  T..T+10min      KEINE neuen Einstiege (Whipsaw der ersten Minuten).
  post  T+10..T+150min  Handelbare Phase: Whipsaw-Fade + Drift nach Pressekonferenz,
                        mehrere Trades im Fenster erlaubt.

Live-Freischaltung (Bonus-Regel, vom Trader gewünscht): Für fomc_event reicht
eine BESTANDENE Backtest-Validierung (services/fomc_backtest.py) als Reife-
Nachweis – keine Pflicht-Paper-Trades. Zusätzlich muss der Trader Live explizit
einschalten (Opt-in, settings/fomc_config). Rückstufung nach schlechten
Live-Trades läuft über die normalen Playbook-Regeln (live_block_reason wird im
Live-Gate VOR dieser Freischaltung geprüft und gewinnt immer).

Overfitting-Schutz: Die Validierung stammt aus festen, ex-ante definierten
Regeln ohne Parameter-Optimierung (siehe fomc_backtest.FIXED) und verlangt
positives Ergebnis auch im chronologischen Out-of-Sample-Teil.
"""
import logging
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

from services.structure_indicators import FOMC_MEETINGS

logger = logging.getLogger(__name__)

ET = ZoneInfo("America/New_York")

SETUP_ID = "fomc_event"
PRE_MIN = 90
LOCK_MIN = 10
POST_MIN = 150
FAST_INTERVAL_MIN = 5          # Analyse-Takt im Event-Fenster (statt Zeitplan)
STATE_ID = "fomc_config"       # settings-Dokument: {live_enabled, validation}

_state: Dict = {"live_enabled": False, "validation": {}}


# ---------------------------------------------------------------------------
# Kalender (rein)
# ---------------------------------------------------------------------------
def decision_dt_utc(date_str: str) -> datetime:
    """Zins-Entscheidung: 14:00 ET am 2. Meeting-Tag (DST-korrekt)."""
    d = datetime.strptime(date_str, "%Y-%m-%d")
    return d.replace(hour=14, minute=0, tzinfo=ET).astimezone(timezone.utc)


def all_decisions_utc() -> List[datetime]:
    return sorted(decision_dt_utc(d2) for _, d2 in FOMC_MEETINGS)


def next_decision(now: Optional[datetime] = None) -> Optional[datetime]:
    now = now or datetime.now(timezone.utc)
    for dt in all_decisions_utc():
        if dt + timedelta(minutes=POST_MIN) >= now:
            return dt
    return None


def past_decisions(now: Optional[datetime] = None, years: float = 2.0) -> List[datetime]:
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(days=365 * years)
    return [dt for dt in all_decisions_utc()
            if cutoff <= dt and dt + timedelta(minutes=POST_MIN) < now]


def phase(now: Optional[datetime] = None) -> Tuple[str, Optional[datetime]]:
    """(phase, decision_dt): none | pre | lock | post."""
    now = now or datetime.now(timezone.utc)
    dt = next_decision(now)
    if not dt:
        return "none", None
    if dt - timedelta(minutes=PRE_MIN) <= now < dt:
        return "pre", dt
    if dt <= now < dt + timedelta(minutes=LOCK_MIN):
        return "lock", dt
    if dt + timedelta(minutes=LOCK_MIN) <= now < dt + timedelta(minutes=POST_MIN):
        return "post", dt
    return "none", dt


def window_active(now: Optional[datetime] = None) -> bool:
    return phase(now)[0] != "none"


# ---------------------------------------------------------------------------
# Einstiegs-Gate (rein): fomc_event nur im Fenster, im Lock gar nichts Neues
# ---------------------------------------------------------------------------
def entry_block_reason(setup: Optional[str], now: Optional[datetime] = None) -> Optional[str]:
    ph, dt = phase(now)
    if setup == SETUP_ID:
        if ph == "none":
            nxt = f" (nächster Termin {dt.strftime('%d.%m. %H:%M')} UTC)" if dt else ""
            return f"Setup '{SETUP_ID}' nur im FOMC-Event-Fenster handelbar{nxt}"
        if ph == "lock":
            return (f"FOMC-Lock: erste {LOCK_MIN} min nach dem Zinsentscheid – "
                    "kein Einstieg in den Whipsaw")
    elif ph == "lock":
        return (f"FOMC-Lock: Zinsentscheid vor < {LOCK_MIN} min – neue Einstiege "
                "pausiert (Whipsaw-Schutz)")
    return None


# ---------------------------------------------------------------------------
# Prompt-Block für die Analyse (rein)
# ---------------------------------------------------------------------------
_GUIDE = {
    "pre": ("PHASE: PRE ({mins} min bis zum Zinsentscheid). Beobachte die Pre-Range "
            "(letzte ~3h). KEINE neuen Positionen in die Entscheidung hinein – "
            "bestehende Positionen kritisch prüfen (SL enger/teilweise schließen). "
            "Plane fomc_event-Szenarien für die Post-Phase (Fade der Übertreibung / "
            "Drift-Folge nach der Pressekonferenz)."),
    "lock": ("PHASE: LOCK (Entscheid vor < {mins2} min). Erst-Reaktion ist Whipsaw – "
             "KEINE neuen Einstiege, nur beobachten (Erst-Spike-Richtung + Rückkehr "
             "in die Pre-Range merken)."),
    "post": ("PHASE: POST (Entscheid vor {since} min, Pressekonferenz 30 min nach dem "
             "Statement). Jetzt ist das fomc_event-Setup aktiv handelbar – MEHRERE "
             "Trades im Fenster sind erlaubt: 1) Whipsaw-FADE: Erst-Spike über die "
             "Pre-Range, der zurück in die Range schließt -> Gegenposition, SL hinter "
             "Spike-Extrem, Ziel Range-Mitte/-Gegenseite. 2) DRIFT: nachhaltiger "
             "5m-Schluss jenseits der Pre-Range nach Beginn der Pressekonferenz -> in "
             "Bewegungsrichtung, SL Range-Mitte. Setup-Feld: \"fomc_event\". Enge "
             "Struktur-SL, keine FOMO-Entries mitten in die Bewegung."),
}


def prompt_block(now: Optional[datetime] = None) -> str:
    now = now or datetime.now(timezone.utc)
    ph, dt = phase(now)
    if not dt:
        return ""
    if ph == "none":
        hours = (dt - now).total_seconds() / 3600
        if 0 < hours <= 24:
            return (f"=== FOMC-KALENDER ===\nZinsentscheid in {hours:.1f}h "
                    f"({dt.strftime('%H:%M')} UTC). Ab {PRE_MIN} min davor gilt das "
                    "FOMC-Protokoll (keine neuen Positionen in die Entscheidung).")
        return ""
    mins_to = max(0, int((dt - now).total_seconds() // 60))
    since = max(0, int((now - dt).total_seconds() // 60))
    guide = _GUIDE[ph].format(mins=mins_to, mins2=LOCK_MIN, since=since)
    return ("=== FOMC-EVENT AKTIV (Prio über normalem Kalender-No-Trade) ===\n"
            f"Zinsentscheid: {dt.strftime('%d.%m.%Y %H:%M')} UTC.\n{guide}")


# ---------------------------------------------------------------------------
# Live-Freischaltung per Backtest-Validierung (State aus settings/fomc_config)
# ---------------------------------------------------------------------------
def live_override(asset_class: str) -> Optional[Tuple[bool, str]]:
    """(True, Grund) wenn fomc_event in dieser Klasse per Backtest validiert
    UND Live vom Trader eingeschaltet ist – sonst None (normaler Reife-Weg)."""
    if not _state.get("live_enabled"):
        return None
    v = (_state.get("validation") or {}).get(asset_class) or {}
    if v.get("validated"):
        return True, (f"FOMC: Backtest-validiert ({v.get('summary', '')}) – "
                      "Bonus-Freischaltung ohne Pflicht-Paper-Trades")
    return None


async def load_state(db) -> Dict:
    global _state
    try:
        doc = await db.settings.find_one({"_id": STATE_ID}) or {}
        _state = {"live_enabled": bool(doc.get("live_enabled")),
                  "validation": doc.get("validation") or {}}
    except Exception as e:
        logger.warning(f"FOMC state load failed: {e}")
    return _state


async def set_live_enabled(db, enabled: bool) -> Dict:
    _state["live_enabled"] = bool(enabled)
    await db.settings.update_one(
        {"_id": STATE_ID}, {"$set": {"live_enabled": bool(enabled)}}, upsert=True)
    return _state


async def set_validation(db, asset_class: str, validated: bool, summary: str,
                         details: Optional[Dict] = None) -> None:
    _state.setdefault("validation", {})[asset_class] = {
        "validated": bool(validated), "summary": summary,
        "at": datetime.now(timezone.utc).isoformat(), **(details or {})}
    await db.settings.update_one(
        {"_id": STATE_ID}, {"$set": {"validation": _state["validation"]}}, upsert=True)


def status_snapshot(now: Optional[datetime] = None) -> Dict:
    now = now or datetime.now(timezone.utc)
    ph, dt = phase(now)
    return {
        "setup": SETUP_ID,
        "phase": ph,
        "window_active": ph != "none",
        "next_decision_utc": dt.isoformat() if dt else None,
        "minutes_to_decision": (round((dt - now).total_seconds() / 60)
                                if dt and dt > now else None),
        "live_enabled": bool(_state.get("live_enabled")),
        "validation": _state.get("validation") or {},
        "windows": {"pre_min": PRE_MIN, "lock_min": LOCK_MIN, "post_min": POST_MIN},
        "fast_interval_min": FAST_INTERVAL_MIN,
    }
