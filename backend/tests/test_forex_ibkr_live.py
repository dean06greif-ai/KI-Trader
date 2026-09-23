"""Regressionstests: IBKR-Forex-Live (OCA-Legs, Sync, Gateway-Diagnose, Fees).

Läuft komplett offline: IBKRClient wird mit einem Fake-Transport gemockt.
"""
import asyncio
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services import fee_model, ibkr_trade  # noqa: E402
from services import ibkr_client as ic  # noqa: E402
from services.bitunix_trade import fee_guard_check, trade_fee_percent  # noqa: E402


def run(coro):
    return asyncio.run(coro)


# ---------------- reine Funktionen ----------------

def test_plan_legs_splits_on_tp1_percent():
    legs = ibkr_trade.plan_legs(20000, 50)
    assert [l["leg"] for l in legs] == ["tp1", "runner"]
    assert legs[0]["qty"] == 10000 and legs[1]["qty"] == 10000


def test_plan_legs_single_when_tp1_disabled_or_too_small():
    assert ibkr_trade.plan_legs(20000, 0) == [{"leg": "runner", "qty": 20000}]
    assert ibkr_trade.plan_legs(20000, 100) == [{"leg": "runner", "qty": 20000}]
    assert ibkr_trade.plan_legs(1, 50) == [{"leg": "runner", "qty": 1}]
    assert ibkr_trade.plan_legs(20000, "abc") == [{"leg": "runner", "qty": 20000}]


def test_be_price_covers_fees_both_sides():
    be_l = ibkr_trade.be_price("LONG", 1.1000, 0.01)
    be_s = ibkr_trade.be_price("SHORT", 1.1000, 0.01)
    assert be_l > 1.1000 > be_s


def test_pnl_usd_quote_and_base_pairs():
    assert ibkr_trade.pnl_usd("EURUSD", "LONG", 1.10, 1.11, 10000) == pytest.approx(100.0)
    assert ibkr_trade.pnl_usd("EURUSD", "SHORT", 1.10, 1.11, 10000) == pytest.approx(-100.0)
    # USDJPY: PnL in JPY -> USD über Exit-Kurs
    assert ibkr_trade.pnl_usd("USDJPY", "LONG", 150.0, 151.0, 10000) == pytest.approx(10000 / 151.0)


def test_parse_order_status_variants():
    assert ibkr_trade.parse_order_status({"order_status": "Filled", "cum_fill": "5000",
                                          "average_price": "1.1"}) == ("filled", 5000.0, 1.1)
    assert ibkr_trade.parse_order_status({"status": "Submitted"}) == ("submitted", 0.0, None)
    assert ibkr_trade.parse_order_status(None) == ("", 0.0, None)


def test_health_server_detection():
    html = "<!DOCTYPE HTML>\n<html><head><title>Error response</title></head></html>"
    assert ic.looks_like_health_server(501, html)
    assert ic.looks_like_health_server(404, html)
    assert not ic.looks_like_health_server(200, html)
    assert not ic.looks_like_health_server(404, '{"error": "x"}')


def test_map_orders_by_ref_and_extract_ids():
    live = [{"orderId": 11, "order_ref": "A"}, {"orderId": 12, "order_ref": "A-SL"}, {"orderId": 99}]
    assert ic.map_orders_by_ref(live, ["A", "A-SL", "A-TP"]) == {"A": "11", "A-SL": "12"}
    assert ic.extract_order_ids([{"order_id": "7"}, {"foo": 1}]) == ["7"]
    assert ic.extract_order_ids({"_error": "x"}) == []


def test_fx_order_ticket_shape():
    c = ic.IBKRClient()
    o = c.fx_order("U1", 12087792, "SELL", 5000, "STP", "X-SL", price=1.09, parent="X")
    assert o["secType"] == "12087792:CASH" and o["tif"] == "GTC"
    assert o["parentId"] == "X" and o["price"] == 1.09 and o["quantity"] == 5000
    assert "price" not in c.fx_order("U1", 1, "BUY", 5, "MKT", "X")


# ---------------- Gebühren / Fee-Wächter ----------------

