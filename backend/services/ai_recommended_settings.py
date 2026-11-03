"""Empfohlene KI-Trader-Einstellungen (Rettungsplan 03.11.2026, Phase 0).

Nichts wird automatisch geändert: `diff` zeigt Abweichungen, `apply` setzt
die Werte erst auf ausdrücklichen Klick über den normalen Config-Pfad
(ai_engine.update_config – gleiche Validierung wie im Panel).
"""
from typing import Dict, List

RECOMMENDED: List[Dict] = [
    {"key": "live_gate_bypass_enabled", "value": False, "label": "Live-Bypass",
     "why": "Konfidenz ist nicht kalibriert (hohe Konfidenz nicht besser) – Bypass wirkt zufällig."},
    {"key": "edge_filter_enabled", "value": True, "label": "Edge-Filter (Live)",
     "why": "Nur Gebühren ≤ 10 % des Risikos + Tagestrend + Krypto lagen im Plus."},
    {"key": "fee_guard_mult", "value": 6.0, "label": "Fee-Faktor",
     "why": "3,5× erlaubt bis 29 % des Risikos als Gebühr; 6× ≤ 17 %."},
    {"key": "fee_guard_atr_mult", "value": 4.0, "label": "ATR-Faktor",
     "why": "Trades < 15 min verloren −0,58 R – Stops lagen im Rauschen."},
    {"key": "guard_autotune_enabled", "value": False, "label": "Fee-Wächter-Autotune",
     "why": "Autotune lockert den Wächter nach einzelnen Gewinner-Schattentrades und zieht ihn auf ≤ Baseline + 1,5 zurück."},
    {"key": "ai_role", "value": "pruefer", "label": "Rolle der KI",
     "why": "KI-geprüfte Detektor-Signale +0,32 R vs. KI-frei −0,13 R; spart den periodischen LLM-Zyklus."},
    {"key": "structure_stop_enabled", "value": True, "label": "Struktur-Stop (15m)",
     "why": "SL 0,4–0,7 % lag bei −0,34 R, SL ≥ 1 % bei −0,03 R – Stop hinter die 15m-Struktur (1–3 %)."},
    {"key": "maker_policy", "value": "all", "label": "Maker-Einstieg",
     "why": "Maker-Trades −0,13 R vs. Market −0,23 R; halbe Gebühren = +0,11 R je Trade."},
]


def diff(cfg: Dict) -> List[Dict]:
    """Rein: Empfehlungen mit aktuellem Wert und ob sie abweichen."""
    cfg = cfg or {}
    out = []
    for r in RECOMMENDED:
        cur = cfg.get(r["key"])
        same = (cur == r["value"]) if not isinstance(r["value"], float) else \
            (cur is not None and abs(float(cur) - r["value"]) < 1e-9)
        out.append({**r, "current": cur, "differs": not same})
    return out


def updates(cfg: Dict) -> Dict:
    return {r["key"]: r["value"] for r in diff(cfg) if r["differs"]}


async def apply(engine) -> Dict:
    upd = updates(engine.config or {})
    if upd:
        await engine.update_config(dict(upd))
    return {"applied": upd, "items": diff(engine.config or {})}
