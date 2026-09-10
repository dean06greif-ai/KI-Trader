"""Tiefen-Diagnose eines Backtest-Ergebnisses für die KI-Revision (rein & testbar).

Statt nur Trades/WR/PnL bekommt der Forschungs-Analyst je Parameter-Satz:
  * Exit-Verteilung (SL / TP / Break-Even / Zeit) mit PnL-Anteil
  * Long/Short- und Symbol-Verteilung, Handelszeit-Fenster (Berlin)
  * Profit-Faktor, Payoff, Erwartungswert je Trade, Gebührenanteil
  * Equity-Kurve (verdichtet), max. Drawdown, längste Verlustserie
  * MFE/MAE in R: wie weit liefen Verlierer ins Plus, Gewinner ins Minus
  * `score()`   – Vergleichsmaß für Parameter-Sätze (PnL je Trade, OOS-gewichtet)
  * `lessons()` – Lernschleife: was hat die letzte KI-Revision verändert und
                  war das Ergebnis besser oder schlechter?
"""
from datetime import datetime
from typing import Dict, List, Optional, Sequence, Tuple
from zoneinfo import ZoneInfo

BERLIN = ZoneInfo("Europe/Berlin")
HOUR_BUCKETS: Tuple[Tuple[str, int, int], ...] = (("00-08", 0, 8), ("08-13", 8, 13),
                                                  ("13-17", 13, 17), ("17-24", 17, 24))
EQUITY_POINTS = 8
TOP_SYMBOLS = 4


def _pnl(t: Dict) -> float:
    return float(t.get("realized_pnl") or 0)


def _bucket(trades: List[Dict]) -> Dict:
    n = len(trades)
    wins = sum(1 for t in trades if _pnl(t) > 0)
    pnl = sum(_pnl(t) for t in trades)
    return {"n": n, "wr": round(wins / n * 100) if n else 0, "pnl": round(pnl, 2)}


def _risk_r(t: Dict) -> Optional[float]:
    entry, sl = float(t.get("entry") or 0), float(t.get("initial_sl") or 0)
    r = abs(entry - sl)
    return r if entry > 0 and r > 0 else None


def _mfe_mae_r(t: Dict) -> Optional[Tuple[float, float]]:
    """(MFE, MAE) in R – maximaler Lauf in Trade-Richtung / dagegen."""
    r = _risk_r(t)
    if r is None:
        return None
    entry = float(t["entry"])
    d = 1 if t.get("side") == "LONG" else -1
    peak, trough = float(t.get("peak_price") or entry), float(t.get("trough_price") or entry)
    fav = (peak - entry) if d == 1 else (entry - trough)
    adv = (entry - trough) if d == 1 else (peak - entry)
    return max(0.0, fav / r), max(0.0, adv / r)


def _median(vals: Sequence[float]) -> Optional[float]:
    s = sorted(vals)
    if not s:
        return None
    m = len(s) // 2
    return round(s[m] if len(s) % 2 else (s[m - 1] + s[m]) / 2, 2)


def _hour_berlin(iso: Optional[str]) -> Optional[int]:
    try:
        return datetime.fromisoformat(str(iso).replace("Z", "+00:00")).astimezone(BERLIN).hour
    except (TypeError, ValueError):
        return None


def _equity(pnls: List[float]) -> Tuple[List[float], float, int]:
    """Verdichtete Equity-Kurve, max. Drawdown (absolut), längste Verlustserie."""
    cum, peak, dd, streak, worst = 0.0, 0.0, 0.0, 0, 0
    curve = []
    for p in pnls:
        cum += p
        peak = max(peak, cum)
        dd = max(dd, peak - cum)
        streak = streak + 1 if p <= 0 else 0
        worst = max(worst, streak)
        curve.append(cum)
    if len(curve) > EQUITY_POINTS:
        step = (len(curve) - 1) / (EQUITY_POINTS - 1)
        curve = [curve[round(k * step)] for k in range(EQUITY_POINTS)]
    return [round(x, 1) for x in curve], round(dd, 2), worst


