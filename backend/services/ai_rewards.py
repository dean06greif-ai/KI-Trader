"""Belohnungssystem (Reward-Score) für den KI Trader.

Jeder geschlossene KI-Trade wird nach transparenten, deterministischen Regeln
bewertet (belohnt/bestraft) und in `ai_rewards` gespeichert:
  + R-Basis        : Gewinn/Verlust je riskiertem Kapital (Audit 2.3)
  + Ergebnis       : Win-Bonus / Loss-Malus / Breakeven
  - Sofort-Stop-Out: Verlust-Trade unter 15 Minuten Haltedauer (violation)
  + CRV-Disziplin  : geplantes CRV >= 2.0 in der Entscheidung

Audit 2.7: Der Konfidenz-Bonus/Malus ist aus dem Reward ENTKOPPELT (Schalter
`confidence_reward` in settings/ai_rewards_config, Default aus) – hohe
Konfidenz zu belohnen erzeugte einen Anreiz zur Selbstüberschätzung. Statt-
dessen gibt es die getrennte Kennzahl KALIBRIERUNGSFEHLER (|Konfidenz −
Trefferquote| je Konfidenz-Bin, `calibration`). Regelverletzungen stehen
separat in `violations` am Reward.

Der Reward-Verlauf und die Auswertung pro Markt-Regime fließen als eigener
Prompt-Block in jeden Lernlauf ein (services/ai_learning.py) – so lernt die
KI direkt an ihrer eigenen Belohnungskurve. Frontend: Lern-Panel (AIRewardPanel).
"""
import logging
import uuid
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

_backfill_ts = 0.0


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def compute_reward(trade: Dict, decision: Optional[Dict] = None,
                   confidence_reward: bool = False) -> Dict:
    """Reiner, testbarer Reward für einen geschlossenen Trade.
    Audit 2.7: Konfidenz-Bonus/Malus nur noch mit confidence_reward=True
    (Default aus); Regelverletzungen separat in `violations`."""
    pnl = float(trade.get("realized_pnl") or 0)
    cap = float(trade.get("max_capital") or 0)
    pnl_pct = (pnl / cap * 100) if cap > 0 else 0.0
    comps: List[Dict] = []

    # R in Geld (Audit 2.3): Basis = pnl / risk_usdt (riskiertes Kapital).
    # 1R Verlust = -1, 2R Gewinn = +2 (gedeckelt ±4). Alte Trades ohne
    # risk_usdt fallen auf die bisherige PnL-%-Basis zurück.
    r_multiple = None
    try:
        risk_usdt = float(trade.get("risk_usdt") or 0)
        if risk_usdt > 0:
            r_multiple = pnl / risk_usdt
    except (TypeError, ValueError):
        pass
    if r_multiple is not None:
        comps.append({"label": "R-Basis (PnL/Risiko)",
                      "value": round(_clamp(r_multiple, -4.0, 4.0), 3)})
    else:
        comps.append({"label": "PnL-Basis", "value": round(_clamp(pnl_pct / 2.5, -4.0, 4.0), 3)})

    result = trade.get("result")
    if result == "win":
        comps.append({"label": "Gewinn-Bonus", "value": 1.0})
    elif result == "loss":
        comps.append({"label": "Verlust-Malus", "value": -1.0})
    else:
        comps.append({"label": "Breakeven", "value": 0.2})

    dur_min = None
    try:
        o = datetime.fromisoformat(str(trade.get("opened_at")).replace("Z", "+00:00"))
        c = datetime.fromisoformat(str(trade.get("closed_at")).replace("Z", "+00:00"))
        dur_min = (c - o).total_seconds() / 60
    except (TypeError, ValueError):
        pass
    violations: List[str] = []
    if dur_min is not None and dur_min < 15 and result == "loss":
        comps.append({"label": "Sofort-Stop-Out (<15 min)", "value": -0.5})
        violations.append("Sofort-Stop-Out (<15 min)")

    conf = (decision or {}).get("confidence")
    if conf is not None and confidence_reward:
        # Alt-Verhalten (Schalter, Default aus – Audit 2.7): koppelte Reward an
        # die eigene Konfidenz und belohnte damit Selbstüberschätzung.
        if result == "loss" and conf < 80:
            comps.append({"label": f"Verlust bei Konfidenz {conf}% (<80%)", "value": -0.5})
            violations.append("Verlust bei Konfidenz <80%")
        elif result == "win" and conf >= 80:
            comps.append({"label": f"Disziplin-Bonus (Konfidenz {conf}%)", "value": 0.25})

    try:
        sl = float((decision or {}).get("sl_pct") or 0)
        tp = float((decision or {}).get("tp1_pct") or 0)
        if sl > 0 and tp / sl >= 2.0:
            comps.append({"label": f"CRV-Disziplin (geplant {round(tp / sl, 2)})", "value": 0.25})
    except (TypeError, ValueError):
        pass

    # Fee-Feedback (bewusst KEIN eigener Malus – reine Transparenz, damit die
    # KI selbst lernt, weitere Stops zu wählen): Wie viel % des Verlusts waren
    # reine Gebühren? realized_pnl ist inkl. Fees -> Anteil = fees / |pnl|.
    fees = float(trade.get("fees_paid") or 0)
    fee_share = None
    if result == "loss" and pnl < 0 and fees > 0:
        fee_share = round(min(100.0, fees / abs(pnl) * 100), 1)

    return {"score": round(sum(c["value"] for c in comps), 3),
            "components": comps,
            "violations": violations,
            "confidence": int(conf) if conf is not None else None,
            "pnl_pct": round(pnl_pct, 3),
            "r_multiple": round(r_multiple, 3) if r_multiple is not None else None,
            "fees": round(fees, 6),
            "fee_share_pct": fee_share,
            "duration_min": round(dur_min, 1) if dur_min is not None else None}


