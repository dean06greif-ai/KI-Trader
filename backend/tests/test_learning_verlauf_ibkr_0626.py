"""06/2026: Verlauf-Welten global, IBKR-Website-Toggle, Lernen aus verpassten/geblockten Trades."""
from services import ai_move_scanner as ms
from services import guard_shadow, notifications, setup_world_stats


def test_world_match_without_class_has_no_symbol_filter():
    m = setup_world_stats.build_match(None, {"pullback": "2026-11-01T00:00:00+00:00"}, 30)
    assert "symbol" not in m and m["strategy_id"] == "ai_trader"
    assert any(w.get("setup") == "pullback" for w in m["$or"])


def test_ibkr_website_toggle_in_catalog():
    keys = [c[0] for c in notifications.NOTIFY_CATALOG]
    assert "website_ibkr_gateway" in keys and "ibkr_gateway" in keys


def test_guard_shadow_paper_default_on():
    assert guard_shadow.DEFAULTS["guard_shadow_paper_enabled"] is True


def test_blocker_of_priorities():
    shadow_trade = {"side": "LONG", "guard_shadow": {"guard": "fee_guard", "reason": "SL zu eng"}}
    assert ms.blocker_of(None, shadow_trade, "LONG")["name"] == "fee_guard"
    assert ms.blocker_of({"action": "LONG", "pipeline_reject": "Anti-Stacking"}, None, "LONG")["kind"] == "guard"
    assert ms.blocker_of({"action": "LONG", "live_gate": "nicht reif"}, None, "LONG")["kind"] == "gate"
    assert ms.blocker_of({"action": "SHORT", "signaled": True}, None, "LONG")["kind"] == "wrong_side"
    assert ms.blocker_of({"action": "LONG", "confidence": 55}, None, "LONG")["kind"] == "low_conf"
    assert ms.blocker_of({"action": "HOLD", "reason": "unklar"}, None, "LONG")["kind"] == "hold"
    assert ms.blocker_of(None, None, "LONG") is None


def test_classify_wrong_side_and_execution_gap_with_blocker():
    b = {"kind": "gate", "name": "live_gate", "detail": "Setup nicht live-reif"}
    w = ms.classify(False, [], False, "LONG", blocker=b, wrong_side=True)
    assert w["kind"] == "wrong_side" and w["missed"] is True
    g = ms.classify(False, [{"setup": "pullback", "side": "LONG"}], False, "LONG", blocker=b)
    assert g["kind"] == "execution_gap" and "live_gate" in g["missed_reason"] and g["blocker"] == b
    # Alt-Aufruf (ohne neue Argumente) bleibt gültig
    assert ms.classify(True, [], False, "LONG")["kind"] == "caught"


def test_summarize_and_prompt_line():
    ev = [{"kind": "caught"}, {"kind": "execution_gap", "blocker": {"name": "fee_guard"},
                               "analysis": {"setup_match": "pullback"}},
          {"kind": "execution_gap", "blocker": {"name": "fee_guard"}, "analysis": {"setup_match": "pullback"}},
          {"kind": "wrong_side", "blocker": {"name": "decision"}}, {"kind": "setup_gap"}]
    s = ms.summarize_moves(ev)
    assert s["total"] == 5 and s["blockers"]["fee_guard"] == 2 and s["setups"]["pullback"] == 2
    line = ms.prompt_line(s, 14)
    assert "VERPASSTE BEWEGUNGEN" in line and "fee_guard 2×" in line and "gegen die Bewegung 1" in line
    assert ms.prompt_line({"total": 0}, 14) == ""
