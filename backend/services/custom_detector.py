"""Regel-Detektoren für KI-eigene Setups (10/2026).

Befund: Die 6 KI-eigenen Setups (Playbook 'custom') hatten in ihrer gesamten
Lebenszeit 0 Trades – sie existierten nur als Textbeschreibung, kein Detektor
hat sie je ausgelöst, und das LLM hat sie nie als Label gewählt. Im neuen
System („Detektor schlägt vor, KI entscheidet“) braucht jedes live-fähige
Setup einen Detektor. Dieses Modul liefert ihn als kleine, validierte Regel-
Sprache auf den 5m-Features des Backtesters:

  {"side": "LONG|SHORT", "conditions": [{"f": "rsi", "op": "<", "v": 25}, ...],
   "confirm": true, "sl_atr": 1.0, "sl_lookback": 3, "tp_r": 2.0}

Die Regel entsteht entweder direkt beim Vorschlag (new_setups.rule) oder wird
aus der Beschreibung einmalig vom Research-Analysten übersetzt (translate_rule).
Signale haben dasselbe Format wie die Bibliotheks-Detektoren (Signal-Klasse),
laufen also durch Setup-Trigger, Signal-Broker und Paper-Datensammlung.
"""
import json
import logging
from typing import Dict, List, Optional, Tuple

import numpy as np

from services.setup_backtest.detectors import Features, Signal

logger = logging.getLogger(__name__)

FEATURES: Dict[str, str] = {
    "rsi": "RSI(14) der 5m-Kerze",
    "chg_15m_pct": "Kursänderung der letzten 15 min in %",
    "chg_1h_pct": "Kursänderung der letzten 60 min in %",
    "vol_ratio": "Volumen der 5m-Kerze / 20er-Schnitt",
    "atr_pct": "ATR(14, 5m) in % vom Kurs",
    "vola_ratio": "ATR(15)/ATR(96) – >1 steigende Volatilität",
    "ema_trend": "+1 = EMA20 > EMA50 (5m), -1 = darunter",
    "htf_trend": "+1/-1 = 1h-Trend (EMA20 vs. EMA50), 0 = unklar",
    "vwap_dist_atr": "Abstand Close–Tages-VWAP in ATR (+ = darüber)",
    "range_pos_24": "Lage des Close in der 2h-Range (0 = Tief, 1 = Hoch)",
    "body_atr": "Kerzenkörper der 5m-Kerze in ATR",
    "hour_berlin": "Stunde (Berlin, 0-23)",
}
OPS = {"<": np.less, "<=": np.less_equal, ">": np.greater, ">=": np.greater_equal}
MAX_CONDITIONS = 6
BOUNDS = {"sl_atr": (0.3, 4.0, 1.0), "tp_r": (1.0, 5.0, 2.0), "sl_lookback": (1, 12, 3)}


def validate(rule) -> Tuple[bool, str, Optional[Dict]]:
    """(ok, grund, bereinigte Regel) – nur bekannte Merkmale/Operatoren, Grenzen geklemmt."""
    if not isinstance(rule, dict):
        return False, "Regel fehlt", None
    side = str(rule.get("side") or "").upper()
    if side not in ("LONG", "SHORT"):
        return False, "side muss LONG oder SHORT sein", None
    conds = rule.get("conditions")
    if not isinstance(conds, list) or not 1 <= len(conds) <= MAX_CONDITIONS:
        return False, f"1-{MAX_CONDITIONS} Bedingungen nötig", None
    clean = []
    for c in conds:
        if not isinstance(c, dict) or c.get("f") not in FEATURES or c.get("op") not in OPS:
            return False, f"ungültige Bedingung {c}", None
        try:
            clean.append({"f": c["f"], "op": c["op"], "v": float(c["v"])})
        except (KeyError, TypeError, ValueError):
            return False, f"Wert fehlt in {c}", None
    out = {"side": side, "conditions": clean, "confirm": bool(rule.get("confirm", True))}
    for k, (lo, hi, default) in BOUNDS.items():
        try:
            v = float(rule.get(k, default))
        except (TypeError, ValueError):
            v = default
        out[k] = int(max(lo, min(hi, v))) if k == "sl_lookback" else round(max(lo, min(hi, v)), 2)
    return True, "", out


