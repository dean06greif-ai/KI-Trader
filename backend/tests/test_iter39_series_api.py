"""Iteration 39 – Nacht-Serie / Job-Warteschlange (/api/series) E2E gegen das
laufende Backend. Tests sind bewusst reihenfolge-abhängig (eine Klasse ->
ein xdist-Worker via loadscope).
"""
import os
import time

import pytest
import requests

BASE = (os.environ.get("REACT_APP_BACKEND_URL") or "").rstrip("/")
if not BASE:
    raise RuntimeError("REACT_APP_BACKEND_URL fehlt")
TIMEOUT = 60
CTX = {}

VALID_BODY = {"strategy_ids": ["rsi_only"], "symbols": ["BTCUSDT"], "days": 2,
              "max_capital": 100, "execution": "cloud"}


@pytest.fixture(scope="module")
def token():
    r = requests.post(f"{BASE}/api/auth/login",
                      json={"username": os.environ["ADMIN_USER"],
                            "password": os.environ["ADMIN_PASSWORD"]}, timeout=TIMEOUT)
    assert r.status_code == 200, r.text[:300]
    tok = r.json().get("token")
    assert tok, r.text[:300]
    return tok


@pytest.fixture(scope="module")
def hdr(token):
    return {"Authorization": f"Bearer {token}"}


def _list():
    r = requests.get(f"{BASE}/api/series", timeout=TIMEOUT)
    assert r.status_code == 200, r.text[:300]
    return r.json()


def _item(data, item_id):
    return next((i for i in data["items"] if i["id"] == item_id), None)


def _add(hdr, kind="backtest", body=None, label=None):
    payload = {"kind": kind, "body": body if body is not None else dict(VALID_BODY)}
    if label:
        payload["label"] = label
    return requests.post(f"{BASE}/api/series/add", json=payload, headers=hdr, timeout=TIMEOUT)


def _wait_status(item_id, statuses, timeout_s):
    deadline = time.time() + timeout_s
    seen = []
    while time.time() < deadline:
        it = _item(_list(), item_id)
        if it:
            if it["status"] not in seen:
                seen.append(it["status"])
            if it["status"] in statuses:
                return it, seen
        time.sleep(5)
    return _item(_list(), item_id), seen


