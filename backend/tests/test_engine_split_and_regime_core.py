"""Regressionstests: Engine-Aufteilung (Mixins) + Regime-Kern-Fassade.

Der Split von services/ai_engine.py in Context-/Governance-/Housekeeping-Mixins
darf NICHTS am öffentlichen Verhalten ändern: gleiche Klasse, gleiche Methoden,
gleiche Modul-Konstanten. regime_core bündelt die Regime-Familie unverändert.
"""


def test_ai_engine_class_has_all_moved_methods():
    from services.ai_engine import AIEngine
    moved = [
        # Context-Mixin
        "_user_directives", "_context_brief", "_macro_block", "_liquidity_block",
        "_capital_risk_block", "_analysis_extra_blocks", "_open_trades_text",
        "_strategy_performance_text", "_role_context_block",
        # Governance-Mixin
        "_tuning_guard", "_handle_config_changes", "review_parked_proposals",
        "actionable_proposals", "decide_proposal", "list_proposals",
        "comment_on_user_change", "_apply_changes", "_macro_gate",
        "_normalize_auto_tuned", "_close_proposal",
        # Housekeeping-Mixin
        "_run_housekeeping", "_daily_reset", "force_daily_summary",
        "_collect_daily_facts", "_statistical_summary", "_cleanup_old_analyses",
        # Kern (nicht verschoben)
        "run_analysis", "run_loop", "update_config", "chat_stream", "status",
    ]
    for name in moved:
        assert callable(getattr(AIEngine, name, None)), f"Methode fehlt: {name}"


def test_ai_engine_mixin_mro_and_reexports():
    from services import ai_engine
    from services.ai_engine_context import AIEngineContextMixin
    from services.ai_engine_governance import AIEngineGovernanceMixin
    from services.ai_engine_housekeeping import AIEngineHousekeepingMixin
    mro = ai_engine.AIEngine.__mro__
    assert AIEngineContextMixin in mro
    assert AIEngineGovernanceMixin in mro
    assert AIEngineHousekeepingMixin in mro
    # Rückwärtskompatible Modul-Konstanten (Re-Exports)
    assert "KI Trader" in ai_engine.SUMMARY_SYSTEM
    assert "stance" in ai_engine.OPINION_SYSTEM
    assert ai_engine.BERLIN_TZ is not None


def test_tuning_guard_still_blocks_correlation_guard_after_split():
    from services.ai_engine import AIEngine

    class _Stub:
        config = {}
    assert "Trader" in AIEngine._tuning_guard(_Stub(), {"correlation_guard": True})
    assert "Trader" in AIEngine._tuning_guard(_Stub(), {"correlation_guard": False})


def test_regime_core_facade():
    from services import regime_core, regime, regime_engine
    assert regime_core.detect_regimes is regime.detect_regimes
    assert regime_core.build_model is regime_engine.build_model
    assert regime_core.engine is regime_engine
    assert regime_core.regime_label(regime_core.regime_id(2, 1, 9), 9)
    # beide segments_from_labels bleiben bewusst getrennt (leicht anderes Verhalten)
    assert regime.segments_from_labels is not regime_engine.segments_from_labels
