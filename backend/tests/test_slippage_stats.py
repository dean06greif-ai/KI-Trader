"""Baustein B: Slippage-Messung (Signalpreis vs. Fill) + Aggregation."""
from core.utils import slippage_aggregate
from services.bitunix_trade import compute_slippage_pct


# ---------- Vorzeichen: + = schlechterer Fill als Signalpreis ----------

def test_long_worse_fill_is_positive():
    assert compute_slippage_pct("LONG", 100.0, 100.2) == 0.2
    assert compute_slippage_pct("LONG", 100.0, 99.9) == -0.1  # besser gefüllt


def test_short_worse_fill_is_positive():
    # SHORT: billiger verkauft = schlechter -> positiv
    assert compute_slippage_pct("SHORT", 100.0, 99.8) == 0.2
    assert compute_slippage_pct("SHORT", 100.0, 100.3) == -0.3  # besser


def test_invalid_prices_return_none():
    assert compute_slippage_pct("LONG", None, 100.0) is None
    assert compute_slippage_pct("LONG", 100.0, 0) is None
    assert compute_slippage_pct("LONG", "x", 100.0) is None


def test_no_negative_zero():
    assert str(compute_slippage_pct("LONG", 100.0, 100.0)) == "0.0"


# ---------- Aggregation nach Strategie × Modus × Order-Art ----------

def _t(**kw):
    t = {"strategy_id": "ai_trader", "mode": "live", "order_kind": "market",
         "side": "LONG", "entry": 100.0, "slippage_pct": 0.1,
         "slippage_usdt": 0.5, "peak_price": 102.0, "trough_price": 99.0}
    t.update(kw)
    return t


def test_aggregate_groups_and_averages():
    rows = slippage_aggregate([
        _t(slippage_pct=0.1), _t(slippage_pct=0.3),
        _t(order_kind="maker", slippage_pct=0.0, slippage_usdt=0.0),
    ])
    by_kind = {r["order_kind"]: r for r in rows}
    assert by_kind["market"]["trades"] == 2
    assert by_kind["market"]["avg_slippage_pct"] == 0.2
    assert by_kind["market"]["total_slippage_usdt"] == 1.0
    assert by_kind["market"]["avg_mfe_pct"] == 2.0   # (102-100)/100
    assert by_kind["market"]["avg_mae_pct"] == -1.0  # (99-100)/100
    assert by_kind["maker"]["trades"] == 1


def test_aggregate_limit_fill_is_own_kind_and_short_signs():
    rows = slippage_aggregate([
        _t(limit_entry=True, side="SHORT", peak_price=98.0, trough_price=101.0),
    ])
    assert rows[0]["order_kind"] == "limit_fill"
    assert rows[0]["avg_mfe_pct"] == 2.0   # SHORT: Tief 98 = +2% in Trade-Richtung
    assert rows[0]["avg_mae_pct"] == -1.0  # SHORT: Hoch 101 = -1% Gegenlauf


def test_aggregate_skips_trades_without_measurement():
    assert slippage_aggregate([_t(slippage_pct=None), {"foo": 1}]) == []