def structural_regime_of(trade: Dict) -> Optional[str]:
    """Strukturelles Lab-Regime aus dem Entry-Snapshot (rein): 'strukturell bär|bulle|seitwärts'
    – bewusst eigener Namensraum, nie mit Kurzfrist-Regimen zu verwechseln."""
    st = ((trade or {}).get("entry_market_snapshot") or {}).get("structural") or {}
    if st.get("state") != "ok" or not st.get("phase"):
        return None
    return f"strukturell {st['phase']}"


async def by_structural_regime(db, days: int = 30, aid: Optional[str] = None) -> List[Dict]:
    """Reward-Auswertung nach Struktur-Regime (Regime-Brücke 3.2, rein wie by_regime).
    Trades ohne Struktur-Kontext (Stufe none / stale) laufen unter 'unbekannt'.
    Mit `aid`: nur Trades dieser Freigabe (Alt-Rewards ohne structural_aid zählen mit)."""
    rows = await db.ai_rewards.find({"ts": {"$gte": _cutoff(days)}}).to_list(3000)
    if aid:
        rows = [r for r in rows if r.get("structural_aid") in (None, aid)]
    agg: Dict[str, Dict] = {}
    for r in rows:
        k = str(r.get("structural_regime") or "unbekannt")
        d = agg.setdefault(k, {"regime": k, "trades": 0, "reward_sum": 0.0, "wins": 0, "pnl": 0.0})
        d["trades"] += 1
        d["reward_sum"] += float(r.get("score") or 0)
        d["pnl"] += float(r.get("pnl") or 0)
        if r.get("result") == "win":
            d["wins"] += 1
    out = []
    for d in agg.values():
        d["avg_reward"] = round(d["reward_sum"] / d["trades"], 3)
        d["win_rate"] = round(d["wins"] / d["trades"] * 100, 1)
        d["reward_sum"] = round(d["reward_sum"], 3)
        d["pnl"] = round(d["pnl"], 2)
        out.append(d)
    return sorted(out, key=lambda x: -x["avg_reward"])


