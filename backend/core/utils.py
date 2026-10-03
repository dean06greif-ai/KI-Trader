"""Gemeinsame Helfer (aus server.py verschoben, Logik unverändert)."""
import logging
from datetime import datetime, timezone
from typing import Dict, List

from core.instruments import kline_available

logger = logging.getLogger(__name__)


def _clean(d: Dict) -> Dict:
    d = dict(d)
    d.pop("_id", None)
    return d


def _enrich_trade(t: Dict, current_price: float = None, exchange: Dict = None) -> Dict:
    """Add computed analytics fields to a trade without changing stored schema.
    Gives the UI and the AI exact numbers: durations, distances (%), R-multiple.

    For OPEN trades a `current_price` (live mark price) can be passed in. We then
    compute the UNREALIZED PnL on the remaining quantity and expose it, plus a
    `live_pnl` (= realized so far + unrealized). The percentage/R fields for open
    trades reflect this live PnL instead of the stored `realized_pnl` (which for a
    fresh open trade is only the negative entry fee → looked like a "loss" before).

    `exchange` (optional): echte Bitunix-Positionsdaten {"upnl", "qty"}. Wenn
    vorhanden, hat der Börsen-uPnL VORRANG vor der Scanner-Berechnung – die
    Scanner-Preise (z.B. Yahoo-Gold GC=F vs. Bitunix XAUUSDT) wichen teils stark
    ab und zeigten grob falsche PnL-Werte an (Bug-Report: Gold -2$ real,
    -170$ angezeigt).
    """
    t = _clean(t)
    entry = float(t.get("entry") or 0)
    side = t.get("side", "LONG")
    sl = float(t.get("sl") or 0)
    init_sl = float(t.get("initial_sl") or sl or 0)
    tp1 = float(t.get("tp1") or 0)
    tpf = float(t.get("tpf") or 0)
    qty = float(t.get("qty") or 0)
    qty_rem = float(t.get("qty_remaining", qty) or 0)
    risk = float(t.get("risk") or 0)
    exit_price = t.get("exit_price")
    is_open = t.get("status") == "open"
    realized = float(t.get("realized_pnl") or 0)

    def pct_from_entry(p):
        if not entry or not p:
            return None
        return round((p - entry) / entry * 100, 3)

    # ---- Live / unrealized PnL for open trades ----
    cur = None
    try:
        cur = float(current_price) if current_price else None
    except (TypeError, ValueError):
        cur = None
    unrealized_pnl = None
    live_pnl = None
    pnl_source = None
    if is_open and cur and entry and qty_rem > 0:
        gross = (cur - entry) * qty_rem if side == "LONG" else (entry - cur) * qty_rem
        unrealized_pnl = round(gross, 6)
        # realized already carries the entry fee (and any TP1 partial) → live = realized + unrealized
        live_pnl = round(realized + gross, 6)
        pnl_source = "scanner"

    # ---- Börsen-Wahrheit: unrealisierter PnL direkt von Bitunix hat Vorrang ----
    if is_open and exchange and exchange.get("upnl") is not None and qty_rem > 0:
        try:
            ex_upnl = float(exchange["upnl"])
            ex_qty = float(exchange.get("qty") or 0)
            # Teil-Position (z.B. nach TP1): anteilig auf die Rest-Menge umlegen
            share = qty_rem / ex_qty if (ex_qty > 0 and qty_rem < ex_qty * 0.98) else 1.0
            gross = ex_upnl * share
            if ex_qty > 0 and entry:
                per_unit = ex_upnl / ex_qty
                cur = entry + per_unit if side == "LONG" else entry - per_unit
            unrealized_pnl = round(gross, 6)
            live_pnl = round(realized + gross, 6)
            pnl_source = "bitunix"
        except (TypeError, ValueError, ZeroDivisionError):
            pass

    # Effective PnL used for the % / R metrics: live PnL while open (if we have a
    # price), otherwise the realized PnL (closed trades or no price available).
    eff_pnl = live_pnl if (is_open and live_pnl is not None) else realized

    # timings
    dur = None
    o, c = t.get("opened_at"), t.get("closed_at")
    try:
        if o:
            o_dt = datetime.fromisoformat(o.replace("Z", "+00:00"))
            end_dt = datetime.fromisoformat(c.replace("Z", "+00:00")) if c \
                else datetime.now(timezone.utc)
            dur = int((end_dt - o_dt).total_seconds())
    except Exception:
        dur = None

    # R-multiple: (effective) PnL relative to the initial 1R risk in USDT
    risk_usd = round(risk * qty, 4) if (risk and qty) else 0.0
    r_multiple = None
    if risk_usd:
        r_multiple = round(eff_pnl / risk_usd, 2)

    # PnL in % on the used capital (margin)
    capital = float(t.get("max_capital") or 0)
    pnl_pct_capital = None
    if capital:
        pnl_pct_capital = round(eff_pnl / capital * 100, 2)

    # PnL in % of the position size (entry * qty)
    pos_size = entry * qty
    pnl_pct = None
    if pos_size:
        pnl_pct = round(eff_pnl / pos_size * 100, 2)

    # PnL in % auf die gebundene Margin – gleiche Basis wie Bitunix.
    # margin_used wird bei Margin-/Hebel-Anpassungen gepflegt; sonst Notional/Hebel.
    lev = float(t.get("leverage") or 1) or 1
    margin = float(t.get("margin_used") or 0)
    # Geschlossene Trades: qty_remaining ist 0 -> volle qty als Margin-Basis,
    # sonst wäre pnl_pct_margin None und die UI fiele auf Positions-% zurück
    # (Bug-Report: DOT +10,18$ bei 99$ Margin zeigte +0,26% statt ~+10,3%).
    margin_qty = qty_rem if qty_rem else qty
    init_margin = (entry * margin_qty) / max(lev, 0.01) if (entry and margin_qty) else 0.0
    if margin <= 0:
        margin = init_margin
    pnl_pct_margin = None
    upnl_pct_margin = None
    # Basis wie Bitunix (Bug-Report 26.08.: manueller BTC-Trade zeigte +335,81%
    # statt ~+158%): GESCHLOSSENE Trades rechnen auf die Initial-Marge
    # (Notional / Hebel-Setting – so rechnet die Bitunix-Position-History-ROI);
    # OFFENE Trades auf die aktuell gebundene Marge (wie die Live-Anzeige).
    # Nach Margen-Entnahme (Profit-Lock/manuell) würde die geschrumpfte Marge
    # den Prozentwert sonst künstlich aufblähen.
    basis = margin if is_open else (init_margin or margin)
    if basis > 0:
        pnl_pct_margin = round(eff_pnl / basis * 100, 2)
    if margin > 0 and unrealized_pnl is not None:
        # exakt die Bitunix-Anzeige: unrealisierter PnL / gebundene Margin
        upnl_pct_margin = round(unrealized_pnl / margin * 100, 2)

    # ---- Gebühren-Aufschlüsselung (Entry / Close / gesamt) ----
    fee_pct = float(t.get("fee_percent", 0.06) or 0.06)
    fees_paid = float(t.get("fees_paid") or 0)
    entry_fee = round(entry * qty * fee_pct / 100, 6) if (entry and qty) else 0.0
    est_close_fee = None
    exit_fee = None
    if is_open:
        basis = cur or entry
        if basis and qty_rem > 0:
            est_close_fee = round(qty_rem * basis * fee_pct / 100, 6)
        fees_total_est = round(fees_paid + (est_close_fee or 0), 6)
    else:
        exit_fee = round(max(fees_paid - entry_fee, 0.0), 6)
        fees_total_est = round(fees_paid, 6)

    # Reine Kursbewegung in Richtung des Trades (ohne Hebel, ohne Gebühren)
    price_move_pct = None
    ref = cur if is_open else (float(exit_price) if exit_price else None)
    if entry and ref:
        move = (ref - entry) / entry * 100
        price_move_pct = round(move if side == "LONG" else -move, 3)

    # ---- Bester Stand im Trade (MFE): LONG = Hoch, SHORT = Tief ----
    # Offene Trades: live aus gespeichertem Peak + aktuellem Kurs berechnet.
    # Geschlossene: gespeicherter peak_price (wird seit dem MFE-Update im
    # Trade-Management getrackt) – ältere Trades ohne Wert zeigen None ("–").
    peak = None
    try:
        peak = float(t.get("peak_price")) if t.get("peak_price") else None
    except (TypeError, ValueError):
        peak = None
    if is_open and cur:
        cand = [v for v in (peak, cur, entry) if v]
        if cand:
            peak = max(cand) if side == "LONG" else min(cand)
    peak_pct = pct_from_entry(peak) if peak else None
    mfe_pct = None
    if peak_pct is not None:
        mfe_pct = round((peak_pct if side == "LONG" else -peak_pct) + 0.0, 3)

    # ---- Schlechtester Stand im Trade (MAE): LONG = Tief, SHORT = Hoch ----
    # mae_pct ist wie mfe_pct in Trade-Richtung signiert (negativ = Gegenlauf).
    trough = None
    try:
        trough = float(t.get("trough_price")) if t.get("trough_price") else None
    except (TypeError, ValueError):
        trough = None
    if is_open and cur:
        # Entry immer als Kandidat (wie der Tick-Tracker): sonst würde bei
        # Trades ohne gespeicherten Wert der aktuelle (Gewinn-)Kurs als
        # "schlechtester Stand" gemeldet und MAE positiv (Testing-Finding).
        cand_t = [v for v in (trough, cur, entry) if v]
        if cand_t:
            trough = min(cand_t) if side == "LONG" else max(cand_t)
    trough_pct = pct_from_entry(trough) if trough else None
    mae_pct = None
    if trough_pct is not None:
        mae_pct = round((trough_pct if side == "LONG" else -trough_pct) + 0.0, 3)

    t["computed"] = {
        "duration_seconds": dur,
        "risk_usd": risk_usd,
        "r_multiple": r_multiple,
        "pnl_pct_capital": pnl_pct_capital,
        "pnl_pct": pnl_pct,
        "pnl_pct_margin": pnl_pct_margin,
        "upnl_pct_margin": upnl_pct_margin,
        "margin_used": round(margin, 4) if margin > 0 else None,
        "notional_usdt": round(entry * qty, 2) if (entry and qty) else None,
        "fee_percent": fee_pct,
        "entry_fee": entry_fee or None,
        "exit_fee": exit_fee,
        "est_close_fee": est_close_fee,
        "fees_paid": round(fees_paid, 6),
        "fees_total_est": fees_total_est,
        "price_move_pct": price_move_pct,
        "peak_price": round(peak, 8) if peak else None,
        "peak_distance_pct": peak_pct,
        "mfe_pct": mfe_pct,
        "trough_price": round(trough, 8) if trough else None,
        "trough_distance_pct": trough_pct,
        "mae_pct": mae_pct,
        "current_price": round(cur, 6) if cur else None,
        "price_distance_pct": pct_from_entry(cur) if cur else None,
        "unrealized_pnl": unrealized_pnl,
        "live_pnl": live_pnl,
        "live_pnl_source": pnl_source,
        "sl_distance_pct": pct_from_entry(sl),
        "initial_sl_distance_pct": pct_from_entry(init_sl),
        "tp1_distance_pct": pct_from_entry(tp1),
        "tpf_distance_pct": pct_from_entry(tpf),
        "exit_distance_pct": pct_from_entry(float(exit_price)) if exit_price else None,
        "rr_tp1": t.get("tp1_crv"),
        "rr_tpf": t.get("tp_full_crv"),
        "sl_moved": round(sl - init_sl, 6) if (sl and init_sl) else 0,
        "side": side,
        # Trade-Chart nur mit Bitunix-Kline (Forex: Button ausblenden)
        "chart_available": kline_available(t.get("symbol") or ""),
    }
    return t


