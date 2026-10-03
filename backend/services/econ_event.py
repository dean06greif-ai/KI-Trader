"""CPI-/NFP-Event-Setups des KI-Traders – exakt das Muster von services/fomc_event.py
(rein & testbar, dünne DB-Anbindung), nur an US-Datenveröffentlichungen angepasst.

Termine: BLS-Veröffentlichungen 8:30 ET (CPI = Inflationsdaten, NFP = Employment
Situation / Nonfarm Payrolls). Kalender fest hinterlegt (2024–2026, ex-ante).

Phasen (alle Zeiten relativ zur Veröffentlichung T, ET -> UTC via zoneinfo):
  pre   T-60min..T      Vorbereitung: Pre-Range beobachten, keine neuen Positionen
                        in die Zahlen hinein (außer bewusste Event-Planung).
  lock  T..T+5min       KEINE neuen Einstiege (Whipsaw der ersten Minuten).
  post  T+5..T+120min   Handelbare Phase: Whipsaw-Fade + Drift, mehrere Trades erlaubt.

Live-Freischaltung wie beim FOMC-Setup: BESTANDENE Backtest-Validierung
(services/econ_backtest.py) ersetzt die Pflicht-Paper-Trades; zusätzlich muss der
Trader Live explizit einschalten (Opt-in, settings/{key}_config). Rückstufung nach
schlechten Live-Trades läuft über die normalen Playbook-Regeln.

Overfitting-Schutz: feste, ex-ante definierte Regeln ohne Parameter-Optimierung
(econ_backtest.FIXED) + positives Out-of-Sample-Ergebnis Pflicht.
"""
import logging
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

ET = ZoneInfo("America/New_York")

# BLS-Veröffentlichungstermine (8:30 ET). Quelle: bls.gov/schedule/news_release
CPI_RELEASES: List[str] = [
    "2024-01-11", "2024-02-13", "2024-03-12", "2024-04-10", "2024-05-15",
    "2024-06-12", "2024-07-11", "2024-08-14", "2024-09-11", "2024-10-10",
    "2024-11-13", "2024-12-11",
    "2025-01-15", "2025-02-12", "2025-03-12", "2025-04-10", "2025-05-13",
    "2025-06-11", "2025-07-15", "2025-08-12", "2025-09-11", "2025-10-24",
    "2025-11-13", "2025-12-18",
    "2026-01-13", "2026-02-13", "2026-03-11", "2026-04-10", "2026-05-12",
    "2026-06-10", "2026-07-14", "2026-08-12", "2026-09-11", "2026-10-14",
    "2026-11-10", "2026-12-10",
]

NFP_RELEASES: List[str] = [
    "2024-01-05", "2024-02-02", "2024-03-08", "2024-04-05", "2024-05-03",
    "2024-06-07", "2024-07-05", "2024-08-02", "2024-09-06", "2024-10-04",
    "2024-11-01", "2024-12-06",
    "2025-01-10", "2025-02-07", "2025-03-07", "2025-04-04", "2025-05-02",
    "2025-06-06", "2025-07-03", "2025-08-01", "2025-09-05",
    # Okt-2025-Bericht entfiel (Government Shutdown); Sep-Daten kamen am 20.11.
    "2025-11-20", "2025-12-16",
    "2026-01-09", "2026-02-11", "2026-03-06", "2026-04-03", "2026-05-08",
    "2026-06-05", "2026-07-02", "2026-08-07", "2026-09-04", "2026-10-02",
    "2026-11-06", "2026-12-04",
]

