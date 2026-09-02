"""KI-Trader Live-Diagnose.

Beantwortet deterministisch (ohne LLM) die drei Nutzer-Fragen:
  1. Fehlen Setups?        -> Setup-Katalog vs. gesammelte Daten vs. Live-Reife
  2. Falsche/ungenaue Daten? -> Kerzen-Frische, Orderflow-Quelle, Slippage, MFE/MAE
  3. Hindert ihn etwas?    -> Guard-Blockaden, Live-Gate-Umleitungen, Tageslimits

Reine Auswertung der bereits erfassten Messdaten (Bausteine A/B/E aus dem
Umsetzungsplan Live-Qualität). Jede Sektion ist fail-safe – ein Fehler in einer
Sektion kippt nicht den ganzen Report.
"""
import logging
import time
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

GUARD_CATS = [
    ("weekend", "Wochenend-Guard", "Markt geschlossen"),
    ("stale", "Stale-Price-Guard", "Stale-Price-Guard"),
    ("regime", "Regime-Sperrfilter", "Regime-Sperrfilter"),
    ("correlation", "Korrelations-Guard", "Korrelations-Guard"),
    ("direction", "Richtungs-Guard", "Richtungs-Guard"),
    ("cluster", "Cluster-Guard", "Cluster-Guard"),
    ("playbook", "Playbook-Sperre", "Playbook"),
    ("master", "MasterPrompt-Limits", "MasterPrompt"),
    ("momentum", "Momentum-News-Bremse", "Momentum-News-Bremse"),
    ("slippage", "Slippage-Wächter", "Slippage-Wächter"),
    ("duplicate", "Duplikat-Guard", "Duplikat-Guard"),
    ("lowvol", "Low-Vol-Block", "Low-Vol"),
]


def _agg(rows: List[Dict]) -> Dict:
    n = len(rows)
    wins = sum(1 for r in rows if float(r.get("realized_pnl") or 0) > 0)
    losses = sum(1 for r in rows if float(r.get("realized_pnl") or 0) < 0)
    pnl = sum(float(r.get("realized_pnl") or 0) for r in rows)
    wr = round(wins / (wins + losses) * 100, 1) if (wins + losses) else None
    return {"trades": n, "wins": wins, "losses": losses, "win_rate": wr,
            "pnl": round(pnl, 2), "avg_pnl": round(pnl / n, 3) if n else None}


def _group(rows: List[Dict], key: str) -> Dict[str, Dict]:
    buckets: Dict[str, List[Dict]] = {}
    for r in rows:
        buckets.setdefault(str(r.get(key) or "?"), []).append(r)
    return {k: _agg(v) for k, v in sorted(buckets.items())}


def _slippage_summary(rows: List[Dict]) -> Dict:
    vals = [float(r["slippage_pct"]) for r in rows if r.get("slippage_pct") is not None]
    usdt = [float(r.get("slippage_usdt") or 0) for r in rows if r.get("slippage_pct") is not None]
    if not vals:
        return {"measured_trades": 0}
    return {"measured_trades": len(vals),
            "avg_slippage_pct": round(sum(vals) / len(vals), 4),
            "worst_slippage_pct": round(max(vals), 4),
            "total_slippage_usdt": round(sum(usdt), 2)}