def _watch_job_task(task, jobs: Dict, job_id: str):
    """Ghost-Job-Schutz: Stirbt der Task, ohne den Status zu setzen,
    wird der Job als Fehler markiert (vorher: 'läuft' blockierte für immer)."""
    def _done(t):
        job = jobs.get(job_id)
        if job and job.get("status") == "running":
            job["status"] = "error"
            job["error"] = "Job-Task unerwartet beendet (automatisch zurückgesetzt)"
            job["phase"] = "Fehler"
        if t.cancelled():
            return
        exc = t.exception()
        if exc:
            logger.error(f"job task {job_id} crashed: {exc}")
    task.add_done_callback(_done)


def _job_public(job: Dict) -> Dict:
    """Job ohne Export-Rohdaten (sonst riesige Antworten) + ETA."""
    j = {k: v for k, v in job.items()
         if k not in ("export_candles", "export_trades", "_bench",
                      "phase_before_pause", "paused_at", "_conn_lost")}
    try:
        from services import job_control
        j.update(job_control.public_state(job))
        created = datetime.fromisoformat(job["created_at"])
        elapsed = (datetime.now(timezone.utc) - created).total_seconds() \
            - job_control.paused_seconds(job)
        j["elapsed_seconds"] = int(max(elapsed, 0))
        # Endlos-Autopilot (kein Zeit-/Runden-Limit): Balken = Bestwert-Score.
        # Beim Lesen überschreiben, damit auch veraltete lokale Worker (die noch
        # eine 95%-Deckelung melden) den echten Score anzeigen; keine ETA, da der
        # Fortschritt hier Güte statt Restzeit bedeutet.
        endless = False
        if job.get("kind") == "autopilot" and job.get("status") == "running":
            prm = job.get("params") or {}
            try:
                endless = not float(prm.get("max_minutes") or 0) \
                    and not int(prm.get("max_rounds") or 0)
            except (TypeError, ValueError):
                endless = False
            score = (job.get("best") or {}).get("score")
            if endless and isinstance(score, (int, float)):
                j["progress"] = int(round(min(max(float(score), 0.0), 100.0)))
        p = j.get("progress") or 0
        if job.get("status") == "running" and p >= 2 and not job.get("paused") \
                and not endless:
            j["eta_seconds"] = int(elapsed / p * (100 - p))
    except Exception:
        pass
    return j


