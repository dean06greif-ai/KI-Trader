"""Setup-Lebenszyklus des KI-Traders: Datensammlung -> Live -> (Rückstufung).

Ergänzt services/ai_playbook.py um klare, testbare Regeln:

  Sammeln (Paper)  ──(≥5 Trades & PnL>0 ODER WR≥55%)──►  LIVE
  LIVE ──(≥8 Live-Trades & PnL ≤ -3 % der Margin ODER WR<35 %)──► Rückstufung
  Rückstufung ──(≥5 gute Paper-Trades SEIT der Rückstufung)──► LIVE

Zusätzlich pro Setup ein Parameter-PROFIL (SL-%, TP-Ratio, Hebel, Timeframe)
aus den echten Trades mit Versionierung:
  * Tuning nur in kleinen Schritten (max. ±20 % je Version) und erst ab
    TUNE_EVERY Trades der aktiven Version -> kein Overfitting auf 3 Trades
  * Auto-Rollback: läuft die aktive Version nachweislich schlechter als die
    beste bekannte Version, wird auf deren Parameter zurückgesetzt.

Alle Regeln sind reine Funktionen (unten), die DB-Anbindung (refresh) ist dünn.
"""
import logging
import statistics
import uuid
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ---- Live-Freischaltung (Paper -> Live) ----
MIN_TRADES_PROMOTE = 5
PROMOTE_MIN_WINRATE = 55.0        # ODER PnL > 0
# ---- Rückstufung (Live -> Paper) ----
DEMOTE_MIN_LIVE_TRADES = 8
DEMOTE_PNL_PCT = -3.0             # Live-PnL relativ zur eingesetzten Margin
DEMOTE_MAX_WINRATE = 35.0
# ---- Parameter-Profil / Versionen ----
PROFILE_MIN_TRADES = 8            # Mindestdaten für ein erstes Profil
TUNE_EVERY = 10                   # Tuning erst nach N Trades der aktiven Version
TUNE_MAX_STEP = 0.20              # max. ±20 % Änderung je Parameter und Version
TUNE_MIN_DELTA = 0.10             # < 10 % Unterschied = kein neues Profil (Rauschen)
ROLLBACK_MIN_TRADES = 5
MAX_VERSIONS = 12

STATE_KEY = "lifecycle"           # Unterfeld im ai_playbook_state-Dokument


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _wr(stats: Dict) -> float:
    n = int(stats.get("trades") or 0)
    return (int(stats.get("wins") or 0) / n * 100.0) if n else 0.0


# --------------------------------------------------------------------------
# Regeln (rein & testbar)
# --------------------------------------------------------------------------
def promotion_ok(stats: Optional[Dict]) -> Tuple[bool, str]:
    """Darf ein Setup live? ≥ MIN_TRADES_PROMOTE Trades und PnL > 0 ODER
    Winrate ≥ PROMOTE_MIN_WINRATE."""
    if not stats or not int(stats.get("trades") or 0):
        return False, "neues Setup – noch keine echten Daten gesammelt"
    n = int(stats.get("trades") or 0)
    if n < MIN_TRADES_PROMOTE:
        return False, f"erst {n}/{MIN_TRADES_PROMOTE} Trades gesammelt"
    pnl = float(stats.get("pnl") or 0)
    wr = _wr(stats)
    if pnl > 0 or wr >= PROMOTE_MIN_WINRATE:
        return True, f"{n} Trades, WR {wr:.0f}%, PnL {pnl:+.2f} USDT"
    return False, (f"{n} Trades, aber PnL {pnl:+.2f} USDT und WR {wr:.0f}% "
                   f"< {PROMOTE_MIN_WINRATE:.0f}% – weiter sammeln/optimieren")


