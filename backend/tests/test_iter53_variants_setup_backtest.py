"""
Iter 53: API-level integration tests for strategy rename/duplicate/variant flows
and setup backtest KI-Trader endpoints (playbook state, run/status).

Tests hit the public preview URL (REACT_APP_BACKEND_URL) and clean up
all created test strategies + reset name overrides + reset playbook state.
"""
import os
import time
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://backtest-hub-63.preview.emergentagent.com").rstrip("/")
API = f"{BASE_URL}/api"

ADMIN_USER = "Admin"
ADMIN_PASS = "Dean06Greif!/Admin"


@pytest.fixture(scope="module")
def token():
    r = requests.post(f"{API}/auth/login", json={"username": ADMIN_USER, "password": ADMIN_PASS}, timeout=15)
    assert r.status_code == 200, r.text
    return r.json()["token"]


@pytest.fixture(scope="module")
def auth_headers(token):
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def _list_strategies():
    r = requests.get(f"{API}/strategies", timeout=15)
    assert r.status_code == 200, r.text
    return r.json()


def _find(strats, sid):
    if isinstance(strats, dict):
        strats = strats.get("strategies") or strats.get("items") or []
    for s in strats:
        if s.get("id") == sid:
            return s
    return None


# ============ Rename / Duplicate / Variant ============

class TestStrategyRenameDuplicate:
    def test_rename_without_auth_forbidden(self):
        r = requests.post(f"{API}/strategies/rsi_only/rename", json={"name": "hacked"}, timeout=10)
        assert r.status_code in (401, 403), r.text

    def test_rename_empty_name_422(self, auth_headers):
        r = requests.post(f"{API}/strategies/rsi_only/rename", json={"name": ""}, headers=auth_headers, timeout=10)
        assert r.status_code == 422, r.text

    def test_rename_unknown_id_404(self, auth_headers):
        r = requests.post(f"{API}/strategies/nonexistent_zzz/rename", json={"name": "X"}, headers=auth_headers, timeout=10)
        assert r.status_code == 404, r.text

    def test_full_lifecycle_builtin_variant(self, auth_headers):
        """Duplicate built-in -> rename variant -> export -> delete -> re-import -> cleanup."""
        # 1) Duplicate rsi_only built-in
        r = requests.post(f"{API}/strategies/rsi_only/duplicate", headers=auth_headers, timeout=10)
        assert r.status_code == 200, r.text
        data = r.json()
        variant_id = data.get("id")
        assert variant_id and variant_id.startswith("variant_"), data
        assert data.get("is_variant") is True

        try:
            # 2) Verify appears in list with base_id
            strats = _list_strategies()
            v = _find(strats, variant_id)
            assert v is not None, f"variant {variant_id} not in list"
            assert v.get("is_variant") is True
            assert v.get("base_id") == "rsi_only"
            # enabled/available
            enabled = strats.get("enabled") if isinstance(strats, dict) else None
            if enabled is not None:
                assert variant_id in enabled

            # 3) ai_trader cannot be duplicated
            r2 = requests.post(f"{API}/strategies/ai_trader/duplicate", headers=auth_headers, timeout=10)
            assert r2.status_code == 400, r2.text

            # 4) Rename the variant
            new_name = "TEST_Variant_Renamed"
            r3 = requests.post(f"{API}/strategies/{variant_id}/rename", json={"name": new_name}, headers=auth_headers, timeout=10)
            assert r3.status_code == 200, r3.text
            v2 = _find(_list_strategies(), variant_id)
            assert v2 is not None and v2.get("name") == new_name

            # 5) Export the variant
            r4 = requests.get(f"{API}/strategies/{variant_id}/export", timeout=10)
            assert r4.status_code == 200, r4.text
            exp = r4.json()
            assert exp.get("is_variant") is True
            variant_obj = exp.get("variant") or {}
            assert variant_obj.get("kind") == "variant"
            assert variant_obj.get("base_id") == "rsi_only"

            # 6) Delete variant
            rd = requests.delete(f"{API}/strategies/{variant_id}", headers=auth_headers, timeout=10)
            assert rd.status_code in (200, 204), rd.text
            assert _find(_list_strategies(), variant_id) is None

            # 7) Re-import from export
            ri = requests.post(f"{API}/strategies/import", json=exp, headers=auth_headers, timeout=10)
            assert ri.status_code == 200, ri.text
            imported_id = ri.json().get("id") or variant_id
            assert _find(_list_strategies(), imported_id) is not None

            # cleanup imported
            requests.delete(f"{API}/strategies/{imported_id}", headers=auth_headers, timeout=10)

        finally:
            # ensure variant deleted
            requests.delete(f"{API}/strategies/{variant_id}", headers=auth_headers, timeout=10)

    def test_rename_builtin_and_restore(self, auth_headers):
        # find a built-in id likely present
        strats = _list_strategies()
        items = strats if isinstance(strats, list) else (strats.get("strategies") or strats.get("items") or [])
        # try scalping_4_rules or first builtin
        target = None
        original_name = None
        for s in items:
            if s.get("id") == "scalping_4_rules":
                target = s.get("id")
                original_name = s.get("name")
                break
        if target is None:
            pytest.skip("scalping_4_rules not present")

        try:
            r = requests.post(f"{API}/strategies/{target}/rename", json={"name": "TEST_RenamedScalping"}, headers=auth_headers, timeout=10)
            assert r.status_code == 200, r.text
            s2 = _find(_list_strategies(), target)
            assert s2.get("name") == "TEST_RenamedScalping"
        finally:
            # restore
            restore = original_name or "Scalping"
            requests.post(f"{API}/strategies/{target}/rename", json={"name": restore}, headers=auth_headers, timeout=10)

    def test_custom_create_duplicate_rename_delete(self, auth_headers):
        payload = {
            "name": "TEST_CustomRSI",
            "long_rules": [{"indicator": "rsi", "op": "<", "value": 30}],
            "timeframe": "5m",
        }
        r = requests.post(f"{API}/strategies/custom", json=payload, headers=auth_headers, timeout=10)
        assert r.status_code in (200, 201), r.text
        created = r.json()
        custom_id = created.get("id")
        assert custom_id and custom_id.startswith("custom_"), created

        dup_id = None
        try:
            # duplicate
            r2 = requests.post(f"{API}/strategies/{custom_id}/duplicate", headers=auth_headers, timeout=10)
            assert r2.status_code == 200, r2.text
            dup = r2.json()
            dup_id = dup.get("id")
            assert dup_id and dup_id.startswith("custom_"), dup
            dup_full = _find(_list_strategies(), dup_id)
            assert dup_full is not None
            # name should contain "(Kopie)" or similar marker
            assert "opie" in (dup_full.get("name") or "") or "opy" in (dup_full.get("name") or "")

            # rename copy
            r3 = requests.post(f"{API}/strategies/{dup_id}/rename", json={"name": "TEST_CustomRSI_Renamed"}, headers=auth_headers, timeout=10)
            assert r3.status_code == 200, r3.text

        finally:
            if dup_id:
                requests.delete(f"{API}/strategies/{dup_id}", headers=auth_headers, timeout=10)
            requests.delete(f"{API}/strategies/{custom_id}", headers=auth_headers, timeout=10)
            # verify gone
            assert _find(_list_strategies(), custom_id) is None


