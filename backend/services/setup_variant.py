"""Aktive Setup-Variante: Zeitfenster + Statistik seit der letzten Änderung.

Die Setup-Reife-Tabelle (Setupverlauf) und die Live-Diagnose zeigten bisher
den GESAMT-PnL eines Setups über den Lookback. Das war irreführend: ein Setup,
das nach einer Rückstufung überarbeitet und wieder live geschaltet wurde,
trug weiter den alten Verlust; ein zurückgestuftes Setup zeigte noch den
früheren Gewinn (Bug-Report 09/2026). Hier wird deshalb pro Setup der Start
der AKTIVEN Variante bestimmt – der jüngste dieser Zeitpunkte:

  * Rückstufung (live_blocked[sid].at)      – Paper-Datensammlung startet neu
  * Neubewertung (eval_since[sid])          – Rückstufung aufgehoben / starke Profil-Änderung
  * KI-Revision (revisions[sid].since)      – Setup-Regeln überarbeitet
  * Parameter-Profil (lifecycle versions[-1].since)

Setups ohne einen dieser Zeitpunkte haben keine Variante -> Gesamtstatistik.
Reine Funktionen (testbar); die DB-Abfrage liegt in ai_playbook.setup_stats_since_map.
"""
from typing import Dict, List, Optional

EMPTY_STATS = {"trades": 0, "wins": 0, "pnl": 0.0, "margin": 0.0, "verdict": "test"}


def _iso(v) -> str:
    return str(v or "")


def variant_since_map(scope: Dict, library, lifecycle_key: str = "lifecycle") -> Dict[str, str]:
    """{setup: ISO-Zeitpunkt der aktiven Variante} – nur Setups MIT Variante."""
    live_blocked = scope.get("live_blocked") or {}
    eval_since = scope.get("eval_since") or {}
    revisions = scope.get("revisions") or {}
    lifecycle = scope.get(lifecycle_key) or {}
    out: Dict[str, str] = {}
    for sid in library:
        candidates: List[str] = []
        if sid in live_blocked:
            candidates.append(_iso((live_blocked.get(sid) or {}).get("at")))
        candidates.append(_iso(eval_since.get(sid)))
        candidates.append(_iso((revisions.get(sid) or {}).get("since")))
        versions = (lifecycle.get(sid) or {}).get("versions") or []
        if versions:
            candidates.append(_iso(versions[-1].get("since")))
        best = max((c for c in candidates if c), default="")
        if best:
            out[sid] = best
    return out


def variant_label(scope: Dict, sid: str, lifecycle_key: str = "lifecycle") -> str:
    """Kurzbeschreibung der aktiven Variante für die UI (z.B. 'Rev.1 · Profil v3')."""
    parts: List[str] = []
    rev = (scope.get("revisions") or {}).get(sid) or {}
    if rev.get("version"):
        parts.append(f"Rev.{rev['version']}")
    versions = ((scope.get(lifecycle_key) or {}).get(sid) or {}).get("versions") or []
    if versions and versions[-1].get("v"):
        parts.append(f"Profil v{versions[-1]['v']}")
    if sid in (scope.get("live_blocked") or {}):
        parts.append("seit Rückstufung")
    elif sid in (scope.get("eval_since") or {}):
        parts.append("seit Neubewertung")
    return " · ".join(parts) or "aktive Variante"


def scoped_stats(sid: str, since_map: Dict[str, str], since_stats: Dict[str, Dict],
                 total: Optional[Dict]) -> Dict:
    """Statistik der aktiven Variante (oder Gesamt, falls keine Variante)."""
    if sid in since_map:
        return dict(since_stats.get(sid) or EMPTY_STATS)
    return dict(total or EMPTY_STATS)


def merge_stats(parts: List[Optional[Dict]]) -> Dict:
    """Mehrere Statistik-Zeilen (z.B. je Anlageklasse) summieren."""
    from services.ai_playbook import verdict_for  # lazy: kein Import-Zyklus
    trades = wins = 0
    pnl = margin = 0.0
    for p in parts:
        if not p:
            continue
        trades += int(p.get("trades") or 0)
        wins += int(p.get("wins") or 0)
        pnl += float(p.get("pnl") or 0)
        margin += float(p.get("margin") or 0)
    return {"trades": trades, "wins": wins, "pnl": round(pnl, 2), "margin": round(margin, 2),
            "verdict": verdict_for(trades, wins, pnl)}


def global_variant_view(classes: Dict[str, Dict], library, class_results: Dict[str, Dict],
                        totals: Dict[str, Dict]) -> Dict[str, Dict]:
    """Globale Sicht: je Setup die Klassen-Statistiken der aktiven Varianten
    summieren (Klassen ohne Variante zählen mit ihrer Gesamtstatistik).
    Rückgabe {setup: {**stats, 'variant_since': ältester Varianten-Start|None}}."""
    out: Dict[str, Dict] = {}
    for sid in library:
        parts: List[Optional[Dict]] = []
        sinces: List[str] = []
        any_variant = False
        for cls, res in class_results.items():
            vs = (res.get("variant_since") or {})
            if sid in vs:
                any_variant = True
                sinces.append(vs[sid])
                parts.append((res.get("variant_stats") or {}).get(sid))
            else:
                parts.append((res.get("stats") or {}).get(sid))
        if not any_variant:
            out[sid] = {**dict(totals.get(sid) or EMPTY_STATS), "variant_since": None}
            continue
        out[sid] = {**merge_stats(parts), "variant_since": min(sinces) if sinces else None}
    return out


