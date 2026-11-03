"""Sweep-Trigger: lokaler Wick-Sweep-Detektor (ohne LLM) auf den 1m-Kerzen des
Scanner-Puffers. Erkennt Stop-Jagden über ein markantes Hoch/Tief (langer Docht
+ Reclaim-Close zurück ins Level) und stößt eine GEZIELTE Einzel-Symbol-Analyse
an, statt bis zum nächsten 15-min-Zyklus zu warten (Latenz-Frage des Traders,
ML_REBUILD_STATUS 26.08.).

Kostenkontrolle: Budget je Tag (sweep_trigger_daily_cap, Default 20 Extra-Calls),
Cooldown je Symbol (sweep_trigger_cooldown_min, Default 30), nur innerhalb der
Handelssession des KI-Traders, nie parallel zu einem laufenden Zyklus.
Detektor und Budget sind rein & testbar; die Engine-Kopplung liegt in check().
"""
import logging
import time
from datetime import datetime, timezone
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

DEFAULTS: Dict = {
    "sweep_trigger_enabled": True,
    "sweep_trigger_daily_cap": 20,
    "sweep_trigger_cooldown_min": 30,
    "sweep_trigger_lookback": 60,      # Kerzen für das Referenz-Hoch/-Tief
    "sweep_trigger_min_wick_atr": 1.2,  # Docht über das Level in ATR(14)
}

CLAMPS = {
    "sweep_trigger_daily_cap": (0, 200),
    "sweep_trigger_cooldown_min": (1, 720),
    "sweep_trigger_lookback": (20, 500),
    "sweep_trigger_min_wick_atr": (0.3, 5.0),
}


def clamp_updates(updates: Dict, cfg: Dict) -> None:
    if "sweep_trigger_enabled" in updates:
        cfg["sweep_trigger_enabled"] = bool(updates["sweep_trigger_enabled"])
    for key, (lo, hi) in CLAMPS.items():
        if key in updates:
            try:
                v = float(updates[key])
            except (TypeError, ValueError):
                continue
            v = max(lo, min(hi, v))
            cfg[key] = v if key == "sweep_trigger_min_wick_atr" else int(v)


def _atr(candles: List[Dict], period: int = 14) -> float:
    trs = []
    for i in range(max(1, len(candles) - period), len(candles)):
        h, l = float(candles[i]["high"]), float(candles[i]["low"])
        pc = float(candles[i - 1]["close"])
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    return sum(trs) / len(trs) if trs else 0.0


def detect_sweep(candles: List[Dict], lookback: int = 60,
                 min_wick_atr: float = 1.2) -> Optional[Dict]:
    """Wick-Sweep auf der LETZTEN abgeschlossenen 1m-Kerze (rein & testbar).

    Bearish Sweep (Short-Setup): Hoch der Kerze > höchstes Hoch der `lookback`
    Vorkerzen um >= min_wick_atr × ATR, Close wieder UNTER dem alten Hoch und in
    der unteren Kerzenhälfte. Bullish spiegelbildlich.
    Rückgabe: {"side": "SHORT"|"LONG", "level", "wick_atr", "price"} oder None."""
    if len(candles) < lookback + 16:
        return None
    last = candles[-1]
    ref = candles[-1 - lookback:-1]
    atr = _atr(candles[:-1])
    if atr <= 0:
        return None
    h, l = float(last["high"]), float(last["low"])
    o, c = float(last["open"]), float(last["close"])
    rng = max(h - l, 1e-12)
    ref_high = max(float(x["high"]) for x in ref)
    ref_low = min(float(x["low"]) for x in ref)
    if h > ref_high and c < ref_high and (h - ref_high) / atr >= min_wick_atr \
            and (h - c) / rng >= 0.5:
        return {"side": "SHORT", "level": round(ref_high, 8), "price": c,
                "wick_atr": round((h - ref_high) / atr, 2)}
    if l < ref_low and c > ref_low and (ref_low - l) / atr >= min_wick_atr \
            and (c - l) / rng >= 0.5:
        return {"side": "LONG", "level": round(ref_low, 8), "price": c,
                "wick_atr": round((ref_low - l) / atr, 2)}
    return None


