"""Setup-Statistik getrennt nach QUELLE (10/2026).

Bisher mischte die Setup-Statistik Regel-Trades (Detektor ohne KI) und
KI-Trades unter demselben Setup-Namen. Hier wird je Quelle gemessen:
  regel        – Detektor-Paper-Trade ohne LLM (Vergleichsbasis)
  ki_geprueft  – Detektor hat gefeuert, die KI hat bestätigt (nur diese gehen live)
  ki_frei      – KI-Entscheidung ohne Detektor-Signal (nur Paper)

Maßeinheit ist R (realisierter PnL je riskiertem USDT). Die Live-Reife eines
Setups je Anlageklasse basiert ausschließlich auf ki_geprueft:
  * die ersten `signal_min_ki_trades` (Default 5) Trades laufen als Paper,
  * danach live, wenn der geschrumpfte Erwartungswert (n/(n+K)) >= signal_live_min_r,
  * Live-Ergebnis klar negativ (>= 5 Live-Trades, geschrumpft <= signal_demote_r) -> zurück in Paper.
"""
import time
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

from services import setup_asset_class as sac

SHRINK_K = 10
LOOKBACK_DAYS = 60
CACHE_S = 300
DELAY_BUCKETS = ((0, 6, "sofort"), (6, 20, "6-20 min"), (20, 10_000, "> 20 min"))

_cache: Dict = {"ts": 0.0, "data": None}


def source_of(trade: Dict) -> str:
    """Quelle eines Trades; Alt-Trades ohne Feld werden über die Begründung erkannt."""
    s = trade.get("signal_source")
    if s:
        return "regel" if s == "regel_live" else str(s)
    if str(trade.get("ai_reasoning") or "").startswith("Setup-Trigger"):
        return "regel"
    return "ki_frei"


def r_of(trade: Dict) -> Optional[float]:
    try:
        risk = float(trade.get("risk_usdt") or 0)
        return float(trade.get("realized_pnl") or 0) / risk if risk > 0 else None
    except (TypeError, ValueError):
        return None


def shrunk(avg: float, n: int, k: int = SHRINK_K) -> float:
    return avg * n / (n + k) if n > 0 else 0.0


def _bucket(delay) -> Optional[str]:
    try:
        d = float(delay)
    except (TypeError, ValueError):
        return None
    return next((lbl for lo, hi, lbl in DELAY_BUCKETS if lo <= d < hi), None)


def aggregate(trades: List[Dict]) -> Dict[Tuple[str, str, str], Dict]:
    """{(quelle, klasse, setup): Kennzahlen} (rein)."""
    out: Dict[Tuple[str, str, str], Dict] = {}
    for t in trades:
        key = (source_of(t), sac.asset_class_of(t.get("symbol")), str(t.get("setup") or "-"))
        st = out.setdefault(key, {"n": 0, "wins": 0, "pnl": 0.0, "r_n": 0, "r_sum": 0.0,
                                  "live_n": 0, "live_r_sum": 0.0, "early": 0, "timing": {}})
        pnl = float(t.get("realized_pnl") or 0)
        st["n"] += 1
        st["wins"] += 1 if pnl > 0 else 0
        st["pnl"] += pnl
        r = r_of(t)
        if r is not None:
            st["r_n"] += 1
            st["r_sum"] += r
            if t.get("mode") == "live" and not t.get("data_collection"):
                st["live_n"] += 1
                st["live_r_sum"] += r
            b = _bucket(t.get("entry_delay_min"))
            if b:
                tb = st["timing"].setdefault(b, {"n": 0, "r_sum": 0.0})
                tb["n"] += 1
                tb["r_sum"] += r
        if t.get("ki_frueh"):
            st["early"] += 1
    for st in out.values():
        st["pnl"] = round(st["pnl"], 2)
        st["winrate"] = round(st["wins"] / st["n"] * 100) if st["n"] else 0
        st["avg_r"] = round(st["r_sum"] / st["r_n"], 3) if st["r_n"] else None
        st["shrunk_r"] = round(shrunk(st["r_sum"] / st["r_n"], st["r_n"]), 3) if st["r_n"] else None
        st["live_avg_r"] = round(st["live_r_sum"] / st["live_n"], 3) if st["live_n"] else None
        for tb in st["timing"].values():
            tb["avg_r"] = round(tb["r_sum"] / tb["n"], 3) if tb["n"] else None
    return out


