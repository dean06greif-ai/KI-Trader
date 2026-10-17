"""Regressionstests für die Prüfbericht-Fixes (Branch-Basis conflict_160926_0902):

1. R01: Discovery-Regeln/Regel-Varianten werden TATSÄCHLICH umgeschaltet
   (strategy_plan.rules_override -> custom_params.apply_params -> Scanner).
2. R09: 'Freigeben' kann fehlende Testnachweise nicht mehr still umgehen.
3. R13: Parallele Bestätigungen können nicht doppelt durchlaufen (DB-CAS).
4. AP07: Kombi-Kalibrierung – Holdout beeinflusst die Kandidatenauswahl
   nicht mehr über den Dauer-Term.
5. R02+: Persönliche Basisparameter werden beim Zurückschalten
   wiederhergestellt statt gelöscht.
6. Kleine Fixes: Beweispaket akzeptiert keinen fremden Coin-Test, bewertet den
   passenden Testbereich konsistent, kennzeichnet fehlenden Sicherheitsstatus.

Alle Tests sind offline (reine Funktionen bzw. simulierte DB-Gegenstellen).
"""
import asyncio

from services import strategy_plan, strategy_release
from services.regime_lab import _train_segment_days
from services.research_validation import walkforward_status_for
from strategies import custom_params


# ---------------- 5. Basisparameter-Restore (merge_overrides) ----------------
class TestMergeOverridesRestore:
    def test_personal_value_restored_after_dynamic_removed(self):
        base = {"leverage": 3, "tp_pct": 1.5}
        step1 = strategy_plan.merge_overrides(base, {"leverage": 10})
        assert step1["leverage"] == 10
        assert step1["dynamic_prev"] == {"leverage": 3}
        # Regime ohne Override -> persönlicher Basiswert kommt zurück
        step2 = strategy_plan.merge_overrides(step1, {})
        assert step2["leverage"] == 3
        assert step2["tp_pct"] == 1.5
        assert "dynamic_prev" not in step2

    def test_snapshot_survives_regime_chain(self):
        base = {"leverage": 3}
        a = strategy_plan.merge_overrides(base, {"leverage": 10})
        b = strategy_plan.merge_overrides(a, {"leverage": 20})
        assert b["dynamic_prev"] == {"leverage": 3}
        back = strategy_plan.merge_overrides(b, {})
        assert back["leverage"] == 3

    def test_pure_dynamic_key_still_removed(self):
        step1 = strategy_plan.merge_overrides({}, {"sl_pct": 2.0})
        step2 = strategy_plan.merge_overrides(step1, {})
        assert "sl_pct" not in step2

    def test_params_restored(self):
        base = {"params": {"rsi_period": 21}}
        a = strategy_plan.merge_overrides(base, {}, params={"rsi_period": 7})
        assert a["params"]["rsi_period"] == 7
        assert a["dynamic_prev_params"] == {"rsi_period": 21}
        b = strategy_plan.merge_overrides(a, {}, params={})
        assert b["params"]["rsi_period"] == 21


# ---------------- 1. Regel-Umschaltung (Discovery / Varianten) ----------------
_L_RULE = {"indicator": "rsi", "op": "<", "value": 30}
_S_RULE = {"indicator": "rsi", "op": ">", "value": 70}


