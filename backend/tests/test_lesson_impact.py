"""PLAN_LEKTIONS_BILANZ Baustein C – Bilanz-Urteile an Grenzwerten, Prompt-Block, API-Cache."""
import asyncio

from services import lesson_impact as li

L = [{"id": "les_a", "title": "A", "weight": 3}, {"id": "les_b", "title": "B", "locked": True}]


def _t(i, result, r, lessons=(), sym="BTCUSDT", coll=False):
    return {"id": f"t{i}", "symbol": sym, "result": result, "realized_pnl": r * 10, "risk_usdt": 10,
            "closed_at": f"2026-09-{(i % 28) + 1:02d}T10:00:00+00:00",
            "applied_lessons": list(lessons), "data_collection": coll}


def test_trade_r_and_group_stats_min_group():
    assert li.trade_r({"realized_pnl": 5, "risk_usdt": 10}) == 0.5
    assert li.trade_r({"realized_pnl": 5}) is None
    g = li._group_stats([_t(i, "win", 1.0) for i in range(4)])
    assert g["n"] == 4 and g["wr"] is None and g["avg_r"] is None       # n<5 -> "—"


def test_too_few_data_11_vs_12():
    trades = [_t(i, "win", 1.0, ["les_a"]) for i in range(11)]
    rows = li.impact_rows(L, trades, {}, {"min_removal_results": 12})
    a = next(r for r in rows if r["id"] == "les_a")
    assert a["verdict"] == "zu_wenig_daten" and a["suggestion"] == "weiter_sammeln"
    trades.append(_t(11, "win", 1.0, ["les_a"]))
    rows = li.impact_rows(L, trades, {}, {"min_removal_results": 12})
    a = next(r for r in rows if r["id"] == "les_a")
    assert a["verdict"] != "zu_wenig_daten"


def test_hinderlich_boundary_minus_099_vs_minus_1():
    trades = [_t(i, "win", 0.5, ["les_a"]) for i in range(8)]
    cf = {"les_a": {"n": 6, "would_win": 5, "would_loss": 1, "net_r": -0.99,
                    "avoided_loss_r": 0.5, "missed_gain_r": 1.49}}
    a = li.impact_rows(L, trades, cf)[0]
    assert a["verdict"] != "hinderlich"
    cf["les_a"]["net_r"] = -1.0
    a = li.impact_rows(L, trades, cf)[0]
    assert a["verdict"] == "hinderlich" and a["suggestion"] == "lockern"
    cf["les_a"]["n"] = 5      # zu wenige verhinderte -> kein Urteil "hinderlich"
    a = li.impact_rows(L, trades, cf)[0]
    assert a["verdict"] != "hinderlich"


def test_edge_and_zu_weich_with_control_group_same_class():
    # mit Lektion: 8 Gewinner à +1R; Kontrolle (gleiche Klasse, gleicher Zeitraum): 6 Verlierer
    mine = [_t(i, "win", 1.0, ["les_a"]) for i in range(8)]
    ctrl = [_t(20 + i, "loss", -1.0) for i in range(6)]
    ctrl = [dict(t, closed_at=mine[0]["closed_at"]) for t in ctrl]
    cf = {"les_a": {"n": 4, "would_win": 0, "would_loss": 4, "net_r": 4.0,
                    "avoided_loss_r": 4.0, "missed_gain_r": 0.0}}
    a = li.impact_rows(L, mine + ctrl, cf)[0]
    assert a["verdict"] == "edge" and a["net_contribution_r"] > 0.5
    assert a["without"]["n"] == 6
    # Kontrolle in ANDERER Klasse zählt nicht
    other = [dict(t, symbol="EURUSD") for t in ctrl]
    a2 = li.impact_rows(L, mine + other, cf)[0]
    assert a2["without"]["n"] == 0
    # zu weich: mit Lektion WR 25 %, ohne 100 %
    weak = [_t(i, "win" if i < 2 else "loss", 1.0 if i < 2 else -1.0, ["les_a"]) for i in range(8)]
    strong = [dict(_t(30 + i, "win", 1.0), closed_at=weak[0]["closed_at"]) for i in range(6)]
    a3 = li.impact_rows(L, weak + strong, {"les_a": {"n": 4}})[0]
    assert a3["verdict"] == "zu_weich" and a3["suggestion"] == "verschaerfen"


def test_collection_trades_separated_and_totals():
    trades = [_t(i, "win", 1.0, ["les_a"], coll=True) for i in range(5)] + [_t(9, "win", 1.0)]
    a = li.impact_rows(L, trades, {})[0]
    assert a["with"]["n"] == 0 and a["collection_n"] == 5
    tot = li.totals(trades, [{"status": "done"}, {"status": "no_data"}], 3)
    assert tot["attributed_trades"] == 5 and tot["unattributed_trades"] == 1
    assert tot["cf_done"] == 1 and tot["cf_no_data"] == 1 and tot["cf_pending"] == 3


def test_prompt_block_max_lines_and_locked_hint():
    rows = [{"id": f"les_{i}", "title": f"L{i}", "verdict": "edge", "suggestion": "behalten",
             "with": {"n": 10, "wr": 60.0}, "without": {"n": 8, "wr": 50.0},
             "prevented": {"n": 3, "would_loss": 2}, "net_contribution_r": 1.2} for i in range(12)]
    rows.append({"id": "les_x", "title": "X", "verdict": "zu_wenig_daten", "suggestion": "weiter_sammeln",
                 "with": {"n": 1, "wr": None}, "without": {"n": 0, "wr": None},
                 "prevented": {"n": 0, "would_loss": 0}, "net_contribution_r": None})
    txt = li.prompt_block(rows, max_lines=8)
    lines = txt.splitlines()
    assert lines[0].startswith("=== LEKTIONS-BILANZ")
    assert len(lines) == 1 + 8 + 1
    assert "LOCKED" in lines[-1]
    assert "les_x" not in txt
    assert li.prompt_block([rows[-1]]) == ""
    assert li.evidence_for(rows, "les_x") == ""
    assert "Bilanz:" in li.evidence_for([dict(rows[0], reason="r")], "les_0")


class _Cur:
    def __init__(self, docs):
        self.docs = docs

    def sort(self, *_):
        return self

    async def to_list(self, *_):
        return list(self.docs)


class _Coll:
    def __init__(self, docs):
        self.docs, self.calls = docs, 0

    def find(self, *_):
        self.calls += 1
        return _Cur(self.docs)

    async def count_documents(self, *_):
        return 0

    async def find_one(self, *_ , **__):
        return None


def test_service_report_cache(monkeypatch):
    from services import lesson_counterfactual as cf
    from services.ai_lessons import lesson_store
    svc = li.LessonImpactService()

    class _DB:
        auto_trades = _Coll([_t(i, "win", 1.0, ["les_a"]) for i in range(3)])
        ai_lesson_cf = _Coll([])
        ai_decisions = _Coll([])
        settings = _Coll([])

    db = _DB()
    svc.setup(db)
    cf.counterfactual.setup(db)

    async def _all():
        return list(L)
    monkeypatch.setattr(lesson_store, "all", _all)
    r1 = asyncio.run(svc.report(90))
    r2 = asyncio.run(svc.report(90))
    assert r1["rows"] and r1["totals"]["attributed_trades"] == 3
    assert db.auto_trades.calls == 1 and r2 is r1        # Cache greift
    # Prompt-Block nur mit Flag
    assert asyncio.run(svc.prompt_block({"lesson_impact_in_prompt": False})) == ""
    assert asyncio.run(svc.rows({})) == []
