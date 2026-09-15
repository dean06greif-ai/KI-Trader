"""AP03 (Befunde R01/R02/R12): EIN reiner, deterministischer Resolver für
dynamische Strategie-/Parameterbindungen.

Kernideen:
  * `resolve_symbol_plan` bildet Dokument + Regime-Zustand eines Symbols auf
    einen EffectivePlan ab – rein, ohne DB/Netz, mit stabilem `plan_hash`
    (identische Inputs -> identischer Plan/Hash).
  * `merge_overrides` leitet die effektive Coin-Konfiguration IMMER von der
    Basis ab: zuvor dynamisch gesetzte Keys, die im neuen Plan fehlen, werden
    ENTFERNT statt kumulativ liegenzubleiben (R02). Manuell gesetzte Keys
    bleiben unangetastet (Ownership: dynamisch vs. persönlich).
  * Unbelegte Regime werden explizit als `unmapped` markiert statt still auf
    veralteten Parametern weiterzulaufen.

Runtime- und Apply-Pfade (services/dynamic_live.py) nutzen diesen Resolver;
öffentliche Signaturen und gespeicherte Dokumentformen bleiben erhalten
(neue Felder nur additiv: `dynamic_keys`, `dynamic_param_keys`, `plan_hash`).
"""
import hashlib
import json
from typing import Dict, List, Optional

from core.defaults import OPT_TRADE_KEYS


def _canonical(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)


def plan_hash(plan: Dict) -> str:
    """Stabiler Hash über den kanonisierten Plan (ohne den Hash selbst)."""
    payload = {k: v for k, v in plan.items() if k != "plan_hash"}
    return hashlib.sha256(_canonical(payload).encode()).hexdigest()[:16]


def resolve_symbol_plan(doc: Dict, symbol: str, st: Optional[Dict]) -> Dict:
    """EffectivePlan für EIN Symbol (rein). `st` ist der Regime-Zustand aus
    last_state.per_symbol[symbol] (mindestens {'regime': id}).

    Auflösungsregeln (verhaltensgleich zum bisherigen Apply, aber explizit):
      * regime_strategies vorhanden -> Multi-Strategie-Modus: Strategie des
        Regimes, sonst unmapped (keine Strategie aktiv geschaltet).
      * sonst Basis-Strategie mit Regime-Config; Regime ohne Config ->
        fallback_config, sonst Baseline ({} = Baseline ist gültig).
      * Overrides werden auf OPT_TRADE_KEYS begrenzt (kein Key-Schmuggel).
    """
    st = st or {}
    rid = st.get("regime")
    key = None if rid is None else str(rid)
    configs = {str(k): v for k, v in (doc.get("configs") or {}).items()}
    mapping = {str(k): v for k, v in (doc.get("regime_strategies") or {}).items()}
    plan: Dict = {"symbol": symbol, "dynamic_id": doc.get("id"),
                  "regime": rid, "label": st.get("label"),
                  "multi": bool(mapping), "unmapped": False, "baseline": False,
                  "strategy_id": None, "overrides": {}, "params": {},
                  "sub_strategy": None}
    if key is None:
        plan["unmapped"] = True
        plan["plan_hash"] = plan_hash(plan)
        return plan
    if mapping:
        plan["strategy_id"] = mapping.get(key)
        plan["unmapped"] = key not in mapping or not mapping.get(key)
    else:
        plan["strategy_id"] = doc.get("strategy_id")
    cfg_r = configs.get(key)
    if cfg_r is None and not mapping:
        fb = doc.get("fallback_config") or {}
        cfg_r = fb
    cfg_r = cfg_r or {}
    plan["baseline"] = not cfg_r
    plan["overrides"] = {k: cfg_r[k] for k in OPT_TRADE_KEYS
                         if cfg_r.get(k) is not None}
    plan["params"] = dict((doc.get("regime_params") or {}).get(key) or {})
    sub = (doc.get("sub_strategies") or {}).get(key)
    if isinstance(sub, dict) and sub.get("rules"):
        plan["sub_strategy"] = {"regime": rid, "rules": sub["rules"]}
    plan["plan_hash"] = plan_hash(plan)
    return plan


def merge_overrides(prev_config: Optional[Dict], overrides: Dict,
                    params: Optional[Dict] = None) -> Dict:
    """Effektive Coin-Konfiguration von der Basis ableiten (rein, R02).

    * Keys aus `prev_config['dynamic_keys']` (frühere dynamische Übernahme),
      die im neuen Plan fehlen, werden ENTFERNT (A->B->Baseline == Baseline).
    * Manuell gesetzte Keys (nicht in dynamic_keys) bleiben erhalten.
    * params analog über `dynamic_param_keys` (params=None -> Params unberührt).
    """
    merged = dict(prev_config or {})
    prev_dyn: List[str] = list(merged.pop("dynamic_keys", []) or [])
    prev_dyn_params: List[str] = list(merged.pop("dynamic_param_keys", []) or [])
    for k in prev_dyn:
        if k not in overrides:
            merged.pop(k, None)
    for k, v in overrides.items():
        merged[k] = v
    merged["dynamic_keys"] = sorted(overrides.keys())
    if params is not None:
        cur = dict(merged.get("params") or {})
        for k in prev_dyn_params:
            if k not in params:
                cur.pop(k, None)
        cur.update(params)
        if cur:
            merged["params"] = cur
        else:
            merged.pop("params", None)
        merged["dynamic_param_keys"] = sorted(params.keys())
    elif prev_dyn_params:
        merged["dynamic_param_keys"] = prev_dyn_params
    return merged
