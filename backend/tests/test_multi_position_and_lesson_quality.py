"""Regressionstests für die Verbesserungen vom 22.08.

1. Multi-Positions-Fix: mehrere gleichzeitige Bitunix-Positionen (auch auf
   demselben Symbol, z.B. Hedge Long+Short oder zwei getrennte Positionen)
   werden als GETRENNTE Trades übernommen und einzeln geschlossen verbucht.
2. Lektions-Qualität: keine pauschalen Hebel-Deckel-Lektionen, keine
   Richtungs-Lektionen ohne Marktkontext, Lektionen mit Verfallsdatum.
3. MasterPrompt: kein unsichtbarer Hebel-Deckel-Default (max_leverage=0).
"""
import asyncio
from datetime import datetime, timezone, timedelta

from services.ai_master_prompt import (DEFAULT_RULES, check_lesson_rules,
                                       check_trade_rules, normalize_rules)
from services.ai_lessons import active_lessons, is_expired, normalize
from services.bitunix_trade import (AutoTradeManager, profit_release_plan,
                                    sl_liq_guard)
from services.position_watchdog import PositionWatchdog


# --------------------------- Fakes ---------------------------------------

class FakeCollection:
    def __init__(self, docs=None):
        self.docs = [dict(d) for d in (docs or [])]

    def _match(self, d, q):
        return all(d.get(k) == v for k, v in (q or {}).items()
                   if not isinstance(v, dict))

    async def find_one(self, q, *a, **kw):
        for d in self.docs:
            if self._match(d, q):
                return dict(d)
        return None

    def find(self, q=None, *a, **kw):
        rows = [dict(d) for d in self.docs if self._match(d, q or {})]

        class _Cursor:
            def sort(self, *a, **kw):
                return self

            def limit(self, *a, **kw):
                return self

            async def to_list(self, n=None):
                return rows

            def __aiter__(self):
                self._it = iter(rows)
                return self

            async def __anext__(self):
                try:
                    return next(self._it)
                except StopIteration:
                    raise StopAsyncIteration
        return _Cursor()

    async def insert_one(self, doc):
        self.docs.append(dict(doc))

    async def update_one(self, q, upd, upsert=False):
        for d in self.docs:
            if self._match(d, q):
                d.update(upd.get("$set", {}))
                return
        if upsert:
            self.docs.append({**{k: v for k, v in q.items()
                                 if not isinstance(v, dict)},
                              **upd.get("$set", {})})

    async def update_many(self, q, upd):
        class R:
            modified_count = 0
        return R()


class FakeDB:
    def __init__(self, trades=None):
        self.auto_trades = FakeCollection(trades)
        self.settings = FakeCollection()


class FakeClient:
    def __init__(self, positions=None):
        self.positions = positions or []

    def configured(self):
        return True

    def to_bitunix_symbol(self, s):
        return s

    def contract_meta(self, symbol):
        return {}

    async def get_positions(self, symbol=None):
        rows = [p for p in self.positions
                if symbol is None or p.get("symbol") == symbol]
        return {"code": 0, "data": rows}

    async def get_pending_tpsl(self, symbol, position_id=None):
        return {"code": 0, "data": [{"slPrice": 1}]}  # SL vorhanden -> kein Eingriff

    async def get_mark_price(self, symbol):
        return 100.0


def make_watchdog(db, client):
    at = AutoTradeManager(client)
    at.set_db(db)
    wd = PositionWatchdog()
    wd.setup(db, client, at, telegram=None)
    return wd


# ----------------- Multi-Positions: Sichtbarkeit (Watchdog) ----------------