def feature_arrays(f: Features) -> Dict[str, np.ndarray]:
    c = f.c5
    n = f.n
    cl, op = c.cl, c.op

    def _chg(k):
        out = np.full(n, np.nan)
        if n > k:
            out[k:] = (cl[k:] / cl[:-k] - 1.0) * 100.0
        return out

    with np.errstate(invalid="ignore", divide="ignore"):
        atr = np.where(f.atr > 0, f.atr, np.nan)
        hi24 = np.array([c.hi[max(0, i - 23):i + 1].max() for i in range(n)]) if n else np.zeros(0)
        lo24 = np.array([c.lo[max(0, i - 23):i + 1].min() for i in range(n)]) if n else np.zeros(0)
        span = np.where(hi24 - lo24 > 0, hi24 - lo24, np.nan)
        i60 = np.clip(f.i60, 0, max(0, len(f.trend60) - 1)) if len(f.trend60) else None
        return {
            "rsi": f.rsi,
            "chg_15m_pct": _chg(3), "chg_1h_pct": _chg(12),
            "vol_ratio": np.where(f.vol_avg > 0, c.vol / f.vol_avg, np.nan),
            "atr_pct": atr / cl * 100.0,
            "vola_ratio": np.where(f.atr_slow > 0, f.atr_fast / f.atr_slow, np.nan),
            "ema_trend": np.sign(f.ema20 - f.ema50),
            "htf_trend": (f.trend60[i60].astype(float) if i60 is not None else np.zeros(n)),
            "vwap_dist_atr": (cl - f.vwap) / atr,
            "range_pos_24": (cl - lo24) / span,
            "body_atr": np.abs(cl - op) / atr,
            "hour_berlin": np.array([d.hour for d in f.berlin()], dtype=float),
        }


def detect(f: Features, rule: Dict, only_last: int = 0) -> List[Signal]:
    """Signale einer validierten Regel; only_last>0 prüft nur die letzten n Kerzen (live)."""
    if f is None or f.n < 120:
        return []
    arr = feature_arrays(f)
    mask = np.ones(f.n, dtype=bool)
    for cnd in rule["conditions"]:
        vals = arr[cnd["f"]]
        with np.errstate(invalid="ignore"):
            mask &= np.nan_to_num(OPS[cnd["op"]](vals, cnd["v"]), nan=False).astype(bool)
    c = f.c5
    long = rule["side"] == "LONG"
    if rule.get("confirm", True):
        mask &= (c.cl > c.op) if long else (c.cl < c.op)
    start = max(100, f.n - only_last) if only_last else 100
    out: List[Signal] = []
    lb = int(rule.get("sl_lookback", 3))
    for i in np.flatnonzero(mask[start:]) + start:
        if not f.ok(int(i)) or np.isnan(f.atr[i]):
            continue
        entry = float(c.cl[i])
        buf = float(rule.get("sl_atr", 1.0)) * float(f.atr[i])
        if long:
            sl = float(c.lo[max(0, i - lb + 1):i + 1].min()) - buf
            risk = entry - sl
        else:
            sl = float(c.hi[max(0, i - lb + 1):i + 1].max()) + buf
            risk = sl - entry
        if risk <= 0:
            continue
        d = 1 if long else -1
        out.append(Signal(int(i), rule["side"], entry, sl, entry + d * risk,
                          entry + d * risk * float(rule.get("tp_r", 2.0)), "KI-Regel-Detektor"))
    return out


TRANSLATE_SYSTEM = (
    "Du übersetzt eine Trading-Setup-Beschreibung in eine maschinenlesbare Regel. Antworte NUR mit "
    "JSON: {\"side\": \"LONG|SHORT\", \"conditions\": [{\"f\": Merkmal, \"op\": \"<|<=|>|>=\", "
    "\"v\": Zahl}], \"confirm\": true|false, \"sl_atr\": 0.3-4, \"sl_lookback\": 1-12, \"tp_r\": 1-5}. "
    "Erlaubte Merkmale (5m-Kerze): " + "; ".join(f"{k} = {v}" for k, v in FEATURES.items())
    + ". Max. 6 Bedingungen, nur was die Beschreibung wirklich verlangt (keine Überoptimierung). "
    "Beide Richtungen beschrieben: wähle die Hauptrichtung. Nicht übersetzbar: {\"side\": null}.")


async def translate_rule(engine, desc: str) -> Tuple[Optional[Dict], str]:
    """Beschreibung -> Regel über den Research-Analysten (eine LLM-Anfrage)."""
    text, _p, _m = await engine.generate_for_role(
        "research_analyst", f"Setup-Beschreibung: {desc}", TRANSLATE_SYSTEM, temperature=0.1)
    try:
        raw = json.loads(str(text).strip().strip("`").removeprefix("json").strip())
    except (TypeError, ValueError):
        return None, "Antwort kein JSON"
    ok, why, rule = validate(raw)
    return (rule, "") if ok else (None, why)
