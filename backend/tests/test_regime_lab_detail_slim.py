"""Regime-Lab Detail-Endpoint (09/2026): schlankes Dokument ohne Chart-Daten +
Chart je Asset nachladbar. Ohne Netzwerk (Fake-DB)."""
import asyncio
import inspect

import pytest

from routers import regime_lab as rl


class _Coll:
    def __init__(self, doc):
        self.doc = doc
        self.calls = []
        self.updates = []

    async def find_one(self, q, proj=None):
        self.calls.append(proj)
        if q.get("id") != self.doc["id"]:
            return None
        if proj is None:
            return dict(self.doc)
        if any(v == 0 for k, v in proj.items() if k != "_id"):
            return {k: v for k, v in self.doc.items() if proj.get(k, 1) != 0}
        out = {}
        for k in proj:
            if k == "_id":
                continue
            top, _, sub = k.partition(".")
            if top in self.doc:
                out[top] = {sub: self.doc[top].get(sub)} if sub else self.doc[top]
        return out

    async def update_one(self, q, upd):
        self.updates.append((q, upd))


DOC = {"id": "ra_x", "name": "T", "symbols": ["BTCUSDT"], "combined": {"model": {"regimes": []}},
       "per_coin": {"BTCUSDT": {"model": {"regimes": []}}},
       "chart": {"BTCUSDT": [[1, 2.0]]}, "chart_emas": {"BTCUSDT": {"ema9": [[1, 2.0]]}}}


@pytest.fixture
def _db(monkeypatch):
    coll = _Coll(DOC)

    class _S:
        regime_analyses = coll
    monkeypatch.setattr(rl.state, "db", _S(), raising=False)
    monkeypatch.setattr(rl.regime_quality, "summarize", lambda d: {"combined": {}})
    return coll


def test_detail_excludes_charts_by_default(_db):
    res = asyncio.run(rl.get_analysis("ra_x"))
    assert res["charts_included"] is False
    assert "chart" not in res["analysis"] and "chart_emas" not in res["analysis"]
    assert res["analysis"]["combined"]["model"] == {"regimes": []}
    assert _db.calls[-1] == {"chart": 0, "chart_emas": 0}


def test_detail_full_flag_returns_everything(_db):
    res = asyncio.run(rl.get_analysis("ra_x", full=True))
    assert res["charts_included"] is True and res["analysis"]["chart"]["BTCUSDT"] == [[1, 2.0]]


def test_chart_endpoint_returns_one_symbol(_db):
    res = asyncio.run(rl.get_analysis_chart("ra_x", "BTCUSDT"))
    assert res == {"symbol": "BTCUSDT", "prices": [[1, 2.0]], "emas": {"ema9": [[1, 2.0]]}}
    assert _db.calls[-1] == {"_id": 0, "chart.BTCUSDT": 1, "chart_emas.BTCUSDT": 1}


def test_label_migration_uses_targeted_set_not_replace():
    src = inspect.getsource(rl.get_analysis)
    assert "regime_analyses.replace_one" not in src and '"$set"' in src
