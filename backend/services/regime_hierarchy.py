"""Mehrstufige Regime-Erkennung je Anlageklasse (ANALYSE_DYNAMIK_REGIME_0626 E3).

Zwei Ebenen, beide kausal (Jump-Modell, nur abgeschlossene Kerzen):
  macro   – übergeordnetes Marktregime (z.B. Krypto 1D)
  primary – Haupt-Regime (z.B. Krypto 8h)
Die Kombination (Matrix) sagt, welche Seite erlaubt ist und mit welcher
Größe: Bull/Bull = volle Größe Long, Bull/Bear = Korrektur (keine Longs,
Shorts nur klein) usw. Für Altcoins zusätzlich der Leitmarkt (BTC): läuft
BTC makro gegen die Richtung, wird die Größe halbiert.

Feste Rezepte je Anlageklasse (kein Fit -> kein Overfitting durch Suche).
Fail-open: Fehler/zu wenig Daten -> Zustand `unknown`, nichts wird blockiert.
"""
import asyncio
import logging
import time
from typing import Dict, List, Optional

import numpy as np

logger = logging.getLogger(__name__)

PRIMARY_JUMP = {"jump_fast_days": 2.0, "jump_slow_days": 14.0, "jump_center": 0.6,
                "jump_penalty_days": 3.0}
MACRO_JUMP = {"jump_fast_days": 7.0, "jump_slow_days": 45.0, "jump_center": 0.5,
              "jump_penalty_days": 10.0}
SLOW_MACRO_JUMP = {"jump_fast_days": 14.0, "jump_slow_days": 60.0, "jump_center": 0.5,
                   "jump_penalty_days": 20.0}

RECIPES: Dict[str, Dict] = {
    "crypto": {"label": "Krypto", "macro_tf": "1d", "primary_tf": "8h", "trade_tfs": "5m–1h",
               "macro": MACRO_JUMP, "primary": PRIMARY_JUMP, "days": 720, "leader": "BTCUSDT"},
    "indices": {"label": "Indizes", "macro_tf": "1w", "primary_tf": "1d", "trade_tfs": "15m–1h",
                "macro": SLOW_MACRO_JUMP, "primary": MACRO_JUMP, "days": 1080, "leader": None},
    "resources": {"label": "Rohstoffe", "macro_tf": "1w", "primary_tf": "1d", "trade_tfs": "15m–1h",
                  "macro": SLOW_MACRO_JUMP, "primary": MACRO_JUMP, "days": 1080, "leader": None},
    "forex": {"label": "Forex", "macro_tf": "1d", "primary_tf": "4h", "trade_tfs": "15m–1h",
              "macro": MACRO_JUMP, "primary": PRIMARY_JUMP, "days": 720, "leader": None},
}
DIR_VAL = {"down": -1, "sideways": 0, "up": 1}
UNCERTAIN_PROB = 0.5
CACHE_TTL_S = 1800

# (makro, haupt) -> Bedeutung, erlaubte Seiten, Größen-Faktor
MATRIX = {
    ("up", "up"): ("Starker Aufwärtstrend", True, False, 1.0),
    ("up", "sideways"): ("Pause im Aufwärtstrend", True, False, 0.75),
    ("up", "down"): ("Korrektur im Aufwärtstrend – Longs erst, wenn das Haupt-Regime dreht",
                     False, True, 0.5),
    ("down", "down"): ("Starker Abwärtstrend", False, True, 1.0),
    ("down", "sideways"): ("Pause im Abwärtstrend", False, True, 0.75),
    ("down", "up"): ("Gegenbewegung im Abwärtstrend – Shorts erst, wenn das Haupt-Regime dreht",
                     True, False, 0.5),
    ("sideways", "up"): ("Aufwärtsbewegung in einer Range", True, False, 0.75),
    ("sideways", "down"): ("Abwärtsbewegung in einer Range", False, True, 0.75),
    ("sideways", "sideways"): ("Seitwärtsmarkt – Range-Strategien", True, True, 0.5),
}


def recipe_for(asset_class: str) -> Dict:
    return RECIPES.get(asset_class) or RECIPES["crypto"]


def layer_config(jump: Dict, tf: str, n_bars: int) -> Dict:
    from services import regime_engine as eng
    return eng.resolve_config({"detector": "jump", "regime_mode": 3, "auto_adapt": False,
                               **jump}, tf, n_bars)


