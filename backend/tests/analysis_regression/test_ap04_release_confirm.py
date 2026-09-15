"""AP04-Solltests (Befunde R04/R09/R13, T04): Release-Status, CAS-Confirm,
Apply-Gate, Archivieren mit scoped Unapply und fail-closed Setup-Live-Gate."""
import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from routers import dynamic as dyn_router
from services import strategy_release as rel

from _fakes import FakeDB

pytestmark = pytest.mark.unit


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _doc(**over):
    base = {"id": "dyn1", "strategy_id": "trend", "symbols": ["BTCUSDT"],
            "timeframe": "5m", "configs": {"0": {"tp1_crv": 1.5}},
            "fallback_config": {}, "model": {"regimes": [{"id": 0, "label": "Trend"}]},
            "last_state": {"per_symbol": {"BTCUSDT": {"regime": 0, "label": "Trend"}}}}
    base.update(over)
    return base


class TestReleaseModel:
    def test_initial_release_draft_without_verdict(self):
        r = rel.initial_release(_doc(), None)
        assert r["status"] == "draft" and r["revision"] == 1

    def test_initial_release_validated_with_passed_verdict(self):
        r = rel.initial_release(_doc(), {"dynamic_better": True})
        assert r["status"] == "validated"
        assert r["fingerprint"] == rel.definition_fingerprint(_doc())

    def test_failed_verdict_stays_draft(self):
        assert rel.initial_release(_doc(), {"dynamic_better": False})["status"] == "draft"

    def test_definition_change_makes_validation_stale(self):
        doc = _doc()
        doc["release"] = rel.initial_release(doc, {"dynamic_better": True})
        assert rel.effective_status(doc) == "validated"
        doc["configs"] = {"0": {"tp1_crv": 9.9}}
        assert rel.effective_status(doc) == "stale"
        assert rel.activation_block_reason(doc)

    def test_legacy_docs_without_release_are_not_gated(self):
        doc = _doc()  # kein 'release'-Feld = Bestand von vor AP04
        assert rel.effective_status(doc) == "legacy"
        assert rel.activation_block_reason(doc) is None

    def test_approve_clears_block_and_binds_current_fingerprint(self):
        doc = _doc()
        doc["release"] = rel.initial_release(doc, None)
        assert rel.activation_block_reason(doc)
        doc["release"] = rel.approve(doc, actor="admin", note="ok")
        assert rel.effective_status(doc) == "approved"
        assert rel.activation_block_reason(doc) is None
        doc["symbols"] = ["ETHUSDT"]  # Edit nach Freigabe -> stale
        assert rel.effective_status(doc) == "stale"

    def test_revised_release_same_definition_keeps_release(self):
        doc = _doc()
        prev = rel.approve(doc, actor="admin")
        assert rel.revised_release(prev, doc) == prev

    def test_revised_release_changed_definition_bumps_revision(self):
        doc = _doc()
        prev = rel.approve(doc, actor="admin")
        doc2 = _doc(configs={"0": {"tp1_crv": 3.0}})
        r = rel.revised_release(prev, doc2)
        assert r["status"] == "draft" and r["revision"] == 2


def _pending(version, expired=False, cmd="cmd_abc"):
    exp = datetime.now(timezone.utc) + timedelta(hours=-1 if expired else 24)
    return {"command_id": cmd, "at": "2026-01-01T00:00:00+00:00",
            "expires_at": exp.isoformat(), "observed_version": version,
            "switched_symbols": [], "per_symbol": {}}


class TestConfirmCAS:
    def _setup(self, monkeypatch, doc):
        from core import state
        db = FakeDB()
        _run(db.dynamic_strategies.insert_one(doc))
        monkeypatch.setattr(state, "db", db)
        applied_calls = []

        async def fake_apply(d):
            applied_calls.append(d["id"])
            return [{"symbol": "BTCUSDT"}]

        monkeypatch.setattr(dyn_router.dynamic_live, "apply_active", fake_apply)
        return db, applied_calls

    def test_valid_confirm_applies_and_clears_pending(self, monkeypatch):
        doc = _doc()
        doc["pending_switch"] = _pending(rel.state_version(doc["last_state"]))
        db, calls = self._setup(monkeypatch, doc)
        res = _run(dyn_router.dynamic_confirm("dyn1", {"command_id": "cmd_abc"}, True))
        assert res["status"] == "success" and calls == ["dyn1"]
        row = _run(db.dynamic_strategies.find_one({"id": "dyn1"}))
        assert "pending_switch" not in row
        assert row["application_status"]["status"] == "applied"
        # Doppelte Bestätigung: kein zweites Apply
        with pytest.raises(HTTPException) as e:
            _run(dyn_router.dynamic_confirm("dyn1", {"command_id": "cmd_abc"}, True))
        assert e.value.status_code == 400 and calls == ["dyn1"]

    def test_stale_confirm_after_new_observation_conflicts(self, monkeypatch):
        doc = _doc()
        doc["pending_switch"] = _pending("veraltete_version")
        db, calls = self._setup(monkeypatch, doc)
        with pytest.raises(HTTPException) as e:
            _run(dyn_router.dynamic_confirm("dyn1", None, True))
        assert e.value.status_code == 409 and not calls

    def test_wrong_command_id_conflicts(self, monkeypatch):
        doc = _doc()
        doc["pending_switch"] = _pending(rel.state_version(doc["last_state"]))
        db, calls = self._setup(monkeypatch, doc)
        with pytest.raises(HTTPException) as e:
            _run(dyn_router.dynamic_confirm("dyn1", {"command_id": "cmd_other"}, True))
        assert e.value.status_code == 409 and not calls

    def test_expired_pending_conflicts_and_is_cleared(self, monkeypatch):
        doc = _doc()
        doc["pending_switch"] = _pending(rel.state_version(doc["last_state"]),
                                         expired=True)
        db, calls = self._setup(monkeypatch, doc)
        with pytest.raises(HTTPException) as e:
            _run(dyn_router.dynamic_confirm("dyn1", None, True))
        assert e.value.status_code == 409 and not calls
        row = _run(db.dynamic_strategies.find_one({"id": "dyn1"}))
        assert "pending_switch" not in row

    def test_draft_release_blocks_confirm(self, monkeypatch):
        doc = _doc()
        doc["release"] = rel.initial_release(doc, None)
        doc["pending_switch"] = _pending(rel.state_version(doc["last_state"]))
        db, calls = self._setup(monkeypatch, doc)
        with pytest.raises(HTTPException) as e:
            _run(dyn_router.dynamic_confirm("dyn1", None, True))
        assert e.value.status_code == 409 and not calls

    def test_apply_error_recorded_as_failed_and_retryable(self, monkeypatch):
        doc = _doc()
        doc["pending_switch"] = _pending(rel.state_version(doc["last_state"]))
        db, _ = self._setup(monkeypatch, doc)

        async def boom(d):
            raise RuntimeError("kaputt")

        monkeypatch.setattr(dyn_router.dynamic_live, "apply_active", boom)
        with pytest.raises(HTTPException) as e:
            _run(dyn_router.dynamic_confirm("dyn1", None, True))
        assert e.value.status_code == 400
        row = _run(db.dynamic_strategies.find_one({"id": "dyn1"}))
        assert row["application_status"]["status"] == "failed"
        assert row.get("pending_switch"), "Vorschlag bleibt für Retry erhalten"