def demotion_reason(live: Optional[Dict]) -> Optional[str]:
    """Live -> Paper zurück? Ab DEMOTE_MIN_LIVE_TRADES echten Live-Trades bei
    stark negativem PnL (≤ -3 % der eingesetzten Margin) ODER miserabler
    Winrate. Ohne Margin-Daten gilt der alte Maßstab (PnL<0 & WR<45 %)."""
    if not live or int(live.get("trades") or 0) < DEMOTE_MIN_LIVE_TRADES:
        return None
    n = int(live["trades"])
    wr = _wr(live)
    pnl = float(live.get("pnl") or 0)
    margin = float(live.get("margin") or 0)
    pnl_pct = (pnl / margin * 100.0) if margin > 0 else None
    bad_pnl = (pnl_pct is not None and pnl_pct <= DEMOTE_PNL_PCT) \
        or (pnl_pct is None and pnl < 0 and wr < 45)
    if wr < DEMOTE_MAX_WINRATE or bad_pnl:
        pct = f", {pnl_pct:+.1f}% der Margin" if pnl_pct is not None else ""
        return f"live {n} Trades, Winrate {wr:.0f}%, PnL {pnl:+.2f} USDT{pct}"
    return None


def trade_params(t: Dict) -> Optional[Dict]:
    """SL-%, TP-Ratio, Hebel, Timeframe eines Trades (rein)."""
    try:
        entry = float(t.get("entry") or 0)
        sl = float(t.get("initial_sl") or t.get("sl") or 0)
        tpf = float(t.get("tpf") or 0)
    except (TypeError, ValueError):
        return None
    if entry <= 0 or sl <= 0 or tpf <= 0:
        return None
    risk = abs(entry - sl)
    if risk <= 0:
        return None
    return {"sl_pct": risk / entry * 100.0,
            "tp_ratio": abs(tpf - entry) / risk,
            "leverage": float(t.get("leverage") or 0) or None,
            "timeframe": str(t.get("timeframe") or "") or None}


def profile_from_trades(trades: List[Dict]) -> Optional[Dict]:
    """Parameter-Profil aus echten Trades: Median der Gewinner (Fallback: alle).
    Keine Extremwerte -> Median/75 %-Quantil, mind. PROFILE_MIN_TRADES."""
    rows = [(trade_params(t), float(t.get("realized_pnl") or 0)) for t in trades]
    rows = [(p, pnl) for p, pnl in rows if p]
    if len(rows) < PROFILE_MIN_TRADES:
        return None
    winners = [p for p, pnl in rows if pnl > 0]
    base = winners if len(winners) >= 4 else [p for p, _ in rows]
    levs = sorted(p["leverage"] for p in base if p.get("leverage"))
    tfs = [p["timeframe"] for p in base if p.get("timeframe")]
    prof = {
        "sl_pct": round(statistics.median(p["sl_pct"] for p in base), 3),
        "tp_ratio": round(statistics.median(p["tp_ratio"] for p in base), 2),
        "max_leverage": (round(levs[int(len(levs) * 0.75) - 1 if len(levs) > 1 else 0], 1)
                         if levs else None),
        "timeframe": (max(set(tfs), key=tfs.count) if tfs else None),
        "sample": len(base), "from_winners": base is winners,
    }
    return prof


def clamp_step(new: Optional[float], old: Optional[float],
               max_rel: float = TUNE_MAX_STEP) -> Optional[float]:
    """Parameter nur schrittweise verändern (Anti-Overfitting)."""
    if new is None:
        return old
    if old is None or old <= 0:
        return new
    lo, hi = old * (1 - max_rel), old * (1 + max_rel)
    return round(min(max(new, lo), hi), 3)


def materially_different(new: Dict, old: Dict, min_delta: float = TUNE_MIN_DELTA) -> bool:
    for k in ("sl_pct", "tp_ratio"):
        a, b = new.get(k), old.get(k)
        if a and b and abs(a - b) / b >= min_delta:
            return True
    return False


