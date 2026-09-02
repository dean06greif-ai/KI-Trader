"""Regression: RAM-Warteschlange (Cloud-Jobs) + Daten-Ordner pro Worker.

- submit startet sofort bei genug RAM, reiht bei Knappheit ein (Phase-Text)
- Watcher startet wartende Jobs automatisch, sobald RAM frei ist
- Abgebrochene wartende Jobs werden aus der Queue entfernt
- get_settings_for_worker liefert worker-spezifisches data_dir
- POST /api/localworker/worker/{id}/data-dir (Admin) + Poll liefert den Pfad
- Free-Modelle nutzen Backup-Keys zuerst (Hauptkonto als Reserve)
"""
import asyncio
import os

import pytest
import requests
from dotenv import dotenv_values

from services import local_exec, ram_queue
from services import ai_providers as ap

_env = dotenv_values("/app/frontend/.env")
BASE_URL = (os.environ.get("REACT_APP_BACKEND_URL")
            or _env.get("REACT_APP_BACKEND_URL")).rstrip("/")
TIMEOUT = 60


@pytest.fixture(autouse=True)
def _clean_queue():
    ram_queue._QUEUE.clear()
    yield
    ram_queue._QUEUE.clear()


class TestRamQueue:
    def test_starts_immediately_when_ram_free(self, monkeypatch):
        monkeypatch.setattr(ram_queue, "free_mb", lambda: 10000.0)
        jobs = {"j1": {"status": "running", "phase": "Startet", "cancel": False}}
        done = {}

        async def run():
            async def work():
                done["ok"] = True
                jobs["j1"]["status"] = "completed"
            queued = ram_queue.submit(jobs, "j1", work, kind="backtest")
            await asyncio.sleep(0.05)
            return queued

        assert asyncio.run(run()) is False
        assert done.get("ok") and not ram_queue._QUEUE

    def test_queues_when_ram_low_then_autostarts(self, monkeypatch):
        monkeypatch.setattr(ram_queue, "free_mb", lambda: 10.0)
        jobs = {"j2": {"status": "running", "phase": "Startet", "cancel": False}}
        done = {}

        async def run():
            async def work():
                done["ok"] = True
                jobs["j2"]["status"] = "completed"
            queued = ram_queue.submit(jobs, "j2", work, kind="optimizer")
            assert queued is True
            assert "Wartet auf freien Server-RAM" in jobs["j2"]["phase"]
            # RAM bleibt knapp -> Job bleibt wartend
            assert ram_queue._try_start_next() is False
            assert len(ram_queue._QUEUE) == 1
            # RAM wird frei -> automatischer Start
            monkeypatch.setattr(ram_queue, "free_mb", lambda: 10000.0)
            assert ram_queue._try_start_next() is True
            await asyncio.sleep(0.05)

        asyncio.run(run())
        assert done.get("ok") and not ram_queue._QUEUE

    def test_cancelled_waiting_job_removed(self, monkeypatch):
        monkeypatch.setattr(ram_queue, "free_mb", lambda: 10.0)
        jobs = {"j3": {"status": "running", "phase": "Startet", "cancel": False}}

        async def run():
            async def work():
                pytest.fail("darf nie starten")
            ram_queue.submit(jobs, "j3", work, kind="regime_analysis")
            jobs["j3"]["cancel"] = True
            assert ram_queue._try_start_next() is True

        asyncio.run(run())
        assert jobs["j3"]["status"] == "cancelled"
        assert not ram_queue._QUEUE

    def test_free_mb_positive(self):
        assert ram_queue.free_mb() > 0


class TestPerWorkerDataDir:
    def test_settings_for_worker_merge(self):
        local_exec._settings_cache = {**local_exec.DEFAULT_SETTINGS,
                                      "data_dir": "D:/global",
                                      "data_dirs": {"w-mein": "D:/mein",
                                                    "w-kumpel": "E:/kumpel"}}
        try:
            s1 = asyncio.run(local_exec.get_settings_for_worker(None, "w-mein"))
            s2 = asyncio.run(local_exec.get_settings_for_worker(None, "w-kumpel"))
            s3 = asyncio.run(local_exec.get_settings_for_worker(None, "w-neu"))
            assert s1["data_dir"] == "D:/mein"
            assert s2["data_dir"] == "E:/kumpel"
            assert s3["data_dir"] == "D:/global"  # Fallback
            assert "data_dirs" not in s1
        finally:
            local_exec._settings_cache = None

    def _admin(self):
        r = requests.post(f"{BASE_URL}/api/auth/login", timeout=TIMEOUT,
                          json={"username": "Admin",
                                "password": "Dean06Greif!/Admin"})
        assert r.status_code == 200
        return r.json()["token"]

    def test_endpoint_requires_admin(self):
        r = requests.post(f"{BASE_URL}/api/localworker/worker/wx/data-dir",
                          json={"data_dir": "D:/x"}, timeout=TIMEOUT)
        assert r.status_code == 401

    def test_set_and_poll_roundtrip(self):
        jwt = self._admin()
        h = {"Authorization": f"Bearer {jwt}"}
        wt = requests.get(f"{BASE_URL}/api/localworker/token", headers=h,
                          timeout=TIMEOUT).json()["token"]
        # Pfad für Worker A setzen
        r = requests.post(f"{BASE_URL}/api/localworker/worker/pytest-wA/data-dir",
                          json={"data_dir": "D:/pfadA"}, headers=h, timeout=TIMEOUT)
        assert r.status_code == 200
        assert r.json()["settings"]["data_dirs"].get("pytest-wA") == "D:/pfadA"
        # Poll als Worker A -> bekommt SEINEN Pfad; Worker B -> nicht
        pa = requests.post(f"{BASE_URL}/api/worker/poll",
                           headers={"X-Worker-Token": wt},
                           json={"worker_id": "pytest-wA", "want_compute": False,
                                 "want_data": False}, timeout=TIMEOUT).json()
        pb = requests.post(f"{BASE_URL}/api/worker/poll",
                           headers={"X-Worker-Token": wt},
                           json={"worker_id": "pytest-wB", "want_compute": False,
                                 "want_data": False}, timeout=TIMEOUT).json()
        assert pa["settings"]["data_dir"] == "D:/pfadA"
        assert pb["settings"]["data_dir"] != "D:/pfadA"
        # Aufräumen: Eintrag entfernen
        r = requests.post(f"{BASE_URL}/api/localworker/worker/pytest-wA/data-dir",
                          json={"data_dir": ""}, headers=h, timeout=TIMEOUT)
        assert "pytest-wA" not in r.json()["settings"]["data_dirs"]


class TestFreeModelKeyPreference:
    def test_free_model_uses_backup_first(self, monkeypatch):
        calls = []

        async def fake_oai(provider, model, key, prompt, system, temperature, json_mode):
            calls.append(key)
            return "ok"

        monkeypatch.setattr(ap, "_oai_generate", fake_oai)
        monkeypatch.setenv("OPENROUTER_API_KEY", "haupt")
        monkeypatch.setenv("OPENROUTER_API_KEY_BACKUP", "backup1")
        ap._key_limited.pop("openrouter", None)
        ap._rr_start.pop("openrouter", None)
        for _ in range(3):  # unabhängig vom Round-Robin-Start
            text, _, _ = asyncio.run(ap.generate_chain(
                [("openrouter", "modell-x:free")], "p", "s"))
            assert text == "ok"
        assert "haupt" not in calls, \
            f"Hauptkonto-Key wurde für Free-Modell genutzt: {calls}"