class SweepBudget:
    """Tagesbudget + Symbol-Cooldown (rein & testbar, In-Memory; Deploy-Reset
    ist bewusst akzeptiert – schlimmstenfalls ein paar Calls mehr am Deploy-Tag)."""

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
        """None = darf feuern, sonst Grund."""
        now = now or time.time()
        self._roll(now)
        if not cfg.get("sweep_trigger_enabled", True):
            return "Sweep-Trigger aus"
        cap = int(cfg.get("sweep_trigger_daily_cap", 20) or 0)
        if self.used >= cap:
            return f"Tagesbudget {cap} erschöpft"
        cd = float(cfg.get("sweep_trigger_cooldown_min", 30) or 30) * 60
        last = self.last_fire.get(symbol, 0.0)
        if now - last < cd:
            return f"Cooldown {symbol} ({int((cd - (now - last)) / 60)} min)"
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
        return {"enabled": bool(cfg.get("sweep_trigger_enabled", True)),
                "used_today": self.used,
                "daily_cap": int(cfg.get("sweep_trigger_daily_cap", 20) or 0),
                "cooldown_min": cfg.get("sweep_trigger_cooldown_min", 30),
                "recent": list(reversed(self.history[-10:]))}


budget = SweepBudget()
_last_seen_ts: Dict[str, float] = {}


def scan(engine, symbols: List[str]) -> List[Dict]:
    """Alle Symbole prüfen – nur die jeweils neue abgeschlossene 1m-Kerze
    (kein Doppel-Feuern auf derselben Kerze). Rein bzgl. Netzwerk."""
    cfg = engine.config
    hits = []
    for sym in symbols:
        candles = engine.scanner.candle_buffer.get(sym) or []
        if len(candles) < 80:
            continue
        ts = float(candles[-1].get("timestamp") or 0)
        if _last_seen_ts.get(sym) == ts:
            continue
        _last_seen_ts[sym] = ts
        hit = detect_sweep(candles, int(cfg.get("sweep_trigger_lookback", 60) or 60),
                           float(cfg.get("sweep_trigger_min_wick_atr", 1.2) or 1.2))
        if hit:
            hits.append({"symbol": sym, **hit})
    return hits


async def check(engine) -> Optional[Dict]:
    """Ein Tick aus dem Engine-Loop: Sweep erkennen -> Budget prüfen -> gezielte
    Analyse. Läuft nie parallel zu einem Zyklus."""
    cfg = engine.config
    if not cfg.get("sweep_trigger_enabled", True) or engine._analyzing:
        return None
    if not engine.scanner.is_trading_session("ai_trader"):
        return None
    symbols = [s for s in engine.symbols
               if not engine.toggle_check or engine.toggle_check("ai_trader", s)]
    for hit in scan(engine, symbols):
        why = budget.allowed(hit["symbol"], cfg)
        if why:
            logger.info(f"Sweep-Trigger {hit['symbol']} {hit['side']} erkannt, aber: {why}")
            continue
        budget.fire(hit["symbol"], {"side": hit["side"], "level": hit["level"],
                                    "wick_atr": hit["wick_atr"]})
        text = (f"WICK-SWEEP {hit['symbol']}: {'Hoch' if hit['side'] == 'SHORT' else 'Tief'} "
                f"{hit['level']:g} um {hit['wick_atr']}×ATR überstochen, Close {hit['price']:g} "
                f"wieder dahinter (Reclaim) -> mögliches {hit['side']}-Setup (liquidity_sweep).")
        logger.info(f"Sweep-Trigger feuert: {text}")
        try:
            return await engine.run_analysis(only_symbols=[hit["symbol"]], trigger=text)
        except Exception as e:  # noqa: BLE001
            logger.error(f"Sweep-Trigger-Analyse {hit['symbol']} fehlgeschlagen: {e}")
            return None
    return None
