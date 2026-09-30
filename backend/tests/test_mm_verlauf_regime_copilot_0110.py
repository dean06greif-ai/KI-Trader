"""Regressionstests 01.10.2026: Verlauf-Reiter (Setup-Reife je Welt, Equity-Kurve),
Lektions-Bilanz/-Gegenprobe-Fixes, Regime-Copilot-Wissen (aus dem Code), Regime-Keys.
Rein (ohne DB/Netz)."""
import asyncio
from datetime import datetime, timezone

from routers.ai import _thin_points
from services import lesson_counterfactual as lcf
from services import lesson_impact as li
from services import regime_copilot_knowledge as rck
from services import regime_engine as eng
from services import setup_world_stats as sws
from services import strategy_copilot as sc


# ---------------- Setup-Reife je Welt ----------------
def _row(setup, dc, live, trades, wins, pnl):
    return {"_id": {"setup": setup, "dc": dc, "live": live}, "trades": trades, "wins": wins, "pnl": pnl}


def test_world_of():
    assert sws.world_of("live", None) == "real"
    assert sws.world_of("paper", False) == "paper"
    assert sws.world_of(None, None) == "paper"
    assert sws.world_of("live", True) == "collection"  # Sammlung hat Vorrang


def test_rows_to_worlds_and_display_live_is_real_plus_paper():
    raw = sws.rows_to_worlds([
        _row("breakout", False, True, 4, 3, 12.5),
        _row("breakout", False, False, 6, 2, -4.0),
        _row("breakout", True, False, 10, 5, 1.0),
        _row("breakout", True, True, 1, 1, 0.5),   # Sammel-Trade bleibt Sammlung
    ])
    d = sws.display_worlds(raw["breakout"])
    assert d["real"] == {"trades": 4, "wins": 3, "winrate": 75, "pnl": 12.5}
    assert d["paper"]["trades"] == 6 and d["paper"]["winrate"] == 33
    assert d["live"] == {"trades": 10, "wins": 5, "winrate": 50, "pnl": 8.5}
    assert d["collection"]["trades"] == 11 and d["collection"]["pnl"] == 1.5


def test_display_worlds_empty_and_attach():
    rows = sws.attach([{"setup": "x"}, {"setup": "y"}], {"x": {"real": {"trades": 2, "wins": 1, "pnl": 3}}})
    assert rows[0]["worlds"]["real"]["winrate"] == 50
    assert rows[0]["worlds"]["live"]["trades"] == 2
    assert rows[1]["worlds"]["collection"] == {"trades": 0, "wins": 0, "winrate": 0, "pnl": 0.0}
    assert set(rows[1]["worlds"]) == set(sws.WORLDS)


def test_merge_worlds_sums_classes():
    a = {"s": {"real": {"trades": 1, "wins": 1, "pnl": 2.0}}}
    b = {"s": {"real": {"trades": 2, "wins": 0, "pnl": -1.0}, "paper": {"trades": 3, "wins": 3, "pnl": 3}}}
    m = sws.merge_worlds([a, b])
    assert m["s"]["real"] == {"trades": 3, "wins": 1, "pnl": 1.0}
    assert m["s"]["paper"]["trades"] == 3 and m["s"]["collection"]["trades"] == 0


def test_build_match_windows_like_playbook():
    now = datetime(2026, 10, 1, tzinfo=timezone.utc)
    m = sws.build_match("crypto", {"a": "2026-09-25T00:00:00+00:00", "b": "2026-01-01T00:00:00+00:00",
                                   "c": None}, 30, now=now)
    assert m["status"] == "closed" and m["strategy_id"] == "ai_trader"
    assert "$in" in m["symbol"]
    w = {x["setup"] if isinstance(x["setup"], str) else "rest": x["opened_at"]["$gte"] for x in m["$or"]}
    assert w["a"].startswith("2026-09-25")          # Varianten-Start nach Cutoff
    assert w["b"].startswith("2026-09-01")          # Varianten-Start vor Cutoff -> Cutoff
    assert "c" not in w                              # leere Varianten zählen als "ohne Variante"
    rest = next(x for x in m["$or"] if not isinstance(x["setup"], str))
    assert set(rest["setup"]["$nin"]) == {"a", "b"}


# ---------------- Equity-Kurve ----------------
def test_thin_points_keeps_first_last_and_limit():
    pts = [{"i": i} for i in range(10001)]
    out = _thin_points(pts, 2000)
    assert len(out) == 2000 and out[0]["i"] == 0 and out[-1]["i"] == 10000
    small = [{"i": 1}, {"i": 2}]
    assert _thin_points(small, 2000) is small


# ---------------- Lektions-Bilanz ----------------
def _g(n, avg_r=None, sum_r=0.0):
    return {"n": n, "wr": None, "avg_r": avg_r, "sum_r": sum_r}


def test_contribution_uses_prevented_when_few_applied_trades():
    prev = {"n": 10, "net_r": 2.5}
    assert li.contribution_r(_g(3), _g(20, 0.1), prev) == 2.5      # vorher None
    assert li.contribution_r(_g(0), _g(0), prev) == 2.5
    assert li.contribution_r(_g(3), _g(20, 0.1), {"n": 0, "net_r": 0}) is None


def test_contribution_with_control_group_unchanged():
    got = li.contribution_r(_g(6, sum_r=3.0), _g(30, 0.2), {"n": 2, "net_r": 0.5})
    assert got == round(3.0 - 6 * 0.2 + 0.5, 3)