def version_stats(trades: List[Dict], since: str, until: Optional[str] = None) -> Dict:
    """Ergebnis der Trades im Zeitfenster einer Version (rein)."""
    rows = [t for t in trades
            if str(t.get("opened_at") or "") >= since
            and (until is None or str(t.get("opened_at") or "") < until)]
    n = len(rows)
    wins = sum(1 for t in rows if float(t.get("realized_pnl") or 0) > 0)
    pnl = sum(float(t.get("realized_pnl") or 0) for t in rows)
    return {"trades": n, "wins": wins, "pnl": round(pnl, 2),
            "pnl_per_trade": round(pnl / n, 3) if n else 0.0,
            "winrate": round(wins / n * 100) if n else 0}


def should_rollback(active: Dict, best: Dict) -> bool:
    """Aktive Version läuft nachweislich schlechter als die beste bekannte."""
    if int(active.get("trades") or 0) < ROLLBACK_MIN_TRADES \
            or int(best.get("trades") or 0) < ROLLBACK_MIN_TRADES:
        return False
    a, b = float(active.get("pnl_per_trade") or 0), float(best.get("pnl_per_trade") or 0)
    if b <= 0:
        return False
    return a < 0 or a < b * 0.5 and active.get("winrate", 0) < best.get("winrate", 0)


def evolve_versions(entry: Dict, trades: List[Dict], now_iso: Optional[str] = None) -> Tuple[Dict, Optional[str]]:
    """Versionen eines Setups fortschreiben (rein): initiales Profil, Tuning in
    kleinen Schritten, Auto-Rollback. Rückgabe (neuer Eintrag, Ereignis-Text)."""
    now_iso = now_iso or _now_iso()
    entry = dict(entry or {})
    versions: List[Dict] = [dict(v) for v in (entry.get("versions") or [])]
    if not versions:
        prof = profile_from_trades(trades)
        if not prof:
            return entry, None
        versions.append({"v": 1, "since": now_iso, "params": prof, "note": "initiales Profil"})
        entry["versions"] = versions
        entry["active"] = 1
        return entry, (f"Profil v1 angelegt (aus {prof['sample']} Trades): SL ~{prof['sl_pct']}%, "
                       f"TP-Ratio ~{prof['tp_ratio']}, TF {prof.get('timeframe') or '?'}")
    # Statistik je Version (Fenster bis zur nächsten Version)
    for i, v in enumerate(versions):
        until = versions[i + 1]["since"] if i + 1 < len(versions) else None
        v["stats"] = version_stats(trades, v["since"], until)
    active = versions[-1]
    a_st = active["stats"]
    event = None
    # 1) Rollback auf die beste Version?
    older = [v for v in versions[:-1] if int(v["stats"].get("trades") or 0) >= ROLLBACK_MIN_TRADES]
    if older:
        best = max(older, key=lambda v: float(v["stats"].get("pnl_per_trade") or 0))
        if best["params"] != active["params"] and should_rollback(a_st, best["stats"]):
            versions.append({"v": active["v"] + 1, "since": now_iso, "params": dict(best["params"]),
                             "note": f"Rollback auf v{best['v']}", "rollback_of": best["v"]})
            event = (f"Profil v{active['v']} lief schlechter ({a_st['trades']} Trades, "
                     f"{a_st['pnl_per_trade']:+.2f} USDT/Trade) als v{best['v']} "
                     f"({best['stats']['pnl_per_trade']:+.2f} USDT/Trade) – Rollback auf v{best['v']}")
    # 2) Tuning in kleinen Schritten (nur wenn genug Trades der aktiven Version)
    if event is None and int(a_st.get("trades") or 0) >= TUNE_EVERY:
        recent = [t for t in trades if str(t.get("opened_at") or "") >= active["since"]]
        prof = profile_from_trades(recent)
        if prof and materially_different(prof, active["params"]):
            tuned = dict(prof)
            tuned["sl_pct"] = clamp_step(prof["sl_pct"], active["params"].get("sl_pct"))
            tuned["tp_ratio"] = clamp_step(prof["tp_ratio"], active["params"].get("tp_ratio"))
            versions.append({"v": active["v"] + 1, "since": now_iso, "params": tuned,
                             "note": f"Tuning aus {prof['sample']} Trades"})
            event = (f"Profil v{active['v'] + 1}: SL {active['params'].get('sl_pct')}% -> {tuned['sl_pct']}%, "
                     f"TP-Ratio {active['params'].get('tp_ratio')} -> {tuned['tp_ratio']} "
                     f"(max. ±{int(TUNE_MAX_STEP * 100)} % je Schritt)")
    if len(versions) > MAX_VERSIONS:
        versions = versions[-MAX_VERSIONS:]
    entry["versions"] = versions
    entry["active"] = versions[-1]["v"]
    return entry, event


