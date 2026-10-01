"""Mindest-SL-Abstand je Anlageklasse (KI-Trader, rein & testbar).

Befund 06/2026 (Prod, 60 Tage KI-Trades): Krypto-Trades mit SL-Abstand < 0,4 %
hatten 17-34 % Trefferquote und waren der größte Verlustblock; ab 0,4 % war das
Ergebnis positiv. Rauschen stoppt zu enge Stops aus, die Gebühren fressen den
Rest. Diese feste Regel verhindert solche Trades VOR der Order – unabhängig vom
Fee-Wächter (der je nach Gebühren/ATR schwankt und autonom gelockert werden kann).

Config (ai_trader_config):
  min_sl_rule_enabled      bool   (Standard an)
  min_sl_pct_by_class      {crypto, resources, indices, forex} in %
  min_sl_apply_collection  bool   (auch Datensammel-Trades, Standard an)
  min_sl_auto_widen        bool   (zu engen SL auf das Minimum erweitern statt
                                   blocken, TPs skalieren mit – Standard an)

Befund 10/2026: Die Engine klemmte Scalp-SL nur auf 0,15 %, die Regel verlangt
aber 0,25–0,5 % -> hunderte Signale (v.a. Forex/Rohstoffe/Indizes und
Detektor-Trigger) wurden verworfen, Setups sammelten keine Daten mehr.
Außerdem blockte der Ausführungs-Check durch Kursdrift/Tick-Rundung
„0.25% < 0.25%“. Daher: Erweitern mit Puffer + Drift-Toleranz bei Ausführung.
"""
from typing import Dict, Optional, Tuple

from services import setup_asset_class as sac

DEFAULT_MIN_SL_PCT: Dict[str, float] = {
    sac.CRYPTO: 0.40,      # Datenbruch bei 0,4 % (darunter WR 17-34 %)
    sac.RESOURCES: 0.50,   # Gold/Öl: breite Dochte, < 0,5 % klar negativ
    sac.INDICES: 0.35,
    sac.FOREX: 0.25,       # engere Tagesrange, aber < 0,25 % reines Rauschen
}
CLASS_LABEL = {sac.CRYPTO: "Krypto", sac.RESOURCES: "Rohstoffe", sac.INDICES: "Indizes",
               sac.FOREX: "Forex"}
BOUNDS = (0.0, 5.0)


def config_values(ai_cfg: Optional[Dict]) -> Dict[str, float]:
    """Wirksame Mindest-SL-% je Klasse (Config überschreibt Standard, rein)."""
    raw = (ai_cfg or {}).get("min_sl_pct_by_class") or {}
    out = dict(DEFAULT_MIN_SL_PCT)
    for cls in out:
        try:
            if cls in raw and raw[cls] is not None:
                out[cls] = round(min(max(float(raw[cls]), BOUNDS[0]), BOUNDS[1]), 3)
        except (TypeError, ValueError):
            continue
    return out


def normalize(updates: Dict) -> Dict[str, float]:
    """Eingaben aus der UI/API säubern (nur bekannte Klassen, Grenzen)."""
    return config_values({"min_sl_pct_by_class": updates or {}})


def min_pct_for(ai_cfg: Optional[Dict], symbol: Optional[str]) -> float:
    return config_values(ai_cfg).get(sac.asset_class_of(symbol), 0.0)


WIDEN_BUFFER = 1.05           # erweitert auf Minimum +5 % (übersteht Drift/Rundung)
EXEC_DRIFT_TOLERANCE = 0.10   # Ausführungs-Check: bis 10 % unter dem Minimum durch Kursdrift ok


def widen(ai_cfg: Optional[Dict], symbol: Optional[str], sl_frac: float, tp1_frac: float,
          tpf_frac: float, collection: bool = False) -> Tuple[float, float, float, Optional[str]]:
    """Zu engen SL (Bruch, 0.004 = 0,4 %) auf das Klassen-Minimum erweitern; TPs
    skalieren im gleichen Verhältnis (CRV bleibt). Liefert (sl, tp1, tpf, notiz|None)."""
    cfg = ai_cfg or {}
    if not cfg.get("min_sl_rule_enabled", True) or not cfg.get("min_sl_auto_widen", True):
        return sl_frac, tp1_frac, tpf_frac, None
    if collection and not cfg.get("min_sl_apply_collection", True):
        return sl_frac, tp1_frac, tpf_frac, None
    cls = sac.asset_class_of(symbol)
    min_pct = config_values(cfg).get(cls, 0.0)
    try:
        sl = float(sl_frac)
    except (TypeError, ValueError):
        return sl_frac, tp1_frac, tpf_frac, None
    if min_pct <= 0 or sl <= 0 or sl * 100.0 + 1e-9 >= min_pct:
        return sl_frac, tp1_frac, tpf_frac, None
    new_sl = min_pct * WIDEN_BUFFER / 100.0
    ratio = new_sl / sl
    note = (f"SL {sl * 100:.2f}%→{new_sl * 100:.2f}% (Mindest-SL {CLASS_LABEL.get(cls, cls)}, "
            f"TPs ×{ratio:.2f})")
    return new_sl, float(tp1_frac) * ratio, float(tpf_frac) * ratio, note


def check(ai_cfg: Optional[Dict], symbol: Optional[str], entry: float, sl: float,
          collection: bool = False, tolerance: float = 0.0) -> Tuple[bool, str]:
    """(ok, grund). Blockt, wenn der SL-Abstand unter dem Klassen-Minimum liegt."""
    cfg = ai_cfg or {}
    if not cfg.get("min_sl_rule_enabled", True):
        return True, ""
    if collection and not cfg.get("min_sl_apply_collection", True):
        return True, ""
    try:
        entry, sl = float(entry), float(sl)
    except (TypeError, ValueError):
        return True, ""
    if entry <= 0 or sl <= 0:
        return True, ""
    cls = sac.asset_class_of(symbol)
    min_pct = config_values(cfg).get(cls, 0.0)
    if min_pct <= 0:
        return True, ""
    dist = abs(entry - sl) / entry * 100.0
    if dist + 1e-9 >= min_pct * (1.0 - max(0.0, min(float(tolerance), 0.5))):
        return True, ""
    return False, (f"Mindest-SL: {dist:.2f}% < {min_pct:g}% ({CLASS_LABEL.get(cls, cls)}) – "
                   f"zu enger Stop wird vom Rauschen ausgestoppt")


def prompt_line(ai_cfg: Optional[Dict]) -> Optional[str]:
    """Regel für den Trade-Rahmen im Prompt (None = aus)."""
    cfg = ai_cfg or {}
    if not cfg.get("min_sl_rule_enabled", True):
        return None
    vals = config_values(cfg)
    parts = ", ".join(f"{CLASS_LABEL[c]} {vals[c]:g}%" for c in DEFAULT_MIN_SL_PCT if vals[c] > 0)
    if cfg.get("min_sl_auto_widen", True):
        return (f"MINDEST-SL je Anlageklasse (feste Regel): {parts}. Liegt der Struktur-SL näher, "
                "setzt das System den SL automatisch auf das Minimum (TPs skalieren mit) – plane "
                "den Trade nur, wenn er mit diesem weiteren Stop noch sinnvoll ist (Daten: < 0,4 % "
                "bei Krypto = Trefferquote < 35 %).")
    return (f"MINDEST-SL je Anlageklasse (feste Regel): {parts}. Liegt der Struktur-SL näher, "
            "gib HOLD – engere Stops werden technisch geblockt (Daten: < 0,4 % bei Krypto = "
            "Trefferquote < 35 %).")
