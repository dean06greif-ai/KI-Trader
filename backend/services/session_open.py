"""Session-Open-Setup: Opening-Range-Kontext für London- und US-Open.

Liefert dem KI-Trader während der ersten 90 Minuten nach London-Open (09:00
deutsche Zeit) bzw. US-Open (15:30 deutsche Zeit, Mo-Fr) einen kompakten
Prompt-Block mit der Opening-Range (erste 15 Minuten) pro Symbol und dem
aktuellen Volatilitäts-Regime (ATR-Ratio kurz/lang aus dem 1m-Candle-Buffer
des Scanners). Modus-Wahl je Regime (Nutzer-Vorgabe: 'beides, je nach Regime'):

  * Vol-Regime hoch    -> Opening-Range-BREAKOUT
  * Vol-Regime niedrig -> Opening-Range-FADE (Fehlausbruch zurück in die Range)
  * normal             -> frei, Struktur/Liquidität entscheidet

Gehandelt wird über das Playbook-Setup 'session_open' (ai_playbook.py), das wie
jedes neue Setup zuerst durch die Paper-Datensammlung / das Reife-Gate läuft.
Alle Berechnungen sind reine Funktionen (testbar); der Block fällt lautlos aus,
wenn keine Session aktiv ist oder Kerzendaten fehlen.
"""
import logging
from datetime import datetime
from typing import Dict, List, Optional
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

BERLIN = ZoneInfo("Europe/Berlin")

SESSIONS = (
    {"id": "london", "name": "London Open", "hour": 9, "minute": 0},
    {"id": "us", "name": "US Open", "hour": 15, "minute": 30},
)
WINDOW_MIN = 90          # so lange ist das Session-Open-Fenster handelbar
RANGE_MIN = 15           # Opening-Range = erste 15 Minuten
VOL_HIGH_RATIO = 1.3     # ATR kurz/lang ab hier: Regime 'hoch' -> Breakout
VOL_LOW_RATIO = 0.85     # darunter: Regime 'niedrig' -> Fade


def active_session(now_berlin: datetime) -> Optional[Dict]:
    """Aktive Open-Session (0..WINDOW_MIN min nach Open, Mo-Fr) oder None."""
    if now_berlin.weekday() >= 5:
        return None
    for s in SESSIONS:
        open_dt = now_berlin.replace(hour=s["hour"], minute=s["minute"],
                                     second=0, microsecond=0)
        mins = (now_berlin - open_dt).total_seconds() / 60.0
        if 0 <= mins < WINDOW_MIN:
            return {**s, "open_dt": open_dt, "minutes_since": round(mins, 1)}
    return None


def opening_range(candles: List[Dict], open_ms: int,
                  range_min: int = RANGE_MIN) -> Optional[Dict]:
    """Opening-Range (High/Low/Breite) aus 1m-Kerzen ab dem Open (rein)."""
    end_ms = open_ms + range_min * 60_000
    rows = [c for c in candles if open_ms <= c["timestamp"] < end_ms]
    if not rows:
        return None
    hi = max(float(c["high"]) for c in rows)
    lo = min(float(c["low"]) for c in rows)
    if lo <= 0:
        return None
    return {"high": hi, "low": lo, "mid": round((hi + lo) / 2, 8),
            "width_pct": round((hi - lo) / lo * 100, 3),
            "complete": rows[-1]["timestamp"] >= end_ms - 60_000,
            "candles": len(rows)}


def _atr(candles: List[Dict], period: int) -> float:
    trs = []
    for i in range(max(1, len(candles) - period), len(candles)):
        h, l = float(candles[i]["high"]), float(candles[i]["low"])
        pc = float(candles[i - 1]["close"])
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    return sum(trs) / len(trs) if trs else 0.0


def vol_regime(candles: List[Dict], fast: int = 15, slow: int = 96) -> Optional[Dict]:
    """Volatilitäts-Regime: ATR(fast) / ATR(slow) auf 1m-Kerzen (rein)."""
    if len(candles) < slow + 2:
        return None
    a_slow = _atr(candles, slow)
    if a_slow <= 0:
        return None
    ratio = _atr(candles, fast) / a_slow
    regime = ("hoch" if ratio >= VOL_HIGH_RATIO
              else "niedrig" if ratio <= VOL_LOW_RATIO else "normal")
    return {"ratio": round(ratio, 2), "regime": regime}


def mode_for(regime: Optional[str]) -> str:
    return {"hoch": "BREAKOUT (Ausbruch/Retest der Range handeln)",
            "niedrig": "FADE (Fehlausbruch zurück in die Range handeln)"}.get(
        regime or "", "frei – Struktur/Liquidität entscheidet")


def context_block(scanner, now: Optional[datetime] = None,
                  max_symbols: int = 8) -> str:
    """Prompt-Block während eines aktiven Session-Open-Fensters, sonst ''."""
    try:
        now_b = now or scanner.berlin_now()
        sess = active_session(now_b)
        if not sess:
            return ""
        open_ms = int(sess["open_dt"].timestamp() * 1000)
        lines = [f"=== SESSION-OPEN: {sess['name']} vor {sess['minutes_since']:.0f} min "
                 f"(Setup 'session_open', Fenster {WINDOW_MIN} min) ==="]
        if sess["minutes_since"] < RANGE_MIN:
            lines.append(f"Opening-Range (erste {RANGE_MIN} min) läuft noch – NICHT "
                         "vorgreifen, erst nach Abschluss der Range handeln.")
        count = 0
        for sym in sorted(getattr(scanner, "candle_buffer", {}) or {}):
            if count >= max_symbols:
                break
            candles = scanner.candle_buffer.get(sym) or []
            rng = opening_range(candles, open_ms)
            if not rng:
                continue
            vr = vol_regime(candles)
            vr_txt = (f", Vol-Regime {vr['regime']} (ATR-Ratio {vr['ratio']}) "
                      f"→ Modus: {mode_for(vr['regime'])}" if vr else "")
            lines.append(f"- {sym}: OR-High {rng['high']:g} / OR-Low {rng['low']:g} "
                         f"(Breite {rng['width_pct']:.2f}%"
                         f"{'' if rng['complete'] else ', Range noch offen'}){vr_txt}")
            count += 1
        if count == 0:
            return ""
        lines.append("Regeln session_open: BREAKOUT = Einstieg beim Ausbruch/Retest über "
                     "OR-High bzw. unter OR-Low, SL Range-Mitte, Ziel 1-2x Range-Breite. "
                     "FADE = Einstieg erst nach Fehlausbruch (Wick über/unter die Range, "
                     "Close zurück innerhalb), SL hinter dem Fakeout-Extrem, Ziel Gegenseite "
                     f"der Range. Nur innerhalb von {WINDOW_MIN} min nach Open; bei "
                     "No-Trade-Fenstern (Makro-Kalender) hat das Verbot Vorrang.")
        return "\n".join(lines)
    except Exception as e:  # noqa: BLE001
        logger.debug(f"session_open context: {e}")
        return ""