def test_forex_fee_percent_min_commission_per_leg():
    pct, min_usd = fee_model.forex_fee_settings()
    one = fee_model.forex_fee_percent(20000, 1)
    two = fee_model.forex_fee_percent(20000, 2)
    assert one == pytest.approx(max(20000 * pct / 100, min_usd) / 20000 * 100)
    assert two >= one  # Mindestkommission je Order -> zwei Legs kosten mehr
    assert fee_model.forex_fee_percent(0) == pct


def test_trade_fee_percent_uses_broker_model():
    cfg = {"fee_percent": 0.06}
    assert trade_fee_percent(cfg) == 0.06
    assert trade_fee_percent(cfg, "BTCUSDT", 10000) == 0.06
    fx = trade_fee_percent(cfg, "EURUSD", 10000, 2)
    assert fx != 0.06 and fx > 0


def test_fee_guard_forex_uses_ibkr_fee_and_two_legs():
    ai = {"fee_guard_enabled": True, "fee_guard_mult": 4, "fee_guard_atr_mult": 0}
    cfg = {"fee_percent": 0.06, "max_capital": 2000, "leverage": 10, "tp1_close_percent": 50}
    # Sehr enger SL (0.005 %): Krypto (0.06 %/Seite) blockt sicher
    ok_crypto, why_crypto = fee_guard_check(ai, cfg, 100.0, 99.995, symbol="BTCUSDT")
    assert not ok_crypto and "IBKR" not in why_crypto
    ok_fx, why_fx = fee_guard_check(ai, cfg, 1.1000, 1.09995, symbol="EURUSD")
    assert not ok_fx and "IBKR" in why_fx
    # Großzügiger SL bei Forex (0.5 %) ist ok
    ok2, _ = fee_guard_check(ai, cfg, 1.1000, 1.0945, symbol="EURUSD")
    assert ok2


# ---------------- Fake-Gateway für Order-Flow ----------------

class FakeGateway:
    """Simuliert /iserver/account/{acct}/orders, orders, order/status, positions."""

    def __init__(self):
        self.calls = []
        self.orders = {}
        self.next_id = 100
        self.position = 0.0
        self.trades = []
        self.status_overrides = {}

    async def req(self, method, path, body=None, params=None):
        self.calls.append((method, path, body))
        if path.endswith("/orders") and method == "POST":
            out = []
            for o in body["orders"]:
                oid = str(self.next_id)
                self.next_id += 1
                self.orders[oid] = {"orderId": oid, "order_ref": o["cOID"], "status": "Submitted",
                                    "conid": o["conid"], **o}
                out.append({"order_id": oid, "order_status": "Submitted"})
            return out
        if path == "/iserver/account/orders":
            return {"orders": list(self.orders.values()), "snapshot": True}
        if path.startswith("/iserver/account/order/status/"):
            oid = path.rsplit("/", 1)[1]
            return self.status_overrides.get(oid, {"order_status": "Submitted"})
        if "/order/" in path and method == "POST":
            oid = path.rsplit("/", 1)[1]
            self.orders[oid].update(body)
            return [{"order_id": oid}]
        if "/order/" in path and method == "DELETE":
            self.orders.pop(path.rsplit("/", 1)[1], None)
            return {"msg": "Request was submitted"}
        if path.endswith("/positions/0"):
            return [{"conid": 12087792, "position": self.position}]
        if path == "/iserver/account/trades":
            return self.trades
        if path == "/portfolio/accounts":
            return [{"id": "U1"}]
        return {}


@pytest.fixture
def gw(monkeypatch):
    fake = FakeGateway()
    monkeypatch.setattr(ic.ibkr_client, "gateway", "https://fake")
    monkeypatch.setattr(ic.ibkr_client, "account_id", "U1")
    monkeypatch.setattr(ic.ibkr_client, "_req", fake.req)

    async def no_sleep(_):
        return None
    monkeypatch.setattr(ic.asyncio, "sleep", no_sleep)
    monkeypatch.setattr(ibkr_trade.asyncio, "sleep", no_sleep)
    return fake


