"""Trendfolge-2-Signal (TF2) für den KI-Trader – dieselben Indikatoren wie die
Website-Strategie „TrendFolge2" (custom, 5m):
  * Preisänderung % über `lookback` 5m-Kerzen  > chg_pct (Long) / < -chg_pct (Short)
  * MACD(12/26/9)-Linie kreuzt Signal-Linie (über = Long, unter = Short)
  * Rel. Volumen der Signalkerze >= vol_mult × 20er-Schnitt
SL an der Struktur (Tief/Hoch der letzten `sl_lookback` Kerzen), Ziel in R.

Zwei Verwendungen (rein lokal, ohne LLM-Kosten):
  * `context_line()`  – kompakte Prompt-Zeile je Asset, NUR wenn in den letzten
    RECENT_BARS 5m-Kerzen ein Kreuz stattfand (sonst None -> keine Tokens).
  * `check(engine)`   – Event-Trigger analog zu services/sweep_trigger.py: bei
    frischem TF2-Signal eine gezielte Einzel-Symbol-Analyse (Tagesbudget +
    Symbol-Cooldown), damit der KI-Trader das Setup `trend_follow2` zeitnah
    handeln kann statt auf den nächsten Zyklus zu warten.
Der Backtest-Detektor (setup_backtest/detectors.detect_trend_follow2) nutzt
dieselbe Logik auf Feature-Arrays – Parameter-Namen sind identisch.
"""
import logging
import time
from datetime import datetime, timezone
from typing import Dict, List, Optional

import numpy as np

from services import vec
from services.timeframes import aggregate_candles

logger = logging.getLogger(__name__)

PARAMS: Dict[str, float] = {"lookback": 5, "chg_pct": 0.56, "vol_mult": 1.2,
                            "sl_lookback": 10, "sl_atr": 0.2, "tp_r": 2.0}
RECENT_BARS = 3            # Kreuz höchstens so viele 5m-Kerzen alt -> Prompt-Zeile
MIN_5M_BARS = 60

DEFAULTS: Dict = {
    "tf2_trigger_enabled": True,
    "tf2_trigger_daily_cap": 12,
    "tf2_trigger_cooldown_min": 90,
}
_LIMITS = {"tf2_trigger_daily_cap": (0, 100), "tf2_trigger_cooldown_min": (5, 720)}


def clamp_updates(updates: Dict, cfg: Dict) -> None:
    if "tf2_trigger_enabled" in updates:
        cfg["tf2_trigger_enabled"] = bool(updates["tf2_trigger_enabled"])
    for key, (lo, hi) in _LIMITS.items():
        if key in updates:
            try:
                cfg[key] = int(max(lo, min(hi, float(updates[key]))))
            except (TypeError, ValueError):
                pass


def _arrays(candles_1m: List[Dict]):
    c5 = aggregate_candles(candles_1m, "5m", drop_partial=True)
    if len(c5) < MIN_5M_BARS:
        return None
    f = lambda k: np.array([float(x.get(k) or 0) for x in c5], dtype=float)  # noqa: E731
    return {"op": f("open"), "hi": f("high"), "lo": f("low"), "cl": f("close"),
            "vol": f("volume"), "ts": [int(x.get("timestamp") or 0) for x in c5]}


def evaluate(a: Dict, i: int, p: Optional[Dict] = None) -> Optional[Dict]:
    """TF2-Bedingungen an Kerze i prüfen (rein). Rückgabe Signal-Dict oder None."""
    p = {**PARAMS, **(p or {})}
    n, k = int(p["lookback"]), int(p["sl_lookback"])
    cl, hi, lo, vol = a["cl"], a["hi"], a["lo"], a["vol"]
    if i < max(n, k, 35) or i >= len(cl):
        return None
    line, sig = vec.macd(cl[:i + 1])
    if np.isnan(line[i - 1]) or np.isnan(sig[i - 1]) or np.isnan(sig[i]):
        return None
    ref = cl[i - n]
    if ref <= 0:
        return None
    chg = (cl[i] - ref) / ref * 100
    vavg = float(np.mean(vol[i - 20:i])) if i >= 20 else 0.0
    rel = vol[i] / vavg if vavg > 0 else 0.0
    if rel < float(p["vol_mult"]):
        return None
    atr = vec.atr(hi[:i + 1], lo[:i + 1], cl[:i + 1], 14)[i]
    if np.isnan(atr) or atr <= 0:
        return None
    if line[i - 1] <= sig[i - 1] and line[i] > sig[i] and chg > float(p["chg_pct"]):
        side, ext, d = "LONG", float(np.min(lo[i - k:i + 1])), 1
    elif line[i - 1] >= sig[i - 1] and line[i] < sig[i] and chg < -float(p["chg_pct"]):
        side, ext, d = "SHORT", float(np.max(hi[i - k:i + 1])), -1
    else:
        return None
    entry = float(cl[i])
    sl = ext - d * float(p.get("sl_atr", 0.2)) * atr
    risk = abs(entry - sl)
    if risk < 0.2 * atr or risk > 3.0 * atr:
        return None
    tp_r = float(p["tp_r"])
    return {"side": side, "idx": i, "entry": entry, "sl": round(sl, 8),
            "tp1": round(entry + d * risk, 8), "tpf": round(entry + d * risk * tp_r, 8),
            "chg_pct": round(float(chg), 2), "rel_vol": round(float(rel), 2),
            "risk_pct": round(risk / entry * 100, 3)}