def _report_bucket() -> Dict:
    return {"trades": 0, "wins": 0, "losses": 0, "pnl": 0.0, "fees": 0.0,
            "slippage_usdt": 0.0, "risk_usdt": 0.0, "r_sum": 0.0, "r_n": 0}


def _report_add(b: Dict, t: Dict, r: Optional[float]):
    b["trades"] += 1
    res = str(t.get("result") or "")
    pnl = float(t.get("realized_pnl") or 0)
    if res == "win" or (not res and pnl > 0):
        b["wins"] += 1
    elif res == "loss" or (not res and pnl < 0):
        b["losses"] += 1
    b["pnl"] += pnl
    b["fees"] += float(t.get("fees_paid") or 0)
    b["slippage_usdt"] += float(t.get("slippage_usdt") or 0)
    b["risk_usdt"] += float(t.get("risk_usdt") or 0)
    if r is not None:
        b["r_sum"] += r
        b["r_n"] += 1


def _report_final(b: Dict) -> Dict:
    decided = b["wins"] + b["losses"]
    out = {k: (round(v, 4) if isinstance(v, float) else v) for k, v in b.items()
           if k not in ("r_sum", "r_n")}
    out["wr"] = round(b["wins"] / decided * 100, 1) if decided else None
    out["avg_r"] = round(b["r_sum"] / b["r_n"], 4) if b["r_n"] else None
    return out


def policy_report_rows(trades: List[Dict]) -> List[Dict]:
    """Netto-Erfolgsbericht je Policy-Version (Audit 3.4): realized_pnl ist bereits
    NETTO (inkl. Fees/Funding, Slippage steckt im Fill) – getrennt nach Welt
    live / paper / collect (Datensammlung) + gesamt; R-Mittel in Geld (Audit 2.3).
    Rein (testbar); sortiert nach jüngstem Trade, Alt-Trades ohne Fingerprint ('') zuletzt."""
    from services.ml_gate import money_r  # lazy: kein Import-Zyklus
    from services.policy_fingerprint import PART_KEYS, group_key
    rows: Dict[str, Dict] = {}
    for t in trades or []:
        fp = t.get("policy_version") or {}
        key = group_key(fp)
        row = rows.setdefault(key, {
            "combined": key or None,
            "policy": ({k: fp.get(k) for k in PART_KEYS} if key else None),
            "first_ts": None, "last_ts": None,
            "worlds": {"live": _report_bucket(), "paper": _report_bucket(),
                       "collect": _report_bucket()},
            "total": _report_bucket()})
        world = ("collect" if t.get("data_collection")
                 else ("live" if str(t.get("mode") or "") == "live" else "paper"))
        r = money_r(t)
        _report_add(row["worlds"][world], t, r)
        _report_add(row["total"], t, r)
        ts = _iso(t.get("opened_at"))
        if ts:
            row["first_ts"] = min(row["first_ts"], ts) if row["first_ts"] else ts
            row["last_ts"] = max(row["last_ts"], ts) if row["last_ts"] else ts
    out = []
    for row in rows.values():
        row["worlds"] = {w: _report_final(b) for w, b in row["worlds"].items()}
        row["total"] = _report_final(row["total"])
        out.append(row)
    # jüngste Policy zuerst, Alt-Trades ohne Fingerprint ('') ganz hinten
    out.sort(key=lambda r: r["last_ts"] or "", reverse=True)
    out.sort(key=lambda r: 0 if r["combined"] else 1)
    return out


def policy_groups(trades: List[Dict]) -> Dict[str, Dict]:
    """Trades nach Policy-Version gruppieren (Audit 3.1) – Auswertung nach
    Fingerprint statt Zeitfenster. Schlüssel = policy_version.combined,
    '' sammelt Alt-Trades ohne Fingerprint. Rein (testbar)."""
    from services.policy_fingerprint import PART_KEYS, group_key  # lazy: kein Zyklus
    out: Dict[str, Dict] = {}
    for t in trades or []:
        fp = t.get("policy_version") or {}
        key = group_key(fp)
        g = out.setdefault(key, {
            "trades": 0, "wins": 0, "pnl": 0.0, "first_ts": None, "last_ts": None,
            "policy": ({k: fp.get(k) for k in PART_KEYS} if key else None)})
        g["trades"] += 1
        pnl = float(t.get("realized_pnl") or 0)
        g["pnl"] = round(g["pnl"] + pnl, 6)
        res = str(t.get("result") or "")
        if res == "win" or (not res and pnl > 0):
            g["wins"] += 1
        ts = _iso(t.get("opened_at"))
        if ts:
            g["first_ts"] = min(g["first_ts"], ts) if g["first_ts"] else ts
            g["last_ts"] = max(g["last_ts"], ts) if g["last_ts"] else ts
    return out