def test_impact_rows_edge_reachable_via_prevented_only():
    lessons = [{"id": "les_a", "title": "A"}]
    trades = [{"id": f"t{i}", "symbol": "BTCUSDT", "result": "win", "realized_pnl": 1,
               "risk_usdt": 1, "closed_at": f"2026-09-0{i + 1}", "applied_lessons": ["les_a"]}
              for i in range(3)]
    cf = {"les_a": {"n": 12, "would_win": 1, "would_loss": 11, "avoided_loss_r": 9.0,
                    "missed_gain_r": 0.5, "net_r": 8.5}}
    row = li.impact_rows(lessons, trades, cf)[0]
    assert row["net_contribution_r"] == 8.5
    assert row["verdict"] == "edge"


# ---------------- HOLD-Gegenprobe ----------------
def test_configured_fee_forex_uses_ibkr_model():
    assert lcf.configured_fee_pct("EURUSD") < 0.01
    assert lcf.configured_fee_pct("BTCUSDT") == 0.06
    assert lcf.cost_pct("EURUSD") < lcf.cost_pct("EURUSD", fee_pct=0.06)


class _Cursor:
    def __init__(self, rows):
        self.rows = rows

    def sort(self, *_a):
        return self

    def limit(self, *_a):
        return self

    async def to_list(self, *_a):
        return self.rows


class _Coll:
    def __init__(self, rows):
        self.rows = rows

    def find(self, *_a, **_k):
        return _Cursor(self.rows)


class _DB:
    def __init__(self, rows):
        self.ai_decisions = _Coll(rows)


def test_pending_includes_frameless_holds_no_head_of_line_blocking():
    svc = lcf.CounterfactualService()
    bad = [{"id": f"bad{i}", "action": "HOLD", "would_be": {"action": "FLAT"}, "symbol": "BTCUSDT",
            "price": 100, "ts": "2026-09-30T00:00:00+00:00"} for i in range(3)]
    svc.setup(_DB(bad))
    out = asyncio.run(svc._pending(limit=12))
    assert [d["id"] for d in out] == ["bad0", "bad1", "bad2"]
    assert lcf.review_hold(bad[0], [])["status"] == "no_frame"


# ---------------- Regime-Copilot ----------------
def test_regime_keys_unique_per_mode():
    for mode in eng.REGIME_MODES:
        keys = [t["key"] for t in eng.taxonomy(mode)]
        assert len(keys) == len(set(keys)), (mode, keys)
    assert [t["key"] for t in eng.taxonomy(5)][2] == "side"


def test_static_knowledge_contains_all_current_labels_and_rules():
    txt = rck.static_knowledge()
    for mode in eng.REGIME_MODES:
        for t in eng.taxonomy(mode):
            assert f"„{t['label']}“" in txt and t["key"] in txt
    lo, hi = __import__("services.regime_quality", fromlist=["x"]).SWEET_SPOT_DAYS
    assert f"{lo:g}–{hi:g} Tage" in txt
    assert "jump" in txt and "let_run" in txt and "skip_regimes" in txt
    assert "sehr gut ≥10" in txt  # notenabhängige Shadow-Trades


def test_regime_lab_prompt_has_no_stale_rules():
    p = sc.REGIME_LAB_SYSTEM_PROMPT
    assert "4–14" not in p and "≥30 Shadow-Trades" not in p
    assert "REGIME-WISSEN" in p and "MarketMaker" in p
    assert "Bulle/Bär/Seitwärts" not in sc.SYSTEM_PROMPT


def test_regime_lab_context_includes_knowledge_without_db(monkeypatch):
    monkeypatch.setattr(sc.StrategyCopilot, "_db", lambda self: None)
    ctx = asyncio.run(sc.StrategyCopilot()._regime_lab_context_block({"settings": {"timeframe": "1h"}}))
    assert "REGIME-WISSEN" in ctx and "REGIME-LAB-EINSTELLUNGEN" in ctx
    assert asyncio.run(rck.live_snapshot(None)) == ""


def test_snapshot_line_helpers():
    lines = rck._release_lines([{"id": "ra_1", "name": "Krypto 1h", "timeframe": "1h",
                                 "release": {"stage": "shadow", "asset_classes": ["crypto"]},
                                 "settings": {"regime_mode": 5, "engine_config": {"detector": "jump"}}}],
                               {"ra_1": 7})
    assert "Stufe shadow" in lines[0] and "5er-Modus" in lines[0] and "Shadow-Trades 7" in lines[0]
    sl = rck._structural_lines({"BTCUSDT": {"state": "ok", "direction": "up", "label": "Starker Aufwärtstrend",
                                            "stage": "active", "since_days": 3.5},
                                "ETHUSDT": {"state": "unknown"}})
    assert sl == ["- BTCUSDT: Starker Aufwärtstrend (up, Stufe active, ok) seit 3.5 T"]
    dl = rck._dynamic_lines([{"id": "dyn_1", "name": "Dyn", "settings": {"analysis_id": "ra_1"},
                              "runtime_state": {"per_symbol": {"BTCUSDT": {"label": "Seitwärtsmarkt"}}}}],
                            ["dyn_1"])
    assert "gehandelt" in dl[0] and "BTCUSDT: Seitwärtsmarkt" in dl[0]