def test_open_live_forex_places_two_oca_brackets(gw):
    res = run(ibkr_trade.open_live_forex("EURUSD", "LONG", 1.1, 1.09, 1.12, 22000,
                                         tp1=1.105, tp1_close_percent=50))
    assert res["ok"] and res["qty"] == 20000
    legs = res["legs"]
    assert [l["leg"] for l in legs] == ["tp1", "runner"]
    assert legs[0]["tp_price"] == 1.105 and legs[1]["tp_price"] == 1.12
    # 6 Tickets: 2x (MKT + STP + LMT), Kinder hängen am jeweiligen Parent
    post = [c for c in gw.calls if c[0] == "POST" and c[1].endswith("/orders")][0]
    tickets = post[2]["orders"]
    assert len(tickets) == 6
    parents = [t for t in tickets if "parentId" not in t]
    assert len(parents) == 2 and all(t["orderType"] == "MKT" for t in parents)
    for t in tickets:
        if "parentId" in t:
            assert t["side"] == "SELL" and t["parentId"] in [p["cOID"] for p in parents]
    assert all(l["order_id"] and l["sl_order_id"] and l["tp_order_id"] for l in legs)
    # Rückwärtskompatible Felder zeigen auf den Runner
    assert res["sl_order_id"] == legs[1]["sl_order_id"]


def test_open_live_forex_single_bracket_without_tp1(gw):
    res = run(ibkr_trade.open_live_forex("EURUSD", "SHORT", 1.1, 1.11, 1.08, 11000))
    assert res["ok"] and len(res["legs"]) == 1 and res["legs"][0]["leg"] == "runner"
    tickets = [c for c in gw.calls if c[1].endswith("/orders") and c[0] == "POST"][0][2]["orders"]
    assert len(tickets) == 3 and tickets[1]["side"] == "BUY"


def test_move_stop_modifies_all_open_legs(gw):
    res = run(ibkr_trade.open_live_forex("EURUSD", "LONG", 1.1, 1.09, 1.12, 22000,
                                         tp1=1.105, tp1_close_percent=50))
    trade = {"id": "t1", "symbol": "EURUSD", "side": "LONG", "ibkr_conid": 12087792,
             "ibkr_legs": res["legs"]}
    mv = run(ibkr_trade.move_stop(trade, 1.095))
    assert mv["ok"]
    mods = [c for c in gw.calls if c[0] == "POST" and "/order/" in c[1]
            and "/orders" not in c[1] and "status" not in c[1]]
    assert len(mods) == 2 and all(c[2]["price"] == 1.095 and c[2]["orderType"] == "STP"
                                  for c in mods)
    assert all(l["sl_price"] == 1.095 for l in trade["ibkr_legs"])


def test_close_live_forex_uses_real_remaining_position(gw):
    res = run(ibkr_trade.open_live_forex("EURUSD", "LONG", 1.1, 1.09, 1.12, 22000,
                                         tp1=1.105, tp1_close_percent=50))
    trade = {"id": "t1", "symbol": "EURUSD", "side": "LONG", "ibkr_conid": 12087792,
             "qty": 20000, "qty_remaining": 20000, "ibkr_legs": res["legs"]}
    gw.position = 10000  # TP1-Leg schon gefüllt -> nur Runner offen
    out = run(ibkr_trade.close_live_forex(trade))
    assert out["ok"] and not out.get("flat")
    dels = [c for c in gw.calls if c[0] == "DELETE"]
    assert len(dels) == 4  # 2x SL + 2x TP storniert
    close = [c for c in gw.calls if c[0] == "POST" and c[1].endswith("/orders")][-1][2]["orders"]
    assert close[0]["quantity"] == 10000 and close[0]["side"] == "SELL"
    gw.position = 0
    out2 = run(ibkr_trade.close_live_forex(trade))
    assert out2["ok"] and out2["flat"]


class FakeColl:
    def __init__(self, docs):
        self.docs = docs
        self.updates = []

    def find(self, q, *_):
        coll = self

        class Cur:
            async def to_list(self, n):
                return [dict(d) for d in coll.docs if all(d.get(k) == v for k, v in q.items())]
        return Cur()

    async def update_one(self, q, upd):
        self.updates.append((q, upd))
        for d in self.docs:
            if d["id"] == q["id"]:
                d.update(upd["$set"])


class FakeDB:
    def __init__(self, docs):
        self.auto_trades = FakeColl(docs)


