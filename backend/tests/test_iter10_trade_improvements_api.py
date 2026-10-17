"""Iteration 10 – API-Regressionstests für die 5 neuen Trade-Verbesserungen:
Pagination (offset/total), Trade-Chart-Endpoint, MFE/Peak-Felder in computed.
Nur LESEN – keine Orders, keine Schreiboperationen."""
import os

import pytest
import requests
from dotenv import dotenv_values

frontend_env = dotenv_values("/app/frontend/.env")
base_url = os.environ.get("REACT_APP_BACKEND_URL") or frontend_env.get("REACT_APP_BACKEND_URL")
if not base_url:
    raise RuntimeError("REACT_APP_BACKEND_URL missing")
BASE_URL = base_url.rstrip("/")
TIMEOUT = 60


@pytest.fixture(scope="module")
def client():
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json"})
    return s


def _get(client, path, **params):
    r = client.get(f"{BASE_URL}{path}", params=params or None, timeout=TIMEOUT)
    return r


# --- Modul: GET /api/autotrade/trades (Pagination) ---
class TestTradesPagination:
    def test_closed_page_has_total_and_limit(self, client):
        r = _get(client, "/api/autotrade/trades", status="closed", offset=0, limit=5)
        assert r.status_code == 200, r.text
        d = r.json()
        assert "total" in d, "Feld 'total' fehlt bei status-Query"
        assert isinstance(d["total"], int) and d["total"] > 0
        assert len(d["trades"]) == 5
        assert all(t["status"] == "closed" for t in d["trades"])
        assert all("_id" not in t for t in d["trades"]), "MongoDB _id darf nicht geleakt werden"

    def test_offset_returns_disjoint_page(self, client):
        a = _get(client, "/api/autotrade/trades", status="closed", offset=0, limit=100).json()
        b = _get(client, "/api/autotrade/trades", status="closed", offset=100, limit=100).json()
        assert a["total"] == b["total"]
        ids_a = {t["id"] for t in a["trades"]}
        ids_b = {t["id"] for t in b["trades"]}
        assert len(ids_a) == 100
        assert not (ids_a & ids_b), "offset=100 überschneidet sich mit offset=0"

    def test_offset_beyond_total_is_empty(self, client):
        total = _get(client, "/api/autotrade/trades", status="closed", limit=1).json()["total"]
        d = _get(client, "/api/autotrade/trades", status="closed", offset=total + 50, limit=10).json()
        assert d["trades"] == []
        assert d["total"] == total

    def test_no_status_no_total_and_open_included(self, client):
        d = _get(client, "/api/autotrade/trades", limit=5).json()
        assert "total" not in d, "Rückwärtskompatibilität: ohne status kein 'total'"
        open_api = _get(client, "/api/autotrade/trades", status="open", limit=200).json()
        open_ids = {t["id"] for t in open_api["trades"]}
        got_ids = {t["id"] for t in d["trades"]}
        assert open_ids <= got_ids, "offene Trades fehlen im Default-Fenster"


# --- Modul: MFE / peak_price in _enrich_trade ---
class TestPeakFields:
    def test_computed_peak_fields_present(self, client):
        d = _get(client, "/api/autotrade/trades", status="closed", limit=20).json()
        assert d["trades"], "keine geschlossenen Trades vorhanden"
        for t in d["trades"]:
            c = t.get("computed") or {}
            for f in ("peak_price", "peak_distance_pct", "mfe_pct"):
                assert f in c, f"computed.{f} fehlt bei {t['id']}"

    def test_old_closed_trade_peak_is_null(self, client):
        d = _get(client, "/api/autotrade/trades", status="closed", offset=200, limit=20).json()
        if not d["trades"]:
            pytest.skip("nicht genug Alt-Trades")
        for t in d["trades"]:
            if t.get("peak_price") in (None, 0):
                assert t["computed"]["peak_price"] is None
                return
        pytest.skip("alle Trades in diesem Fenster haben peak_price")

    def test_open_trade_peak_direction(self, client):
        d = _get(client, "/api/autotrade/trades", status="open", limit=50).json()
        if not d["trades"]:
            pytest.skip("keine offenen Trades")
        for t in d["trades"]:
            pk = (t.get("computed") or {}).get("peak_price")
            if pk is None:
                continue
            entry = float(t["entry"])
            if t["side"] == "LONG":
                assert pk >= entry * 0.999, f"LONG peak {pk} < entry {entry}"
            else:
                assert pk <= entry * 1.001, f"SHORT peak {pk} > entry {entry}"


# --- Modul: GET /api/autotrade/trades/{id}/chart ---
class TestTradeChart:
    @pytest.fixture(scope="class")
    def closed_trade(self, client):
        d = _get(client, "/api/autotrade/trades", status="closed", limit=5).json()
        assert d["trades"]
        return d["trades"][0]

    def test_chart_structure_and_coverage(self, client, closed_trade):
        r = _get(client, f"/api/autotrade/trades/{closed_trade['id']}/chart")
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["symbol"] == closed_trade["symbol"]
        assert d["interval"] in ("1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "1d")
        candles = d["candles"]
        assert isinstance(candles, list) and len(candles) > 0, "keine Kerzen geliefert"
        tr = d["trade"]
        for f in ("side", "entry", "sl", "initial_sl", "tp1", "tpf",
                  "exit_price", "peak_price", "opened_ts", "closed_ts", "status"):
            assert f in tr, f"trade.{f} fehlt"
        # Kerzen-Zeit in SEKUNDEN (nicht ms)
        assert candles[0]["time"] < 4_000_000_000, "candle.time ist nicht in Sekunden"
        for k in ("open", "high", "low", "close"):
            assert isinstance(candles[0][k], (int, float))
        assert candles[0]["time"] <= tr["opened_ts"] <= candles[-1]["time"], \
            f"Kerzen decken opened_ts nicht ab: {candles[0]['time']}..{candles[-1]['time']} vs {tr['opened_ts']}"
        if tr["closed_ts"]:
            assert candles[0]["time"] <= tr["closed_ts"] <= candles[-1]["time"] + 86400

    def test_chart_multiple_trades(self, client):
        d = _get(client, "/api/autotrade/trades", status="closed", limit=5).json()
        ok = 0
        for t in d["trades"]:
            r = _get(client, f"/api/autotrade/trades/{t['id']}/chart")
            assert r.status_code == 200, f"{t['id']}: {r.status_code} {r.text[:200]}"
            j = r.json()
            assert len(j["candles"]) > 0, f"{t['id']} ({t['symbol']}): leere candles"
            ok += 1
        assert ok == len(d["trades"])

    def test_chart_unknown_id_404(self, client):
        r = _get(client, "/api/autotrade/trades/DOES-NOT-EXIST-123/chart")
        assert r.status_code == 404, r.status_code