class TestSeriesApi:
    """GET/POST/DELETE /api/series – Warteschlange, Ablauf, Steuerung"""

    # ---------------- Struktur ----------------
    def test_list_shape_and_existing_done_items(self):
        d = _list()
        assert isinstance(d["items"], list)
        st = d["state"]
        for k in ("paused", "start_at", "notify_each", "notify_done"):
            assert k in st, f"state.{k} fehlt"
        assert isinstance(d["may_start"], bool)
        assert isinstance(d["queued"], int)
        done = [i for i in d["items"] if i["status"] == "done"]
        assert len(done) >= 2, "erwartet mind. 2 fertige Einträge"
        kinds = {i["kind"] for i in done}
        assert {"backtest", "optimizer"} <= kinds
        for i in done:
            assert i.get("job_id")
            s = i.get("summary") or {}
            assert "pnl" in s and "max_drawdown" in s
        CTX["done_backtest_id"] = next(i["id"] for i in done if i["kind"] == "backtest")

    def test_result_of_finished_item(self):
        item_id = CTX["done_backtest_id"]
        r = requests.get(f"{BASE}/api/series/{item_id}/result", timeout=TIMEOUT)
        assert r.status_code == 200, r.text[:300]
        d = r.json()
        assert d["item"]["id"] == item_id
        assert "_id" not in d["item"]
        assert d["result"], "result fehlt"
        assert isinstance(d["result"].get("per_strategy"), list)

    def test_result_unknown_404(self):
        r = requests.get(f"{BASE}/api/series/nope-unknown/result", timeout=TIMEOUT)
        assert r.status_code == 404, r.status_code

    # ---------------- Validierung / Auth ----------------
    def test_add_without_token_denied(self):
        r = requests.post(f"{BASE}/api/series/add",
                          json={"kind": "backtest", "body": dict(VALID_BODY)}, timeout=TIMEOUT)
        assert r.status_code in (401, 403), r.status_code

    def test_add_unknown_strategy_400(self, hdr):
        r = _add(hdr, body={"strategy_ids": ["nope"], "symbols": ["BTCUSDT"], "days": 2})
        assert r.status_code == 400, r.text[:300]
        assert "Unbekannte Strategien" in r.text

    def test_add_unknown_kind_400(self, hdr):
        r = _add(hdr, kind="foo")
        assert r.status_code == 400, r.text[:300]

    # ---------------- Ablauf ----------------
    def test_add_valid_and_runs_to_done(self, hdr):
        r = _add(hdr, label="QA Backtest")
        assert r.status_code == 200, r.text[:300]
        item = r.json()["item"]
        assert item["status"] == "queued"
        assert item["label"] == "QA Backtest"
        CTX["run_id"] = item["id"]

        it, seen = _wait_status(item["id"], ("done", "error"), 200)
        assert it is not None, "Eintrag verschwunden"
        assert it["status"] == "done", f"status={it['status']} error={it.get('error')} seen={seen}"
        s = it.get("summary") or {}
        assert isinstance(s.get("pnl"), (int, float))
        assert isinstance(s.get("trades"), int)
        assert it.get("job_id")
        assert it.get("progress") == 100

        rr = requests.get(f"{BASE}/api/series/{item['id']}/result", timeout=TIMEOUT)
        assert rr.status_code == 200, rr.text[:300]
        assert isinstance(rr.json()["result"].get("per_strategy"), list)

    # ---------------- Steuerung ----------------
    def test_pause_keeps_item_queued(self, hdr):
        r = requests.post(f"{BASE}/api/series/state", json={"paused": True},
                          headers=hdr, timeout=TIMEOUT)
        assert r.status_code == 200, r.text[:300]
        assert r.json()["state"]["paused"] is True
        d = _list()
        assert d["may_start"] is False and d["wait_reason"] == "pausiert"

        add = _add(hdr, label="QA Paused")
        assert add.status_code == 200, add.text[:300]
        CTX["paused_id"] = add.json()["item"]["id"]
        time.sleep(45)
        it = _item(_list(), CTX["paused_id"])
        assert it["status"] == "queued", f"darf nicht starten, status={it['status']}"

    def test_state_start_at_future_blocks(self, hdr):
        r = requests.post(f"{BASE}/api/series/state",
                          json={"paused": False, "start_at": "2099-01-01T00:00"},
                          headers=hdr, timeout=TIMEOUT)
        assert r.status_code == 200, r.text[:300]
        d = _list()
        assert d["may_start"] is False
        assert d["wait_reason"].startswith("geplant ab"), d["wait_reason"]
        assert d["state"]["start_at"]

    def test_reorder_swaps_positions(self, hdr):
        add = _add(hdr, label="QA Second")
        assert add.status_code == 200, add.text[:300]
        CTX["second_id"] = add.json()["item"]["id"]
        a, b = CTX["paused_id"], CTX["second_id"]
        items = {i["id"]: i["position"] for i in _list()["items"]}
        assert items[a] < items[b]
        r = requests.post(f"{BASE}/api/series/reorder", json={"ids": [b, a]},
                          headers=hdr, timeout=TIMEOUT)
        assert r.status_code == 200, r.text[:300]
        after = {i["id"]: i["position"] for i in _list()["items"]}
        assert after[b] < after[a], f"reorder wirkungslos: {after}"

    def test_notify_toggles(self, hdr):
        r = requests.post(f"{BASE}/api/series/state",
                          json={"notify_each": False, "notify_done": False},
                          headers=hdr, timeout=TIMEOUT)
        assert r.status_code == 200, r.text[:300]
        st = r.json()["state"]
        assert st["notify_each"] is False and st["notify_done"] is False
        r = requests.post(f"{BASE}/api/series/state",
                          json={"notify_each": True, "notify_done": True},
                          headers=hdr, timeout=TIMEOUT)
        assert r.json()["state"]["notify_each"] is True

    def test_resume_starts_queued_item(self, hdr):
        r = requests.post(f"{BASE}/api/series/state",
                          json={"paused": False, "start_at": None},
                          headers=hdr, timeout=TIMEOUT)
        assert r.status_code == 200, r.text[:300]
        d = _list()
        assert d["may_start"] is True and d["state"]["start_at"] in (None, "")
        it, seen = _wait_status(CTX["second_id"], ("done", "error"), 240)
        assert it is not None and it["status"] == "done", \
            f"status={it and it['status']} error={it and it.get('error')} seen={seen}"

    def test_delete_item_and_unknown(self, hdr):
        it, _ = _wait_status(CTX["paused_id"], ("queued", "done", "error"), 240)
        r = requests.delete(f"{BASE}/api/series/{CTX['paused_id']}", headers=hdr, timeout=TIMEOUT)
        assert r.status_code == 200, r.text[:300]
        assert _item(_list(), CTX["paused_id"]) is None
        r = requests.delete(f"{BASE}/api/series/unknown", headers=hdr, timeout=TIMEOUT)
        assert r.status_code == 404, r.status_code
        r = requests.delete(f"{BASE}/api/series/{CTX['run_id']}", timeout=TIMEOUT)
        assert r.status_code in (401, 403), r.status_code

    def test_zz_clear_finished_last(self, hdr):
        r = requests.delete(f"{BASE}/api/series/finished", headers=hdr, timeout=TIMEOUT)
        assert r.status_code == 200, r.text[:300]
        assert isinstance(r.json()["removed"], int) and r.json()["removed"] >= 1
        d = _list()
        assert not [i for i in d["items"] if i["status"] in ("done", "error")], \
            "fertige Einträge nicht entfernt"