def test_two_positions_same_symbol_side_become_two_trades():
    """Bug-Report: zwei Bitunix-Positionen auf demselben Symbol+Seite wurden
    nur als eine angezeigt – der Symbol+Seite-Fallback band beide an denselben
    lokalen Trade. Jetzt: die zweite Position wird separat übernommen."""
    client = FakeClient(positions=[
        {"symbol": "BTCUSDT", "side": "BUY", "qty": "0.5",
         "avgOpenPrice": "50000", "positionId": "p1", "leverage": "10"},
        {"symbol": "BTCUSDT", "side": "BUY", "qty": "0.2",
         "avgOpenPrice": "51000", "positionId": "p2", "leverage": "20"},
    ])
    db = FakeDB(trades=[{"id": "t1", "status": "open", "mode": "live",
                         "symbol": "BTCUSDT", "side": "LONG", "sl": 49000,
                         "qty": 0.5, "qty_remaining": 0.5, "entry": 50000,
                         "strategy_id": "ai_trader", "leverage": 10}])
    wd = make_watchdog(db, client)
    status = asyncio.run(wd.check(manage=False))
    assert status["adopted"] == 1
    open_rows = db.auto_trades.docs
    assert len(open_rows) == 2
    t1 = next(d for d in open_rows if d["id"] == "t1")
    assert t1["bitunix_position_id"] == "p1"  # Bindung persistiert
    ext = next(d for d in open_rows if d["id"] != "t1")
    assert ext["bitunix_position_id"] == "p2"
    assert ext["external_adopted"] is True


def test_hedge_long_short_bound_separately():
    """Hedge-Modus (Long+Short gleichzeitig): jede Seite bekommt ihren
    eigenen Trade, nichts wird zusammengerechnet."""
    client = FakeClient(positions=[
        {"symbol": "BTCUSDT", "side": "SELL", "qty": "0.088",
         "avgOpenPrice": "76824", "positionId": "ps", "leverage": "48"},
        {"symbol": "BTCUSDT", "side": "BUY", "qty": "0.0104",
         "avgOpenPrice": "72703", "positionId": "pl", "leverage": "200"},
    ])
    db = FakeDB()
    wd = make_watchdog(db, client)
    status = asyncio.run(wd.check(manage=False))
    assert status["adopted"] == 2
    sides = sorted(d["side"] for d in db.auto_trades.docs)
    assert sides == ["LONG", "SHORT"]
    pids = {d["bitunix_position_id"] for d in db.auto_trades.docs}
    assert pids == {"ps", "pl"}


def test_rebind_does_not_steal_claimed_trade():
    """Zweiter Zyklus: beide Positionen bereits gebunden – keine Adoption,
    keine Umbindung."""
    client = FakeClient(positions=[
        {"symbol": "BTCUSDT", "side": "BUY", "qty": "0.5",
         "avgOpenPrice": "50000", "positionId": "p1"},
        {"symbol": "BTCUSDT", "side": "BUY", "qty": "0.2",
         "avgOpenPrice": "51000", "positionId": "p2"},
    ])
    db = FakeDB(trades=[
        {"id": "t1", "status": "open", "mode": "live", "symbol": "BTCUSDT",
         "side": "LONG", "qty": 0.5, "qty_remaining": 0.5, "entry": 50000,
         "bitunix_position_id": "p1", "strategy_id": "ai_trader"},
        {"id": "t2", "status": "open", "mode": "live", "symbol": "BTCUSDT",
         "side": "LONG", "qty": 0.2, "qty_remaining": 0.2, "entry": 51000,
         "bitunix_position_id": "p2", "strategy_id": "external",
         "external_adopted": True},
    ])
    wd = make_watchdog(db, client)
    status = asyncio.run(wd.check(manage=False))
    assert status["adopted"] == 0
    assert len(db.auto_trades.docs) == 2


# ----------- Multi-Positions: externer Close (Bitunix-Sync) ---------------

def test_sync_closes_only_the_gone_position_id():
    """Bug-Report: wurde EINE von zwei Positionen auf demselben Symbol+Seite
    in Bitunix geschlossen, blieb der lokale Trade offen (Symbol+Seite war ja
    noch offen). Jetzt: Abgleich über die Position-ID."""
    client = FakeClient(positions=[
        {"symbol": "BTCUSDT", "side": "BUY", "qty": "0.2",
         "avgOpenPrice": "51000", "positionId": "p2"},
    ])
    db = FakeDB(trades=[
        {"id": "t1", "status": "open", "mode": "live", "symbol": "BTCUSDT",
         "side": "LONG", "qty": 0.5, "qty_remaining": 0.5, "entry": 50000,
         "bitunix_position_id": "p1"},
        {"id": "t2", "status": "open", "mode": "live", "symbol": "BTCUSDT",
         "side": "LONG", "qty": 0.2, "qty_remaining": 0.2, "entry": 51000,
         "bitunix_position_id": "p2"},
    ])
    at = AutoTradeManager(client)
    at.set_db(db)
    booked = []

    async def _fake_book(t):
        booked.append(t["id"])
        return {"result": "win"}
    at._book_external_close = _fake_book
    synced = asyncio.run(at.sync_live_positions())
    assert synced == 1
    assert booked == ["t1"]


