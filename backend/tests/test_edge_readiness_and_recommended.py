"""Live-Freigabe-Check, empfohlene Einstellungen, Wochen-Edge – reine Regressionstests."""
import asyncio

from services import edge_filter as ef
from services import ai_recommended_settings as rec
from services import edge_weekly as ew


def _t(i, pnl, ok=True):
    return {"opened_at": f"2026-11-{1 + i // 24:02d}T{i % 24:02d}:00", "realized_pnl": pnl,
            "risk_usdt": 10, "edge_filter": {"ok": ok}}


def test_readiness_empty():
    out = ef.readiness([])
    assert out["trades"] == 0 and out["ready"] is False and "startet" in out["text"]


def test_readiness_ready_only_with_enough_positive_trades():
    rows = [_t(i, 4 if i % 2 else -1) for i in range(60)]  # Ø +0,15 R
    assert ef.readiness(rows)["ready"] is True
    assert ef.readiness(rows[:40])["ready"] is False                # zu wenige Trades
    neg_recent = [_t(i, 5) for i in range(30)] + [_t(30 + i, -1) for i in range(30)]
    out = ef.readiness(neg_recent)
    assert out["ready"] is False and "jüngere Hälfte" in out["text"]


def test_readiness_ignores_failed_or_unmarked_trades():
    rows = [_t(i, 5, ok=False) for i in range(80)] + [{"realized_pnl": 5, "risk_usdt": 10}]
    assert ef.readiness(rows)["trades"] == 0


def test_recommended_diff_and_updates():
    cfg = {"live_gate_bypass_enabled": True, "edge_filter_enabled": True, "fee_guard_mult": 3.5,
           "fee_guard_atr_mult": 4, "guard_autotune_enabled": True, "maker_policy": "setup"}
    d = {i["key"]: i for i in rec.diff(cfg)}
    assert d["live_gate_bypass_enabled"]["differs"] and not d["edge_filter_enabled"]["differs"]
    assert not d["fee_guard_atr_mult"]["differs"]                   # 4 == 4.0
    upd = rec.updates(cfg)
    assert upd == {"live_gate_bypass_enabled": False, "fee_guard_mult": 6.0,
                   "guard_autotune_enabled": False, "maker_policy": "all",
                   "ai_role": "pruefer", "structure_stop_enabled": True}


def test_recommended_values_survive_engine_validation():
    from services.ai_engine import AIEngine
    eng = AIEngine.__new__(AIEngine)
    calls = {}

    async def fake_update(u):
        calls.update(u)
        eng.config.update(u)
        return eng.config
    eng.config = {"fee_guard_mult": 3.5}
    eng.update_config = fake_update
    res = asyncio.run(rec.apply(eng))
    assert res["applied"] and all(not i["differs"] for i in res["items"])
    assert calls["fee_guard_mult"] == 6.0


def test_recommended_values_within_ui_and_backend_ranges():
    vals = {r["key"]: r["value"] for r in rec.RECOMMENDED}
    assert vals["fee_guard_mult"] in [1.5, 2, 2.5, 3, 3.5, 4, 5, 6, 8, 10]   # Panel-Optionen
    assert vals["fee_guard_atr_mult"] in [0, 1.5, 2, 2.5, 3, 4, 6, 8]
    assert vals["maker_policy"] in ("setup", "all")


def test_weekly_format_report():
    week = {"overall": {"with_risk": 12, "exp_r": -0.1, "gross_r": 0.05, "fee_r": 0.15, "pnl": -20,
                        "tp1_rate": 25, "tp1_breakeven": 30},
            "live": {"with_risk": 2, "exp_r": 0.3, "pnl": 4},
            "by_edge_filter": {"besteht": {"with_risk": 4, "exp_r": 0.2}}}
    txt = ew.format_report(week, {"text": "Noch nicht bereit: 56 Trades fehlen."}, {"with_risk": 3, "exp_r": -0.5})
    assert "12" in txt and "Live: 2" in txt and "besteht: 4" in txt and "Schatten" in txt
    assert "56 Trades fehlen" in txt
    assert "Keine geschlossenen" in ew.format_report({}, {}, {})


def test_weekly_defaults_and_catalog():
    from services import notifications
    assert ew.DEFAULTS["weekday"] == 6 and ew.DEFAULTS["enabled"] is True
    assert any(k == "edge_weekly" for k, *_ in notifications.NOTIFY_CATALOG)


def test_policy_fingerprint_tracks_edge_filter():
    from services import policy_fingerprint as pf
    assert "edge_filter_enabled" in pf.POLICY_CONFIG_KEYS


def test_bucket_prefers_entry_marker():
    t = {"risk_usdt": 10, "fees_paid": 5, "side": "LONG", "symbol": "OIL", "edge_filter": {"ok": True}}
    assert ef.bucket_of_trade(t) == "besteht"
    t["edge_filter"] = {"ok": False}
    assert ef.bucket_of_trade(t) == "besteht nicht"


def test_weekly_set_config_rejects_garbage():
    import pytest

    class _DB:
        class settings:  # noqa: N801
            @staticmethod
            async def update_one(*a, **k):
                return None

            @staticmethod
            async def find_one(*a, **k):
                return {}
    with pytest.raises(ValueError):
        asyncio.run(ew.set_config(_DB, {"hour": "abc"}))


def test_manual_send_does_not_touch_schedule_stamp(monkeypatch):
    seen = {}

    class _Settings:
        @staticmethod
        async def update_one(q, upd, upsert=False):
            seen.update(upd["$set"])

    class _DB:
        settings = _Settings

    async def fake_text(db, cfg):
        return "x"

    async def fake_notify(*a, **k):
        return False
    monkeypatch.setattr(ew, "build_text", fake_text)
    from services import notifications
    monkeypatch.setattr(notifications, "telegram_notify", fake_notify)
    asyncio.run(ew.send_report(_DB, {}, trigger="manual"))
    assert "last_manual_sent" in seen and "last_sent" not in seen
