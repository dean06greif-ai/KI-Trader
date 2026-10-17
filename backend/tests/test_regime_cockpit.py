"""PLAN_REGIME_COCKPIT: reine Funktionen von regime_cockpit / regime_context / orphan_info."""
import pytest

from services import regime_cockpit as rc
from services import regime_context as rx
from services.dynamic_live import orphan_info

pytestmark = pytest.mark.unit

T0 = 1789725600000  # 2026-09-18T10:00:00+00:00

H = 3600 * 1000


def _prices(n=48, start=100.0, step=1.0):
    return [(i * H, start + i * step) for i in range(n)]


class TestPure:
    def test_observer_direction(self):
        assert rc.observer_direction("trend_up_normal") == "up"
        assert rc.observer_direction("trend_down_volatil") == "down"
        assert rc.observer_direction("range_ruhig") == "side"
        assert rc.observer_direction("drift_normal") == "side"
        assert rc.observer_direction("breakout_normal") is None
        assert rc.observer_direction(None) is None

    def test_ts_ms(self):
        assert rc.ts_ms("2026-09-18T10:00:00+00:00") == T0
        assert rc.ts_ms(T0) == T0
        assert rc.ts_ms(T0 // 1000) == T0
        assert rc.ts_ms("kaputt") is None
        assert rc.ts_ms(None) is None

    def test_segments_merge_same_labels(self):
        pts = [{"ts": 0, "label": "a", "direction": "up"}, {"ts": 1, "label": "a", "direction": "up"},
               {"ts": 2, "label": "b", "direction": "side"}, {"ts": 3, "label": "a", "direction": "up"}]
        seg = rc.segments(pts)
        assert [s["label"] for s in seg] == ["a", "b", "a"]
        assert seg[0]["n"] == 2 and seg[0]["from_ts"] == 0 and seg[0]["to_ts"] == 1
        assert rc.segments([]) == []

    def test_price_at(self):
        p = _prices(5)
        assert rc.price_at(p, 0) == 100.0
        assert rc.price_at(p, H + 1) == 101.0
        assert rc.price_at(p, -1) is None
        assert rc.price_at([], 5) is None


class TestForwardHits:
    def test_up_labels_hit_in_rising_market(self):
        pts = [{"ts": i * H, "label": "trend_up_normal", "direction": "up"} for i in range(10)]
        res = rc.forward_hits(pts, _prices(), 4 * H)
        assert res["n"] == 10 and res["hit_pct"] == 100.0 and res["reliable"]
        assert res["per_label"][0]["label"] == "trend_up_normal"

    def test_down_labels_miss_in_rising_market(self):
        pts = [{"ts": i * H, "label": "trend_down_normal", "direction": "down"} for i in range(10)]
        res = rc.forward_hits(pts, _prices(), 4 * H)
        assert res["hit_pct"] == 0.0

    def test_side_hit_when_move_below_flat(self):
        flat = [(i * H, 100.0) for i in range(48)]
        pts = [{"ts": i * H, "label": "range_ruhig", "direction": "side"} for i in range(6)]
        res = rc.forward_hits(pts, flat, 4 * H, flat_pct=0.5)
        assert res["hit_pct"] == 100.0
        res2 = rc.forward_hits(pts, _prices(), 4 * H, flat_pct=0.5)
        assert res2["hit_pct"] == 0.0

    def test_points_without_future_price_are_skipped(self):
        pts = [{"ts": 46 * H, "label": "trend_up_normal", "direction": "up"},
               {"ts": 47 * H, "label": "trend_up_normal", "direction": "up"}]
        res = rc.forward_hits(pts, _prices(), 4 * H)
        assert res["n"] == 0 and res["hit_pct"] is None and not res["reliable"]

    def test_no_direction_ignored(self):
        pts = [{"ts": 0, "label": "breakout_normal", "direction": None}]
        assert rc.forward_hits(pts, _prices(), 4 * H)["n"] == 0

    def test_median_flat_default(self):
        pts = [{"ts": i * H, "label": "trend_up_normal", "direction": "up"} for i in range(4)]
        res = rc.forward_hits(pts, _prices(), 4 * H)
        assert res["flat_pct"] > 0


class TestAgreement:
    def test_agreement_counts_matching_direction(self):
        obs = [{"ts": 10, "direction": "up"}, {"ts": 20, "direction": "side"}, {"ts": 30, "direction": "up"}]
        st = [{"ts": 0, "direction": "up"}]
        a = rc.agreement(obs, st)
        assert a["n"] == 3 and a["agree_pct"] == 66.7

    def test_agreement_none_without_structural(self):
        assert rc.agreement([{"ts": 1, "direction": "up"}], []) is None

    def test_observer_before_structural_start_ignored(self):
        obs = [{"ts": 5, "direction": "up"}]
        st = [{"ts": 10, "direction": "up"}]
        assert rc.agreement(obs, st)["n"] == 0


class TestHistory:
    def test_changed_on_first_or_on_switch(self):
        ctx = {"regime_id": 2, "direction": "up", "state": "ok", "stage": "shadow"}
        assert rc.history_changed(None, ctx, 0)
        prev = {**ctx, "ts": 0}
        assert not rc.history_changed(prev, ctx, 1000)
        assert rc.history_changed(prev, {**ctx, "direction": "down"}, 1000)
        assert rc.history_changed(prev, {**ctx, "state": "stale"}, 1000)
        assert rc.history_changed(prev, ctx, rc.HEARTBEAT_S * 1000)

    def test_history_entry_fields(self):
        e = rc.history_entry("BTCUSDT", {"regime_id": 1, "direction": "side", "phase": "seitwärts",
                                         "state": "ok", "stage": "active", "aid": "ra_x"}, T0)
        assert e["symbol"] == "BTCUSDT" and e["at"].startswith("2026-09-18T10:00")
        assert e["phase"] == "seitwärts" and e["aid"] == "ra_x"


class TestTradesAndSummary:
    def test_trades_to_markers(self):
        m = rc.trades_to_markers([{"id": "t1", "side": "LONG", "opened_at": "2026-09-18T10:00:00+00:00",
                                   "entry": 1.0, "realized_pnl": 2.345, "result": "win",
                                   "entry_market_snapshot": {"features": {"regime": "trend_up_normal"},
                                                             "structural": {"phase": "bulle"}}}])[0]
        assert m["pnl"] == 2.35 and m["regime"] == "trend_up_normal" and m["structural"] == "bulle"
        assert m["opened_ts"] == T0 and m["closed_ts"] is None

    def test_summary_of(self):
        s = rc.summary_of({"symbol": "X", "days": 14,
                           "observer": {"hits": {"hit_pct": 44.0, "n": 20, "reliable": True},
                                        "current": {"label": "range_ruhig"}},
                           "structural": {"stage": "none", "hits": None, "current": None},
                           "trades": [{"pnl": 1.0, "result": "win"}, {"pnl": -2.0, "result": "loss"},
                                      {"pnl": None, "result": "open"}],
                           "agreement": None})
        assert s["trades"] == 2 and s["wins"] == 1 and s["pnl"] == -1.0
        assert s["observer_hit_pct"] == 44.0 and s["structural_current"] is None


class TestContextBlock:
    def test_empty_when_nothing_reliable(self):
        assert rx.format_block([], [], [{"symbol": "X", "observer_reliable": False}], {}) == ""

    def test_block_contains_rows_and_rule(self):
        blk = rx.format_block(
            [{"regime": "range_ruhig", "trades": 12, "win_rate": 25.0, "pnl": -30.5, "avg_reward": -0.4},
             {"regime": "trend_up_normal", "trades": 2, "win_rate": 100.0, "pnl": 5, "avg_reward": 1}],
            [{"regime": "unbekannt", "trades": 14}, {"regime": "strukturell bär", "trades": 4, "win_rate": 75.0, "pnl": 9.0}],
            [{"symbol": "BTCUSDT", "days": 14, "observer_hit_pct": 44.0, "observer_n": 300, "observer_reliable": True,
              "observer_current": "drift_normal", "structural_current": "bär", "agreement_pct": 61.0}],
            {"crypto": {"label": "Krypto", "stage": "shadow", "overall": {"grade": "gut", "pct": 71.0, "basis": "holdout"}}})
        assert "REGIME-BILANZ" in blk
        assert "range_ruhig: 12 Trades · WR 25%" in blk
        assert "trend_up_normal" not in blk          # < MIN_TRADES_ROW
        assert "unbekannt" not in blk
        assert "strukturell bär: 4 Trades" in blk
        assert "BTCUSDT: 44% (300 Punkte)" in blk and "kaum besser als Zufall" in blk
        assert "strukturell bär" in blk and "Ebenen einig 61%" in blk
        assert "Krypto: Note gut (71% Live=Final" in blk
        assert blk.strip().endswith("Trefferquote begründen.")


class TestOrphan:
    def test_orphan_info(self):
        assert orphan_info({"settings": {}}, False) == {"orphaned": False}
        assert orphan_info({"settings": {"analysis_id": "ra_1"}}, True) == {"orphaned": False}
        o = orphan_info({"settings": {"analysis_id": "ra_1"}}, False)
        assert o["orphaned"] and "ra_1" in o["orphan_reason"]