# ============ AI Playbook Backtest ============

class TestPlaybookBacktest:
    def test_playbook_backtest_fields(self):
        r = requests.get(f"{API}/ai/playbook/backtest", timeout=10)
        assert r.status_code == 200, r.text
        data = r.json()
        assert data.get("modes") == ["single", "loop", "ai_loop"], data.get("modes")
        ai_def = data.get("ai_defaults") or {}
        assert ai_def.get("ai_revise") is True
        assert ai_def.get("ai_rounds") == 3
        assert ai_def.get("target_passed") == 3
        auto = data.get("auto") or {}
        for k in ("mode", "ai_rounds", "target_passed", "ai_revise"):
            assert k in auto, f"auto missing {k}: {auto}"

    def test_playbook_backtest_auto_update_and_reset(self, auth_headers):
        # save ai_loop
        payload = {"mode": "ai_loop", "ai_rounds": 5, "target_passed": 2}
        r = requests.post(f"{API}/ai/playbook/backtest/auto", json=payload, headers=auth_headers, timeout=10)
        assert r.status_code == 200, r.text
        got = requests.get(f"{API}/api/ai/playbook/backtest".replace("/api/api", "/api"), timeout=10)
        got = requests.get(f"{API}/ai/playbook/backtest", timeout=10).json()
        auto = got.get("auto") or {}
        assert auto.get("mode") == "ai_loop"
        assert auto.get("ai_rounds") == 5
        assert auto.get("target_passed") == 2

        # invalid mode falls back to loop
        r2 = requests.post(f"{API}/ai/playbook/backtest/auto", json={"mode": "garbage"}, headers=auth_headers, timeout=10)
        assert r2.status_code == 200, r2.text
        got2 = requests.get(f"{API}/ai/playbook/backtest", timeout=10).json()
        assert (got2.get("auto") or {}).get("mode") == "loop"

        # final reset: mode loop, enabled false
        requests.post(f"{API}/ai/playbook/backtest/auto", json={"mode": "loop", "enabled": False}, headers=auth_headers, timeout=10)

    def test_playbook_backtest_run(self, auth_headers):
        payload = {
            "asset_classes": ["crypto"],
            "days": 14,
            "mode": "ai_loop",
            "setups": ["breakout"],
            "ai_rounds": 1,
            "target_passed": 1,
        }
        # a running backtest might return 409 - wait/retry briefly
        job_id = None
        for _ in range(6):
            r = requests.post(f"{API}/ai/playbook/backtest/run", json=payload, headers=auth_headers, timeout=15)
            if r.status_code == 409:
                time.sleep(10)
                continue
            assert r.status_code == 200, r.text
            data = r.json()
            assert data.get("status") in ("started", "running", "queued"), data
            params = data.get("params") or {}
            for k in ("ai_revise", "ai_rounds", "target_passed"):
                assert k in params, f"params missing {k}: {params}"
            job_id = data.get("job_id") or data.get("id")
            break
        if not job_id:
            pytest.skip("Backtest could not be started (persistent 409)")

        # poll status
        result = None
        deadline = time.time() + 180
        while time.time() < deadline:
            s = requests.get(f"{API}/ai/playbook/backtest/status/{job_id}", timeout=15)
            assert s.status_code == 200, s.text
            sd = s.json()
            status = sd.get("status")
            if status not in ("running", "queued", "started"):
                result = sd
                break
            time.sleep(5)
        assert result is not None, "Job did not finish in time"
        # verify result shape
        res = result.get("result") or {}
        rows = res.get("rows") or []
        # rows may or may not include 'breakout' if no data - do a soft assertion
        if rows:
            found_breakout = any(row.get("setup") == "breakout" for row in rows)
            assert found_breakout, f"no breakout row in {rows[:3]}"
            row0 = next((r for r in rows if r.get("setup") == "breakout"), rows[0])
            assert "ai_rounds" in row0
            assert "ai_proposal" in row0  # may be None (no LLM key)
        summary = res.get("summary") or res
        for k in ("passed", "tested", "ai_rounds", "ai_proposals"):
            assert k in summary or k in res, f"missing {k} in summary/result"