def _equity_points(rows: List[Dict]) -> List[Dict]:
    rows = [r for r in rows if r.get("closed")]
    rows.sort(key=lambda r: r["closed"])
    points = []
    eq, peak = 0.0, 0.0
    for r in rows:
        pnl = float(r.get("pnl") or 0)
        eq += pnl
        peak = max(peak, eq)
        points.append({"t": r["closed"], "equity": round(eq, 4),
                       "peak": round(peak, 4), "drawdown": round(peak - eq, 4),
                       "pnl": round(pnl, 4), "symbol": r.get("symbol"),
                       "strategy_id": r.get("strategy_id"),
                       "strategy_name": r.get("strategy_name"),
                       "side": r.get("side"), "result": r.get("result"),
                       "liquidated": bool(r.get("liquidated"))})
    return points


def _rows_to_csv(rows: List[Dict], fieldnames: List[str]) -> str:
    import csv
    import io
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=fieldnames, extrasaction="ignore")
    w.writeheader()
    for r in rows:
        w.writerow(r)
    return buf.getvalue()


def slippage_aggregate(trades) -> list:
    """Fill-Qualität (Baustein B): aggregiert Trades mit slippage_pct nach
    (strategy_id, mode, order_kind) – inkl. Ø MFE/MAE (bester/schlechtester
    Stand nach dem Fill) als Adverse-Selection-Check je Order-Art."""
    groups = {}
    for t in trades:
        slip = t.get("slippage_pct")
        if slip is None:
            continue
        kind = "limit_fill" if t.get("limit_entry") else (t.get("order_kind") or "market")
        key = (t.get("strategy_id") or "unknown", t.get("mode") or "?", kind)
        g = groups.setdefault(key, {"trades": 0, "slip_sum": 0.0, "slip_usdt": 0.0,
                                    "mfe_sum": 0.0, "mfe_n": 0,
                                    "mae_sum": 0.0, "mae_n": 0})
        g["trades"] += 1
        g["slip_sum"] += float(slip)
        g["slip_usdt"] += float(t.get("slippage_usdt") or 0)
        try:
            entry = float(t.get("entry") or 0)
        except (TypeError, ValueError):
            entry = 0.0
        side = str(t.get("side") or "").upper()
        for field, s_key, n_key in (("peak_price", "mfe_sum", "mfe_n"),
                                    ("trough_price", "mae_sum", "mae_n")):
            try:
                val = float(t.get(field) or 0)
            except (TypeError, ValueError):
                val = 0.0
            if entry and val:
                pct = (val - entry) / entry * 100.0
                g[s_key] += pct if side == "LONG" else -pct
                g[n_key] += 1
    rows = []
    for (sid, mode, kind), g in groups.items():
        rows.append({
            "strategy_id": sid, "mode": mode, "order_kind": kind,
            "trades": g["trades"],
            "avg_slippage_pct": round(g["slip_sum"] / g["trades"] + 0.0, 4),
            "total_slippage_usdt": round(g["slip_usdt"] + 0.0, 4),
            "avg_mfe_pct": round(g["mfe_sum"] / g["mfe_n"] + 0.0, 3) if g["mfe_n"] else None,
            "avg_mae_pct": round(g["mae_sum"] / g["mae_n"] + 0.0, 3) if g["mae_n"] else None,
        })
    rows.sort(key=lambda r: -r["trades"])
    return rows
