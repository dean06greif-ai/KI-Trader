"""Regressionstests: Strategien umbenennen/duplizieren (Custom, Built-in-Varianten)
und Anzeigename-Overrides – rein auf der Registry (ohne DB/Backend)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from strategies.registry import StrategyRegistry, VARIANT_KIND  # noqa: E402


def _custom_def(sid="custom_t1", name="Mein Test"):
    return {"id": sid, "name": name, "timeframe": "5m",
            "indicators": {"rsi_period": 14},
            "long_rules": [{"indicator": "rsi", "op": "<", "value": 30}],
            "short_rules": []}


def test_variant_of_builtin_shares_code_but_has_own_identity():
    reg = StrategyRegistry()
    base = reg.get("scalping_4_rules")
    v = reg.upsert_variant({"id": "variant_abc", "base_id": "scalping_4_rules",
                            "name": "Scalping Kopie", "timeframe": "15m"})
    assert v is not None
    assert type(v) is type(base)
    assert v.STRATEGY_ID == "variant_abc" and v.STRATEGY_NAME == "Scalping Kopie"
    assert v.STRATEGY_TIMEFRAME == "15m"
    assert v.IS_VARIANT and v.BASE_STRATEGY_ID == "scalping_4_rules"
    # Original unverändert (Instanz-Attribute, keine Klassen-Attribute überschrieben)
    assert base.STRATEGY_ID == "scalping_4_rules" and not base.IS_VARIANT
    assert reg.is_variant("variant_abc") and not reg.is_variant("scalping_4_rules")
    meta = {m["id"]: m for m in reg.list_all()}
    assert meta["variant_abc"]["is_variant"] is True
    assert meta["variant_abc"]["base_id"] == "scalping_4_rules"
    assert meta["scalping_4_rules"]["is_variant"] is False


def test_variant_rejects_custom_and_unknown_base():
    reg = StrategyRegistry()
    reg.upsert_custom(_custom_def())
    assert reg.upsert_variant({"id": "v1", "base_id": "custom_t1"}) is None
    assert reg.upsert_variant({"id": "v2", "base_id": "gibt_es_nicht"}) is None
    # Variante einer Variante nicht erlaubt (Router zeigt stattdessen auf base_id)
    reg.upsert_variant({"id": "v3", "base_id": "rsi_only"})
    assert reg.upsert_variant({"id": "v4", "base_id": "v3"}) is None


def test_load_custom_roundtrip_includes_variants_for_worker():
    reg = StrategyRegistry()
    reg.upsert_custom(_custom_def())
    reg.upsert_variant({"id": "variant_x", "base_id": "macd_rsi_momentum", "name": "MACD Kopie"})
    payload = reg.list_custom_definitions()
    kinds = {d.get("kind") for d in payload}
    assert VARIANT_KIND in kinds and None in kinds
    # Worker baut daraus dieselbe Registry (make_registry -> load_custom)
    worker = StrategyRegistry()
    worker.load_custom(payload)
    assert worker.get("custom_t1") is not None
    assert worker.get("variant_x") is not None and worker.is_variant("variant_x")
    assert worker.get("variant_x").STRATEGY_NAME == "MACD Kopie"
    # Erneutes load_custom ersetzt Altbestand vollständig
    worker.load_custom([])
    assert worker.get("custom_t1") is None and worker.get("variant_x") is None


def test_rename_custom_variant_and_builtin():
    reg = StrategyRegistry()
    reg.upsert_custom(_custom_def())
    reg.upsert_variant({"id": "variant_r", "base_id": "rsi_only", "name": "Alt"})
    assert reg.set_name("custom_t1", "  Neuer Name ")
    assert reg.get("custom_t1").STRATEGY_NAME == "Neuer Name"
    assert reg.get("custom_t1").definition["name"] == "Neuer Name"
    assert reg.set_name("variant_r", "Variante Neu")
    assert reg.get("variant_r").variant_definition["name"] == "Variante Neu"
    assert reg.set_name("scalping_4_rules", "Mein Scalper")
    assert reg.get("scalping_4_rules").STRATEGY_NAME == "Mein Scalper"
    assert not reg.set_name("scalping_4_rules", "   ")
    assert not reg.set_name("unbekannt", "x")


def test_apply_name_overrides_only_for_builtins():
    reg = StrategyRegistry()
    reg.upsert_custom(_custom_def())
    reg.apply_name_overrides({"scalping_4_rules": "Scalper", "custom_t1": "ignoriert",
                              "unbekannt": "egal"})
    assert reg.get("scalping_4_rules").STRATEGY_NAME == "Scalper"
    assert reg.get("custom_t1").STRATEGY_NAME == "Mein Test"


def test_remove_variant():
    reg = StrategyRegistry()
    reg.upsert_variant({"id": "variant_del", "base_id": "rsi_only"})
    reg.remove_variant("variant_del")
    assert reg.get("variant_del") is None and not reg.is_variant("variant_del")
    # Built-in bleibt beim Versuch, es als Variante zu entfernen, erhalten
    reg.remove_variant("rsi_only")
    assert reg.get("rsi_only") is not None
