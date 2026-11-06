"""Regime-Haltedauer im Live-Betrieb (dynamische Strategien).

Befund Prod 11/2026: 1076 Regime-Wechsel, Median-Abstand 1,0 h, 56 % Ping-Pong
(A→B→A < 6 h) – obwohl die Strategien `min_hold_days` = 1 eingestellt haben.
Ursache: jeder Runtime-Refresh rechnet das Regime neu aus einem gleitenden
Fenster; am rechten Rand fehlt die Glättung, der Live-Zustand hat kein
Gedächtnis. Diese reine Funktion ergänzt das Gedächtnis:
  * Wechsel erst nach `min_hold_days` im aktuellen Regime (wie im Backtest),
  * und nur, wenn das neue Regime in `confirm` Refreshes hintereinander kommt.
Abschaltbar je Strategie: settings.runtime_dwell_enabled = False.
"""
from datetime import datetime, timezone
from typing import Dict, Optional, Tuple

DEFAULT_CONFIRM = 2


def _age_h(since: Optional[str], now: datetime) -> float:
    try:
        return (now - datetime.fromisoformat(str(since))).total_seconds() / 3600.0
    except (TypeError, ValueError):
        return 1e9


def decide(prev: Optional[Dict], st: Dict, settings: Optional[Dict],
           now: Optional[datetime] = None) -> Tuple[Dict, bool]:
    """(effektiver Zustand, gewechselt?). Rein."""
    s = settings or {}
    now = now or datetime.now(timezone.utc)
    iso = now.isoformat()
    if not prev or prev.get("regime") is None:
        return {**st, "since": iso}, False
    if not s.get("runtime_dwell_enabled", True):
        if prev["regime"] != st.get("regime"):
            return {**st, "since": iso}, True
        return {**st, "since": prev.get("since") or iso}, False
    if st.get("regime") == prev["regime"]:
        return {**st, "since": prev.get("since") or iso}, False
    hold_h = max(0.0, float(s.get("min_hold_days") or 0)) * 24.0
    confirm = max(1, int(s.get("runtime_confirm_refreshes") or DEFAULT_CONFIRM))
    pend = prev.get("pending") or {}
    count = int(pend.get("count") or 0) + 1 if pend.get("regime") == st.get("regime") else 1
    if _age_h(prev.get("since"), now) >= hold_h and count >= confirm:
        return {**st, "since": iso}, True
    why = (f"Haltedauer {_age_h(prev.get('since'), now):.1f}/{hold_h:.0f} h"
           if _age_h(prev.get("since"), now) < hold_h else f"Bestätigung {count}/{confirm}")
    held = {k: v for k, v in prev.items() if k not in ("pending", "held")}
    held.update(pending={"regime": st.get("regime"), "label": st.get("label"), "count": count},
                held=f"Wechsel zu „{st.get('label')}“ zurückgehalten ({why})")
    return held, False