def diagnose(trades: List[Dict], fee_rt_pct: Optional[float] = None) -> Dict:
    """Kennzahlen-Paket eines Trade-Fensters (IS oder OOS). Leer -> {'n': 0}."""
    if not trades:
        return {"n": 0}
    trades = sorted(trades, key=lambda t: str(t.get("opened_at") or ""))
    pnls = [_pnl(t) for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    gross_win, gross_loss = sum(wins), -sum(losses)
    n = len(trades)
    margin = float(trades[0].get("margin_used") or 0)
    fees = round(n * (fee_rt_pct or 0) / 100 * margin, 2) if fee_rt_pct and margin else None
    exits: Dict[str, Dict] = {}
    for t in trades:
        exits.setdefault(str(t.get("exit_reason") or "?"), []).append(t)
    sides = {s: _bucket([t for t in trades if t.get("side") == s]) for s in ("LONG", "SHORT")}
    by_sym: Dict[str, List[Dict]] = {}
    for t in trades:
        by_sym.setdefault(str(t.get("symbol")), []).append(t)
    top = sorted(by_sym.items(), key=lambda kv: -len(kv[1]))[:TOP_SYMBOLS]
    hours: Dict[str, Dict] = {}
    for label, lo, hi in HOUR_BUCKETS:
        grp = [t for t in trades if (h := _hour_berlin(t.get("opened_at"))) is not None and lo <= h < hi]
        if grp:
            hours[label] = _bucket(grp)
    mm = [(t, _mfe_mae_r(t)) for t in trades]
    mfe_losers = [x[0] for t, x in mm if x and _pnl(t) <= 0]
    mae_winners = [x[1] for t, x in mm if x and _pnl(t) > 0]
    sl_trades = [x for t, x in mm if x and t.get("exit_reason") == "sl"]
    curve, max_dd, streak = _equity(pnls)
    avg_win = gross_win / len(wins) if wins else 0.0
    avg_loss = gross_loss / len(losses) if losses else 0.0
    return {
        "n": n, "wr": round(len(wins) / n * 100), "pnl": round(sum(pnls), 2),
        "pf": round(gross_win / gross_loss, 2) if gross_loss > 0 else (9.99 if gross_win > 0 else 0.0),
        "avg_win": round(avg_win, 2), "avg_loss": round(avg_loss, 2),
        "payoff": round(avg_win / avg_loss, 2) if avg_loss > 0 else None,
        "expectancy": round(sum(pnls) / n, 3), "fees": fees,
        "exits": {k: _bucket(v) for k, v in exits.items()},
        "sides": sides, "symbols": {k: _bucket(v) for k, v in top},
        "hours": hours, "equity": curve, "max_dd": max_dd, "loss_streak": streak,
        "mfe_losers_r": _median(mfe_losers), "mae_winners_r": _median(mae_winners),
        "sl_reached_half_r": (round(sum(1 for x in sl_trades if x[0] >= 0.5) / len(sl_trades) * 100)
                              if sl_trades else None),
        "hold_bars": _median([_bars(t) for t in trades if _bars(t) is not None]),
    }


def _bars(t: Dict) -> Optional[int]:
    try:
        a = datetime.fromisoformat(str(t["opened_at"]).replace("Z", "+00:00"))
        b = datetime.fromisoformat(str(t["closed_at"]).replace("Z", "+00:00"))
        return max(0, int((b - a).total_seconds() // 300))
    except (KeyError, TypeError, ValueError):
        return None


def compact(diag: Optional[Dict]) -> Dict:
    """Kleine Teilmenge für die persistierte Historie (settings-Dokument bleibt klein)."""
    if not diag or not diag.get("n"):
        return {"n": 0}
    keys = ("n", "wr", "pnl", "pf", "payoff", "expectancy", "max_dd", "loss_streak",
            "mfe_losers_r", "mae_winners_r", "sl_reached_half_r", "hold_bars", "fees")
    out = {k: diag.get(k) for k in keys}
    out["exits"] = {k: v["n"] for k, v in (diag.get("exits") or {}).items()}
    out["sides"] = {k: [v["n"], v["wr"], v["pnl"]] for k, v in (diag.get("sides") or {}).items() if v["n"]}
    out["hours"] = {k: [v["n"], v["wr"], v["pnl"]] for k, v in (diag.get("hours") or {}).items()}
    return out


def _fmt_split(d) -> str:
    if isinstance(d, dict):
        return f"{d.get('n')}T/WR {d.get('wr')}%/{float(d.get('pnl') or 0):+.1f}"
    if isinstance(d, (list, tuple)) and len(d) == 3:
        return f"{d[0]}T/WR {d[1]}%/{float(d[2] or 0):+.1f}"
    return "-"


def describe(diag: Optional[Dict], label: str) -> List[str]:
    """Prompt-Zeilen aus voller ODER kompakter Diagnose."""
    if not diag or not diag.get("n"):
        return [f"{label}: keine Trades"]
    ex = diag.get("exits") or {}
    ex_txt = ", ".join(f"{k}={v['n'] if isinstance(v, dict) else v}" for k, v in ex.items())
    rows = [f"{label}: {diag['n']} Trades, WR {diag.get('wr')}%, PnL {float(diag.get('pnl') or 0):+.2f}, "
            f"PF {diag.get('pf')}, Payoff {diag.get('payoff')}, Ø/Trade {diag.get('expectancy')}, "
            f"max. Drawdown {diag.get('max_dd')}, längste Verlustserie {diag.get('loss_streak')}"
            + (f", Gebühren gesamt {diag['fees']}" if diag.get("fees") is not None else "")]
    rows.append(f"  Exits: {ex_txt or '-'} · Haltedauer median {diag.get('hold_bars')} Kerzen")
    sides = diag.get("sides") or {}
    if sides:
        rows.append("  Seiten: " + " · ".join(f"{k} {_fmt_split(v)}" for k, v in sides.items()))
    hours = diag.get("hours") or {}
    if hours:
        rows.append("  Uhrzeit (Berlin): " + " · ".join(f"{k}h {_fmt_split(v)}" for k, v in hours.items()))
    syms = diag.get("symbols") or {}
    if syms:
        rows.append("  Symbole: " + " · ".join(f"{k} {_fmt_split(v)}" for k, v in syms.items()))
    if diag.get("mfe_losers_r") is not None:
        rows.append(f"  Verlierer liefen median {diag['mfe_losers_r']}R ins Plus, Gewinner median "
                    f"{diag.get('mae_winners_r')}R ins Minus; {diag.get('sl_reached_half_r')}% der "
                    "SL-Trades hatten vorher >= 0.5R Gewinn")
    if diag.get("equity"):
        rows.append("  Equity-Kurve: " + " → ".join(f"{x:+.0f}" for x in diag["equity"]))
    return rows


# --------------------------------------------------------------------------
# Vergleichsmaß + Lernschleife
# --------------------------------------------------------------------------
def score(is_stats: Optional[Dict], oos_stats: Optional[Dict], diag: Optional[Dict] = None,
          min_trades: int = 10) -> float:
    """PnL je Trade (nach Gebühren), OOS stärker gewichtet, wenige Trades abgewertet,
    tiefe Drawdowns bestraft (dd_factor, max. Halbierung – glatte Equity-Kurven
    schlagen Glückssträhnen). Ein Satz ohne Trades in einem Fenster wird stark bestraft.
    `diag` = {'is': compact, 'oos': compact} (optional, Alt-Historie ohne diag bleibt gültig)."""
    def part(st: Optional[Dict], d: Optional[Dict]) -> float:
        n = int((st or {}).get("trades") or 0)
        if n == 0:
            return -1.0
        pnl = float((st or {}).get("pnl") or 0)
        base = pnl / n * min(1.0, n / max(1, min_trades))
        f = dd_factor(pnl, (d or {}).get("max_dd"))
        return base * f if base >= 0 else base / f
    diag = diag or {}
    return round(0.4 * part(is_stats, diag.get("is")) + 0.6 * part(oos_stats, diag.get("oos")), 4)


def dd_factor(pnl: float, max_dd: Optional[float]) -> float:
    """Drawdown-Strafe (rein): 1 − DD/(|PnL|+DD), gedeckelt bei 0.5.
    PnL +10 / DD 2 -> 0.83, PnL +10 / DD 10 -> 0.5, kein DD -> 1.0."""
    try:
        dd = float(max_dd or 0)
    except (TypeError, ValueError):
        dd = 0.0
    if dd <= 0:
        return 1.0
    return round(1.0 - min(0.5, dd / (abs(float(pnl or 0)) + dd)), 4)


def entry_score(h: Dict) -> float:
    return score(h.get("is"), h.get("oos"), h.get("diag"))


def windows_text(windows: Sequence[Dict]) -> str:
    """Walk-Forward-Fenster als '+/−/+' (0 Trades = '0')."""
    return "/".join("0" if not int(w.get("trades") or 0) else ("+" if float(w.get("pnl") or 0) > 0 else "−")
                    for w in windows or [])


def timeline(history: List[Dict]) -> List[Dict]:
    """Lernkurve fürs UI (rein): je Historien-Eintrag Name, Score, KI-Flag, bestanden."""
    return [{"name": h.get("name"), "score": entry_score(h), "ai": bool(h.get("ai")),
             "passed": bool(h.get("passed")), "at": h.get("at"),
             "oos_pnl": float(((h.get("oos") or {}).get("pnl")) or 0)} for h in (history or [])]


def param_diff(old: Optional[Dict], new: Optional[Dict]) -> List[str]:
    """Geänderte Schlüssel als 'k: a→b' (rein)."""
    old, new = old or {}, new or {}
    out = []
    for k in sorted(set(old) | set(new)):
        if k == "name":
            continue
        a, b = old.get(k), new.get(k)
        if a != b:
            out.append(f"{k}: {a if a is not None else '–'}→{b if b is not None else '–'}")
    return out


def best_entry(history: List[Dict]) -> Optional[Dict]:
    """Bester Eintrag der Historie MIT Parametern (Basis der nächsten Revision)."""
    cands = [h for h in (history or []) if isinstance(h.get("params"), dict)]
    if not cands:
        return None
    return max(cands, key=entry_score)


def lessons(history: List[Dict], defaults: Optional[Dict] = None) -> List[str]:
    """Für jede KI-Revision: Änderung vs. Vorgänger, Hypothese und ob das
    Ergebnis (Score) besser oder schlechter als das bis dahin Beste war.
    `defaults` (optionale Schlüssel) macht Änderungen gegenüber dem Altverhalten
    lesbar ('tp1_r: 1.0→0.5' statt '–→0.5')."""
    out: List[str] = []
    best_so_far: Optional[float] = None
    prev: Optional[Dict] = None
    seen: List[Dict] = []
    defaults = defaults or {}
    for h in history or []:
        sc = entry_score(h)
        if h.get("ai") and isinstance(h.get("params"), dict):
            # Referenz = der Satz, von dem die Revision ausging (base), sonst der Vorgänger
            ref = next((e for e in reversed(seen) if h.get("base") and e.get("name") == h["base"]), None) or prev
            diff = param_diff({**defaults, **((ref or {}).get("params") or {})}, {**defaults, **h["params"]})
            verdict = "erster Versuch" if best_so_far is None else (
                "BESSER als bisher Bestes" if sc > best_so_far + 0.01 else
                "SCHLECHTER als bisher Bestes" if sc < best_so_far - 0.01 else "kaum Unterschied")
            change_txt = ", ".join(diff) or "Bestätigungslauf, Parameter unverändert"
            base_txt = f" von {h['base']}" if h.get("base") and diff else ""
            line = f"- {h.get('name')} ({change_txt}{base_txt}): Score {sc:+.3f} → {verdict}"
            if h.get("reason"):
                line += f". Hypothese: {h['reason']}"
            if h.get("expect"):
                line += f" Erwartet: {h['expect']}"
            o = h.get("oos") or {}
            line += f" · OOS {o.get('trades', 0)}T/WR {o.get('winrate', 0)}%/{float(o.get('pnl') or 0):+.2f}"
            if o.get("windows"):
                line += f" · Fenster {windows_text(o['windows'])}"
            out.append(line)
        best_so_far = sc if best_so_far is None else max(best_so_far, sc)
        prev = h
        seen.append(h)
    return out[-6:]
