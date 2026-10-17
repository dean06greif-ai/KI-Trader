"""AP03-Solltests (Befunde R01/R02/R12): deterministischer Resolver und
Basis-Ableitung ohne kumulative Merges."""
import pytest

from services import strategy_plan

pytestmark = pytest.mark.unit

DOC = {
    "id": "dyn1", "strategy_id": "trend",
    "configs": {"0": {"tp1_crv": 1.0, "leverage": 5},
                "1": {"tp1_crv": 2.0},
                "2": {}},
    "fallback_config": {"tp1_crv": 0.5},
    "regime_params": {"0": {"ema_fast": 9}},
    "sub_strategies": {"0": {"rules": [{"if": "rsi<30", "then": "long"}]}},
}


class TestResolverDeterminism:
    def test_identical_inputs_identical_hash(self):
        p1 = strategy_plan.resolve_symbol_plan(DOC, "BTC", {"regime": 0})
        p2 = strategy_plan.resolve_symbol_plan(DOC, "BTC", {"regime": 0})
        assert p1 == p2 and p1["plan_hash"] == p2["plan_hash"]

    def test_two_parameter_variants_have_different_hashes(self):
        doc_b = {**DOC, "configs": {"0": {"tp1_crv": 1.5, "leverage": 5}}}
        p1 = strategy_plan.resolve_symbol_plan(DOC, "BTC", {"regime": 0})
        p2 = strategy_plan.resolve_symbol_plan(doc_b, "BTC", {"regime": 0})
        assert p1["plan_hash"] != p2["plan_hash"]

    def test_two_assets_same_regime_same_overrides(self):
        p1 = strategy_plan.resolve_symbol_plan(DOC, "BTC", {"regime": 1})
        p2 = strategy_plan.resolve_symbol_plan(DOC, "ETH", {"regime": 1})
        assert p1["overrides"] == p2["overrides"] == {"tp1_crv": 2.0}


class TestResolverStates:
    def test_unknown_regime_is_unmapped(self):
        p = strategy_plan.resolve_symbol_plan(DOC, "BTC", {"regime": None})
        assert p["unmapped"] is True and p["overrides"] == {}

    def test_empty_config_is_baseline(self):
        p = strategy_plan.resolve_symbol_plan(DOC, "BTC", {"regime": 2})
        assert p["baseline"] is True and p["overrides"] == {}

    def test_regime_without_config_uses_fallback(self):
        p = strategy_plan.resolve_symbol_plan(DOC, "BTC", {"regime": 9})
        assert p["overrides"] == {"tp1_crv": 0.5}

    def test_multi_mapping_unmapped_regime_has_no_strategy(self):
        doc = {"id": "d", "regime_strategies": {"0": "nnfx"}, "configs": {}}
        p = strategy_plan.resolve_symbol_plan(doc, "BTC", {"regime": 3})
        assert p["unmapped"] is True and p["strategy_id"] is None

    def test_sub_strategy_rules_exposed(self):
        """R01: Discovery-Substrategie ist Teil des aufgelösten Plans."""
        p = strategy_plan.resolve_symbol_plan(DOC, "BTC", {"regime": 0})
        assert p["sub_strategy"]["rules"][0]["then"] == "long"

    def test_non_opt_keys_are_not_smuggled(self):
        doc = {**DOC, "configs": {"0": {"tp1_crv": 1.0, "evil_key": 1}}}
        p = strategy_plan.resolve_symbol_plan(doc, "BTC", {"regime": 0})
        assert "evil_key" not in p["overrides"]


class TestMergeOverrides:
    def test_stale_dynamic_keys_removed_on_partial_followup(self):
        """R02: Regime A setzt tp1_crv+leverage, Regime B nur tp1_crv ->
        leverage darf NICHT liegenbleiben."""
        after_a = strategy_plan.merge_overrides({}, {"tp1_crv": 1.0, "leverage": 5})
        assert after_a["leverage"] == 5
        after_b = strategy_plan.merge_overrides(after_a, {"tp1_crv": 2.0})
        assert "leverage" not in after_b
        assert after_b["tp1_crv"] == 2.0

    def test_a_to_b_to_baseline_equals_direct_baseline(self):
        a = strategy_plan.merge_overrides({}, {"tp1_crv": 1.0, "leverage": 5})
        b = strategy_plan.merge_overrides(a, {"tp1_crv": 2.0})
        via_ab = strategy_plan.merge_overrides(b, {})
        direct = strategy_plan.merge_overrides({}, {})
        assert {k: v for k, v in via_ab.items() if k != "dynamic_keys"} == \
               {k: v for k, v in direct.items() if k != "dynamic_keys"}

    def test_manual_keys_survive_dynamic_cycles(self):
        base = {"tp_full_crv": 9.9}  # manuell gesetzt, nicht in dynamic_keys
        a = strategy_plan.merge_overrides(base, {"tp1_crv": 1.0})
        b = strategy_plan.merge_overrides(a, {})
        assert b["tp_full_crv"] == 9.9 and "tp1_crv" not in b

    def test_stale_dynamic_params_removed(self):
        a = strategy_plan.merge_overrides({}, {}, params={"ema_fast": 9, "rsi": 14})
        assert a["params"] == {"ema_fast": 9, "rsi": 14}
        b = strategy_plan.merge_overrides(a, {}, params={"ema_fast": 21})
        assert b["params"] == {"ema_fast": 21}
        c = strategy_plan.merge_overrides(b, {}, params={})
        assert "params" not in c

    def test_params_none_leaves_params_untouched(self):
        a = strategy_plan.merge_overrides({}, {}, params={"ema_fast": 9})
        b = strategy_plan.merge_overrides(a, {"tp1_crv": 1.0})
        assert b["params"] == {"ema_fast": 9}