async def _regime_for(db, symbol: str, trade: Dict) -> Optional[str]:
    """Markt-Regime zum Trade. Prio (Fix ai_rewards-RCA + P1 Tech-Debt):
    1) Entry-Regime direkt vom Trade (entry_market_snapshot, Fix 0.2) –
       das ML-relevante Regime im Entscheidungs-Moment,
    2) historischer 15-min-Snapshot <= closed_at (korrekt für Backfill),
    3) Live-Beobachter (nur Notnagel für frisch geschlossene Trades)."""
    try:
        r = ((trade.get("entry_market_snapshot") or {}).get("features")
             or {}).get("regime")
        if r:
            return str(r)
    except Exception:
        pass
    try:
        ref = trade.get("closed_at") or _now_iso()
        snap = await db.ai_market_snapshots.find(
            {"symbol": symbol, "ts": {"$lte": ref}}).sort("ts", -1).limit(1).to_list(1)
        if snap:
            r = (snap[0].get("features") or {}).get("regime")
            if r:
                return str(r)
    except Exception:
        pass
    try:
        from services.ai_market_observer import market_observer
        r = (market_observer.features_for(symbol) or {}).get("regime")
        if r:
            return str(r)
    except Exception:
        pass
    return None


CONFIG_ID = "ai_rewards_config"


async def _confidence_reward_enabled(db) -> bool:
    """Schalter für den alten Konfidenz-Bonus/Malus (Audit 2.7, Default aus)."""
    try:
        doc = await db.settings.find_one({"_id": CONFIG_ID}) or {}
        return bool(doc.get("confidence_reward", False))
    except Exception:  # noqa: BLE001
        return False


async def on_trade_closed(db, trade: Dict) -> Optional[Dict]:
    """Hook nach jedem geschlossenen KI-Trade: Reward berechnen + speichern."""
    if trade.get("strategy_id") != "ai_trader" or trade.get("status") != "closed":
        return None
    try:
        if trade.get("id") and await db.ai_rewards.find_one({"trade_id": trade["id"]}):
            return None
        decision = None
        if trade.get("signal_id"):
            decision = await db.ai_decisions.find_one({"signal_id": trade["signal_id"]})
        r = compute_reward(trade, decision,
                           confidence_reward=await _confidence_reward_enabled(db))
        doc = {"id": uuid.uuid4().hex[:12], "trade_id": trade.get("id"),
               "symbol": trade.get("symbol"), "side": trade.get("side"),
               "mode": trade.get("mode"), "result": trade.get("result"),
               "regime": await _regime_for(db, trade.get("symbol"), trade),
               # Regime-Brücke 3.2: strukturelles Lab-Regime zum Entry (ab Stufe shadow)
               "structural_regime": structural_regime_of(trade),
               "structural_aid": (((trade.get("entry_market_snapshot") or {}).get("structural") or {})
                                  .get("aid")),
               "pnl": float(trade.get("realized_pnl") or 0),
               **r, "ts": trade.get("closed_at") or _now_iso()}
        await db.ai_rewards.insert_one(dict(doc))
        doc.pop("_id", None)
        return doc
    except Exception as e:
        logger.warning(f"Reward-Berechnung fehlgeschlagen: {e}")
        return None