# ------------------- Lektions-Qualität (MasterPrompt) ----------------------

def test_default_rules_have_no_hidden_leverage_cap():
    """Bug-Report (Screenshot): die KI meldete eine 'harte Regel Max. Hebel
    25x', die der Trader nie gesetzt hatte. Der Code-Default ist jetzt 0."""
    r = normalize_rules(DEFAULT_RULES)
    assert r["max_leverage"] == 0
    ok, _ = check_trade_rules(DEFAULT_RULES, "BTCUSDT", "LONG", leverage=200)
    assert ok


def test_leverage_cap_lessons_rejected():
    for title, detail in (
        ("Hebel begrenzen", "Longs nur mit max 20x Hebel handeln"),
        ("Shorts drosseln", "Bei Shorts den Hebel auf 10 reduzieren"),
        ("Risiko", "Nicht mehr als 15x Hebel verwenden"),
    ):
        ok, why = check_lesson_rules(DEFAULT_RULES, title, detail)
        assert not ok, f"'{title}' hätte abgelehnt werden müssen"
        assert "Marge" in why or "Hebel" in why


def test_margin_and_sl_lessons_still_allowed():
    ok, why = check_lesson_rules(
        DEFAULT_RULES, "SL bei hoher Volatilität weiter",
        "Bei ATR% > 0.8 den SL-Abstand auf 1.2% erhöhen und die Marge "
        "(capital_pct) auf 50% senken")
    assert ok, why


def test_blanket_direction_lesson_rejected_contextual_allowed():
    ok, why = check_lesson_rules(DEFAULT_RULES, "Shorts meiden",
                                 "Shorts generell vermeiden, Longs bevorzugen")
    assert not ok
    assert "Marktkontext" in why or "Regime" in why or "regime" in why.lower()
    ok, why = check_lesson_rules(
        DEFAULT_RULES, "Shorts nur im Abwärtstrend",
        "Shorts meiden, solange BTC im 4h-Aufwärtstrend über der EMA200 liegt")
    assert ok, why


def test_direction_check_can_be_disabled():
    rules = {**DEFAULT_RULES, "require_direction_context": False,
             "block_leverage_lessons": False}
    ok, _ = check_lesson_rules(rules, "Shorts meiden", "Shorts generell vermeiden")
    assert ok
    ok, _ = check_lesson_rules(rules, "Hebel", "max 10x Hebel nutzen")
    assert ok


# ------------------- Lektionen mit Verfallsdatum ---------------------------

def test_expired_lessons_leave_the_prompt():
    past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    future = (datetime.now(timezone.utc) + timedelta(days=7)).isoformat()
    lessons = [
        normalize({"title": "Regime-Bias Long", "detail": "x",
                   "valid_until": past}),
        normalize({"title": "Noch gültig", "detail": "y",
                   "valid_until": future}),
        normalize({"title": "Ohne Ablauf", "detail": "z"}),
        normalize({"title": "Trader-Regel", "detail": "w",
                   "valid_until": past, "locked": True}),
    ]
    assert is_expired(lessons[0]) is True
    assert is_expired(lessons[3]) is False  # locked verfällt nie
    titles = {l["title"] for l in active_lessons(lessons)}
    assert titles == {"Noch gültig", "Ohne Ablauf", "Trader-Regel"}


# ------------- Gewinnsicherung: Regler + SL/Liq-Schutz ---------------------

