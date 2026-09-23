"""Regressionstests Session 06/2026: VWAP-Prompt, KI-Setup-Enum, Mindest-Trade (nur Live),
Telegram-Katalog, Verlauf-Aufteilung, Optimizer-Ausreißer, Score-Text, Endlos-Suche."""
import sys

sys.path.insert(0, "/app/backend")

from services import ai_playbook, min_trade, notifications, optimizer_outliers, vwap_context  # noqa: E402
from services.optimizer import score_text  # noqa: E402

DAY = 86_400_000


def _candles(prices, start=None, vol=10.0):
    start = start if start is not None else (1_750_000_000_000 // DAY) * DAY
    out = []
    for i, p in enumerate(prices):
        out.append({"timestamp": start + i * 60_000, "open": p, "high": p * 1.001,
                    "low": p * 0.999, "close": p, "volume": vol})
    return out


# ---------------- VWAP ----------------
def test_vwap_distance_and_reclaim():
    # 90 min unter 100, dann Rückeroberung über den VWAP
    prices = [100 - 0.5] * 90 + [99.0] * 30 + [101.0] * 10
    c = _candles(prices)
    v = vwap_context.compute(c)
    assert v and v["side"] == "above" and v["dist_pct"] > 0
    assert v["state"] == "reclaim"
    line = vwap_context.context_line(c)
    assert "VWAP(Tag)" in line and "RECLAIM" in line


def test_vwap_loss_short():
    prices = [100 + i * 0.01 for i in range(120)] + [100.5] * 10
    v = vwap_context.compute(_candles(prices))
    assert v["side"] == "below" and v["state"] == "loss"


def test_vwap_none_without_volume_or_unanchored():
    assert vwap_context.compute(_candles([100.0] * 60, vol=0)) is None
    # Buffer startet mitten am Tag -> nicht tagesverankert
    late = (1_750_000_000_000 // DAY) * DAY + 3 * 3_600_000
    assert vwap_context.compute(_candles([100.0] * 60, start=late)) is None
    assert vwap_context.compute([]) is None


# ---------------- KI-eigene Setups im Prompt-Schema ----------------
def test_dynamic_setup_enum_includes_custom():
    prompt = 'x "setup": "' + ai_playbook.SETUP_ENUM + '" y'
    ai_playbook.set_custom_cache({})
    assert ai_playbook.with_dynamic_setup_enum(prompt) == prompt
    ai_playbook.set_custom_cache({"v_reversal": {"desc": "V"}})
    try:
        out = ai_playbook.with_dynamic_setup_enum(prompt)
        assert "|v_reversal" in out and "vwap_reclaim" in out
        assert ai_playbook.normalize_setup("v_reversal") == "v_reversal"
    finally:
        ai_playbook.set_custom_cache({})


# ---------------- Mindest-Trade ----------------
def test_min_trade_screenshot_case_hype():
    # HYPE: Entry 93.916, SL +2 %, 53.5x, ~30 USDT frei -> Mindest-Trade statt Ablehnung
    r = min_trade.plan(93.916, 95.79432, 53.5, min_qty=0.1, qty_step=0.1,
                       exchange_free=30.0, equity=30.0, open_min_trades=0)
    assert r["ok"], r
    assert r["qty"] >= 0.1 and r["margin"] <= 30 and r["risk"] <= 30 * 0.03 + 1e-9


def test_min_trade_shrinks_to_exchange_minimum_when_risky():
    # Ziel-Marge 1 USDT @ 50x = 50 USDT Notional * 2 % = 1 USDT Risiko > 0.9 -> Börsen-Minimum
    r = min_trade.plan(10.0, 10.2, 50, min_qty=1, qty_step=1, exchange_free=30, equity=30)
    assert r["ok"] and r["qty"] == 1


def test_min_trade_limits():
    base = dict(min_qty=1, qty_step=1, equity=30)
    assert not min_trade.plan(10, 10.2, 20, exchange_free=0, **base)["ok"]
    assert not min_trade.plan(10, 10.2, 20, exchange_free=30, open_min_trades=3, **base)["ok"]
    # Risiko des Börsen-Minimums zu hoch (1000 × 0.2 = 200 USDT)
    r = min_trade.plan(10, 10.2, 20, exchange_free=5000, equity=30, min_qty=1000, qty_step=1)
    assert not r["ok"] and "Risiko" in r["reason"]
    assert not min_trade.plan(10, 10.2, 20, exchange_free=30, cfg={"enabled": False}, **base)["ok"]


# ---------------- Telegram-Katalog ----------------
def test_notify_catalog_covers_used_types_and_subtoggles():
    keys = {c["key"] for c in notifications.catalog()}
    for k in ("signals", "signals_ai", "trade_opened_live", "trade_closed_paper", "order_rejected",
              "order_rejected_internal", "min_trade", "funding_guard", "maker_mode", "setup_review",
              "live_gate_bypass", "policy_lab", "key_credits", "copilot_weekly", "confluence"):
        assert k in keys, k
    assert set(notifications.DEFAULT_CONFIG) == keys
    assert notifications.is_internal_reject("Risikobudget: Rest-Budget 0.00 USDT")
    assert not notifications.is_internal_reject("code 30011 insufficient balance")


# ---------------- Verlauf: Paper-Logik vs. Sammlung ----------------
def test_playbook_world_split_keeps_sum():
    rows = [{"_id": {"setup": "range_fade", "dc": False}, "trades": 3, "wins": 2, "pnl": 5.0, "margin": 0, "risk_usdt": 0},
            {"_id": {"setup": "range_fade", "dc": True}, "trades": 7, "wins": 3, "pnl": -2.0, "margin": 0, "risk_usdt": 0}]
    st = ai_playbook._rows_to_stats(rows, True)["range_fade"]
    assert st["trades"] == 10 and st["wins"] == 5 and st["pnl"] == 3.0
    assert st["paper"]["trades"] == 3 and st["collection"]["trades"] == 7
    f = ai_playbook._world_fields("coll", st["collection"])
    assert f == {"coll_trades": 7, "coll_winrate": 43, "coll_pnl": -2.0}


# ---------------- Optimizer ----------------
def test_outlier_detection_minority_only():
    per = {"BTC": {"pnl": 40, "trades": 30, "win_rate": 55}, "ETH": {"pnl": 35, "trades": 28, "win_rate": 52},
           "SOL": {"pnl": 30, "trades": 25, "win_rate": 50}, "XRP": {"pnl": 25, "trades": 20, "win_rate": 51},
           "HYPE": {"pnl": -120, "trades": 22, "win_rate": 30}, "ADA": {"pnl": 20, "trades": 19, "win_rate": 50}}
    ol = optimizer_outliers.detect(per)
    assert ol["outliers"] == ["HYPE"] and "HYPE" not in ol["kept"]
    # Mehrheit schwach -> kein Ausreißer-, sondern Strategie-Problem
    bad = {k: {"pnl": -10 - i, "trades": 20} for i, k in enumerate("ABCDE")}
    assert optimizer_outliers.detect(bad)["outliers"] == []
    # zu wenige Assets / zu wenige Trades
    assert optimizer_outliers.detect({"A": {"pnl": 5, "trades": 9}, "B": {"pnl": -50, "trades": 9}})["outliers"] == []
    few = dict(per, HYPE={"pnl": -120, "trades": 2})
    assert optimizer_outliers.detect(few)["outliers"] == []


def test_recommendation_levels():
    good = {"metrics": {"pnl": 10}, "test_metrics": {"pnl": 3}, "passed": True, "positive_symbols_pct": 80}
    assert optimizer_outliers.recommendation(good)["level"] == "recommended"
    ex = {**good, "positive_symbols_pct": 40,
          "outlier_variant": {"metrics": {"pnl": 20}, "test_metrics": {"pnl": 4}}}
    assert optimizer_outliers.recommendation(ex)["level"] == "recommended_ex"
    assert optimizer_outliers.recommendation({**good, "passed": False})["level"] == "usable"
    assert optimizer_outliers.recommendation({"metrics": {"pnl": -1}})["level"] == "rejected"


def test_score_text_no_raw_penalty():
    assert score_text(-500003228.66).startswith("DD-Filter verletzt")
    assert "-3228.66" in score_text(-500003228.66)
    assert score_text(-1e9 + 12).startswith("zu wenige Trades (12)")
    assert score_text(123.456) == "123.46"


# ---------------- Setup-Nutzung ----------------
def test_setup_usage_funnel_and_never_chosen():
    from services import setup_usage
    decs = [{"setup": "range_fade", "signaled": True, "confidence": 70},
            {"setup": "range_fade", "blocked_by": "Richtungs-Guard: bereits 3 offene SHORT-Risiken (A, B) – Limit 3"},
            {"setup": "range_fade", "confidence": 40},
            {"setup": "divergence", "confidence": 60}]
    trades = [{"setup": "range_fade", "mode": "live"}, {"setup": "range_fade", "data_collection": True}]
    rows = {r["setup"]: r for r in setup_usage.summarize(decs, trades, {"range_fade": "x", "vwap_reclaim": "y",
                                                                         "v_rev": "[KI-Setup] z"})}
    rf = rows["range_fade"]
    assert rf["decisions"] == 3 and rf["signaled"] == 1 and rf["low_conf"] == 1
    assert rf["trades_real"] == 1 and rf["trades_collection"] == 1
    assert rf["top_reasons"][0]["reason"].startswith("Richtungs-Guard")
    assert rows["vwap_reclaim"]["never_chosen"] and rows["v_rev"]["custom"]
    assert rows["divergence"]["top_reasons"][0]["reason"].startswith("Cooldown")


def test_outlier_variant_evaluates_without_excluded(monkeypatch):
    import asyncio
    from services import optimizer as opt
    seen = {}

    async def fake_eval(job, pool, items, hists, fsm, stop):
        seen.setdefault("calls", []).append(sorted(hists))
        return [{"pnl": 50.0 if "HYPE" not in hists else -10.0, "trades": 40, "win_rate": 55}]
    monkeypatch.setattr(opt, "_evaluate_batch", fake_eval)
    ol = {"outliers": ["HYPE"], "kept": ["BTC", "ETH"], "reasons": {"HYPE": "x"}}
    hist = {"BTC": [1], "ETH": [1], "HYPE": [1]}
    res = asyncio.run(opt._outlier_variant({}, ol, None, {}, {}, hist, None, hist, {"BTC": 0, "ETH": 0, "HYPE": 0},
                                           None, {"pnl": 20.0}))
    assert seen["calls"] == [["BTC", "ETH"], ["BTC", "ETH"]]
    assert res["excluded"] == ["HYPE"] and res["pnl_gain"] == 30.0 and res["test_metrics"]["pnl"] == 50.0


class _Coll:
    async def find_one(self, *a, **k):
        return None

    async def count_documents(self, *a, **k):
        return 0


class _DB:
    settings = _Coll()
    auto_trades = _Coll()


class _Client:
    def configured(self):
        return True

    def to_bitunix_symbol(self, s):
        return s

    def contract_meta(self, s):
        return {"min_qty": 0.1, "qty_step": 0.1}


def _mgr():
    from services.bitunix_trade import AutoTradeManager
    from services import entry_guard
    m = AutoTradeManager.__new__(AutoTradeManager)
    m.db, m.client = _DB(), _Client()

    async def bal():
        return 30.0
    m._live_available_balance = bal
    m._live_total_balance = bal
    return m, entry_guard.EntryChecks()


def test_try_min_trade_live_opens_minimum_and_skips_collection():
    import asyncio
    min_trade._cfg_cache = None
    m, checks = _mgr()
    sig = {"symbol": "HYPEUSDT", "type": "SHORT"}
    res = asyncio.run(m._try_min_trade(sig, checks, 93.916, 95.79432, 53.5, "Risikobudget erschöpft"))
    assert res and res[1] >= 0.1 and sig["_min_trade"] and "MINDEST-TRADE" in sig["_min_trade_note"]
    coll = {"symbol": "HYPEUSDT", "type": "SHORT", "data_collection": True}
    assert asyncio.run(m._try_min_trade(coll, checks, 93.9, 95.8, 53.5, "x")) is None
    assert "_min_trade" not in coll


def test_min_trade_only_wired_for_live_paths():
    import inspect
    from services.bitunix_trade import AutoTradeManager
    src = inspect.getsource(AutoTradeManager._on_signal_impl)
    # Kapital-Grenze: Mindest-Trade nur im Live-Zweig, vor dem alten Paper-Ausweichen
    i_mt = src.index('Kapital-Grenze erreicht (')
    assert 'if fit["reject"] and mode == "live" and not use_ibkr:' in src[:i_mt]
    assert src.index("Live-Kapitalgrenze erreicht -> stattdessen Paper-Trade") > i_mt
    fit_src = inspect.getsource(AutoTradeManager._fit_risk_budget)
    assert 'if mode == "live":' in fit_src and "_try_min_trade" in fit_src


def test_win_rate_objective_never_prefers_losers():
    from services.optimizer import _score
    loser = _score({"trades": 50, "win_rate": 60, "pnl": -500}, "win_rate", 10)
    winner = _score({"trades": 50, "win_rate": 55, "pnl": 1000}, "win_rate", 10)
    assert winner > loser


# ---------------- Mindest-Trade-Übersicht ----------------
def test_min_trade_summary_r_multiple():
    rows = [{"status": "closed", "realized_pnl": 0.3, "fees_paid": 0.01, "risk_usdt": 0.2},
            {"status": "closed", "realized_pnl": -0.2, "fees_paid": 0.01, "risk_usdt": 0.2},
            {"status": "open"}]
    s = min_trade.summarize(rows)
    assert s["trades"] == 2 and s["open"] == 1 and s["winrate"] == 50.0
    assert s["pnl"] == 0.1 and s["r_multiple"] == 0.25
    assert min_trade.summarize([])["r_multiple"] is None


# ---------------- Regime-Lab-Hinweise ----------------
def test_regime_advice_flags_user_settings_and_missing_reference():
    from services import regime_advice as ra
    adv = ra.settings_advice("15m", 1080, 11, 1.0, 0.0)
    assert any("Obergrenze" in a for a in adv) and any("flackert" in a for a in adv)
    assert any("Timeframe 15m" in a for a in adv)
    assert ra.settings_advice("1h", 720, 5, 4.0, 14.0) == []
    w = ra.result_warnings({"direction_pct": 98.7, "avg_live_phase_days": 29.4}, 3.0, 15.0)
    assert any("Referenz" in x for x in w) and any("über der Obergrenze" in x for x in w)
    assert ra.result_warnings({"direction_pct": 70, "reference_pct": 55, "avg_live_phase_days": 8}, 4, 14) == []