def build_findings(live: Dict, paper: Dict, per_setup_live: Dict, per_setup_paper: Dict,
                   per_side_live: Dict, slippage: Dict, guards: List[Dict],
                   setups: List[Dict], data_quality: List[Dict],
                   live_gate_redirects: int, daily_limit_hits: int) -> List[Dict]:
    """Deterministische Befunde (rein & testbar). frage: setups|daten|blockaden|allgemein."""
    f: List[Dict] = []

    def add(severity, frage, text):
        f.append({"severity": severity, "frage": frage, "text": text})

    # ---- Allgemein: Statistik-Vorbehalt & Gesamtlage ----
    lt = live.get("trades") or 0
    if lt == 0:
        add("info", "allgemein", "Im Zeitraum gab es KEINE geschlossenen Live-Trades – "
            "die Diagnose stützt sich auf Paper-/Sammel-Daten und Blockade-Statistik.")
    elif lt < 10:
        add("info", "allgemein", f"Nur {lt} geschlossene Live-Trades im Zeitraum – "
            "jede Winrate-Aussage ist statistisch schwach (Warnsignal, kein Beweis).")
    if lt >= 5 and (live.get("win_rate") or 0) < 40:
        add("kritisch", "allgemein",
            f"Live-Winrate {live.get('win_rate')}% über {lt} Trades "
            f"(PnL {live.get('pnl'):+.2f} USDT) – deutlich unter Plan.")

    # ---- Daten/Ausführung: Paper-vs-Live-Kluft + Slippage ----
    if (live.get("win_rate") is not None and paper.get("win_rate") is not None
            and lt >= 5 and (paper.get("trades") or 0) >= 10):
        gap = paper["win_rate"] - live["win_rate"]
        if gap > 15:
            add("warnung", "daten",
                f"Paper-Winrate {paper['win_rate']}% vs. Live {live['win_rate']}% "
                f"({gap:.0f} Punkte Kluft): Die Analyse-Richtung stimmt, die AUSFÜHRUNG "
                "verliert – typische Ursachen: Taker-Fees + Slippage bei engen Setups.")
    slip = slippage.get("avg_slippage_pct")
    if slip is not None and slippage.get("measured_trades", 0) >= 5:
        if slip > 0.03:
            add("kritisch", "daten",
                f"Ø Entry-Slippage {slip:+.3f}% über {slippage['measured_trades']} Trades "
                f"(gesamt {slippage.get('total_slippage_usdt', 0):+.2f} USDT): Einstiege sind "
                "systematisch teurer als der Signal-Preis. Empfehlung: mehr Limit-Entries an "
                "Key-Levels (entry_type=limit) bzw. Maker-Entries statt Market, enge 1m-Scalps meiden.")
        else:
            add("info", "daten", f"Ø Entry-Slippage {slip:+.3f}% "
                f"({slippage['measured_trades']} gemessene Trades) – unauffällig.")

    # ---- Daten: Kerzen-Frische & Orderflow-Quelle ----
    stale_syms = [d for d in data_quality if d.get("last_candle_age_min", 0) > 5]
    if stale_syms:
        names = ", ".join(f"{d['symbol']} ({d['last_candle_age_min']:.0f} min)" for d in stale_syms[:5])
        add("kritisch", "daten",
            f"Veraltete Kursdaten bei {len(stale_syms)} Symbol(en): {names} – der "
            "Stale-Price-Guard blockt hier zu Recht; Datenfeed prüfen (Bitunix-WS/Backfill).")
    proxy_syms = [d["symbol"] for d in data_quality if d.get("orderflow_real") is False]
    if proxy_syms and len(proxy_syms) <= 6:
        add("info", "daten",
            f"Orderflow läuft bei {', '.join(proxy_syms[:6])} nur als Kerzen-Proxy "
            "(kein echter Tick-Stream) – die KI sieht dort ungenauere Käufer-/Verkäufer-Daten.")

    # ---- Setups: Verlierer live, Gewinner nicht freigegeben, ungenutzte ----
    for sid, st in sorted(per_setup_live.items(), key=lambda kv: kv[1].get("pnl") or 0):
        if (st.get("trades") or 0) >= 5 and (st.get("win_rate") or 0) < 40:
            p = per_setup_paper.get(sid) or {}
            extra = (f" (Paper: {p.get('win_rate')}% über {p.get('trades')} Trades – "
                     "eher Ausführungs- als Setup-Problem)"
                     if (p.get("trades") or 0) >= 10 and (p.get("win_rate") or 0) >= 50 else "")
            add("kritisch", "setups",
                f"Setup '{sid}' live nur {st.get('win_rate')}% Winrate über {st['trades']} "
                f"Trades (PnL {st.get('pnl'):+.2f} USDT){extra} – Kandidat für "
                "Playbook-Sperre oder Konfidenzband-Bremse.")
    ready_not_live = [s for s in setups
                      if not s.get("live_ready") and (s.get("trades") or 0) == 0]
    if ready_not_live:
        add("warnung", "setups",
            f"{len(ready_not_live)} Setup(s) aus dem Katalog wurden im Zeitraum NIE gehandelt "
            f"({', '.join(s['setup'] for s in ready_not_live[:6])}) – hier fehlen der KI "
            "schlicht Daten; die Datensammlung (Paper) füllt das über Zeit.")
    proven_blocked = [s for s in setups if s.get("live_ready") is False
                      and (s.get("trades") or 0) >= 10 and (s.get("verdict") == "bewährt")]
    for s in proven_blocked:
        add("warnung", "setups",
            f"Setup '{s['setup']}' ist datenreif ({s['trades']} Trades, Urteil "
            f"'{s['verdict']}'), wird aber nicht live gehandelt: {s.get('live_reason')}")

    # ---- Blockaden ----
    total_blocked = sum(int(g.get("count") or 0) for g in guards)
    if total_blocked:
        top = max(guards, key=lambda g: int(g.get("count") or 0))
        if int(top.get("count") or 0) >= max(5, total_blocked * 0.3):
            add("info", "blockaden",
                f"{total_blocked} KI-Einstiege wurden von Wächtern verhindert – häufigster: "
                f"{top.get('label')} ({top.get('count')}×). Das ist Schutz, kein Fehler – "
                "aber prüfen, ob die Schwellen noch zur Marktlage passen.")
    if live_gate_redirects >= 5:
        add("warnung", "blockaden",
            f"{live_gate_redirects} Einstiege wurden vom Setup-Live-Gate in die "
            "Datensammlung umgeleitet (Setup noch nicht live-reif) – die KI handelt "
            "live also nur einen Teil ihrer Ideen. Optional: live_gate_bypass_per_day erhöhen.")
    if daily_limit_hits:
        add("warnung", "blockaden",
            f"An {daily_limit_hits} Tag(en) wurde das Tagesverlust-Limit erreicht – "
            "danach sind Live-Einstiege für den Rest des Tages gesperrt.")

    if not f:
        add("info", "allgemein", "Keine Auffälligkeiten gefunden – Datenlage im Zeitraum unauffällig.")
    return f


