"""Rest-Kapital-Trading (services/capital_fit.py): reicht das freie Kapital nicht
für die gewünschte Marge, wird mit dem Rest gehandelt – Untergrenze ~1 USDT,
Börsen-Minimum wird beachtet. Gilt für Market-Entries, Live-Limit-Orders und den
Retry nach 'insufficient balance'."""
from services import capital_fit
from services import limit_live_sync


def test_full_capital_when_enough_free():
    r = capital_fit.fit_margin(50, 200, 10, 100)
    assert r["margin"] == 50 and r["note"] is None and r["reject"] is None


def test_rest_capital_used_when_short_paper():
    r = capital_fit.fit_margin(50, 22, 10, 100, live=False)
    assert r["margin"] == 22 and r["reject"] is None
    assert "Rest 22.00" in r["note"]


def test_rest_capital_live_keeps_fee_buffer():
    r = capital_fit.fit_margin(50, 22, 10, 100, live=True)
    assert r["reject"] is None
    assert 21.0 < r["margin"] < 22.0          # 3 % Puffer für Fee/Drift


def test_reject_below_one_usdt():
    r = capital_fit.fit_margin(50, 0.8, 10, 100)
    assert r["reject"] and "Untergrenze" in r["reject"]
    assert capital_fit.fit_margin(50, 0, 10, 100)["reject"]


def test_one_usdt_is_still_traded():
    r = capital_fit.fit_margin(50, 1.0, 10, 100, live=False)
    assert r["reject"] is None and r["margin"] == 1.0


def test_min_qty_bumps_margin_if_affordable():
    # min_qty 0.01 @ 3000 / 10x = 3 USDT nötig, frei 22 -> hochziehen
    r = capital_fit.fit_margin(2, 22, 10, 3000, min_qty=0.01)
    assert r["reject"] is None and r["margin"] >= 3.0
    assert "Börsen-Minimum" in r["note"]


def test_min_qty_rejects_if_not_affordable():
    r = capital_fit.fit_margin(50, 2, 10, 3000, min_qty=0.01)
    assert r["reject"] and "Börsen-Minimum" in r["reject"]


def test_no_free_info_keeps_capital():
    r = capital_fit.fit_margin(50, None, 10, 100)
    assert r["margin"] == 50 and r["reject"] is None


def test_insufficient_balance_detection():
    assert capital_fit.looks_like_insufficient_balance("Insufficient balance")
    assert capital_fit.looks_like_insufficient_balance("margin insufficient, code 30011")
    assert not capital_fit.looks_like_insufficient_balance("price out of range")
    assert not capital_fit.looks_like_insufficient_balance(None)


def test_retry_qty_shrinks_to_exchange_balance():
    q = capital_fit.retry_qty(free_avail=22, lev=10, entry=100, planned_qty=5.0, qty_step=0.001)
    assert q is not None and q < 5.0 and abs(q - 2.134) < 0.01


def test_retry_qty_none_when_not_smaller_or_dust():
    assert capital_fit.retry_qty(22, 10, 100, planned_qty=2.0) is None     # Marge war nicht das Problem
    assert capital_fit.retry_qty(0.5, 10, 100, planned_qty=5.0) is None    # unter Untergrenze
    assert capital_fit.retry_qty(None, 10, 100, planned_qty=5.0) == 5.0 * 0 or \
        capital_fit.retry_qty(None, 10, 100, planned_qty=5.0) is None


def test_limit_live_sync_arms_with_rest_capital_when_scarce():
    cfg = dict(limit_live_sync.DEFAULT_CONFIG)
    fitted = capital_fit.fit_margin(50, 22, 10, 100, live=True)["margin"]
    # knapp (22 - 21.34 < Reserve 25) -> nur nahe am Level, aber mit Rest-Marge erlaubt
    assert limit_live_sync.should_arm(22, fitted, 0.5, cfg) is True
    assert limit_live_sync.should_arm(22, fitted, 3.0, cfg) is False   # zu weit weg
    assert limit_live_sync.should_arm(22, 50, 0.5, cfg) is False       # ungefittete Marge