class TestApplyGateAndArchive:
    def _setup(self, monkeypatch, doc):
        from core import state
        db = FakeDB()
        _run(db.dynamic_strategies.insert_one(doc))
        monkeypatch.setattr(state, "db", db)
        return db

    def test_manual_apply_blocked_for_draft_then_allowed_after_approve(self, monkeypatch):
        doc = _doc()
        doc["release"] = rel.initial_release(doc, None)
        db = self._setup(monkeypatch, doc)

        async def fake_apply(d):
            return [{"symbol": "BTCUSDT"}]

        monkeypatch.setattr(dyn_router.dynamic_live, "apply_active", fake_apply)
        with pytest.raises(HTTPException) as e:
            _run(dyn_router.dynamic_apply("dyn1", True))
        assert e.value.status_code == 409
        res = _run(dyn_router.dynamic_approve("dyn1", {"note": "test"}, True))
        assert res["release_status"] == "approved"
        out = _run(dyn_router.dynamic_apply("dyn1", True))
        assert out["status"] == "success"

    def test_delete_archives_with_scoped_unapply_and_keeps_log(self, monkeypatch):
        doc = _doc()
        db = self._setup(monkeypatch, doc)
        _run(db.dynamic_switch_log.insert_one({"dynamic_id": "dyn1", "at": "x"}))
        unapplied = []

        async def fake_unapply(d):
            unapplied.append(d["id"])
            return {"cleaned_overrides": [], "removed_locks": 0}

        monkeypatch.setattr(dyn_router.dynamic_live, "unapply_dynamic", fake_unapply)
        res = _run(dyn_router.dynamic_delete("dyn1", True))
        assert res["archived"] is True and unapplied == ["dyn1"]
        row = _run(db.dynamic_strategies.find_one({"id": "dyn1"}))
        assert row and row.get("archived") is True
        assert _run(db.dynamic_switch_log.find_one({"dynamic_id": "dyn1"}))
        # Archivierte Strategien erscheinen nicht mehr in der Liste
        lst = _run(dyn_router.dynamic_list())
        assert all(s["id"] != "dyn1" for s in lst["strategies"])

    def test_list_exposes_release_status(self, monkeypatch):
        doc = _doc()
        doc["release"] = rel.initial_release(doc, {"dynamic_better": True})
        self._setup(monkeypatch, doc)
        lst = _run(dyn_router.dynamic_list())
        assert lst["strategies"][0]["release_status"] == "validated"


class TestSetupLiveGateFailClosed:
    def test_bypass_default_is_off(self):
        from services.ai_engine import DEFAULT_AI_CONFIG, live_gate_bypass_ok
        assert DEFAULT_AI_CONFIG["live_gate_bypass_enabled"] is False
        assert live_gate_bypass_ok(99, 65, 0, {}) is False
        assert live_gate_bypass_ok(99, 65, 0, {"live_gate_bypass_enabled": True}) is True

    def test_gate_exception_is_fail_closed(self, monkeypatch):
        """T04-Abnahme: Scheitert die Reife-Prüfung, geht der Trade in die
        Datensammlung statt ungeprüft live (kein None-Return mehr)."""
        from services import ai_engine as mod
        from core import state as core_state

        monkeypatch.setattr(core_state, "autotrader", SimpleNamespace(
            effective_mode=lambda *a, **k: "live"), raising=False)

        def boom(*a, **k):
            raise RuntimeError("Statistik nicht verfügbar")

        monkeypatch.setattr(mod.ai_playbook, "live_block_reason", boom)
        self_stub = SimpleNamespace(config={"setup_live_gate": True}, db=None)
        note = _run(mod.AIEngine._setup_live_gate(
            self_stub, {"setup": "breakout", "symbol": "BTCUSDT"}))
        assert note is not None and "fehlgeschlagen" in note

    def test_gate_disabled_still_returns_none(self):
        from services import ai_engine as mod
        self_stub = SimpleNamespace(config={"setup_live_gate": False}, db=None)
        assert _run(mod.AIEngine._setup_live_gate(
            self_stub, {"setup": "breakout", "symbol": "BTCUSDT"})) is None
