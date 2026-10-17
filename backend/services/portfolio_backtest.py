"""Portfolio-Backtest (Audit 3.5) – eigener Modus, keine Änderung bestehender Läufe.

Beantwortet die Frage, die Einzel-Backtests nicht beantworten können: Was passiert,
wenn ALLE gewählten Strategien/Coins GLEICHZEITIG aus EINEM gemeinsamen Kapitaltopf
handeln? Dazu:
  1. Einzel-Simulationen wie bisher (unveränderte `backtester.simulate_pair`-Logik,
     collect_trades=True) liefern alle Trades mit Zeitstempeln.
  2. `portfolio_replay` spielt alle Trades chronologisch gegen ein gemeinsames Kapital
     ab: Margin-Reservierung je Trade, max. gleichzeitige Positionen, Risikobudget
     (Regeln analog services/risk_budget: Summe offenes Risiko <= max_portfolio_risk_pct
     × Equity, Cluster-Limit je Anlageklasse) – übersprungene Trades werden je Grund
     gezählt.
  3. Korrelation der Tages-PnL je Symbol (gleichzeitige Verluste sichtbar machen).
  4. Szenarien: Slippage-Stress (Aufschlag je Fill) und Ausfall (deterministische
     Offline-Fenster: Entries entfallen, Exits im Fenster schließen schlechtestenfalls
     am initialen SL).

Alles REINE Funktionen bis auf den Runner (eigener Job-Store PJOBS, Muster bt.JOBS).
"""
import asyncio
import gc
import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Dokumentation der Defaults: start_capital = gemeinsamer Topf (USDT);
# per_trade_margin 0 = Marge je Trade wie in der Einzel-Simulation (max_capital);
# outage_* = Anzahl/Dauer der deterministischen Ausfall-Fenster im Zeitraum.
DEFAULT_PORTFOLIO_CFG = {
    "start_capital": 1000.0,
    "per_trade_margin": 0.0,
    "max_open_trades": 5,
    "max_portfolio_risk_pct": 6.0,   # wie risk_budget.DEFAULT_CONFIG
    "max_cluster_risk_pct": 4.0,     # 0 = aus
    "slippage_pct": 0.05,            # Szenario: Aufschlag je Fill-Seite in %
    "outage_count": 3,
    "outage_hours": 12.0,
}

PJOBS: Dict[str, Dict] = {}


def sanitize_portfolio_cfg(overrides: Optional[Dict]) -> Dict:
    """Nutzer-Overrides validieren (rein): nur bekannte Keys, Zahlen, Untergrenzen."""
    cfg = dict(DEFAULT_PORTFOLIO_CFG)
    for k, v in (overrides or {}).items():
        if k not in DEFAULT_PORTFOLIO_CFG:
            continue
        try:
            f = float(v)
        except (TypeError, ValueError):
            continue
        if k == "max_open_trades":
            cfg[k] = max(1, min(int(f), 50))
        elif k == "outage_count":
            cfg[k] = max(0, min(int(f), 20))
        else:
            cfg[k] = max(0.0, f)
    if cfg["start_capital"] <= 0:
        cfg["start_capital"] = DEFAULT_PORTFOLIO_CFG["start_capital"]
    return cfg


def _ms(iso: str) -> Optional[int]:
    try:
        return int(datetime.fromisoformat(str(iso)).timestamp() * 1000)
    except (ValueError, TypeError):
        return None


def normalize_trades(export_trades: List[Dict]) -> List[Dict]:
    """Einzel-Sim-Trades (backtester all_trades + strategy_id/symbol) -> Replay-Zeilen
    (rein). Zeilen ohne gültige Zeitstempel werden verworfen."""
    from services.risk_budget import cluster_of
    rows = []
    for t in export_trades or []:
        o, c = _ms(t.get("opened")), _ms(t.get("closed"))
        if not o or not c or c < o:
            continue
        qty = float(t.get("qty") or 0)
        rows.append({
            "symbol": t.get("symbol"), "strategy_id": t.get("strategy_id"),
            "open_ms": o, "close_ms": c,
            "pnl": float(t.get("pnl") or 0), "fees": float(t.get("fees") or 0),
            "risk_usdt": max(0.0, float(t.get("risk") or 0)) * qty,
            "notional": float(t.get("entry") or 0) * qty,
            "cluster": cluster_of(t.get("symbol")),
        })
    rows.sort(key=lambda r: (r["open_ms"], r["close_ms"]))
    return rows