PPI_RELEASES: List[str] = [
    "2024-01-12", "2024-02-16", "2024-03-14", "2024-04-11", "2024-05-14",
    "2024-06-13", "2024-07-12", "2024-08-13", "2024-09-12", "2024-10-11",
    "2024-11-14", "2024-12-12",
    "2025-01-14", "2025-02-13", "2025-03-13", "2025-04-11", "2025-05-15",
    "2025-06-12", "2025-07-16", "2025-08-14", "2025-09-10",
    # Okt-2025-PPI entfiel (Government Shutdown); Sep-Daten kamen am 25.11.
    "2025-11-25",
    "2026-01-14", "2026-01-30", "2026-02-27", "2026-03-18", "2026-04-14",
    "2026-05-13", "2026-06-11", "2026-07-15", "2026-08-13", "2026-09-10",
    "2026-10-15", "2026-11-13", "2026-12-15",
]

# BEA Personal Income & Outlays (Core-PCE-Preisindex), 8:30 ET
PCE_RELEASES: List[str] = [
    "2024-01-26", "2024-02-29", "2024-03-29", "2024-04-26", "2024-05-31",
    "2024-06-28", "2024-07-26", "2024-08-30", "2024-09-27", "2024-10-31",
    "2024-11-27", "2024-12-20",
    "2025-01-31", "2025-02-28", "2025-03-28", "2025-04-30", "2025-05-30",
    "2025-06-27", "2025-07-31", "2025-08-29", "2025-09-26", "2025-10-28",
    "2025-11-25", "2025-12-19",
    "2026-01-22", "2026-02-20", "2026-03-13", "2026-04-09", "2026-04-30",
    "2026-05-28", "2026-06-25", "2026-07-30", "2026-08-26", "2026-09-30",
    "2026-10-29", "2026-11-25", "2026-12-23",
]

PRE_MIN = 60
LOCK_MIN = 5
POST_MIN = 120
FAST_INTERVAL_MIN = 5          # Analyse-Takt im Event-Fenster (wie FOMC)

_GUIDE = {
    "pre": ("PHASE: PRE ({mins} min bis {label}). Beobachte die Pre-Range (letzte ~3h). "
            "KEINE neuen Positionen in die Zahlen hinein – bestehende Positionen kritisch "
            "prüfen (SL enger/teilweise schließen). Plane {setup}-Szenarien für die "
            "Post-Phase (Fade der Übertreibung / Drift-Folge nach den Zahlen)."),
    "lock": ("PHASE: LOCK ({label} vor < {mins2} min). Erst-Reaktion ist Whipsaw – KEINE "
             "neuen Einstiege, nur beobachten (Erst-Spike-Richtung + Rückkehr in die "
             "Pre-Range merken)."),
    "post": ("PHASE: POST ({label} vor {since} min). Jetzt ist das {setup}-Setup aktiv "
             "handelbar – MEHRERE Trades im Fenster sind erlaubt: 1) Whipsaw-FADE: "
             "Erst-Spike über die Pre-Range, der zurück in die Range schließt -> "
             "Gegenposition, SL hinter Spike-Extrem, Ziel Range-Mitte/-Gegenseite. "
             "2) DRIFT: nachhaltiger 5m-Schluss jenseits der Pre-Range -> in "
             "Bewegungsrichtung, SL Range-Mitte. Setup-Feld: \"{setup}\". Enge "
             "Struktur-SL, keine FOMO-Entries mitten in die Bewegung."),
}