def test_profit_release_plan_respects_reduce_pct():
    full = profit_release_plan(1.0, 100.0, 10.0, 100.0, reduce_pct=100)
    half = profit_release_plan(1.0, 100.0, 10.0, 100.0, reduce_pct=50)
    assert full["new_leverage"] == 100.0
    assert abs(half["release"] - full["release"] / 2) < 1e-6
    assert 10.0 < half["new_leverage"] < 100.0
    # Ziel-Hebel unter aktuellem Hebel -> nichts freizusetzen
    assert profit_release_plan(1.0, 100.0, 50.0, 20.0) is None


def test_sl_liq_guard_pulls_sl_behind_liq():
    # LONG, Hebel 100 -> Liq ~99.5; SL 99.0 läge HINTER der Liq
    needed, liq, ok = sl_liq_guard("LONG", 100.0, 100.0, 101.0, 99.0)
    assert ok and needed is not None
    assert needed > liq                     # SL mit Abstand VOR der Liq
    assert needed < 101.0                   # und unter dem Kurs
    # SL bereits sicher vor der Liq -> kein Eingriff
    needed2, _, ok2 = sl_liq_guard("LONG", 100.0, 100.0, 101.0, 99.9)
    assert ok2 and needed2 is None
    # SHORT-Spiegelung
    needed3, liq3, ok3 = sl_liq_guard("SHORT", 100.0, 100.0, 99.0, 101.2)
    assert ok3 and needed3 is not None and needed3 < liq3


def test_sl_liq_guard_blocks_when_too_close_to_price():
    # Kurs quasi auf der nötigen SL-Höhe -> Änderung ablehnen (zu früh)
    needed, _, ok = sl_liq_guard("LONG", 100.0, 100.0, 99.85, 99.0)
    assert not ok and needed is not None


def test_release_secured_margin_defers_until_safe():
    at = AutoTradeManager(FakeClient())
    at.set_db(FakeDB())
    t = {"id": "x", "symbol": "BTCUSDT", "side": "LONG", "mode": "paper",
         "entry": 100.0, "sl": 99.0, "qty": 1.0, "qty_remaining": 1.0,
         "leverage": 10.0, "profit_secure_max_leverage": 100,
         "profit_secure_margin_reduce_pct": 100,
         "profit_secure_sl_liq_buffer_pct": 0.3}
    # Kurs zu nah an der nötigen SL-Höhe -> verschieben, nichts ändern
    upd, ev = {}, []
    asyncio.run(at._release_secured_margin(t, upd, ev, 1.0, price=99.9))
    assert "profit_margin_released" not in upd and "leverage" not in upd
    # Kurs weit genug im Gewinn -> freisetzen + SL hinter die neue Liq ziehen
    upd, ev = {}, []
    asyncio.run(at._release_secured_margin(t, upd, ev, 1.0, price=103.0))
    assert upd.get("profit_margin_released") is True
    assert upd["leverage"] == 100.0
    assert upd["sl"] > upd["liq_price"]
    assert any("Marge" in e for e in ev)


def test_release_secured_margin_caps_at_coin_max_leverage():
    """Der Ziel-Hebel der Gewinnsicherung wird am Max-Hebel des Coins
    gedeckelt (Bitunix-Katalog, z.B. BNB=75x) – nicht pauschal 200x."""
    class CappedClient(FakeClient):
        def max_leverage_for(self, symbol):
            return 75.0
    at = AutoTradeManager(CappedClient())
    at.set_db(FakeDB())
    t = {"id": "x", "symbol": "BNBUSDT", "side": "LONG", "mode": "paper",
         "entry": 100.0, "sl": 99.0, "qty": 1.0, "qty_remaining": 1.0,
         "leverage": 10.0, "profit_secure_max_leverage": 200,
         "profit_secure_margin_reduce_pct": 100,
         "profit_secure_sl_liq_buffer_pct": 0.3}
    upd, ev = {}, []
    asyncio.run(at._release_secured_margin(t, upd, ev, 1.0, price=105.0))
    assert upd.get("profit_margin_released") is True
    assert upd["leverage"] == 75.0
    # Fake ohne max_leverage_for -> Fallback 200 (kein Crash)
    at2 = AutoTradeManager(FakeClient())
    at2.set_db(FakeDB())
    assert at2._coin_max_lev("BTCUSDT") == 200.0
