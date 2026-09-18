"""Regressionstests Regime-Brücke – Härtung 18.09.2026:
Retention-Pinning, Brücken-Gesundheit, Label-Zuverlässigkeit (reine Funktionen)."""
from datetime import datetime, timedelta, timezone

from services import regime_bridge_health as h
from services import regime_context, retention
from services.ai_market_observer import snapshot_to_text


# ------------------------------------------------------------- Retention
def test_retention_rule_regime_analyses_has_protect():
    rule = next(r for r in retention.DEFAULT_POLICY if r["coll"] == "regime_analyses")
    assert rule["protect"] == "regime_analyses" and rule["keep_last"] == 8


def test_pinned_ids_release_and_dynamic_base():
    analyses = [{"id": "a1", "release": {"stage": "shadow"}}, {"id": "a2", "release": {"stage": "none"}},
                {"id": "a3", "release": {"stage": "active"}}, {"id": "a4"}]
    dyn = [{"settings": {"analysis_id": "a4"}}, {"settings": {"analysis_id": "a2"}, "archived": True},
           {"settings": {}}]
    assert retention.pinned_analysis_ids(analyses, dyn) == {"a1", "a3", "a4"}


def test_merged_policy_keeps_protect_key():
    pol = {p["coll"]: p for p in retention.merged_policy({"regime_analyses": {"keep_last": 5}})}
    assert pol["regime_analyses"]["protect"] == "regime_analyses"
    assert pol["regime_analyses"]["keep_last"] == 5


# ------------------------------------------------------------- Health
def _dyn(aid="ra_1", auto=False, checked_days=None, archived=False, **kw):
    now = datetime.now(timezone.utc)
    d = {"id": kw.get("id", "dyn_1"), "name": kw.get("name", "Dyn"), "archived": archived,
         "settings": {"analysis_id": aid, "auto_check_enabled": auto}}
    if checked_days is not None:
        d["last_state"] = {"checked_at": (now - timedelta(days=checked_days)).isoformat()}
    return d


def test_health_orphan_and_idle_detected():
    checks = {c["name"]: c for c in h.evaluate(
        [_dyn(aid="ra_gone", auto=False, checked_days=50)], existing_aids={"ra_ok"},
        n_analyses=12, n_released=0, cockpit_rows=[])}
    assert checks["orphaned_dynamic"]["level"] == "warn" and checks["orphaned_dynamic"]["count"] == 1
    assert checks["dynamic_idle"]["level"] == "warn" and checks["dynamic_idle"]["items"][0]["reason"] == "Auto-Check aus"
    assert checks["no_release"]["level"] == "warn"
    assert h.overall_level(list(checks.values())) == "warn"


def test_health_archived_ignored_and_fresh_ok():
    checks = {c["name"]: c for c in h.evaluate(
        [_dyn(aid="ra_gone", archived=True), _dyn(aid="ra_ok", auto=True, checked_days=1, id="dyn_2")],
        existing_aids={"ra_ok"}, n_analyses=3, n_released=1, cockpit_rows=[])}
    assert all(c["level"] == "ok" for c in checks.values())
    assert h.notify_text(list(checks.values())) == ""


def test_health_stale_check_and_low_hit():
    rows = [{"symbol": "ETHUSDT", "observer_hit_pct": 48.0, "observer_n": 900, "observer_reliable": True},
            {"symbol": "BTCUSDT", "observer_hit_pct": 51.0, "observer_n": 900, "observer_reliable": True},
            {"symbol": "X", "observer_hit_pct": 10.0, "observer_n": 2, "observer_reliable": False}]
    checks = {c["name"]: c for c in h.evaluate(
        [_dyn(aid="ra_ok", auto=True, checked_days=9)], existing_aids={"ra_ok"},
        n_analyses=3, n_released=1, cockpit_rows=rows)}
    assert checks["dynamic_idle"]["level"] == "warn" and "9.0" in checks["dynamic_idle"]["items"][0]["reason"]
    assert checks["observer_low_hit"]["level"] == "info"
    assert [i["symbol"] for i in checks["observer_low_hit"]["items"]] == ["ETHUSDT"]
    txt = h.notify_text(list(checks.values()))
    assert txt.startswith("⚠️") and "Regime-Cockpit" in txt and "Kurzfrist" not in txt


def test_health_no_analyses_is_ok():
    checks = {c["name"]: c for c in h.evaluate([], set(), 0, 0, [])}
    assert checks["no_release"]["level"] == "ok"
    assert h.overall_level(list(checks.values())) == "ok"


# ------------------------------------------------------------- Label-Zuverlässigkeit
def test_annotate_label_marks_unreliable_and_reliable():
    low = {"observer_hit_pct": 48.0, "observer_reliable": True}
    ok = {"observer_hit_pct": 58.0, "observer_reliable": True}
    assert "unzuverlässig" in regime_context.annotate_label("range_volatil", low)
    assert regime_context.annotate_label("trend_up_normal", ok) == "trend_up_normal (Trefferquote 58 %)"
    assert regime_context.annotate_label("range_ruhig", None) == "range_ruhig"
    assert regime_context.annotate_label("range_ruhig", {"observer_hit_pct": 30, "observer_reliable": False}) == "range_ruhig"


def test_snapshot_to_text_default_unchanged():
    snap = {"symbol": "BTCUSDT", "features": {"regime": "range_ruhig", "rsi": 50, "trend_pct": 0.1,
                                              "volatility_pct": 0.2, "atr_pct": 0.3, "volume_ratio": 1.0,
                                              "range_pos": 40}}
    assert snapshot_to_text(snap).startswith("BTCUSDT: range_ruhig | RSI 50")
    assert snapshot_to_text(snap, "range_ruhig (Trefferquote 60 %)").startswith("BTCUSDT: range_ruhig (Trefferquote 60 %) | RSI")