class EconEvent:
    """Ein Daten-Event (CPI oder NFP) im FOMC-Muster: Kalender, Phasen,
    Einstiegs-Gate, Prompt-Block, Live-Opt-in-State."""

    def __init__(self, key: str, setup_id: str, label: str, dates: List[str]):
        self.key = key
        self.setup_id = setup_id
        self.label = label
        self.dates = dates
        self.fast_interval_min = FAST_INTERVAL_MIN
        self.state_id = f"{key}_config"
        self._state: Dict = {"live_enabled": False, "validation": {}}

    # ---- Kalender (rein) ----
    def release_dt_utc(self, date_str: str) -> datetime:
        d = datetime.strptime(date_str, "%Y-%m-%d")
        return d.replace(hour=8, minute=30, tzinfo=ET).astimezone(timezone.utc)

    def all_releases_utc(self) -> List[datetime]:
        return sorted(self.release_dt_utc(d) for d in self.dates)

    def next_release(self, now: Optional[datetime] = None) -> Optional[datetime]:
        now = now or datetime.now(timezone.utc)
        for dt in self.all_releases_utc():
            if dt + timedelta(minutes=POST_MIN) >= now:
                return dt
        return None

    def past_releases(self, now: Optional[datetime] = None, years: float = 2.0) -> List[datetime]:
        now = now or datetime.now(timezone.utc)
        cutoff = now - timedelta(days=365 * years)
        return [dt for dt in self.all_releases_utc()
                if cutoff <= dt and dt + timedelta(minutes=POST_MIN) < now]

    def phase(self, now: Optional[datetime] = None) -> Tuple[str, Optional[datetime]]:
        """(phase, release_dt): none | pre | lock | post."""
        now = now or datetime.now(timezone.utc)
        dt = self.next_release(now)
        if not dt:
            return "none", None
        if dt - timedelta(minutes=PRE_MIN) <= now < dt:
            return "pre", dt
        if dt <= now < dt + timedelta(minutes=LOCK_MIN):
            return "lock", dt
        if dt + timedelta(minutes=LOCK_MIN) <= now < dt + timedelta(minutes=POST_MIN):
            return "post", dt
        return "none", dt

    def window_active(self, now: Optional[datetime] = None) -> bool:
        return self.phase(now)[0] != "none"

    # ---- Einstiegs-Gate (rein): Setup nur im Fenster, im Lock gar nichts Neues ----
    def entry_block_reason(self, setup: Optional[str],
                           now: Optional[datetime] = None) -> Optional[str]:
        ph, dt = self.phase(now)
        if setup == self.setup_id:
            if ph == "none":
                nxt = f" (nächster Termin {dt.strftime('%d.%m. %H:%M')} UTC)" if dt else ""
                return f"Setup '{self.setup_id}' nur im {self.label}-Event-Fenster handelbar{nxt}"
            if ph == "lock":
                return (f"{self.label}-Lock: erste {LOCK_MIN} min nach den Zahlen – "
                        "kein Einstieg in den Whipsaw")
        elif ph == "lock":
            return (f"{self.label}-Lock: Zahlen vor < {LOCK_MIN} min – neue Einstiege "
                    "pausiert (Whipsaw-Schutz)")
        return None

    # ---- Prompt-Block für die Analyse (rein) ----
    def prompt_block(self, now: Optional[datetime] = None) -> str:
        now = now or datetime.now(timezone.utc)
        ph, dt = self.phase(now)
        if not dt:
            return ""
        if ph == "none":
            hours = (dt - now).total_seconds() / 3600
            if 0 < hours <= 24:
                return (f"=== {self.label.upper()}-KALENDER ===\n{self.label} in {hours:.1f}h "
                        f"({dt.strftime('%H:%M')} UTC). Ab {PRE_MIN} min davor gilt das "
                        "Event-Protokoll (keine neuen Positionen in die Zahlen).")
            return ""
        mins_to = max(0, int((dt - now).total_seconds() // 60))
        since = max(0, int((now - dt).total_seconds() // 60))
        guide = _GUIDE[ph].format(mins=mins_to, mins2=LOCK_MIN, since=since,
                                  label=self.label, setup=self.setup_id)
        return (f"=== {self.label.upper()}-EVENT AKTIV (Prio über normalem Kalender-No-Trade) ===\n"
                f"{self.label}: {dt.strftime('%d.%m.%Y %H:%M')} UTC.\n{guide}")

    # ---- Live-Freischaltung per Backtest-Validierung (State aus settings) ----
    def live_override(self, asset_class: str) -> Optional[Tuple[bool, str]]:
        if not self._state.get("live_enabled"):
            return None
        v = (self._state.get("validation") or {}).get(asset_class) or {}
        if v.get("validated"):
            return True, (f"{self.label}: Backtest-validiert ({v.get('summary', '')}) – "
                          "Bonus-Freischaltung ohne Pflicht-Paper-Trades")
        return None

    async def load_state(self, db) -> Dict:
        try:
            doc = await db.settings.find_one({"_id": self.state_id}) or {}
            self._state = {"live_enabled": bool(doc.get("live_enabled")),
                           "validation": doc.get("validation") or {}}
        except Exception as e:
            logger.warning(f"{self.label} state load failed: {e}")
        return self._state

    async def set_live_enabled(self, db, enabled: bool) -> Dict:
        self._state["live_enabled"] = bool(enabled)
        await db.settings.update_one(
            {"_id": self.state_id}, {"$set": {"live_enabled": bool(enabled)}}, upsert=True)
        return self._state

    async def set_validation(self, db, asset_class: str, validated: bool, summary: str,
                             details: Optional[Dict] = None) -> None:
        self._state.setdefault("validation", {})[asset_class] = {
            "validated": bool(validated), "summary": summary,
            "at": datetime.now(timezone.utc).isoformat(), **(details or {})}
        await db.settings.update_one(
            {"_id": self.state_id}, {"$set": {"validation": self._state["validation"]}},
            upsert=True)

    def status_snapshot(self, now: Optional[datetime] = None) -> Dict:
        now = now or datetime.now(timezone.utc)
        ph, dt = self.phase(now)
        return {
            "event": self.key,
            "label": self.label,
            "setup": self.setup_id,
            "phase": ph,
            "window_active": ph != "none",
            "next_release_utc": dt.isoformat() if dt else None,
            "minutes_to_release": (round((dt - now).total_seconds() / 60)
                                   if dt and dt > now else None),
            "live_enabled": bool(self._state.get("live_enabled")),
            "validation": self._state.get("validation") or {},
            "windows": {"pre_min": PRE_MIN, "lock_min": LOCK_MIN, "post_min": POST_MIN},
            "fast_interval_min": self.fast_interval_min,
        }


EVENTS: Dict[str, EconEvent] = {
    "cpi": EconEvent("cpi", "cpi_event", "CPI", CPI_RELEASES),
    "nfp": EconEvent("nfp", "nfp_event", "NFP", NFP_RELEASES),
    "ppi": EconEvent("ppi", "ppi_event", "PPI", PPI_RELEASES),
    "pce": EconEvent("pce", "pce_event", "PCE", PCE_RELEASES),
}
SETUP_IDS = tuple(ev.setup_id for ev in EVENTS.values())


def get(key: str) -> Optional[EconEvent]:
    return EVENTS.get((key or "").lower())


def by_setup(setup: Optional[str]) -> Optional[EconEvent]:
    for ev in EVENTS.values():
        if ev.setup_id == setup:
            return ev
    return None


def active_event(now: Optional[datetime] = None) -> Optional[EconEvent]:
    for ev in EVENTS.values():
        if ev.window_active(now):
            return ev
    return None


def any_window_active(now: Optional[datetime] = None) -> bool:
    return active_event(now) is not None


def entry_block_reason(setup: Optional[str], now: Optional[datetime] = None) -> Optional[str]:
    """Gate über ALLE Daten-Events (wie fomc_event.entry_block_reason)."""
    for ev in EVENTS.values():
        reason = ev.entry_block_reason(setup, now)
        if reason:
            return reason
    return None


def prompt_blocks(now: Optional[datetime] = None) -> str:
    blocks = [b for b in (ev.prompt_block(now) for ev in EVENTS.values()) if b]
    return "\n\n".join(blocks)


def live_override_for_setup(setup: str, asset_class: str) -> Optional[Tuple[bool, str]]:
    ev = by_setup(setup)
    return ev.live_override(asset_class) if ev else None


async def load_all_states(db) -> None:
    for ev in EVENTS.values():
        await ev.load_state(db)


def status_snapshots(now: Optional[datetime] = None) -> Dict[str, Dict]:
    return {key: ev.status_snapshot(now) for key, ev in EVENTS.items()}