def maturity(st: Optional[Dict], cfg: Optional[Dict]) -> Tuple[bool, str, str]:
    """(live_ok, Begründung, Phase) für ki_geprueft-Kennzahlen eines Setups×Klasse (rein).
    Phasen: sammeln | live | nicht_validiert | zurueckgestuft."""
    cfg = cfg or {}
    need = int(cfg.get("signal_min_ki_trades", 5) or 5)
    min_r = float(cfg.get("signal_live_min_r", 0.05))
    demote_r = float(cfg.get("signal_demote_r", -0.15))
    st = st or {}
    n = int(st.get("n") or 0)
    if n < need:
        return False, f"Datensammlung {n}/{need} KI-geprüfte Paper-Trades", "sammeln"
    live_n = int(st.get("live_n") or 0)
    if live_n >= 5 and st.get("live_avg_r") is not None \
            and shrunk(float(st["live_avg_r"]), live_n) <= demote_r:
        return False, (f"live {live_n} Trades Ø {st['live_avg_r']:+.2f} R – zurück in Paper"), "zurueckgestuft"
    if st.get("shrunk_r") is None or int(st.get("r_n") or 0) < max(1, int(0.8 * n)):
        ok = float(st.get("pnl") or 0) > 0 and int(st.get("winrate") or 0) >= 50
        return ok, (f"{n} Trades, PnL {st.get('pnl', 0):+.2f} USDT, WR {st.get('winrate', 0)} % "
                    "(ohne R-Daten)"), "live" if ok else "nicht_validiert"
    if float(st["shrunk_r"]) >= min_r:
        return True, f"{n} Trades Ø {st['avg_r']:+.2f} R (geschrumpft {st['shrunk_r']:+.2f} R)", "live"
    return False, (f"{n} Trades Ø {st['avg_r']:+.2f} R (geschrumpft {st['shrunk_r']:+.2f} R "
                   f"< {min_r:+.2f} R) – weiter Paper"), "nicht_validiert"


async def load(db, days: int = LOOKBACK_DAYS, force: bool = False) -> Dict:
    if not force and _cache["data"] is not None and time.time() - _cache["ts"] < CACHE_S:
        return _cache["data"]
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    rows = await db.auto_trades.find(
        {"strategy_id": "ai_trader", "status": "closed", "opened_at": {"$gte": cutoff},
         "backtest": {"$ne": True}},
        {"_id": 0, "symbol": 1, "setup": 1, "signal_source": 1, "ai_reasoning": 1, "mode": 1,
         "data_collection": 1, "realized_pnl": 1, "risk_usdt": 1, "entry_delay_min": 1,
         "ki_frueh": 1}).to_list(50000)
    data = aggregate(rows)
    _cache.update(ts=time.time(), data=data)
    return data


def invalidate() -> None:
    _cache.update(ts=0.0, data=None)


def rows_for_ui(data: Dict, cfg: Optional[Dict]) -> List[Dict]:
    """Tabelle je Klasse×Setup mit allen drei Quellen nebeneinander (rein)."""
    keys = sorted({(k[1], k[2]) for k in data})
    out = []
    for cls, setup in keys:
        row = {"asset_class": cls, "setup": setup}
        for src in ("regel", "ki_geprueft", "ki_frei"):
            st = data.get((src, cls, setup))
            row[src] = ({k: st.get(k) for k in ("n", "winrate", "pnl", "avg_r", "shrunk_r",
                                                 "live_n", "live_avg_r", "early", "timing")}
                        if st else None)
        ok, why, phase = maturity(data.get(("ki_geprueft", cls, setup)), cfg)
        row["maturity"] = {"live_ok": ok, "reason": why, "phase": phase}
        reg, ki = row["regel"], row["ki_geprueft"]
        row["ki_value_r"] = (round(ki["avg_r"] - reg["avg_r"], 3)
                             if ki and reg and ki.get("avg_r") is not None
                             and reg.get("avg_r") is not None else None)
        out.append(row)
    return out


def prompt_lines(data: Dict, classes: Optional[List[str]] = None, limit: int = 12) -> List[str]:
    """Kurzbilanz je Setup für den Prompt: Regel vs. KI-geprüft (rein)."""
    rows = []
    for (src, cls, setup), st in data.items():
        if src != "ki_geprueft" and src != "regel":
            continue
        if classes and cls not in classes:
            continue
        rows.append((cls, setup))
    def _n(key):
        return sum(int((data.get((src, *key)) or {}).get("n") or 0) for src in ("regel", "ki_geprueft"))
    lines = []
    for cls, setup in sorted(set(rows), key=lambda k: -_n(k)):
        reg, ki = data.get(("regel", cls, setup)), data.get(("ki_geprueft", cls, setup))
        def _f(st):
            return f"{st['n']}T Ø{st['avg_r']:+.2f}R" if st and st.get("avg_r") is not None else "–"

        def _t(st):
            tm = (st or {}).get("timing") or {}
            parts = [f"{k} Ø{v['avg_r']:+.2f}R ({v['n']})" for k, v in tm.items() if v.get("avg_r") is not None]
            return f" [Timing: {', '.join(parts)}]" if parts else ""
        lines.append(f"- {setup}@{cls}: Regel {_f(reg)} · KI-geprüft {_f(ki)}{_t(ki)}")
    return lines[:limit]