def portfolio_replay(rows: List[Dict], pcfg: Dict, sim_margin: float) -> Dict:
    """Chronologisches Replay gegen gemeinsames Kapital (rein).

    Marge je Trade = per_trade_margin (0 = sim_margin, dann Skalierung 1). PnL/Fees/
    Risiko der Einzel-Sim skalieren linear mit der Marge (qty ~ capital)."""
    margin = float(pcfg.get("per_trade_margin") or 0) or float(sim_margin or 0) or 100.0
    scale = margin / float(sim_margin or margin)
    start = float(pcfg.get("start_capital", 1000.0))
    max_open = int(pcfg.get("max_open_trades", 5))
    pmax = float(pcfg.get("max_portfolio_risk_pct") or 0)
    cmax = float(pcfg.get("max_cluster_risk_pct") or 0)

    equity, peak, max_dd = start, start, 0.0
    open_list: List[Dict] = []          # aktive Trades (skaliert)
    skipped = {"max_positions": 0, "kapital": 0, "risikobudget": 0, "cluster": 0}
    taken: List[Dict] = []
    curve: List[Tuple[int, float]] = []
    conc_samples: List[int] = []
    eps = 1e-6

    def _close_due(now_ms: int):
        nonlocal equity, peak, max_dd
        due = [t for t in open_list if t["close_ms"] <= now_ms]
        for t in sorted(due, key=lambda x: x["close_ms"]):
            open_list.remove(t)
            equity += t["pnl"]
            peak = max(peak, equity)
            max_dd = max(max_dd, peak - equity)
            curve.append((t["close_ms"], round(equity, 2)))

    for r in rows:
        _close_due(r["open_ms"])
        s = {**r, "pnl": r["pnl"] * scale, "fees": r["fees"] * scale,
             "risk_usdt": r["risk_usdt"] * scale, "notional": r["notional"] * scale}
        if len(open_list) >= max_open:
            skipped["max_positions"] += 1
            continue
        if len(open_list) * margin + margin > equity + eps:
            skipped["kapital"] += 1
            continue
        open_risk = sum(t["risk_usdt"] for t in open_list)
        if pmax > 0 and open_risk + s["risk_usdt"] > equity * pmax / 100 + eps:
            skipped["risikobudget"] += 1
            continue
        if cmax > 0:
            cluster_risk = sum(t["risk_usdt"] for t in open_list if t["cluster"] == s["cluster"])
            if cluster_risk + s["risk_usdt"] > equity * cmax / 100 + eps:
                skipped["cluster"] += 1
                continue
        open_list.append(s)
        taken.append(s)
        conc_samples.append(len(open_list))

    _close_due(2**62)  # Rest schließen

    wins = sum(1 for t in taken if t["pnl"] > eps)
    losses = sum(1 for t in taken if t["pnl"] < -eps)
    per_symbol: Dict[str, Dict] = {}
    for t in taken:
        agg = per_symbol.setdefault(t["symbol"], {"symbol": t["symbol"], "trades": 0, "pnl": 0.0})
        agg["trades"] += 1
        agg["pnl"] = round(agg["pnl"] + t["pnl"], 2)
    # Equity-Kurve deckeln (UI/Payload)
    if len(curve) > 400:
        step = len(curve) / 400.0
        curve = [curve[int(i * step)] for i in range(400)] + [curve[-1]]
    return {
        "candidates": len(rows), "trades": len(taken),
        "wins": wins, "losses": losses,
        "win_rate": round(wins / (wins + losses) * 100, 1) if (wins + losses) else 0.0,
        "pnl": round(equity - start, 2),
        "return_pct": round((equity - start) / start * 100, 2) if start else 0.0,
        "fees": round(sum(t["fees"] for t in taken), 2),
        "final_equity": round(equity, 2),
        "max_drawdown": round(max_dd, 2),
        "max_drawdown_pct": round(max_dd / peak * 100, 2) if peak > 0 else 0.0,
        "skipped": skipped,
        "max_concurrent": max(conc_samples) if conc_samples else 0,
        "avg_concurrent": round(sum(conc_samples) / len(conc_samples), 1) if conc_samples else 0.0,
        "per_trade_margin": round(margin, 2),
        "equity_curve": [{"ts": datetime.fromtimestamp(ts / 1000, tz=timezone.utc).isoformat(),
                          "equity": eq} for ts, eq in curve],
        "per_symbol": sorted(per_symbol.values(), key=lambda x: x["pnl"]),
    }


def daily_pnl_by_symbol(rows: List[Dict]) -> Dict[str, Dict[str, float]]:
    """Tages-PnL je Symbol (rein), Tag = UTC-Datum des Trade-Close."""
    out: Dict[str, Dict[str, float]] = {}
    for r in rows or []:
        day = datetime.fromtimestamp(r["close_ms"] / 1000, tz=timezone.utc).date().isoformat()
        d = out.setdefault(r["symbol"], {})
        d[day] = d.get(day, 0.0) + r["pnl"]
    return out