async def backfill_missing(db, include_cleared: bool = False) -> int:
    """Lückenfüllender Backfill (RCA 'ai_rewards leer', 2026-08-13): bewertet
    ALLE geschlossenen KI-Trades, die noch keinen Reward-Eintrag haben –
    idempotent (Dedupe über trade_id in on_trade_closed). Repariert damit auch
    einzelne Hook-Ausfälle statt nur den 'Collection komplett leer'-Fall.

    Ein bewusstes 'Belohnungsdaten löschen' des Traders (cleared_at) wird
    respektiert: nur Trades mit closed_at NACH cleared_at werden nachbewertet.
    include_cleared=True hebt die Löschung auf (cleared_at wird entfernt) und
    bewertet auch die historischen Trades neu (Admin-Endpoint)."""
    st = await db.settings.find_one({"_id": "ai_rewards_state"}) or {}
    cleared_at = st.get("cleared_at")
    if include_cleared and cleared_at:
        await db.settings.update_one({"_id": "ai_rewards_state"},
                                     {"$unset": {"cleared_at": ""}})
        logger.info("Reward-Backfill: cleared_at aufgehoben (include_cleared)")
        cleared_at = None
    # Rekonstruierte Trades (services/data_recovery.py) haben keine Entry-
    # Snapshots/Levels -> nicht nachbewerten.
    flt = {"strategy_id": "ai_trader", "status": "closed", "recovered": {"$ne": True}}
    if cleared_at:
        flt["closed_at"] = {"$gt": cleared_at}
    have = {r.get("trade_id") async for r in
            db.ai_rewards.find({}, {"trade_id": 1, "_id": 0})}
    trades = await db.auto_trades.find(flt).sort("closed_at", 1).to_list(1000)
    n = 0
    for t in trades:
        if t.get("id") in have:
            continue
        if await on_trade_closed(db, t):
            n += 1
    if n:
        logger.info(f"Reward-Backfill: {n} geschlossene KI-Trades bewertet")
    return n


async def ensure_backfill(db) -> int:
    """Periodischer Lücken-Check (max. alle 10 Minuten), respektiert cleared_at."""
    global _backfill_ts
    import time as _t
    if _t.time() - _backfill_ts < 600:
        return 0
    _backfill_ts = _t.time()
    try:
        return await backfill_missing(db, include_cleared=False)
    except Exception as e:
        logger.warning(f"Reward-Backfill fehlgeschlagen: {e}")
        return 0


async def clear(db) -> int:
    """Alle Belohnungsdaten löschen. Historische Trades vor dem Löschzeitpunkt
    werden danach NICHT mehr auto-backfilled (cleared_at); neue Trades werden
    weiterhin normal bewertet. Aufhebbar via POST /api/ai/rewards/backfill
    mit include_cleared=true."""
    res = await db.ai_rewards.delete_many({})
    await db.settings.update_one({"_id": "ai_rewards_state"},
                                 {"$set": {"cleared_at": _now_iso()}}, upsert=True)
    logger.info(f"Belohnungssystem: {res.deleted_count} Reward-Einträge gelöscht")
    return res.deleted_count


def _cutoff(days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=max(1, min(365, days)))).isoformat()


async def history(db, days: int = 30) -> List[Dict]:
    """Reward-Verlauf (chronologisch, mit kumulierter Kurve)."""
    rows = await db.ai_rewards.find({"ts": {"$gte": _cutoff(days)}}) \
        .sort("ts", 1).to_list(3000)
    out, cum = [], 0.0
    for r in rows:
        cum = round(cum + float(r.get("score") or 0), 3)
        out.append({"ts": r.get("ts"), "score": r.get("score"), "cum": cum,
                    "symbol": r.get("symbol"), "side": r.get("side"),
                    "mode": r.get("mode"), "pnl": r.get("pnl"),
                    "fees": r.get("fees"), "fee_share_pct": r.get("fee_share_pct"),
                    "regime": r.get("regime"), "result": r.get("result")})
    return out


async def by_regime(db, days: int = 30) -> List[Dict]:
    """Reward-Auswertung pro Markt-Regime (datenbasis für Regime-Lektionen)."""
    rows = await db.ai_rewards.find({"ts": {"$gte": _cutoff(days)}}).to_list(3000)
    agg: Dict[str, Dict] = {}
    for r in rows:
        k = str(r.get("regime") or "unbekannt")
        d = agg.setdefault(k, {"regime": k, "trades": 0, "reward_sum": 0.0,
                               "wins": 0, "pnl": 0.0})
        d["trades"] += 1
        d["reward_sum"] += float(r.get("score") or 0)
        d["pnl"] += float(r.get("pnl") or 0)
        if r.get("result") == "win":
            d["wins"] += 1
    out = []
    for d in agg.values():
        d["avg_reward"] = round(d["reward_sum"] / d["trades"], 3)
        d["win_rate"] = round(d["wins"] / d["trades"] * 100, 1)
        d["reward_sum"] = round(d["reward_sum"], 3)
        d["pnl"] = round(d["pnl"], 2)
        out.append(d)
    return sorted(out, key=lambda x: -x["avg_reward"])


