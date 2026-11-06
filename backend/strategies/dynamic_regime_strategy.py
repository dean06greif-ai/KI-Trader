"""Dynamische Strategie als eigenständige, handelbare Strategie.

Eine dynamische Strategie (Dokument in `dynamic_strategies`) erscheint damit
wie jede andere Strategie in der Strategie-Leiste eines Assets: Blitz
(Auto-Trade Live/Paper), Glocke, Kapital, Verlauf. Intern delegiert sie je
Symbol an die Strategie des AKTUELLEN Regimes (services.dynamic_runtime hält
den Live-Regime-Zustand) – exakt über denselben Resolver wie der bisherige
Apply-Pfad (services.strategy_plan.resolve_symbol_plan).

Regime ohne Strategie -> keine neuen Signale. Nicht freigegebene Entwürfe
zeigen den Regel-Zustand, erzeugen aber keine Signale.
"""
from typing import Dict, List, Optional

from strategies.base_strategy import BaseStrategy

# Geld-/Hebel-Keys bleiben in der Hand des Nutzers (Blitz-Einstellungen) –
# die optimierten Regime-Werte liefern nur SL/TP/Breakeven & Co.
USER_MONEY_KEYS = {"leverage", "auto_leverage_enabled", "auto_lev_mode",
                   "auto_lev_value", "auto_lev_max", "max_capital", "sessions"}