def _pearson(xs: List[float], ys: List[float]) -> Optional[float]:
    n = len(xs)
    if n < 2:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    vx = sum((x - mx) ** 2 for x in xs)
    vy = sum((y - my) ** 2 for y in ys)
    if vx <= 0 or vy <= 0:
        return None
    return cov / (vx ** 0.5 * vy ** 0.5)


def correlation_pairs(daily: Dict[str, Dict[str, float]], min_days: int = 5) -> Dict:
    """Pearson-Korrelation der Tages-PnL je Symbol-Paar (rein).
    Nur Tage, an denen BEIDE Symbole aktiv waren; Paare mit < min_days ausgelassen."""
    symbols = sorted(daily or {})
    pairs = []
    for i, a in enumerate(symbols):
        for b in symbols[i + 1:]:
            common = sorted(set(daily[a]) & set(daily[b]))
            if len(common) < min_days:
                continue
            corr = _pearson([daily[a][d] for d in common], [daily[b][d] for d in common])
            if corr is None:
                continue
            pairs.append({"a": a, "b": b, "corr": round(corr, 3), "days": len(common)})
    pairs.sort(key=lambda p: p["corr"], reverse=True)
    avg = round(sum(p["corr"] for p in pairs) / len(pairs), 3) if pairs else None
    return {"pairs": pairs[:15], "avg_corr": avg, "n_pairs": len(pairs)}