def classify_layer(candles: List[Dict], jump: Dict, tf: str) -> Dict:
    """Kausale Richtung je Kerze (-1/0/1, -9 = Warmup) + Zustand der letzten Kerze."""
    from services import regime_engine as eng
    from services import market_context as mc
    cfg = layer_config(jump, tf, len(candles))
    f = eng.compute_matrix(candles, cfg)
    ids, conf, det = eng.classify_arrays(f, cfg, return_detail=True)
    d = np.full(len(ids), -9, dtype=int)
    for i, rid in enumerate(ids):
        if rid >= 0:
            d[i] = DIR_VAL.get(mc.direction_from_regime_id(int(rid), 3), -9)
    out = {"tf": tf, "series": d, "direction": None, "prob": None, "probs": None,
           "confidence": None, "since_ts": None}
    if not len(d) or d[-1] == -9:
        return out
    last = int(d[-1])
    name = {-1: "down", 0: "sideways", 1: "up"}[last]
    probs = (det or {}).get("probs")
    if probs is not None and len(probs):
        p = [float(x) for x in probs[-1]]
        out["probs"] = {"down": round(p[0], 3), "sideways": round(p[1], 3), "up": round(p[2], 3)}
        out["prob"] = out["probs"][name]
    j = len(d) - 1
    while j > 0 and d[j - 1] == last:
        j -= 1
    out.update({"direction": name, "confidence": round(float(conf[-1]) * 100, 1),
                "since_ts": int(candles[j]["timestamp"])})
    return out


def combine(macro_dir: Optional[str], primary_dir: Optional[str],
            primary_prob: Optional[float] = None, leader_macro: Optional[str] = None,
            is_leader: bool = True) -> Dict:
    """Matrix-Entscheid (rein). Unbekannt -> beide Seiten erlaubt (fail-open)."""
    key = (macro_dir, primary_dir)
    if key not in MATRIX:
        return {"state": "unknown", "label": "Regime unbekannt – kein Filter aktiv",
                "allow_long": True, "allow_short": True, "size_factor": 1.0,
                "bias": None, "notes": []}
    label, al, ash, size = MATRIX[key]
    notes = []
    if primary_prob is not None and primary_prob < UNCERTAIN_PROB:
        size *= 0.5
        notes.append(f"unsicherer Übergang (Haupt-Regime nur {primary_prob * 100:.0f} %)")
    bias = "long" if al and not ash else "short" if ash and not al else None
    if not is_leader and leader_macro in ("up", "down") and bias:
        against = (bias == "long" and leader_macro == "down") or \
                  (bias == "short" and leader_macro == "up")
        if against:
            size *= 0.5
            notes.append(f"Leitmarkt (BTC) makro {'abwärts' if leader_macro == 'down' else 'aufwärts'}"
                         " – Größe halbiert")
    return {"state": "ok", "label": label, "allow_long": al, "allow_short": ash,
            "size_factor": round(size, 3), "bias": bias, "notes": notes}


def combined_series(macro: np.ndarray, primary: np.ndarray) -> np.ndarray:
    """Gefilterte Richtung je Kerze (rein): +1 nur Long, -1 nur Short, 0 beide/neutral."""
    names = {-1: "down", 0: "sideways", 1: "up"}
    out = np.full(len(primary), -9, dtype=int)
    for i in range(len(primary)):
        if macro[i] == -9 or primary[i] == -9:
            continue
        c = combine(names[int(macro[i])], names[int(primary[i])])
        out[i] = 1 if c["bias"] == "long" else -1 if c["bias"] == "short" else 0
    return out


def align_macro(primary_candles: List[Dict], macro_candles: List[Dict], macro_series: np.ndarray,
                primary_ms: int, macro_ms: int) -> np.ndarray:
    """Makro-Richtung auf die Haupt-Kerzen abbilden – nur ABGESCHLOSSENE Makro-Kerzen
    (Makro-Ende <= Haupt-Ende), damit kein Blick in die Zukunft entsteht."""
    m_end = np.array([int(c["timestamp"]) + macro_ms for c in macro_candles], dtype=np.int64)
    p_end = np.array([int(c["timestamp"]) + primary_ms for c in primary_candles], dtype=np.int64)
    idx = np.searchsorted(m_end, p_end, side="right") - 1
    out = np.full(len(primary_candles), -9, dtype=int)
    ok = idx >= 0
    out[ok] = np.asarray(macro_series)[idx[ok]]
    return out


def aggregate(candles: List[Dict], tf: str, base_tf: str) -> List[Dict]:
    from services.timeframes import aggregate_candles, tf_minutes
    return aggregate_candles(candles, tf, drop_partial=True, base_ms=tf_minutes(base_tf) * 60000)


