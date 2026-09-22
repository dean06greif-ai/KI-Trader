"""
Iteration 46 backend tests:
- Admin login
- Data recovery (preview / run / report + idempotency)
- MasterPrompt history & restore (incl. 404)
- Lessons history & restore (incl. 404)
- Trade trash: clear + list + restore + 404 cases
- Seeding automation API (/api/ai/playbook/backtest + /auto with clamping)
- Regression: /api/autotrade/trades, /api/performance, /api/ai/lessons
"""
import os
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://trader-recovery-1.preview.emergentagent.com").rstrip("/")
ADMIN_USER = "Admin"
ADMIN_PASS = "Dean06Greif!/Admin"


@pytest.fixture(scope="module")
def token():
    r = requests.post(f"{BASE_URL}/api/auth/login", json={"username": ADMIN_USER, "password": ADMIN_PASS}, timeout=30)
    assert r.status_code == 200, f"login failed: {r.status_code} {r.text}"
    tok = r.json().get("token")
    assert tok
    return tok


@pytest.fixture(scope="module")
def admin_headers(token):
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


# ---------- Admin Login ----------
class TestAuth:
    def test_login(self, token):
        assert isinstance(token, str) and len(token) > 10


# ---------- Recovery ----------
class TestRecovery:
    def test_preview(self, admin_headers):
        r = requests.get(f"{BASE_URL}/api/admin/recovery/preview", headers=admin_headers, timeout=30)
        assert r.status_code == 200, r.text
        j = r.json()
        assert j.get("dry_run") is True
        for k in ("master_prompt", "lessons", "paper_trades"):
            assert k in j, f"missing key {k}"
        assert isinstance(j["paper_trades"].get("recovered"), int)

    def test_run_and_idempotent(self, admin_headers):
        r1 = requests.post(f"{BASE_URL}/api/admin/recovery/run", headers=admin_headers, timeout=60)
        assert r1.status_code == 200, r1.text
        j1 = r1.json()
        assert j1.get("dry_run") is False
        # second run: idempotent
        r2 = requests.post(f"{BASE_URL}/api/admin/recovery/run", headers=admin_headers, timeout=60)
        assert r2.status_code == 200, r2.text
        j2 = r2.json()
        assert j2["paper_trades"]["recovered"] == 0
        assert j2["master_prompt"]["restored"] is False

    def test_report(self, admin_headers):
        r = requests.get(f"{BASE_URL}/api/admin/recovery/report", headers=admin_headers, timeout=30)
        assert r.status_code == 200
        j = r.json()
        assert "last_report" in j


# ---------- MasterPrompt History / Restore ----------
class TestMasterPrompt:
    def test_history_and_restore_cycle(self, admin_headers):
        r = requests.get(f"{BASE_URL}/api/ai/master-prompt?history=true", timeout=30)
        assert r.status_code == 200
        j = r.json()
        mp = j.get("master_prompt") or {}
        prev_version = mp.get("version")
        prev_text = mp.get("text", "")
        history = j.get("history")
        assert prev_version is not None
        assert isinstance(history, list)

        # write new version with TESTMARKER
        new_text = f"{prev_text} TESTMARKER"
        r2 = requests.post(f"{BASE_URL}/api/ai/master-prompt", headers=admin_headers,
                           json={"text": new_text}, timeout=30)
        assert r2.status_code == 200, r2.text
        j2 = r2.json()
        new_version = (j2.get("master_prompt") or j2).get("version") or j2.get("version")
        assert new_version is not None and new_version > prev_version

        # restore to prev_version
        r3 = requests.post(f"{BASE_URL}/api/ai/master-prompt/restore", headers=admin_headers,
                           json={"version": prev_version}, timeout=30)
        assert r3.status_code == 200, r3.text

        # verify current text no longer contains TESTMARKER and version increased again
        r4 = requests.get(f"{BASE_URL}/api/ai/master-prompt", timeout=30)
        assert r4.status_code == 200
        j4 = r4.json()
        cur = (j4.get("master_prompt") or j4)
        assert "TESTMARKER" not in cur.get("text", "")
        assert cur.get("version", 0) > new_version

    def test_restore_unknown_version_404(self, admin_headers):
        r = requests.post(f"{BASE_URL}/api/ai/master-prompt/restore", headers=admin_headers,
                         json={"version": 999999}, timeout=30)
        assert r.status_code == 404, r.text