def _trade(legs):
    return {"id": "t1", "symbol": "EURUSD", "side": "LONG", "mode": "live", "broker": "ibkr",
            "status": "open", "entry": 1.1, "sl": 1.09, "tp1": 1.105, "tpf": 1.12,
            "qty": 20000, "qty_remaining": 20000, "fee_percent": 0.01, "fees_paid": 2.2,
            "realized_pnl": 0.0, "tp1_close_percent": 50, "be_mode": "tp1",
            "breakeven_enabled": True, "ibkr_conid": 12087792, "ibkr_legs": legs, "events": []}


def test_sync_books_tp1_fill_and_moves_runner_sl_to_breakeven(gw):
    res = run(ibkr_trade.open_live_forex("EURUSD", "LONG", 1.1, 1.09, 1.12, 22000,
                                         tp1=1.105, tp1_close_percent=50))
    t = _trade(res["legs"])
    db = FakeDB([t])
    tp1_oid = res["legs"][0]["tp_order_id"]
    gw.status_overrides[tp1_oid] = {"order_status": "Filled", "cum_fill": 10000,
                                    "average_price": 1.1052}
    gw.position = 10000
    n = run(ibkr_trade.sync_open_trades(db))
    assert n == 1
    assert t["tp1_hit"] and t["qty_remaining"] == 10000 and t["status"] == "open"
    assert t["realized_pnl"] == pytest.approx((1.1052 - 1.1) * 10000 - 1.1052 * 10000 * 0.0001, abs=1e-4)
    assert t["breakeven_moved"] and t["sl"] > 1.1
    runner_sl = res["legs"][1]["sl_order_id"]
    assert gw.orders[runner_sl]["price"] == t["sl"]
    assert t["ibkr_legs"][0]["closed"] and not t["ibkr_legs"][1]["closed"]
    # Zweiter Lauf: nichts doppelt verbuchen
    assert run(ibkr_trade.sync_open_trades(db)) == 0


def test_sync_books_close_when_position_flat(gw):
    res = run(ibkr_trade.open_live_forex("EURUSD", "LONG", 1.1, 1.09, 1.12, 22000,
                                         tp1=1.105, tp1_close_percent=50))
    t = _trade(res["legs"])
    t.update({"tp1_hit": True, "qty_remaining": 10000, "realized_pnl": 50.0})
    t["ibkr_legs"][0]["closed"] = True
    db = FakeDB([t])
    gw.position = 0
    gw.trades = [{"conid": 12087792, "side": "S", "price": 1.12, "trade_time_r": 5},
                 {"conid": 12087792, "side": "B", "price": 1.10, "trade_time_r": 1}]
    assert run(ibkr_trade.sync_open_trades(db)) == 1
    assert t["status"] == "closed" and t["exit_price"] == 1.12 and t["closed_by"] == "ibkr_sync"
    assert t["result"] == "win"
    assert t["realized_pnl"] == pytest.approx(50.0 + (1.12 - 1.1) * 10000 - 1.12 * 10000 * 0.0001, abs=1e-4)
    assert t["qty_remaining"] == 0


def test_status_reports_health_server_hint(monkeypatch):
    c = ic.IBKRClient()
    c.gateway = "https://fake"
    html = "<!DOCTYPE HTML>\n<html><head><title>Error response</title></head></html>"

    async def raw(method, url, body=None, params=None):
        if url.endswith("/livez"):
            return 200, "OK"
        if url.endswith("/readyz"):
            return 503, "Not Ready"
        return 501, html
    monkeypatch.setattr(c, "_raw", raw)
    monkeypatch.setattr(ibkr_trade, "ibkr_client", c)
    st = run(ibkr_trade.status())
    assert st["authenticated"] is False
    assert "health-server" in (st["hint"] or "").lower()
    assert st["probe"] == {"livez": 200, "readyz": 503}


def test_gateway_watch_logout_and_readyz_alarm():
    w = ibkr_trade.GatewayWatch()
    # Erster Lauf: eingeloggt, grün -> kein Alarm
    assert w.evaluate(1000, True, 200) == []
    # Logout -> genau ein Alarm, danach nichts mehr solange ausgeloggt
    assert w.evaluate(1030, False, 503) == ["logout"]
    assert w.evaluate(1060, False, 503) == []
    # readyz rot gehört zum selben Ausfall -> KEIN zweiter Alarm (Nachrichten sparen)
    assert w.evaluate(1030 + 299, False, 503) == []
    assert w.evaluate(1030 + 301, False, 503) == []
    # Wieder eingeloggt + grün -> genau eine Entwarnung
    assert w.evaluate(1030 + 500, True, 200) == ["login"]
    assert w.evaluate(1030 + 530, True, 200) == []
    # Nach stabiler OK-Phase (>= STABLE_OK_SEC) ist der Alarm wieder scharf
    t_ok = 1030 + 500
    assert w.evaluate(t_ok + w.STABLE_OK_SEC + 1, True, 200) == []
    assert w.evaluate(t_ok + w.STABLE_OK_SEC + 60, False, 503) == ["logout"]