async def summary(db, days: int = 30) -> Dict:
    rows = await history(db, days)
    n = len(rows)
    total = round(sum(float(r.get("score") or 0) for r in rows), 3)
    last10 = [float(r["score"] or 0) for r in rows[-10:]]
    prev10 = [float(r["score"] or 0) for r in rows[-20:-10]]
    trend = None
    if last10 and prev10:
        trend = round(sum(last10) / len(last10) - sum(prev10) / len(prev10), 3)
    return {"trades": n, "total": total,
            "avg": round(total / n, 3) if n else 0.0,
            "trend": trend, "days": days}


# ---- Kalibrierungsfehler (Audit 2.7): |Konfidenz − Trefferquote| je Bin ----
CALIB_BINS = ((0, 60), (60, 70), (70, 80), (80, 90), (90, 101))


def calibration_from_rows(rows: List[Dict]) -> Dict:
    """Rein & testbar: Konfidenz-Kalibrierung aus Reward-Zeilen (brauchen
    `confidence` und result win/loss). gap je Bin = |Ø-Konfidenz − Winrate|;
    Gesamtfehler = trade-gewichtetes Mittel der Bin-Gaps."""
    bins = []
    total_n, weighted = 0, 0.0
    for lo, hi in CALIB_BINS:
        sel = [r for r in rows
               if r.get("confidence") is not None
               and lo <= int(r["confidence"]) < hi
               and r.get("result") in ("win", "loss")]
        n = len(sel)
        if not n:
            continue
        avg_conf = sum(int(r["confidence"]) for r in sel) / n
        wr = sum(1 for r in sel if r["result"] == "win") / n * 100
        gap = abs(avg_conf - wr)
        bins.append({"bin": f"{lo}–{hi - 1}", "trades": n,
                     "avg_confidence": round(avg_conf, 1),
                     "win_rate": round(wr, 1), "gap": round(gap, 1)})
        total_n += n
        weighted += gap * n
    return {"bins": bins, "rated": total_n,
            "calibration_error": round(weighted / total_n, 1) if total_n else None}


async def calibration(db, days: int = 30) -> Dict:
    """Kalibrierung der letzten N Tage (nur Rewards mit gespeicherter Konfidenz)."""
    rows = await db.ai_rewards.find(
        {"ts": {"$gte": _cutoff(days)}, "confidence": {"$ne": None}},
        {"_id": 0, "confidence": 1, "result": 1}).to_list(3000)
    return calibration_from_rows(rows)


def _top_penalties(rows: List[Dict], limit: int = 4) -> List[str]:
    counts: Dict[str, int] = {}
    for r in rows:
        for c in r.get("components") or []:
            if float(c.get("value") or 0) < 0:
                # Konfidenz-Label vereinheitlichen, damit gezählt werden kann
                label = str(c.get("label", ""))
                if label.startswith("Verlust bei Konfidenz"):
                    label = "Verlust bei Konfidenz <80%"
                counts[label] = counts.get(label, 0) + 1
    return [f"{k} ({v}×)" for k, v in
            sorted(counts.items(), key=lambda kv: -kv[1])[:limit]]