class TestRulesSwitching:
    def _doc(self, **kw):
        return {"id": "dyn_1", "strategy_id": "cust_1", "configs": {"0": {}},
                **kw}

    def test_discovery_definition_becomes_rules_override(self):
        doc = self._doc(sub_strategies={"0": {
            "rules": ["RSI < 30"],
            "definition": {"long_rules": [_L_RULE], "short_rules": [_S_RULE]}}})
        plan = strategy_plan.resolve_symbol_plan(doc, "BTCUSDT", {"regime": 0})
        assert plan["rules_override"] == {"long_rules": [_L_RULE],
                                          "short_rules": [_S_RULE]}

    def test_rule_variant_becomes_additive_override(self):
        doc = self._doc(rule_variants={"0": {"rule_label": "ADX > 25",
                                             "rule_long": {"indicator": "adx",
                                                           "op": ">", "value": 25},
                                             "rule_short": None}})
        plan = strategy_plan.resolve_symbol_plan(doc, "BTCUSDT", {"regime": 0})
        assert plan["rules_override"]["add_long_rules"] == [
            {"indicator": "adx", "op": ">", "value": 25}]
        assert plan["rules_override"]["add_short_rules"] == []

    def test_no_override_without_sub_or_variant(self):
        plan = strategy_plan.resolve_symbol_plan(self._doc(), "BTCUSDT",
                                                 {"regime": 0})
        assert plan["rules_override"] is None

    def test_plan_hash_changes_with_rules(self):
        base = self._doc()
        with_sub = self._doc(sub_strategies={"0": {
            "rules": ["x"], "definition": {"long_rules": [_L_RULE]}}})
        p1 = strategy_plan.resolve_symbol_plan(base, "BTCUSDT", {"regime": 0})
        p2 = strategy_plan.resolve_symbol_plan(with_sub, "BTCUSDT", {"regime": 0})
        assert p1["plan_hash"] != p2["plan_hash"]

    def test_apply_params_replaces_rules(self):
        definition = {"id": "cust_1", "long_rules": [{"indicator": "price",
                                                      "op": ">", "value": 1}],
                      "short_rules": []}
        out = custom_params.apply_params(definition, {"rules_override": {
            "long_rules": [_L_RULE], "short_rules": [_S_RULE]}})
        assert out["long_rules"] == [_L_RULE]
        assert out["short_rules"] == [_S_RULE]
        # Original bleibt unverändert (reine Funktion)
        assert definition["long_rules"][0]["indicator"] == "price"

    def test_apply_params_additive_rules(self):
        definition = {"id": "cust_1", "long_rules": [_L_RULE], "short_rules": []}
        out = custom_params.apply_params(definition, {"rules_override": {
            "add_long_rules": [{"indicator": "adx", "op": ">", "value": 25}]}})
        assert len(out["long_rules"]) == 2
        assert out["long_rules"][1]["indicator"] == "adx"


# ---------------- 2. Freigabe-Gate (R09) ----------------
class TestApproveGate:
    def test_draft_has_missing_evidence(self):
        doc = {"id": "d", "strategy_id": "s", "configs": {}}
        doc["release"] = strategy_release.initial_release(doc, None)
        assert strategy_release.approval_evidence_missing(doc) is not None

    def test_validated_has_no_missing_evidence(self):
        doc = {"id": "d", "strategy_id": "s", "configs": {}}
        doc["release"] = strategy_release.initial_release(
            doc, {"dynamic_better": True})
        assert strategy_release.approval_evidence_missing(doc) is None

    def test_stale_after_definition_change(self):
        doc = {"id": "d", "strategy_id": "s", "configs": {}}
        doc["release"] = strategy_release.initial_release(
            doc, {"dynamic_better": True})
        doc["configs"] = {"0": {"leverage": 5}}  # Definition geändert
        assert "geändert" in strategy_release.approval_evidence_missing(doc)

    def test_approve_records_missing_evidence(self):
        doc = {"id": "d", "strategy_id": "s", "configs": {}}
        doc["release"] = strategy_release.initial_release(doc, None)
        rel = strategy_release.approve(doc, actor="admin", note="bewusst",
                                       missing_evidence="kein Walkforward")
        assert rel["approved_without_evidence"] is True
        assert rel["missing_evidence"] == "kein Walkforward"

    def test_approve_with_evidence_clean(self):
        doc = {"id": "d", "strategy_id": "s", "configs": {}}
        doc["release"] = strategy_release.initial_release(
            doc, {"dynamic_better": True})
        rel = strategy_release.approve(doc, actor="admin")
        assert "approved_without_evidence" not in rel


# ---------------- 3. Confirm-CAS (R13) ----------------
class _FakeDynColl:
    """Simulierte dynamic_strategies-Collection: find_one_and_update mit
    Mongo-Semantik (atomarer Match+Update) für den Claim-Test."""

    def __init__(self, doc):
        self.doc = doc

    async def find_one_and_update(self, q, u):
        d = self.doc
        if d is None or d.get("id") != q.get("id"):
            return None
        p = d.get("pending_switch") or {}
        if p.get("at") != q.get("pending_switch.at"):
            return None
        if "pending_switch.command_id" in q \
                and p.get("command_id") != q["pending_switch.command_id"]:
            return None
        if "claimed_at" in p:
            return None
        p["claimed_at"] = u["$set"]["pending_switch.claimed_at"]
        d["pending_switch"] = p
        return dict(d)