class DynamicRegimeStrategy(BaseStrategy):
    IS_DYNAMIC = True
    DEFAULT_PARAMS = {}

    def __init__(self, doc: Dict):
        super().__init__()
        self.update_doc(doc)

    def update_doc(self, doc: Dict):
        self.doc = doc
        self.STRATEGY_ID = doc["id"]
        self.STRATEGY_NAME = doc.get("name") or doc["id"]
        n = len(((doc.get("model") or {}).get("regimes")) or [])
        self.STRATEGY_DESCRIPTION = (f"Dynamische Strategie: wechselt je nach Marktphase "
                                     f"({n} Regime) automatisch die Strategie")
        self.STRATEGY_TIMEFRAME = doc.get("timeframe") or "1h"

    def regimes(self) -> List[Dict]:
        from services import strategy_plan
        from strategies.registry import registry
        out = []
        for r in (self.doc.get("model") or {}).get("regimes") or []:
            plan = strategy_plan.resolve_symbol_plan(self.doc, "_", {"regime": r["id"],
                                                                   "label": r.get("label")})
            sub = registry.get(plan["strategy_id"] or "")
            own = bool(plan.get("rules_override") and plan.get("sub_strategy"))
            traded = bool(sub) and not plan["unmapped"] \
                and not getattr(sub, "IS_DYNAMIC", False)
            out.append({"id": r["id"], "label": r.get("label"), "traded": traded,
                        "strategy_id": plan["strategy_id"] if traded else None,
                        "strategy_name": (("Eigene Regeln (Discovery)" if own else
                                           getattr(sub, "STRATEGY_NAME", plan["strategy_id"]))
                                          if traded else None),
                        "trade_params": plan["overrides"],
                        "strategy_params": plan["params"]})
        return out

    def get_metadata(self) -> Dict:
        meta = super().get_metadata()
        meta["is_dynamic"] = True
        meta["dynamic"] = {"regimes": self.regimes(),
                           "analysis_id": (self.doc.get("settings") or {}).get("analysis_id"),
                           "symbols": self.doc.get("symbols") or []}
        return meta

    def get_params(self, settings: Dict, symbol: str = None) -> Dict:
        return {}

    def _idle(self, info: Dict, note: str) -> Dict:
        return {"indicators": {}, "rules": [], "bias": None, "signal_type": None,
                "is_pre_signal": False, "levels": None, "long_count": 0,
                "short_count": 0, "rules_total": 0,
                "dynamic": {**info, "status": note}}

    def analyze(self, candles: List[Dict], symbol: str, params: Dict) -> Optional[Dict]:
        from core.state import scanner
        from services import dynamic_runtime as rt
        from services import strategy_plan, strategy_release
        from strategies.registry import registry
        st = rt.current_state(self.STRATEGY_ID, symbol)
        info = {"id": self.STRATEGY_ID, "name": self.STRATEGY_NAME,
                "regime": (st or {}).get("regime"), "label": (st or {}).get("label"),
                "confidence": (st or {}).get("confidence")}
        if not st or st.get("regime") is None:
            return self._idle(info, "Regime noch unbekannt – Erkennung läuft")
        plan = strategy_plan.resolve_symbol_plan(self.doc, symbol, st)
        sub = registry.get(plan["strategy_id"] or "")
        if plan["unmapped"] or sub is None or getattr(sub, "IS_DYNAMIC", False):
            return self._idle(info, f"Regime '{st.get('label')}' wird nicht gehandelt")
        info.update({"sub_strategy_id": sub.STRATEGY_ID,
                     "sub_strategy_name": sub.STRATEGY_NAME,
                     "own_rules": bool(plan.get("rules_override"))})
        sub_params = sub.get_params(scanner.settings, symbol)
        sub_params.update(plan["params"] or {})
        if plan.get("rules_override") and getattr(sub, "IS_CUSTOM", False):
            sub_params["rules_override"] = plan["rules_override"]
        res = sub.analyze(candles, symbol, sub_params)
        if not res:
            return self._idle(info, "Sub-Strategie liefert keinen Zustand")
        res = dict(res)
        block = strategy_release.activation_block_reason(self.doc)
        if block:
            res["signal_type"] = None
            info["status"] = "Nicht freigegeben – keine Signale"
            info["block_reason"] = block
        else:
            self._apply_guards(res, info, symbol, st)
        res["dynamic"] = info
        res["cfg_overrides"] = {k: v for k, v in (plan["overrides"] or {}).items()
                                if k not in USER_MONEY_KEYS}
        return res

    def _apply_guards(self, res: Dict, info: Dict, symbol: str, st: Dict) -> None:
        """Optionale Schutzfilter (06/2026, Standard AUS = Alt-Verhalten):
        - transition_guard_min_conf: kein neues Signal, solange die Regime-
          Sicherheit (%) darunter liegt (unsichere Übergänge)
        - macro_filter_enabled: Signal-Seite muss zur Makro×Haupt-Matrix
          passen (services.regime_hierarchy, fail-open ohne Daten)."""
        s = self.doc.get("settings") or {}
        guard = float(s.get("transition_guard_min_conf") or 0)
        conf = st.get("confidence")
        if guard > 0 and conf is not None and float(conf) < guard and res.get("signal_type"):
            info["guard"] = f"Übergangsschutz: Regime-Sicherheit {float(conf):.0f} % < {guard:.0f} %"
            res["signal_type"] = None
        if not s.get("macro_filter_enabled"):
            return
        from services import regime_hierarchy as rh
        hz = rh.cached(symbol)
        _refresh_hierarchy(symbol, hz)
        if hz:
            comb = hz.get("combined") or {}
            info["hierarchy"] = {"label": comb.get("label"), "size_factor": comb.get("size_factor"),
                                 "macro": (hz.get("macro") or {}).get("direction"),
                                 "primary": (hz.get("primary") or {}).get("direction")}
        if res.get("signal_type") and not rh.side_allowed(hz, res["signal_type"]):
            info["guard"] = (f"Makro-Filter: {res['signal_type']} passt nicht zu "
                             f"„{(hz.get('combined') or {}).get('label')}“")
            res["signal_type"] = None


def _refresh_hierarchy(symbol: str, hz: Optional[Dict]) -> None:
    """Makro-Zustand im Hintergrund aktualisieren (analyze ist synchron)."""
    import asyncio
    import time
    from services import regime_hierarchy as rh
    if hz and time.time() - float(hz.get("checked_at") or 0) < rh.CACHE_TTL_S:
        return
    if symbol in _PENDING:
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    _PENDING.add(symbol)
    task = loop.create_task(rh.resolve(symbol))
    task.add_done_callback(lambda _t: _PENDING.discard(symbol))


_PENDING: set = set()