async def context_text(db, days: int = 14) -> str:
    """Kompakter Prompt-Block für Lernläufe (leerer String, wenn keine Daten)."""
    await ensure_backfill(db)
    rows = await db.ai_rewards.find({"ts": {"$gte": _cutoff(days)}}) \
        .sort("ts", 1).to_list(3000)
    if not rows:
        return ""
    n = len(rows)
    total = sum(float(r.get("score") or 0) for r in rows)
    last10 = [float(r.get("score") or 0) for r in rows[-10:]]
    prev10 = [float(r.get("score") or 0) for r in rows[-20:-10]]
    trend_txt = ""
    if last10 and prev10:
        diff = sum(last10) / len(last10) - sum(prev10) / len(prev10)
        trend_txt = (f" | Trend: {'BESSER' if diff > 0.1 else ('SCHLECHTER' if diff < -0.1 else 'stabil')} "
                     f"({diff:+.2f} Ø-Reward, letzte 10 vs. 10 davor)")
    lines = [
        "=== BELOHNUNGSSYSTEM (Reward-Score deiner geschlossenen Trades) ===",
        "Jeder Trade wird belohnt/bestraft: R-Basis (PnL je riskiertem Kapital), Win/Loss, "
        "Sofort-Stop-Outs (<15 min) und CRV-Planung (>=2.0 = Bonus). "
        "Dein Ziel ist es, den kumulierten Reward zu MAXIMIEREN – leite Lektionen ab, "
        "die die häufigsten Malus-Gründe eliminieren.",
        f"Gesamt-Reward ({days} Tage): {total:+.2f} über {n} Trades "
        f"(Ø {total / n:+.2f}/Trade){trend_txt}",
    ]
    # Kalibrierung (Audit 2.7): getrennt vom Reward – die KI sieht, ob ihre
    # Konfidenz zur echten Trefferquote passt, ohne dafür belohnt zu werden.
    try:
        cal = calibration_from_rows([r for r in rows if r.get("confidence") is not None])
        if cal["calibration_error"] is not None:
            worst = max(cal["bins"], key=lambda b: b["gap"])
            lines.append(
                f"KONFIDENZ-KALIBRIERUNG (getrennt vom Reward): Ø-Fehler "
                f"{cal['calibration_error']:g} Punkte über {cal['rated']} Trades; größte "
                f"Abweichung im Bin {worst['bin']}%: Ø-Konfidenz {worst['avg_confidence']:g}% "
                f"vs. echte Winrate {worst['win_rate']:g}%. Gib Konfidenzen an, die deiner "
                "echten Trefferquote entsprechen – Überschätzung wird hier sichtbar.")
    except Exception as e:  # noqa: BLE001
        logger.debug(f"Kalibrierungs-Block übersprungen: {e}")
    regimes = await by_regime(db, days)
    if regimes:
        reg_parts = [f"{d['regime']}: Ø {d['avg_reward']:+.2f} ({d['trades']} Trades, "
                     f"Winrate {d['win_rate']}%)" for d in regimes[:6]]
        lines.append("Reward pro Markt-Regime: " + " | ".join(reg_parts))
        best, worst = regimes[0], regimes[-1]
        if best["regime"] != worst["regime"]:
            lines.append(f"Bestes Regime: {best['regime']} (Ø {best['avg_reward']:+.2f}) | "
                         f"Schwächstes: {worst['regime']} (Ø {worst['avg_reward']:+.2f})")
    penalties = _top_penalties(rows)
    if penalties:
        lines.append("Häufigste Malus-Gründe: " + " | ".join(penalties))
    # Fee-Feedback: zeigt der KI explizit, welcher Anteil ihrer Verluste reine
    # Gebühren waren – sie soll daraus SELBST lernen (keine Stil-Vorgabe).
    shares = [float(r["fee_share_pct"]) for r in rows
              if r.get("result") == "loss" and r.get("fee_share_pct") is not None]
    if shares:
        avg_share = sum(shares) / len(shares)
        fee_dom = sum(1 for s in shares if s >= 50)
        lines.append(
            f"GEBÜHREN-ANTEIL an Verlusten: Ø {avg_share:.0f}% des Verlusts waren reine "
            f"Roundtrip-Fees; bei {fee_dom} von {len(shares)} Verlusten machten Fees >=50% aus. "
            "Ein hoher Anteil heißt: Der Trade verlor an die Gebühren (zu enger Stop und/oder "
            "zu großes Notional), nicht an den Markt – wähle dann von dir aus weitere "
            "SL-Distanzen oder kleinere Positionen.")
    return "\n".join(lines)