def test_gateway_watch_flapping_does_not_spam():
    w = ibkr_trade.GatewayWatch()
    # readyz-Ausfall ohne Login-Historie: ein Alarm nach RED_AFTER_SEC
    assert w.evaluate(0, None, 503) == []
    assert w.evaluate(301, None, 503) == ["readyz_red"]
    # kurzer Grün-Blip -> eine Entwarnung, aber Alarm noch nicht wieder scharf
    assert w.evaluate(400, None, 200) == ["readyz_green"]
    # sofort wieder rot: KEIN neuer Alarm (Grün-Phase war nicht stabil)
    assert w.evaluate(430, None, 503) == []
    assert w.evaluate(430 + 400, None, 503) == []
    # erneuter Blip: keine weitere Entwarnung
    assert w.evaluate(900, None, 200) == []
    # erst nach stabiler Grün-Phase wird der Alarm wieder scharf
    assert w.evaluate(900 + w.STABLE_OK_SEC, None, 200) == []
    assert w.evaluate(900 + w.STABLE_OK_SEC + 100, None, 503) == []
    assert w.evaluate(900 + w.STABLE_OK_SEC + 100 + 301, None, 503) == ["readyz_red"]


def test_gateway_watch_no_logout_alarm_without_prior_login():
    w = ibkr_trade.GatewayWatch()
    # Start-Zustand „nie eingeloggt“ (z.B. Health-Server statt Gateway): kein
    # Logout-Alarm, aber readyz-Alarm nach 5 Minuten (auch bei readyz=None)
    assert w.evaluate(0, False, None) == []
    assert w.evaluate(200, False, None) == []
    assert w.evaluate(300, False, None) == ["readyz_red"]


def test_gateway_alerts_send_telegram_and_website(monkeypatch):
    sent = []

    async def tg(db, telegram, ntype, text, **kw):
        sent.append(("tg", ntype, text))
        return True

    async def web(db, ntype, title, message, **kw):
        sent.append(("web", ntype, title))
        return True
    from services import notifications
    from core import state
    monkeypatch.setattr(notifications, "telegram_notify", tg)
    monkeypatch.setattr(notifications, "website_notify", web)
    monkeypatch.setattr(state, "db", object(), raising=False)
    run(ibkr_trade._gateway_alerts(["readyz_red"], "HTTP 501", 503))
    assert ("tg", "ibkr_gateway") == sent[0][:2] and "5 Minuten" in sent[0][2]
    assert sent[1][:2] == ("web", "ibkr_gateway")
    assert "ibkr_gateway" in notifications.DEFAULT_CONFIG


def test_manage_trade_guard_flags_for_ibkr():
    from services.bitunix_trade import AutoTradeManager
    assert AutoTradeManager.is_ibkr_trade({"broker": "ibkr", "mode": "live"})
    assert not AutoTradeManager.is_ibkr_trade({"broker": "ibkr", "mode": "paper"})
    assert not AutoTradeManager.is_ibkr_trade({"mode": "live"})


def test_proxy_module_is_stdlib_only():
    """Der Token-Proxy muss im voyz/ibeam-Image ohne Zusatzpakete laufen."""
    src = open(os.path.join(os.path.dirname(__file__), "..", "..",
                            "ibeam_gateway", "proxy.py")).read()
    import ast
    tree = ast.parse(src)
    mods = {n.names[0].name.split(".")[0] for n in ast.walk(tree)
            if isinstance(n, ast.Import)} | {n.module.split(".")[0] for n in ast.walk(tree)
                                             if isinstance(n, ast.ImportFrom)}
    assert mods <= {"http", "json", "os", "ssl", "sys", "urllib"}
