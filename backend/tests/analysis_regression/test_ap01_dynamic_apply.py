"""AP01/AP03-Solltests (Befunde R02/R03): dynamischer Apply nutzt den Resolver,
Übergangsschutz ist gescoped und läuft nur im freigegebenen Apply-Pfad."""
import ast
import asyncio
import inspect

import pytest

from services import dynamic_live

from _fakes import FakeDB

pytestmark = pytest.mark.unit


class TestTransitionScope:
    def test_close_query_is_scoped_to_own_strategies_and_excludes_manual(self):
        doc = {"id": "d1", "strategy_id": "trend"}
        q = dynamic_live.transition_close_query(doc, ["BTC", "ETH"])
        assert q["strategy_id"] == {"$in": ["trend"]}
        assert q["manual_trade"] == {"$ne": True}
        assert q["symbol"] == {"$in": ["BTC", "ETH"]}
        assert q["status"] == "open"

    def test_multi_mapping_strategies_included(self):
        doc = {"id": "d1", "strategy_id": "trend",
               "regime_strategies": {"0": "nnfx", "1": "meanrev"}}
        q = dynamic_live.transition_close_query(doc, ["BTC"])
        assert q["strategy_id"] == {"$in": ["meanrev", "nnfx", "trend"]}

    def test_foreign_and_manual_trades_not_matched(self):
        from _fakes import _match
        doc = {"id": "d1", "strategy_id": "trend"}
        q = dynamic_live.transition_close_query(doc, ["BTC"])
        own = {"symbol": "BTC", "status": "open", "strategy_id": "trend"}
        foreign = {"symbol": "BTC", "status": "open", "strategy_id": "other"}
        manual = {"symbol": "BTC", "status": "open", "strategy_id": "trend",
                  "manual_trade": True}
        assert _match(own, q) is True
        assert _match(foreign, q) is False
        assert _match(manual, q) is False


class TestRefreshHasNoTradingEffect:
    def test_transition_protect_only_called_inside_apply_branch(self):
        """AP01/R03-Abnahme: Refresh (auto_apply=False) oder offener
        Bestätigungs-Vorschlag darf keinen Übergangsschutz auslösen –
        struktureller Nachweis am aktuellen Quelltext von check_one."""
        src = inspect.getsource(dynamic_live.check_one)
        import textwrap
        tree = ast.parse(textwrap.dedent(src))
        calls_in_guarded_branch = []
        calls_elsewhere = []

        def guard_matches(test):
            names = {n.id for n in ast.walk(test) if isinstance(n, ast.Name)}
            return {"auto_apply", "switches", "needs_confirm"} <= names

        def walk(node, guarded):
            for child in ast.iter_child_nodes(node):
                g = guarded
                if isinstance(child, ast.If) and guard_matches(child.test):
                    for sub in child.body:
                        walk(sub, True)
                    for sub in child.orelse:
                        walk(sub, guarded)
                    continue
                if isinstance(child, ast.Call):
                    fn = child.func
                    name = getattr(fn, "id", None) or getattr(fn, "attr", None)
                    if name == "transition_protect":
                        (calls_in_guarded_branch if g else calls_elsewhere).append(child)
                walk(child, g)

        walk(tree, False)
        assert calls_in_guarded_branch, "transition_protect fehlt im Apply-Zweig"
        assert not calls_elsewhere, \
            "transition_protect wird außerhalb des freigegebenen Apply-Zweigs aufgerufen"


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class TestApplyConfigsResolver:
    def _doc(self, active_config, regime=0):
        return {"id": "dyn1", "strategy_id": "trend",
                "configs": {str(regime): active_config or {}},
                "last_state": {"per_symbol": {"BTC": {
                    "regime": regime, "label": "Trend", "confidence": 80,
                    "active_config": active_config}}}}

    def _setup_state(self, monkeypatch):
        from core import state
        db = FakeDB()
        monkeypatch.setattr(state, "db", db)
        monkeypatch.setattr(dynamic_live.autotrader, "config", {}, raising=False)
        return db

    def test_apply_then_baseline_removes_stale_overrides(self, monkeypatch):
        """R02-Abnahme: A -> Baseline hinterlässt KEINE dynamischen Reste."""
        db = self._setup_state(monkeypatch)
        doc_a = self._doc({"tp1_crv": 1.5, "leverage": 7})
        applied = _run(dynamic_live.apply_configs(doc_a))
        assert applied and applied[0].get("plan_hash")
        cfg = _run(db.strategy_coin_configs.find_one({"_id": "trend_BTC"}))["config"]
        assert cfg["tp1_crv"] == 1.5 and cfg["leverage"] == 7
        assert sorted(cfg["dynamic_keys"]) == ["leverage", "tp1_crv"]

        doc_b = self._doc({}, regime=1)  # Baseline-Regime
        applied2 = _run(dynamic_live.apply_configs(doc_b))
        assert applied2[0].get("baseline") is True
        cfg2 = _run(db.strategy_coin_configs.find_one({"_id": "trend_BTC"}))["config"]
        assert "tp1_crv" not in cfg2 and "leverage" not in cfg2
        assert "dynamic_id" not in cfg2

    def test_manual_config_keys_survive(self, monkeypatch):
        db = self._setup_state(monkeypatch)
        _run(db.strategy_coin_configs.insert_one(
            {"_id": "trend_BTC", "config": {"tp_full_crv": 9.9}}))
        _run(dynamic_live.apply_configs(self._doc({"tp1_crv": 1.5})))
        _run(dynamic_live.apply_configs(self._doc({}, regime=1)))
        cfg = _run(db.strategy_coin_configs.find_one({"_id": "trend_BTC"}))["config"]
        assert cfg.get("tp_full_crv") == 9.9 and "tp1_crv" not in cfg

    def test_sub_strategy_rules_attached_to_override(self, monkeypatch):
        """R01 (transparent): Substrategie-Regeln des Regimes werden am
        Coin-Override sichtbar hinterlegt."""
        db = self._setup_state(monkeypatch)
        doc = self._doc({"tp1_crv": 1.5})
        doc["sub_strategies"] = {"0": {"rules": [{"if": "x", "then": "long"}]}}
        _run(dynamic_live.apply_configs(doc))
        cfg = _run(db.strategy_coin_configs.find_one({"_id": "trend_BTC"}))["config"]
        assert cfg["dynamic_sub_strategy"]["rules"][0]["then"] == "long"