# ---------- Lessons History / Restore ----------
class TestLessons:
    def test_lessons_history_restore(self, admin_headers):
        # ensure baseline
        r = requests.get(f"{BASE_URL}/api/ai/lessons", timeout=30)
        assert r.status_code == 200

        # add test lesson
        r2 = requests.post(f"{BASE_URL}/api/ai/lessons", headers=admin_headers,
                          json={"title": "QA_TEST_History", "detail": "Testeintrag"}, timeout=30)
        assert r2.status_code in (200, 201), r2.text

        # find the test lesson id
        r3 = requests.get(f"{BASE_URL}/api/ai/lessons", timeout=30)
        assert r3.status_code == 200
        lessons = r3.json().get("lessons") or r3.json().get("items") or []
        if isinstance(lessons, dict):
            lessons = lessons.get("lessons", [])
        test_lesson = next((l for l in lessons if l.get("title") == "QA_TEST_History"), None)
        assert test_lesson, f"QA_TEST_History not found in {lessons[:3]}"
        lesson_id = test_lesson.get("id") or test_lesson.get("_id")
        assert lesson_id

        # history
        rh = requests.get(f"{BASE_URL}/api/ai/lessons/history", timeout=30)
        assert rh.status_code == 200
        hist = rh.json().get("history") or rh.json().get("items") or []
        assert len(hist) >= 1
        first = hist[0]
        for k in ("id", "ts", "count", "titles"):
            assert k in first, f"snapshot missing key {k}: {first}"
        snapshot_id = first["id"]

        # delete lesson
        rd = requests.delete(f"{BASE_URL}/api/ai/lessons/{lesson_id}", headers=admin_headers, timeout=30)
        assert rd.status_code in (200, 204), rd.text

        # restore
        rr = requests.post(f"{BASE_URL}/api/ai/lessons/restore/{snapshot_id}", headers=admin_headers, timeout=30)
        assert rr.status_code == 200, rr.text
        jj = rr.json()
        assert jj.get("status") == "success"
        assert isinstance(jj.get("restored"), int)

        # unknown snapshot -> 404
        r404 = requests.post(f"{BASE_URL}/api/ai/lessons/restore/nonexistent_id_xyz", headers=admin_headers, timeout=30)
        assert r404.status_code == 404, r404.text

        # cleanup: remove test lesson if returned
        r4 = requests.get(f"{BASE_URL}/api/ai/lessons", timeout=30)
        lessons2 = r4.json().get("lessons") or []
        if isinstance(lessons2, dict):
            lessons2 = lessons2.get("lessons", [])
        again = next((l for l in lessons2 if l.get("title") == "QA_TEST_History"), None)
        if again:
            lid = again.get("id") or again.get("_id")
            requests.delete(f"{BASE_URL}/api/ai/lessons/{lid}", headers=admin_headers, timeout=30)


# ---------- Trade Trash ----------
class TestTradeTrash:
    def test_trash_cycle(self, admin_headers):
        # list current trash
        rl = requests.get(f"{BASE_URL}/api/analytics/trash", timeout=30)
        assert rl.status_code == 200
        assert "batches" in rl.json()

        # clear manual strategy
        rc = requests.post(f"{BASE_URL}/api/analytics/clear", headers=admin_headers,
                          json={"range": "all", "scope": "strategy", "strategy_id": "manual"}, timeout=60)
        assert rc.status_code == 200, rc.text
        j = rc.json()
        deleted = j.get("deleted", {})
        assert "auto_trades" in deleted
        assert isinstance(deleted["auto_trades"], int)
        assert "trash_batch" in deleted
        batch_id = deleted["trash_batch"]

        if batch_id:
            # batch should appear
            rl2 = requests.get(f"{BASE_URL}/api/analytics/trash", timeout=30)
            batches = rl2.json().get("batches", [])
            b = next((x for x in batches if x.get("id") == batch_id or x.get("batch_id") == batch_id), None)
            assert b, f"batch {batch_id} not found"
            for k in ("count", "reason", "deleted_at"):
                assert k in b, f"missing {k} in batch"
            assert "trades" not in b, "trades array must not be in list response"
            count = b["count"]

            # restore
            rr = requests.post(f"{BASE_URL}/api/analytics/trash/restore/{batch_id}", headers=admin_headers, timeout=30)
            assert rr.status_code == 200, rr.text
            assert rr.json().get("restored") == count

            # batch gone
            rl3 = requests.get(f"{BASE_URL}/api/analytics/trash", timeout=30)
            batches3 = rl3.json().get("batches", [])
            assert not any(x.get("id") == batch_id or x.get("batch_id") == batch_id for x in batches3)

    def test_restore_unknown_404(self, admin_headers):
        r = requests.post(f"{BASE_URL}/api/analytics/trash/restore/does_not_exist_xyz", headers=admin_headers, timeout=30)
        assert r.status_code == 404

    def test_discard_unknown_404(self, admin_headers):
        r = requests.delete(f"{BASE_URL}/api/analytics/trash/does_not_exist_xyz", headers=admin_headers, timeout=30)
        assert r.status_code == 404


