"""Lokaler Worker: Auto-Update der Kerzendaten (10/2026).

Vorher meldete jeder Auto-Update-Lauf (auch direkt nach dem Neustart)
„Job auto-xxxx: fertig (error) – Ergebnis hochgeladen“, sobald EIN Symbol
nicht nachladbar war – und lud das Ergebnis eines dem Server unbekannten
Jobs hoch. Jetzt: Fehler je Symbol isoliert, Auto-Jobs nur lokal protokolliert."""
import importlib.util
import shutil
import sys
from pathlib import Path

import pytest

WORKER_SRC = Path(__file__).resolve().parents[2] / "local_worker" / "worker.py"


@pytest.fixture()
def worker(tmp_path, monkeypatch):
    # Kopie in tmp_path: Import legt worker_config.json neben dem Skript an
    dst = tmp_path / "worker.py"
    shutil.copy(WORKER_SRC, dst)
    monkeypatch.setenv("WORKER_SERVER_URL", "http://worker-test.invalid")
    monkeypatch.setenv("WORKER_TOKEN", "t")
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(sys, "argv", ["worker.py", "--data-dir", str(tmp_path / "data")])
    spec = importlib.util.spec_from_file_location("_worker_under_test", dst)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    logs = []
    monkeypatch.setattr(mod, "log", logs.append)
    mod._test_logs = logs
    return mod


def _fake_cache(monkeypatch, symbols):
    from services import candle_cache
    monkeypatch.setattr(candle_cache, "list_disk_symbols", lambda: [{"symbol": s} for s in symbols])
    monkeypatch.setattr(candle_cache, "disk_meta", lambda s: {"first_ts": 0})


def test_update_all_symbols_isolates_failing_symbol(worker, monkeypatch):
    _fake_cache(monkeypatch, ["BTCUSDT", "OLDSYM", "ETHUSDT"])

    async def dl(jobd, syms, days):
        if syms == ["OLDSYM"]:
            raise RuntimeError("API-Fehler -1121: Invalid symbol")
        return [{"symbol": syms[0], "candles": 1}]
    monkeypatch.setattr(worker, "_download_symbols", dl)
    updated, skipped = worker._update_all_symbols({"cancel": False})
    assert updated == ["BTCUSDT", "ETHUSDT"]
    assert skipped == [{"symbol": "OLDSYM", "error": "API-Fehler -1121: Invalid symbol"}]


def test_update_all_symbols_still_honours_cancel(worker, monkeypatch):
    _fake_cache(monkeypatch, ["BTCUSDT"])

    async def dl(jobd, syms, days):
        raise worker.JobCancelledLocal()
    monkeypatch.setattr(worker, "_download_symbols", dl)
    with pytest.raises(worker.JobCancelledLocal):
        worker._update_all_symbols({})


def _no_reporter(monkeypatch, worker):
    monkeypatch.setattr(worker, "progress_reporter", lambda *a: None)


def test_auto_update_logs_locally_and_does_not_upload(worker, monkeypatch):
    _fake_cache(monkeypatch, ["BTCUSDT", "OLDSYM"])
    _no_reporter(monkeypatch, worker)
    uploads = []
    monkeypatch.setattr(worker, "upload_result", lambda jid, p: uploads.append((jid, p)))

    async def dl(jobd, syms, days):
        if syms == ["OLDSYM"]:
            raise RuntimeError("Quelle down")
        return []
    monkeypatch.setattr(worker, "_download_symbols", dl)
    worker.run_data_job("auto-1234abcd", "data_update", {})
    assert uploads == []
    text = "\n".join(worker._test_logs)
    assert "fertig (error)" not in text
    assert "1 Symbol(e) aktuell, 1 übersprungen" in text and "OLDSYM: Quelle down" in text


def test_auto_update_without_failures_logs_single_line(worker, monkeypatch):
    _fake_cache(monkeypatch, ["BTCUSDT"])
    _no_reporter(monkeypatch, worker)
    monkeypatch.setattr(worker, "upload_result", lambda *a: pytest.fail("kein Upload erwartet"))

    async def dl(jobd, syms, days):
        return []
    monkeypatch.setattr(worker, "_download_symbols", dl)
    worker.run_data_job("auto-00000000", "data_update", {})
    assert worker._test_logs == ["Auto-Update der Kerzendaten fertig: 1 Symbol(e) aktuell"]


def test_server_data_update_job_still_uploads_with_skipped(worker, monkeypatch):
    _fake_cache(monkeypatch, ["BTCUSDT", "OLDSYM"])
    _no_reporter(monkeypatch, worker)
    uploads = []
    monkeypatch.setattr(worker, "upload_result", lambda jid, p: uploads.append((jid, p)))

    async def dl(jobd, syms, days):
        if syms == ["OLDSYM"]:
            raise RuntimeError("weg")
        return []
    monkeypatch.setattr(worker, "_download_symbols", dl)
    worker.run_data_job("lj_server1", "data_update", {})
    (jid, p), = uploads
    assert jid == "lj_server1" and p["status"] == "done"
    assert p["summary"]["symbols"] == ["BTCUSDT"] and p["summary"]["skipped"][0]["symbol"] == "OLDSYM"



def test_flush_pending_results_deletes_leftover_auto_files(worker, monkeypatch, tmp_path):
    """Altlast älterer Worker-Versionen: pending auto-*.json.gz Dateien dürfen
    NICHT zum Server hochgeladen werden (der kennt die Job-IDs nicht), sondern
    müssen beim nächsten flush_pending_results gelöscht werden. Nicht-auto Jobs
    werden wie bisher hochgeladen."""
    pending_dir = worker._pending_dir()
    pending_dir.mkdir(parents=True, exist_ok=True)
    auto_file = pending_dir / "auto-deadbeef.json.gz"
    auto_file.write_bytes(b"legacy-garbage")
    server_file = pending_dir / "lj_srv42.json.gz"
    server_file.write_bytes(b"real-payload")

    uploaded = []
    def fake_try_upload(job_id, raw):
        uploaded.append((job_id, raw))
        return True
    monkeypatch.setattr(worker, "_try_upload", fake_try_upload)

    worker.flush_pending_results()

    assert not auto_file.exists(), "auto-*.json.gz muss gelöscht werden (Server kennt den Job nicht)"
    assert not server_file.exists(), "erfolgreich hochgeladene Server-Jobs werden entfernt"
    assert uploaded == [("lj_srv42", b"real-payload")], "nur Nicht-auto Jobs werden hochgeladen"
