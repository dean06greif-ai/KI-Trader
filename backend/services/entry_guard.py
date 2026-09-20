"""Zentrale Einstiegsprüfung (Audit 2.1, Phase 2).

Bündelt die bestehenden Schutz-Bausteine – KEIN Neuschreiben, die Bausteine
bleiben eigenständig: Kill-Switch/Lernpflicht/Anti-Stacking (trade_guard),
Marktphasen-Filter (regime_gate) und Risikobudget (risk_budget). Fee-Wächter,
Low-Vol-ATR-Block und Kapital-Limit werden an ihrer bestehenden Stelle in
bitunix_trade._on_signal_impl ausgeführt und hier nur protokolliert.

Jede gelaufene Prüfung landet als Eintrag im `EntryChecks`-Protokoll und wird
als `entry_checks[]` am Trade gespeichert (Nachvollziehbarkeit: WAS wurde mit
welchem Ergebnis geprüft). Alle Einstiegswege (Strategie-Signal, KI-Trader,
Key-Level-Limit-Fill, manueller Trade, IBKR-Forex) laufen über
core/pipeline.py -> AutoTradeManager.on_signal und damit durch dieses Modul.
"""
import logging
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

REASON_MAX_LEN = 300


class EntryChecks:
    """Sammelt die Ergebnisse aller Einstiegsprüfungen eines Signals (rein)."""

    def __init__(self):
        self.items: List[Dict] = []

    def record(self, name: str, ok: bool = True, reason: str = "", **extra) -> bool:
        row: Dict = {"name": name, "ok": bool(ok)}
        if reason:
            row["reason"] = str(reason)[:REASON_MAX_LEN]
        for k, v in extra.items():
            if v is not None:
                row[k] = v
        self.items.append(row)
        return bool(ok)

    def to_list(self) -> List[Dict]:
        return [dict(r) for r in self.items]


async def check_entry(db, signal: Dict, cfg: Dict, mode: str, timeframe: str,
                      checks: EntryChecks, collection: bool = False,
                      skip: Optional[set] = None) -> Tuple[bool, str]:
    """Stufe-1-Bündel vor der Level-Berechnung: Kill-Switch, Lernpflicht,
    Übergangsschutz und Anti-Stacking (trade_guard.check_open_allowed) plus
    Marktphasen-Filter (regime_gate). Fail-open beim Regime-Gate wie bisher.
    `skip`: Wächter, die ein Schattentrade (services/guard_shadow.py) bewusst
    überspringt – der Block wird dann nur protokolliert, nicht angewendet."""
    from services import trade_guard
    skip = skip or set()
    ok, reason = await trade_guard.check_open_allowed(db, signal, timeframe, mode=mode)
    if not ok and "trade_guard" in skip:
        checks.record("trade_guard", True, f"übersprungen (Schattentrade): {reason}", mode=mode)
        ok = True
    else:
        checks.record("trade_guard", ok, reason, mode=mode)
    if not ok:
        return False, reason
    # Sicherheitsstatus (Audit 2.4): kritischer Betriebszustand (SL fehlt an
    # der Börse, Close fehlgeschlagen, Abgleich veraltet) blockt neue
    # LIVE-Risiken. Fail-open, abschaltbar über safety_status_config.
    if mode == "live":
        from services import safety_status
        ok, reason = await safety_status.entry_allowed(db)
        checks.record("safety_status", ok, reason)
        if not ok:
            return False, reason
    if cfg.get("regime_filter_enabled") and not collection:
        from services import regime_gate
        ok, reason = await regime_gate.check_signal_allowed(cfg, signal.get("symbol"))
        checks.record("regime_gate", ok, reason)
        if not ok:
            return False, reason
    return True, ""


async def check_risk_budget(db, mode: str, symbol: str, new_risk_usdt: float,
                            equity: Optional[float],
                            checks: EntryChecks) -> Tuple[bool, str]:
    """Gesamt-Risikobudget (services/risk_budget.py) mit Protokoll-Eintrag.
    Fail-open bei internen Fehlern (kein Block auf Basis fehlender Daten)."""
    try:
        from services import risk_budget
        ok, why = await risk_budget.check_new_trade(db, mode, symbol, new_risk_usdt, equity)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"{symbol}: Risikobudget-Prüfung fehlgeschlagen (fail-open): {e}")
        checks.record("risk_budget", True, f"fail-open: {e}")
        return True, ""
    checks.record("risk_budget", ok, why, new_risk_usdt=round(float(new_risk_usdt or 0), 6))
    return ok, why