# ---------- Seeding automation ----------
class TestSeedingAuto:
    def test_backtest_shape(self):
        r = requests.get(f"{BASE_URL}/api/ai/playbook/backtest", timeout=30)
        assert r.status_code == 200
        j = r.json()
        for k in ("auto", "classes", "last_result", "rules"):
            assert k in j, f"missing {k}"
        auto = j["auto"]
        for k in ("enabled", "interval_hours", "days", "mode", "asset_classes", "next_run_at", "due"):
            assert k in auto, f"auto missing {k}"

    def test_auto_endpoint(self):
        r = requests.get(f"{BASE_URL}/api/ai/playbook/backtest/auto", timeout=30)
        assert r.status_code == 200
        j = r.json()
        for k in ("enabled", "interval_hours", "days", "asset_classes"):
            assert k in j

    def test_last_result_shape(self):
        r = requests.get(f"{BASE_URL}/api/ai/playbook/backtest", timeout=30)
        j = r.json()
        lr = j.get("last_result") or {}
        assert lr, "last_result should exist"
        assert lr.get("trigger") == "manual"
        rows = lr.get("rows") or []
        assert len(rows) > 0
        allowed_status = {"passed", "tuned", "failed", "exhausted", "live", "no_data"}
        for row in rows:
            assert "setup" in row
            assert row.get("status") in allowed_status, f"bad status: {row.get('status')}"
            assert isinstance(row.get("tried"), int) and row["tried"] >= 1
            for sec in ("is", "oos"):
                s = row.get(sec) or {}
                for k in ("trades", "wins", "pnl", "winrate"):
                    assert k in s, f"{sec} missing {k}"

    def test_update_auto_and_clamps(self, admin_headers):
        # set valid config
        r = requests.post(f"{BASE_URL}/api/ai/playbook/backtest/auto", headers=admin_headers,
                          json={"enabled": True, "interval_hours": 48, "days": 60, "asset_classes": ["indices"]},
                          timeout=30)
        assert r.status_code == 200, r.text
        j = r.json()
        assert j.get("enabled") is True
        assert j.get("interval_hours") == 48
        assert j.get("days") == 60
        assert j.get("asset_classes") == ["indices"]
        assert j.get("next_run_at")

        # interval_hours 1 -> clamped to 6
        r2 = requests.post(f"{BASE_URL}/api/ai/playbook/backtest/auto", headers=admin_headers,
                          json={"interval_hours": 1}, timeout=30)
        assert r2.status_code == 200
        assert r2.json().get("interval_hours") == 6

        # days 9999 -> clamped to 365
        r3 = requests.post(f"{BASE_URL}/api/ai/playbook/backtest/auto", headers=admin_headers,
                          json={"days": 9999}, timeout=30)
        assert r3.status_code == 200
        assert r3.json().get("days") == 365

        # asset_classes ["quatsch"] -> defaults to all 4
        r4 = requests.post(f"{BASE_URL}/api/ai/playbook/backtest/auto", headers=admin_headers,
                          json={"asset_classes": ["quatsch"]}, timeout=30)
        assert r4.status_code == 200
        ac = r4.json().get("asset_classes")
        assert set(ac) == {"crypto", "indices", "resources", "forex"}, f"got {ac}"

        # disable at end
        r5 = requests.post(f"{BASE_URL}/api/ai/playbook/backtest/auto", headers=admin_headers,
                          json={"enabled": False}, timeout=30)
        assert r5.status_code == 200
        assert r5.json().get("enabled") is False


# ---------- Regression ----------
class TestRegression:
    def test_autotrade_trades(self):
        r = requests.get(f"{BASE_URL}/api/autotrade/trades?status=closed&limit=50", timeout=30)
        assert r.status_code == 200
        j = r.json()
        trades = j.get("trades") or j.get("items") or (j if isinstance(j, list) else [])
        assert isinstance(trades, list)

    def test_performance(self):
        r = requests.get(f"{BASE_URL}/api/performance", timeout=30)
        assert r.status_code == 200

    def test_lessons(self):
        r = requests.get(f"{BASE_URL}/api/ai/lessons", timeout=30)
        assert r.status_code == 200