def analyze_candles(symbol: str, asset_class: str, primary: List[Dict],
                    leader_macro: Optional[str] = None) -> Dict:
    """Beide Ebenen + Kombination aus Haupt-Kerzen (Makro wird daraus aggregiert)."""
    from services.timeframes import tf_minutes
    rc = recipe_for(asset_class)
    macro_c = aggregate(primary, rc["macro_tf"], rc["primary_tf"])
    pl = classify_layer(primary, rc["primary"], rc["primary_tf"])
    ml = classify_layer(macro_c, rc["macro"], rc["macro_tf"]) if len(macro_c) >= 30 else \
        {"tf": rc["macro_tf"], "series": np.full(len(macro_c), -9), "direction": None}
    comb = combine(ml.get("direction"), pl.get("direction"), pl.get("prob"),
                   leader_macro, is_leader=(rc.get("leader") in (None, symbol)))
    aligned = align_macro(primary, macro_c, ml["series"], tf_minutes(rc["primary_tf"]) * 60000,
                          tf_minutes(rc["macro_tf"]) * 60000) if len(macro_c) else \
        np.full(len(primary), -9)
    return {"symbol": symbol, "asset_class": asset_class, "recipe": public_recipe(asset_class),
            "macro": {k: v for k, v in ml.items() if k != "series"},
            "primary": {k: v for k, v in pl.items() if k != "series"},
            "combined": comb, "_primary_series": pl["series"], "_macro_series": aligned,
            "_primary_candles": primary}


def public_recipe(asset_class: str) -> Dict:
    rc = recipe_for(asset_class)
    return {"asset_class": asset_class, "label": rc["label"], "macro_tf": rc["macro_tf"],
            "primary_tf": rc["primary_tf"], "trade_tfs": rc["trade_tfs"], "days": rc["days"],
            "leader": rc["leader"], "detector": "jump"}


def public(res: Dict) -> Dict:
    return {k: v for k, v in res.items() if not k.startswith("_")}


# ---------------- Live-Abfrage (gecacht, fail-open) ----------------
_cache: Dict[str, Dict] = {}
_locks: Dict[str, asyncio.Lock] = {}


def live_days(asset_class: str) -> int:
    """Historie für den Live-Zustand: Warmup der langsamen Makro-Ebene + Reserve."""
    rc = recipe_for(asset_class)
    return int(min(max(rc["macro"]["jump_slow_days"] * 4 + 30, 150), rc["days"]))


async def _fetch(symbol: str, days: int, tf: str) -> List[Dict]:
    from services import regime_lab as lab
    h = await lab.fetch_histories([symbol], days, tf)
    return h.get(symbol) or []


async def resolve(symbol: str, max_age_s: float = CACHE_TTL_S) -> Dict:
    from services import setup_asset_class
    ent = _cache.get(symbol)
    if ent and time.time() - ent["_at"] < max_age_s:
        return ent["res"]
    lock = _locks.setdefault(symbol, asyncio.Lock())
    async with lock:
        ent = _cache.get(symbol)
        if ent and time.time() - ent["_at"] < max_age_s:
            return ent["res"]
        cls = setup_asset_class.asset_class_of(symbol)
        rc = recipe_for(cls)
        try:
            leader_macro = None
            if rc.get("leader") and rc["leader"] != symbol:
                lead = await resolve(rc["leader"], max_age_s)
                leader_macro = (lead.get("macro") or {}).get("direction")
            candles = await _fetch(symbol, live_days(cls), rc["primary_tf"])
            if len(candles) < 60:
                raise RuntimeError(f"zu wenig Kerzen ({len(candles)})")
            res = public(analyze_candles(symbol, cls, candles, leader_macro))
            res["leader_macro"] = leader_macro
        except Exception as e:  # noqa: BLE001 – fail-open
            logger.warning(f"regime_hierarchy {symbol}: {e}")
            res = {"symbol": symbol, "asset_class": cls, "recipe": public_recipe(cls),
                   "macro": {}, "primary": {}, "combined": combine(None, None),
                   "error": str(e)[:160]}
        res["checked_at"] = time.time()
        _cache[symbol] = {"_at": time.time(), "res": res}
        return res


def cached(symbol: str) -> Optional[Dict]:
    ent = _cache.get(symbol)
    return ent["res"] if ent else None


def side_allowed(res: Optional[Dict], side: Optional[str]) -> bool:
    """Signal-Seite gegen die Matrix prüfen (rein, fail-open)."""
    c = (res or {}).get("combined") or {}
    if c.get("state") != "ok" or not side:
        return True
    s = str(side).upper()
    return bool(c.get("allow_long")) if s == "LONG" else \
        bool(c.get("allow_short")) if s == "SHORT" else True