def outage_windows(start_ms: int, end_ms: int, count: int, hours: float) -> List[Tuple[int, int]]:
    """Deterministische, gleichverteilte Ausfall-Fenster im Zeitraum (rein, kein Zufall)."""
    count = max(0, int(count))
    dur = int(max(0.0, float(hours)) * 3600 * 1000)
    if count == 0 or dur == 0 or end_ms <= start_ms:
        return []
    span = end_ms - start_ms
    return [(start_ms + span * (i + 1) // (count + 1),
             start_ms + span * (i + 1) // (count + 1) + dur) for i in range(count)]


def _in_window(ts: int, windows: List[Tuple[int, int]]) -> bool:
    return any(a <= ts < b for a, b in windows)


def apply_outage(rows: List[Dict], windows: List[Tuple[int, int]]) -> Tuple[List[Dict], Dict]:
    """Ausfall-Szenario (rein): Entry im Fenster -> Trade entfällt (kein Signal);
    Exit im Fenster -> schlechtester Fall: Close am initialen SL (pnl = -risk - fees),
    nie besser als das echte Ergebnis."""
    out, dropped, forced = [], 0, 0
    for r in rows or []:
        if _in_window(r["open_ms"], windows):
            dropped += 1
            continue
        if _in_window(r["close_ms"], windows):
            worst = -(r["risk_usdt"] + r["fees"])
            if worst < r["pnl"]:
                r = {**r, "pnl": worst, "outage_forced_sl": True}
                forced += 1
        out.append(r)
    return out, {"entries_dropped": dropped, "exits_forced_sl": forced,
                 "windows": len(windows)}


def apply_slippage(rows: List[Dict], slippage_pct: float) -> List[Dict]:
    """Slippage-Stress (rein): Aufschlag je Fill-Seite (Entry+Exit) auf das Notional."""
    f = max(0.0, float(slippage_pct or 0)) / 100
    return [{**r, "pnl": r["pnl"] - r["notional"] * 2 * f} for r in rows or []]


def run_portfolio_analysis(export_trades: List[Dict], sim_margin: float, pcfg: Dict) -> Dict:
    """Gesamtauswertung (rein): Basis-Replay + Korrelation + Szenarien."""
    rows = normalize_trades(export_trades)
    base = portfolio_replay(rows, pcfg, sim_margin)
    # Korrelation auf den tatsächlich genommenen Trades wäre zirkulär (Skips) –
    # bewusst auf ALLEN Kandidaten, das zeigt die strukturelle Gleichläufigkeit.
    correlation = correlation_pairs(daily_pnl_by_symbol(rows))
    scenarios = {}
    slip = portfolio_replay(apply_slippage(rows, pcfg.get("slippage_pct", 0)), pcfg, sim_margin)
    slip.pop("equity_curve", None)
    scenarios["slippage_stress"] = {"slippage_pct": pcfg.get("slippage_pct", 0), **slip}
    if rows and int(pcfg.get("outage_count", 0)) > 0:
        windows = outage_windows(rows[0]["open_ms"], max(r["close_ms"] for r in rows),
                                 pcfg.get("outage_count", 3), pcfg.get("outage_hours", 12))
        o_rows, o_meta = apply_outage(rows, windows)
        o_res = portfolio_replay(o_rows, pcfg, sim_margin)
        o_res.pop("equity_curve", None)
        scenarios["outage"] = {**o_meta, "outage_hours": pcfg.get("outage_hours", 12), **o_res}
    return {"portfolio": base, "correlation": correlation, "scenarios": scenarios,
            "config": dict(pcfg), "sim_margin": sim_margin}


# ------------------------------ Runner (Job) --------------------------------
def create_job(params: Dict) -> str:
    job_id = uuid.uuid4().hex[:12]
    PJOBS[job_id] = {"id": job_id, "status": "running", "progress": 0,
                     "phase": "Daten laden", "params": params, "cancel": False,
                     "created_at": datetime.now(timezone.utc).isoformat(),
                     "result": None, "error": None}
    if len(PJOBS) > 5:
        for k in list(PJOBS.keys())[:-5]:
            PJOBS.pop(k, None)
    return job_id


def latest_job() -> Optional[Dict]:
    return list(PJOBS.values())[-1] if PJOBS else None


async def run_portfolio_backtest(job_id: str, strategy_ids: List[str], symbols: List[str],
                                 days: int, cfg: Dict, pcfg: Dict, registry, settings: Dict,
                                 strategy_configs: Dict = None, default_timeframe: str = None):
    """Einzel-Sims (unveränderte simulate_pair-Logik) + Portfolio-Auswertung."""
    import aiohttp
    from services import backtester as bt
    from services import fast_sim
    from services.timeframes import aggregate_candles

    job = PJOBS[job_id]
    strategy_configs = strategy_configs or {}

    def cancelled():
        return bool(job.get("cancel"))

    try:
        export_trades: List[Dict] = []
        total_units = max(len(symbols) * (1 + len(strategy_ids)), 1)
        done_units = 0
        async with aiohttp.ClientSession() as session:
            for sym in symbols:
                if cancelled():
                    raise bt.JobCancelled()
                job["phase"] = f"Lade Daten: {sym}"
                history = await bt.fetch_history(session, sym, days, job=job)
                done_units += 1
                job["progress"] = round(done_units / total_units * 100)
                if len(history) <= 100:
                    done_units += len(strategy_ids)
                    job["progress"] = round(done_units / total_units * 100)
                    continue
                tf_cache: Dict[str, List[Dict]] = {}
                fs_cache: Dict[str, "fast_sim.FastSeries"] = {}
                for sid in strategy_ids:
                    if cancelled():
                        raise bt.JobCancelled()
                    strat = registry.get(sid)
                    if not strat:
                        done_units += 1
                        continue
                    scfg = strategy_configs.get(sid) or {}
                    strat = bt._effective_strategy(strat, sid, scfg)
                    tf = bt.resolve_timeframe(strat, sid, scfg, settings, default_timeframe)
                    if tf not in tf_cache:
                        tf_cache[tf] = aggregate_candles(history, tf)
                    candles = tf_cache[tf]
                    pair_cfg = bt._pair_trade_cfg(cfg, scfg)
                    eff_settings = bt._pair_settings(settings, sid, scfg)
                    provider = None
                    try:
                        if tf not in fs_cache:
                            fs_cache[tf] = fast_sim.FastSeries(candles)
                        provider = fast_sim.provider_for(strat, fs_cache[tf], eff_settings, sym)
                    except Exception as e:
                        logger.warning(f"portfolio fast_sim fallback {sid}: {e}")
                    job["phase"] = f"Simuliere {getattr(strat, 'STRATEGY_NAME', sid)} auf {sym} ({tf})"
                    res = await asyncio.to_thread(bt.simulate_pair, strat, candles, sym,
                                                  eff_settings, pair_cfg, None, True,
                                                  cancelled, provider)
                    for t in res.get("all_trades", []):
                        export_trades.append({"strategy_id": sid, "symbol": sym, **t})
                    done_units += 1
                    job["progress"] = round(done_units / total_units * 100)
                del history, tf_cache, fs_cache
                gc.collect()

        job["phase"] = "Portfolio-Replay"
        result = run_portfolio_analysis(export_trades, float(cfg.get("max_capital", 100.0)), pcfg)
        result["days"] = days
        result["strategy_ids"] = strategy_ids
        result["symbols"] = symbols
        job["result"] = result
        job["status"] = "done"
        job["progress"] = 100
        job["phase"] = "Fertig"
    except bt.JobCancelled:
        job["status"] = "cancelled"
        job["phase"] = "Abgebrochen"
    except Exception as e:
        logger.exception(f"Portfolio-Backtest {job_id} fehlgeschlagen")
        job["status"] = "error"
        job["error"] = str(e)