def data_quality_rows(scanner) -> List[Dict]:
    rows: List[Dict] = []
    buf = (getattr(scanner, "candle_buffer", {}) or {}) if scanner is not None else {}
    for sym, candles in buf.items():
        if not candles:
            continue
        try:
            age_min = (time.time() * 1000 - float(candles[-1].get("timestamp") or 0)) / 60000.0
        except (TypeError, ValueError):
            age_min = -1
        of_real: Optional[bool] = None
        try:
            from services.orderflow import orderflow
            of_real = bool(orderflow.snapshot_text(sym))
        except Exception:
            of_real = None
        rows.append({"symbol": sym, "candles": len(candles),
                     "last_candle_age_min": round(max(age_min, 0), 1),
                     "orderflow_real": of_real})
    rows.sort(key=lambda r: -r["last_candle_age_min"])
    return rows


async def build(db, scanner, config: Dict, days: int = 14) -> Dict:
    days = max(1, min(90, int(days)))
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

    rows: List[Dict] = []
    try:
        rows = await db.auto_trades.find(
            {"strategy_id": "ai_trader", "status": "closed", "opened_at": {"$gte": cutoff}},
            {"symbol": 1, "side": 1, "mode": 1, "setup": 1, "realized_pnl": 1,
             "data_collection": 1, "slippage_pct": 1, "slippage_usdt": 1,
             "trade_date": 1, "result": 1, "collection_reason": 1}).to_list(8000)
    except Exception as e:
        logger.warning(f"Diagnose: Trades nicht lesbar: {e}")

    live_rows = [r for r in rows if r.get("mode") == "live" and not r.get("data_collection")]
    paper_rows = [r for r in rows if r.get("mode") != "live" and not r.get("data_collection")]
    coll_rows = [r for r in rows if r.get("data_collection")]

    live = _agg(live_rows)
    paper = _agg(paper_rows)
    per_setup_live = _group([r for r in live_rows if r.get("setup")], "setup")
    per_setup_paper = _group([r for r in paper_rows if r.get("setup")], "setup")
    per_side_live = _group(live_rows, "side")
    per_symbol_live = _group(live_rows, "symbol")
    slippage = _slippage_summary(live_rows)

    # ---- Blockaden (ai_decisions.blocked_by) ----
    guards: List[Dict] = []
    try:
        dec_rows = await db.ai_decisions.find(
            {"ts": {"$gte": cutoff}, "blocked_by": {"$nin": [None, ""]}},
            {"blocked_by": 1}).to_list(20000)
        counts: Dict[str, int] = {}
        for r in dec_rows:
            why = str(r.get("blocked_by") or "")
            key = next((k for k, _, pat in GUARD_CATS if pat in why), "other")
            counts[key] = counts.get(key, 0) + 1
        labels = {k: lbl for k, lbl, _ in GUARD_CATS}
        labels["other"] = "Sonstige"
        guards = [{"key": k, "label": labels.get(k, k), "count": c}
                  for k, c in sorted(counts.items(), key=lambda kv: -kv[1])]
    except Exception as e:
        logger.warning(f"Diagnose: Guard-Statistik nicht lesbar: {e}")

    live_gate_redirects = sum(
        1 for r in coll_rows if "live-reif" in str(r.get("collection_reason") or ""))

    # ---- Setup-Katalog + Live-Reife ----
    setups: List[Dict] = []
    try:
        from services import ai_playbook
        stats = await ai_playbook.setup_stats(db)
        for sid, desc in ai_playbook.SETUPS.items():
            st = stats.get(sid) or {}
            ok, why = ai_playbook.live_ready(st if st else None)
            setups.append({"setup": sid, "beschreibung": desc,
                           "trades": int(st.get("trades") or 0),
                           "wins": int(st.get("wins") or 0),
                           "pnl": float(st.get("pnl") or 0),
                           "verdict": st.get("verdict"),
                           "live_ready": ok, "live_reason": why,
                           "disabled": ai_playbook.disabled_reason(sid)})
    except Exception as e:
        logger.warning(f"Diagnose: Setup-Katalog nicht lesbar: {e}")

    # ---- Tageslimit-Treffer ----
    daily_limit_hits = 0
    try:
        from services.ai_master_prompt import master_prompt
        limit = float(master_prompt.rules.get("max_daily_loss_usdt") or 0)
        if limit > 0:
            by_day: Dict[str, float] = {}
            for r in live_rows:
                d = str(r.get("trade_date") or "")
                by_day[d] = by_day.get(d, 0) + float(r.get("realized_pnl") or 0)
            daily_limit_hits = sum(1 for v in by_day.values() if v <= -limit)
    except Exception:
        pass

    dq = data_quality_rows(scanner)
    findings = build_findings(live, paper, per_setup_live, per_setup_paper,
                              per_side_live, slippage, guards, setups, dq,
                              live_gate_redirects, daily_limit_hits)
    return {"days": days,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "findings": findings,
            "live": live, "paper": paper,
            "collection": _agg(coll_rows),
            "per_setup_live": per_setup_live,
            "per_setup_paper": per_setup_paper,
            "per_side_live": per_side_live,
            "per_symbol_live": per_symbol_live,
            "slippage": slippage,
            "guards": guards,
            "live_gate_redirects": live_gate_redirects,
            "daily_limit_hits": daily_limit_hits,
            "setups": setups,
            "data_quality": dq[:30]}
