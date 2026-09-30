"""AP09-Solltests (Befunde T05/T07/T08/W02): Policy-/Lernprovenienz.

T05: fingerprint_schema=2 – Nicht-Sizing-Policyänderung ändert den combined.
T07: Outcome-Sync markiert erst NACH Konsumenten-Erfolg; PnL-Revision erzeugt
     neue Outcome-Version und wird exakt einmal nachkonsumiert.
T08: Wiederholte Sichtung derselben Lektion zählt ohne neue Trades nicht als
     Bestätigung; Collection getrennt von Paper/Live im Gesamtsaldo.
W02: Block-Bootstrap für korrelierte Trades.
"""
import inspect

import pytest

from services import policy_fingerprint as pf
from services import policy_promotion as pp
from services.ai_learning import aggregate_performance, outcome_version

pytestmark = pytest.mark.unit


class TestT05FingerprintSchema2:
    def test_non_sizing_config_change_changes_combined(self):
        cfg_a = {"min_confidence": 65, "fee_guard_mult": 2.5,
                 "sizing_mode": "risk"}
        cfg_b = {**cfg_a, "min_confidence": 80}
        fp_a = pf.build(prompt_hash="p", sizing_h=pf.sizing_hash(cfg_a),
                        policy_config_h=pf.policy_config_hash(cfg_a))
        fp_b = pf.build(prompt_hash="p", sizing_h=pf.sizing_hash(cfg_b),
                        policy_config_h=pf.policy_config_hash(cfg_b))
        assert fp_a["schema"] == fp_b["schema"] == 2
        # Sizing identisch, aber min_confidence anders -> NEUE Policy
        assert fp_a["sizing_hash"] == fp_b["sizing_hash"]
        assert fp_a["combined"] != fp_b["combined"]

    def test_min_confidence_not_in_sizing_hash(self):
        assert "min_confidence" not in pf.SIZING_KEYS
        assert "min_confidence" in pf.POLICY_CONFIG_KEYS
        a = pf.sizing_hash({"min_confidence": 65, "lev_fixed": 5})
        b = pf.sizing_hash({"min_confidence": 90, "lev_fixed": 5})
        assert a == b

    def test_legacy_schema1_unchanged(self):
        """Alt-Aufrufer ohne neue Teile: identischer Schema-1-combined wie
        vor AP09 (gespeicherte Alttrades bleiben stabil gruppierbar)."""
        fp = pf.build(prompt_hash="p", lessons_h="l", playbook_version="v1",
                      model="m", gate_version=3, sizing_h="s")
        assert "schema" not in fp and "config_hash" not in fp
        assert set(fp) == set(pf.PART_KEYS) | {"combined"}

    def test_regime_artifact_part_of_v2(self):
        a = pf.build(policy_config_h="c", regime_artifact="abc123")
        b = pf.build(policy_config_h="c", regime_artifact="def456")
        assert a["combined"] != b["combined"]


class TestT07OutcomeVersion:
    def test_version_changes_on_pnl_revision(self):
        t = {"realized_pnl": 5.0, "result": "win", "closed_at": "2026-01-01",
             "fees_paid": 0.1}
        v1 = outcome_version(t)
        assert v1 == outcome_version(dict(t))  # deterministisch
        v2 = outcome_version({**t, "realized_pnl": 3.2, "result": "win"})
        assert v1 != v2

    def test_sync_marks_after_consumer_success(self):
        """AST-Nachweis: ai_decisions.update_many kommt VOR dem Setzen von
        ai_learn_synced; Fehler je Trade -> continue (bleibt unsynced)."""
        from services.ai_learning import AILearning
        src = inspect.getsource(AILearning.sync_outcomes)
        tail = src[src.index('"strategy_id": "ai_trader", "status": "closed"'):]
        i_dec = tail.index("ai_decisions.update_many")
        i_mark = tail.index('"ai_learn_synced": True')
        assert i_dec < i_mark, "Trade wird vor Konsumenten-Erfolg markiert"
        assert "ai_learn_outcome_version" in tail
        assert "continue" in tail

    def test_pnl_revision_resets_sync_flag(self):
        from services.pnl_reconcile import build_updates
        t = {"realized_pnl": 5.0, "events": []}
        upd = build_updates(t, {"net_pnl": 2.0, "fee": 0.3, "funding": 0.0})
        assert upd["ai_learn_synced"] is False  # neue Outcome-Version folgt
        # Kleinst-Differenz unter MIN_DIFF: kein Reset (kein Sync-Sturm)
        upd2 = build_updates({"realized_pnl": 2.0, "events": []},
                             {"net_pnl": 2.0, "fee": 0.3, "funding": 0.0})
        assert "ai_learn_synced" not in (upd2 or {})


class TestT08Evidence:
    def test_collection_separated_from_paper_live(self):
        trades = [
            {"status": "closed", "mode": "paper", "realized_pnl": 10.0},
            {"status": "closed", "mode": "paper", "realized_pnl": 7.0,
             "data_collection": True},
            {"status": "closed", "mode": "live", "realized_pnl": -2.0},
        ]
        stats = aggregate_performance([], trades)
        assert stats["trades"]["paper"]["count"] == 1
        assert stats["trades"]["live"]["count"] == 1
        assert stats["trades"]["collect"]["count"] == 1
        assert stats["trades"]["collect"]["pnl"] == 7.0
        # Gesamtsaldo = paper+live OHNE Collection
        assert stats["totals"]["total_pnl"] == pytest.approx(8.0)

    def test_lesson_bump_requires_fresh_trades(self):
        """AST-Nachweis: erneuter Kandidaten-Bump zählt nur mit genügend
        NEUEN geschlossenen Trades seit der letzten Bestätigung."""
        from services.ai_learning import AILearning
        src = inspect.getsource(AILearning._bump_lesson_candidate)
        assert "lesson_evidence_min_trades" in src
        assert "evidence_ts" in src and "count_documents" in src


class TestW02BlockBootstrap:
    def test_block_bootstrap_wider_than_iid_for_correlated_data(self):
        """Stark autokorrelierte Serien: Block-Untergrenze ist konservativer
        (kleiner) als das unabhängige Einzeltrade-Resampling."""
        cand = ([1.0] * 10 + [-1.0] * 10) * 3   # Blöcke gleicher Ergebnisse
        champ = [0.0] * 30
        iid = pp.bootstrap_diff_lower(cand, champ, 800, 0.05, seed=7, block=1)
        blk = pp.bootstrap_diff_lower(cand, champ, 800, 0.05, seed=7, block=10)
        assert blk < iid
        assert pp.bootstrap_diff_lower([], champ) == float("-inf")

    def test_promotion_check_uses_block_default(self):
        assert pp.DEFAULTS["bootstrap_block"] == 5
        src = inspect.getsource(pp.promotion_check)
        assert "bootstrap_block" in src
        # Deterministisch bei gleichem Seed/Config
        cand = [0.5, 0.7, -0.2, 0.9, 0.4] * 10
        champ = [0.1, -0.1, 0.0, 0.2] * 10
        r1 = pp.promotion_check(cand, champ)
        r2 = pp.promotion_check(cand, champ)
        assert r1["metrics"]["bootstrap_lower"] == r2["metrics"]["bootstrap_lower"]