def detect_last(candles_1m: List[Dict], p: Optional[Dict] = None) -> Optional[Dict]:
    """Signal auf der letzten GESCHLOSSENEN 5m-Kerze (rein)."""
    a = _arrays(candles_1m)
    if not a:
        return None
    sig = evaluate(a, len(a["cl"]) - 1, p)
    if sig:
        sig["ts"] = a["ts"][sig["idx"]]
    return sig


def context_line(candles_1m: List[Dict], price: float, p: Optional[Dict] = None) -> Optional[str]:
    """Kompakte Prompt-Zeile, wenn ein TF2-Kreuz höchstens RECENT_BARS Kerzen alt ist."""
    a = _arrays(candles_1m)
    if not a:
        return None
    last = len(a["cl"]) - 1
    for back in range(RECENT_BARS):
        sig = evaluate(a, last - back, p)
        if sig:
            age = "aktuell" if back == 0 else f"vor {back}×5m"
            return (f"TF2-Signal {sig['side']} ({age}): MACD-Kreuz, Δ{int((p or PARAMS).get('lookback', 5))}×5m "
                    f"{sig['chg_pct']:+.2f}%, RelVol {sig['rel_vol']:.1f}, Struktur-SL {sig['sl']:g} "
                    f"({sig['risk_pct']:.2f}%)")
    return None


class TF2Budget:
    """Tagesbudget + Symbol-Cooldown für Trigger-Analysen (In-Memory, wie Sweep)."""

    def __init__(self):
        self.day: Optional[str] = None
        self.used = 0
        self.last_fire: Dict[str, float] = {}
        self.history: List[Dict] = []

    def _roll(self, now: float):
        d = datetime.fromtimestamp(now, timezone.utc).strftime("%Y-%m-%d")
        if d != self.day:
            self.day, self.used = d, 0

    def allowed(self, symbol: str, cfg: Dict, now: Optional[float] = None) -> Optional[str]:
        now = now or time.time()
        self._roll(now)
        if not cfg.get("tf2_trigger_enabled", True):
            return "TF2-Trigger aus"
        cap = int(cfg.get("tf2_trigger_daily_cap", 12) or 0)
        if self.used >= cap:
            return f"Tagesbudget {cap} erschöpft"
        cd = float(cfg.get("tf2_trigger_cooldown_min", 90) or 90) * 60
        if now - self.last_fire.get(symbol, 0.0) < cd:
            return f"Cooldown {symbol}"
        return None

    def fire(self, symbol: str, info: Dict, now: Optional[float] = None) -> None:
        now = now or time.time()
        self._roll(now)
        self.used += 1
        self.last_fire[symbol] = now
        self.history.append({"ts": datetime.fromtimestamp(now, timezone.utc).isoformat(),
                             "symbol": symbol, **info})
        del self.history[:-50]

    def status(self, cfg: Dict) -> Dict:
        self._roll(time.time())
        return {"enabled": bool(cfg.get("tf2_trigger_enabled", True)), "used_today": self.used,
                "daily_cap": int(cfg.get("tf2_trigger_daily_cap", 12) or 0),
                "cooldown_min": cfg.get("tf2_trigger_cooldown_min", 90),
                "recent": list(reversed(self.history[-10:]))}


budget = TF2Budget()
_last_seen_ts: Dict[str, int] = {}


def scan(engine, symbols: List[str]) -> List[Dict]:
    """Nur die jeweils neue geschlossene 5m-Kerze je Symbol prüfen (kein Doppel-Feuern)."""
    hits = []
    for sym in symbols:
        candles = engine.scanner.candle_buffer.get(sym) or []
        if len(candles) < MIN_5M_BARS * 5:
            continue
        sig = detect_last(candles)
        if not sig or _last_seen_ts.get(sym) == sig["ts"]:
            continue
        _last_seen_ts[sym] = sig["ts"]
        hits.append({"symbol": sym, **sig})
    return hits


async def check(engine) -> Optional[Dict]:
    """Ein Tick aus dem Engine-Loop: TF2-Signal -> Budget -> gezielte Analyse."""
    cfg = engine.config
    if not cfg.get("tf2_trigger_enabled", True) or engine._analyzing:
        return None
    if not engine.scanner.is_trading_session("ai_trader"):
        return None
    symbols = [s for s in engine.symbols
               if not engine.toggle_check or engine.toggle_check("ai_trader", s)]
    for hit in scan(engine, symbols):
        why = budget.allowed(hit["symbol"], cfg)
        if why:
            logger.info(f"TF2-Trigger {hit['symbol']} {hit['side']} erkannt, aber: {why}")
            continue
        budget.fire(hit["symbol"], {"side": hit["side"], "chg_pct": hit["chg_pct"],
                                    "rel_vol": hit["rel_vol"]})
        text = (f"TF2-SIGNAL {hit['side']} auf {hit['symbol']}: MACD-Kreuz, "
                f"Δ{int(PARAMS['lookback'])}×5m {hit['chg_pct']:+.2f}%, RelVol {hit['rel_vol']:.1f}, "
                f"Struktur-SL {hit['sl']:g} ({hit['risk_pct']:.2f}%), Ziel {PARAMS['tp_r']:g}R")
        logger.info(f"TF2-Trigger: {text}")
        return await engine.run_analysis(manual=False, only_symbols=[hit["symbol"]], trigger=text)
    return None