def phase_of(sid: str, live_ready: bool, live_blocked: Dict, disabled: Dict) -> str:
    if sid in (disabled or {}):
        return "gesperrt"
    if sid in (live_blocked or {}):
        return "rückgestuft"
    return "live" if live_ready else "sammelt"


def profile_line(sid: str, entry: Dict) -> Optional[str]:
    """Prompt-Zeile mit dem aktiven Parameter-Profil (rein)."""
    versions = (entry or {}).get("versions") or []
    if not versions:
        return None
    v = versions[-1]
    p = v.get("params") or {}
    st = v.get("stats") or {}
    lev = f", Hebel ≤{p['max_leverage']:g}" if p.get("max_leverage") else ""
    tf = f", TF {p['timeframe']}" if p.get("timeframe") else ""
    return (f"- {sid}: Profil v{v['v']} ({v.get('note', '')}, {st.get('trades', 0)} Trades seit "
            f"{str(v.get('since', ''))[:10]}): SL ~{p.get('sl_pct')}%, TP-Ratio ~{p.get('tp_ratio')}{tf}{lev}")


def context_lines(state: Dict) -> List[str]:
    rows = [profile_line(sid, e) for sid, e in sorted((state or {}).items())]
    rows = [r for r in rows if r]
    if not rows:
        return []
    return (["SETUP-PROFILE (aus echten Trades, versioniert mit Auto-Rollback – halte SL/TP/"
             f"Hebel/Timeframe im Rahmen des Profils, Abweichung max. ±{int(TUNE_MAX_STEP * 100)} %):"]
            + rows)


# --------------------------------------------------------------------------
# DB-Anbindung (dünn)
# --------------------------------------------------------------------------
async def refresh_profiles(db, doc: Dict, setups: List[str], lookback_days: int) -> Tuple[Dict, bool]:
    """Profile/Versionen aller Setups fortschreiben. Rückgabe (state, changed)."""
    from datetime import timedelta
    state: Dict[str, Dict] = dict(doc.get(STATE_KEY) or {})
    cutoff = (datetime.now(timezone.utc) - timedelta(days=lookback_days)).isoformat()
    rows = await db.auto_trades.find(
        {"strategy_id": "ai_trader", "status": "closed", "opened_at": {"$gte": cutoff},
         "setup": {"$in": list(setups)}},
        {"_id": 0, "setup": 1, "entry": 1, "sl": 1, "initial_sl": 1, "tpf": 1, "leverage": 1,
         "timeframe": 1, "realized_pnl": 1, "opened_at": 1}).to_list(5000)
    by_setup: Dict[str, List[Dict]] = {}
    for r in rows:
        by_setup.setdefault(str(r.get("setup")), []).append(r)
    original = dict(doc.get(STATE_KEY) or {})
    for sid, trades in by_setup.items():
        new_entry, event = evolve_versions(state.get(sid) or {}, trades)
        if new_entry.get("versions"):
            state[sid] = new_entry
        if event:
            logger.info(f"Setup-Profil '{sid}': {event}")
            try:
                await db.ai_chat.insert_one({
                    "id": str(uuid.uuid4()), "role": "playbook", "setup": sid,
                    "text": f"Setup '{sid}' – {event}", "ts": _now_iso()})
            except Exception as e:  # noqa: BLE001
                logger.debug(f"Setup-Profil Feed '{sid}' fehlgeschlagen: {e}")
    return state, state != original