class TestConfirmClaim:
    def test_query_binds_command_id_and_at(self):
        q = strategy_plan and None  # noqa: F841 – nur Lesbarkeit
        from services import dynamic_live
        pending = {"command_id": "cmd_1", "at": "2026-01-01T00:00:00+00:00"}
        query = dynamic_live.pending_claim_query("dyn_1", pending)
        assert query["pending_switch.command_id"] == "cmd_1"
        assert query["pending_switch.at"] == pending["at"]
        assert query["pending_switch.claimed_at"] == {"$exists": False}

    def test_second_claim_rejected(self):
        from core import state
        from services import dynamic_live
        pending = {"command_id": "cmd_1", "at": "2026-01-01T00:00:00+00:00"}
        doc = {"id": "dyn_1", "pending_switch": dict(pending)}

        class _Db:
            dynamic_strategies = _FakeDynColl(doc)

        old = state.db
        state.db = _Db()
        try:
            first = asyncio.run(dynamic_live.claim_pending_switch("dyn_1", pending))
            second = asyncio.run(dynamic_live.claim_pending_switch("dyn_1", pending))
        finally:
            state.db = old
        assert first is True
        assert second is False

    def test_replaced_pending_rejected(self):
        from core import state
        from services import dynamic_live
        doc = {"id": "dyn_1", "pending_switch": {"command_id": "cmd_NEW",
                                                 "at": "2026-01-02T00:00:00+00:00"}}

        class _Db:
            dynamic_strategies = _FakeDynColl(doc)

        old = state.db
        state.db = _Db()
        try:
            ok = asyncio.run(dynamic_live.claim_pending_switch(
                "dyn_1", {"command_id": "cmd_OLD",
                          "at": "2026-01-01T00:00:00+00:00"}))
        finally:
            state.db = old
        assert ok is False


# ---------------- 4. Kombi-Kalibrierung ohne Holdout-Leck ----------------
class TestTrainSegmentDays:
    BPD = 96.0  # 15m

    def test_holdout_segments_excluded(self):
        segs = [{"to_ts": 100, "bars": 96}, {"to_ts": 200, "bars": 192},
                {"to_ts": 900, "bars": 960}]  # letztes Segment liegt im Holdout
        d = _train_segment_days(segs, train_end_ts=250, bpd=self.BPD)
        assert d == (96 + 192) / 2 / self.BPD

    def test_without_bound_uses_all(self):
        segs = [{"to_ts": 100, "bars": 96}]
        assert _train_segment_days(segs, None, self.BPD) == 96 / self.BPD

    def test_no_train_segments_returns_none(self):
        segs = [{"to_ts": 900, "bars": 960}]
        assert _train_segment_days(segs, 250, self.BPD) is None

    def test_empty(self):
        assert _train_segment_days([], 250, self.BPD) is None


# ---------------- 6. Beweispaket-Kleinfixes ----------------
class TestEvidenceSmallFixes:
    def test_foreign_coin_test_rejected(self):
        doc = {"symbols": ["ETHUSDT"]}
        analysis = {"walkforward": {"coin:BTCUSDT": {"verdict": {"dynamic_better": True}}}}
        key, wf = strategy_release._wf_for_doc(doc, analysis)
        assert key is None and wf is None

    def test_own_coin_test_accepted(self):
        doc = {"symbols": ["ETHUSDT"]}
        analysis = {"walkforward": {"coin:ETHUSDT": {"verdict": {"dynamic_better": True}}}}
        key, _ = strategy_release._wf_for_doc(doc, analysis)
        assert key == "coin:ETHUSDT"

    def test_walkforward_status_for_selected_scope(self):
        analysis = {"walkforward": {
            "combined": {"verdict": {"dynamic_better": True},
                         "created_at": "2026-01-02T00:00:00"},
            "coin:XRPUSDT": {"verdict": {"dynamic_better": False},
                             "created_at": "2026-01-02T00:00:00"}},
            "assignments": {"coin:XRPUSDT:trend": {"assigned_at": "2026-01-03T00:00:00"}}}
        st = walkforward_status_for(analysis, "combined")
        assert st["passed"] is True and st["stale"] is False
        st2 = walkforward_status_for(analysis, "coin:XRPUSDT")
        assert st2["passed"] is False and st2["stale"] is True

    def test_missing_safety_is_flagged(self):
        doc = {"id": "d", "strategy_id": "s", "configs": {},
               "settings": {}}
        doc["release"] = strategy_release.initial_release(
            doc, {"dynamic_better": True})
        bundle = strategy_release.evidence_bundle(doc, analysis=None, safety=None)
        assert bundle["runtime_health"] == {"level": "unknown"}
        assert any("Sicherheitsstatus unbekannt" in w for w in bundle["warnings"])

    def test_approved_without_evidence_visible_in_bundle(self):
        doc = {"id": "d", "strategy_id": "s", "configs": {}, "settings": {}}
        doc["release"] = strategy_release.approve(
            {**doc, "release": strategy_release.initial_release(doc, None)},
            actor="admin", note="x", missing_evidence="kein Walkforward")
        bundle = strategy_release.evidence_bundle(doc, analysis=None,
                                                  safety={"level": "ok"})
        assert any("OHNE Testnachweis" in b for b in bundle["blockers"])
