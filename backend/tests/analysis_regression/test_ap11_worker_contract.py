"""AP11 (W01): Worker-/Rechen- und Strukturvertrag – Solltests.

Verträge: Input-Hash am Auftrag + Echo-Prüfung in der Antwort, Evidenz-Hash
am Ergebnis, idempotenter Jobabschluss (Doppel-/Spät-Upload), Paketmanifest
mit Dateihashes/Commit und rekursiver geprüfter Unterpaket-Allowlist.
"""
import asyncio
import io
import zipfile

import pytest

from services import local_exec as lx


@pytest.fixture(autouse=True)
def _clean_stores():
    lx.LOCAL_JOBS.clear()
    lx.COMPUTE_QUEUE.clear()
    from services import backtester as bt
    saved = dict(bt.JOBS)
    bt.JOBS.clear()
    yield
    bt.JOBS.clear()
    bt.JOBS.update(saved)
    lx.LOCAL_JOBS.clear()
    lx.COMPUTE_QUEUE.clear()


def _mk_job(job_id="j1"):
    from services import backtester as bt
    bt.JOBS[job_id] = {"id": job_id, "status": "running", "progress": 0,
                       "phase": "Startet", "params": {}, "best": None,
                       "cancel": False, "created_at": "2026-06-01T00:00:00+00:00",
                       "result": None, "error": None}
    return bt.JOBS[job_id]


class TestInputHash:
    def test_deterministic_and_order_independent(self):
        a = lx.payload_input_hash({"x": 1, "y": [1, 2]})
        b = lx.payload_input_hash({"y": [1, 2], "x": 1})
        assert a == b and len(a) == 16

    def test_transport_key_excluded(self):
        p = {"x": 1}
        h = lx.payload_input_hash(p)
        p["_input_hash"] = h
        assert lx.payload_input_hash(p) == h

    def test_different_payload_different_hash(self):
        assert lx.payload_input_hash({"x": 1}) != lx.payload_input_hash({"x": 2})

    def test_non_serializable_safe(self):
        h = lx.payload_input_hash({"obj": object()})
        assert isinstance(h, str) and h


class TestEnqueueCarriesHash:
    def test_enqueue_sets_hash_on_job_meta_and_payload(self):
        job = _mk_job()
        payload = {"args": {"days": 3}}
        lx.enqueue_compute("backtest", "j1", payload)
        ih = lx.payload_input_hash(payload)
        assert job["input_hash"] == ih
        assert lx.LOCAL_JOBS["j1"]["input_hash"] == ih
        assert payload["_input_hash"] == ih
        assert lx.COMPUTE_QUEUE[0]["payload"]["_input_hash"] == ih


class TestApplyResultContract:
    def _enqueue_claimed(self):
        job = _mk_job()
        lx.enqueue_compute("backtest", "j1", {"args": {"days": 3}})
        lx.COMPUTE_QUEUE.clear()
        lx.LOCAL_JOBS["j1"]["state"] = "claimed"
        return job, lx.LOCAL_JOBS["j1"]["input_hash"]

    def test_hash_mismatch_rejected_with_reason(self):
        job, ih = self._enqueue_claimed()
        asyncio.run(lx.apply_result(
            "j1", {"kind": "backtest", "status": "done",
                   "input_hash": "deadbeef00000000",
                   "result": {"pnl": 1}}, None))
        assert job["status"] == "error"
        assert "Input-Hash" in (job["error"] or "")
        assert job["result"] is None  # kein "done" mit unbrauchbaren Daten

    def test_matching_hash_accepted_with_evidence(self):
        job, ih = self._enqueue_claimed()
        res = {"pnl": 5}
        asyncio.run(lx.apply_result(
            "j1", {"kind": "backtest", "status": "done",
                   "input_hash": ih, "result": res}, None))
        assert job["status"] == "done"
        assert job["evidence"]["input_hash"] == ih
        assert job["evidence"]["result_hash"]
        assert job["result"]["evidence_hash"] == job["evidence"]["result_hash"]
        assert job["result"]["input_hash"] == ih

    def test_old_worker_without_echo_still_accepted(self):
        job, _ = self._enqueue_claimed()
        asyncio.run(lx.apply_result(
            "j1", {"kind": "backtest", "status": "done",
                   "result": {"pnl": 2}}, None))
        assert job["status"] == "done"

    def test_duplicate_result_upload_is_idempotent(self):
        job, ih = self._enqueue_claimed()
        asyncio.run(lx.apply_result(
            "j1", {"kind": "backtest", "status": "done",
                   "input_hash": ih, "result": {"pnl": 1}}, None))
        first = job["result"]
        asyncio.run(lx.apply_result(
            "j1", {"kind": "backtest", "status": "done",
                   "input_hash": ih, "result": {"pnl": 999}}, None))
        assert job["status"] == "done"
        assert job["result"] is first  # zweiter Upload verworfen

    def test_late_result_after_cancel_ignored(self):
        job, ih = self._enqueue_claimed()
        job["status"] = "cancelled"
        lx.LOCAL_JOBS.pop("j1", None)
        asyncio.run(lx.apply_result(
            "j1", {"kind": "backtest", "status": "done",
                   "input_hash": ih, "result": {"pnl": 7}}, None))
        assert job["status"] == "cancelled" and job["result"] is None


class TestPackageManifest:
    def test_manifest_hashes_commit_fingerprint(self):
        from routers import local_worker as lwr
        m1 = lwr._package_manifest()
        m2 = lwr._package_manifest()
        assert m1["complete"] is True
        assert m1["file_hashes"] and all(len(v) == 16 for v in m1["file_hashes"].values())
        assert m1["code_fingerprint"] == m2["code_fingerprint"]
        assert m1["commit"]

    def test_subpackage_allowlist_recursive_py_only(self):
        from routers import local_worker as lwr
        files = lwr._package_module_files()
        rels = [rel for rel, _ in files]
        assert any(r.startswith("services/setup_backtest/") for r in rels)
        assert all(r.endswith(".py") for r in rels)
        assert not any("__pycache__" in r for r in rels)

    def test_no_secrets_or_configs_in_package(self):
        from routers import local_worker as lwr
        rels = [rel for rel, _ in lwr._package_module_files()]
        rels += [p.name for p in lwr._worker_files()]
        assert not any(".env" in r for r in rels)
        assert not any("worker_config" in r for r in rels)
        assert not any("/tests/" in r for r in rels)

    def test_zip_contains_subpackage_and_init(self):
        from routers import local_worker as lwr
        resp = asyncio.run(lwr.localworker_package())
        zf = zipfile.ZipFile(io.BytesIO(resp.body))
        names = zf.namelist()
        assert "worker.py" in names and "PACKAGE_INFO.json" in names
        assert any(n.startswith("services/setup_backtest/") for n in names)
        assert "services/__init__.py" in names
        assert not any(".env" in n for n in names)

    def test_worker_echoes_input_hash(self):
        src = (lx.__file__.replace("backend/services/local_exec.py",
                                   "local_worker/worker.py"))
        text = open(src).read()
        assert text.count('"input_hash": (payload or {}).get("_input_hash")') >= 3
