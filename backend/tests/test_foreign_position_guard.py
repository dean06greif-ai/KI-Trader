"""Regression (Bug-Report 06/2026): Manuelle Bitunix-Trades dürfen von der
Website NIE angefasst werden.

Symptom: Manuell in Bitunix eröffneter Trade mit SL – jeder gelöschte oder
verschobene SL wurde binnen eines Watchdog-Zyklus wieder an dieselbe Stelle
gesetzt ('LIQ-SCHUTZ (move_sl)' im Trade-Verlauf).
Ursache: Watchdog -> sync_position_state -> guard_fill_liq lief auch für
'Manuell (Bitunix)'-Übernahmen und schrieb den Website-SL an die Börse.

Abgedeckt:
  * exakter Bug-Ablauf (mehrere Watchdog-Zyklen) -> kein schreibender Call
  * Website-Trades behalten den Liq-Schutz (keine Regression)
  * alle schreibenden Chokepoints (SL/TP, Close, Marge, Hebel) blocken
  * manage_external=True (Opt-in) hebt die Sperre auf
  * Position-ID-Zuordnung greift nie auf fremde Positionen
"""
import asyncio
from datetime import datetime, timezone

import pytest

from services import trade_ownership
from services.bitunix_trade import AutoTradeManager
from services.position_watchdog import PositionWatchdog, position_newer_than_trade, watchdog

WRITE_CALLS = ("place_tpsl", "modify_tpsl", "cancel_tpsl", "flash_close",
               "adjust_margin", "set_leverage")


class _Coll:
    def __init__(self, docs=None):
        self.docs = [dict(d) for d in (docs or [])]

    def _match(self, d, q):
        return all(not isinstance(v, dict) and d.get(k) == v for k, v in (q or {}).items())

    async def find_one(self, q, *a, **kw):
        return next((dict(d) for d in self.docs if self._match(d, q)), None)

    async def insert_one(self, doc):
        self.docs.append(dict(doc))

    async def update_one(self, q, upd, **kw):
        for d in self.docs:
            if self._match(d, q):
                d.update(upd.get("$set", {}))

    async def update_many(self, q, upd):
        class R:
            modified_count = 0
        return R()

    async def delete_one(self, q):
        self.docs = [d for d in self.docs if not self._match(d, q)]

    async def count_documents(self, q):
        return len([d for d in self.docs if self._match(d, q)])

    def find(self, q=None, *a, **kw):
        rows = [dict(d) for d in self.docs if self._match(d, q)]

        class _C:
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
        return _C()


class _DB:
    def __init__(self, trades=None):
        self.auto_trades = _Coll(trades)
        self.settings = _Coll()
        self.pending_entry_orders = _Coll()
        self.dynamic_transition_locks = _Coll()


class _Client:
    """Bitunix-Stub, protokolliert jeden Call."""

    def __init__(self, positions, tpsl_rows=None, mark=2694.0):
        self.positions = positions
        self.tpsl_rows = tpsl_rows if tpsl_rows is not None else [
            {"id": "user-sl", "slPrice": "2600"}]
        self.mark = mark
        self.calls = []

    def configured(self):
        return True

    def to_bitunix_symbol(self, s):
        return s

    def contract_meta(self, s):
        return {"min_qty": 0.001, "price_tick": 0.01}

    async def get_positions(self, symbol=None):
        return {"code": 0, "data": [dict(p) for p in self.positions]}

    async def get_pending_tpsl(self, symbol, position_id=None):
        return {"code": 0, "data": list(self.tpsl_rows)}

    async def get_mark_price(self, symbol):
        return self.mark

    async def resolve_position_id(self, symbol, side):
        self.calls.append(("resolve_position_id", symbol, side))
        return self.positions[0]["positionId"] if self.positions else None

    async def place_position_tp_sl(self, symbol, position_id, side, **kw):
        self.calls.append(("place_tpsl", position_id, kw.get("sl_price")))
        return {"code": 0, "data": {"orderId": "new"}}

    async def modify_tpsl_order(self, symbol, order_id, **kw):
        self.calls.append(("modify_tpsl", order_id, kw.get("sl_price")))
        return {"code": 0}

    async def cancel_tpsl_order(self, symbol, order_id):
        self.calls.append(("cancel_tpsl", order_id))
        return {"code": 0}

    async def flash_close(self, symbol, position_id, side, qty, full=False):
        self.calls.append(("flash_close", position_id, qty))
        return {"code": 0}

    async def adjust_position_margin(self, symbol, amount, position_id=None, side=None):
        self.calls.append(("adjust_margin", position_id, amount))
        return {"code": 0}

    async def set_leverage(self, symbol, leverage, margin_mode="ISOLATION"):
        self.calls.append(("set_leverage", leverage))
        return {"code": 0}


def _writes(client):
    return [c for c in client.calls if c[0] in WRITE_CALLS]


def _eth_pos(pid="8388865741619796225", qty="5.621", ctime=None):
    row = {"positionId": pid, "symbol": "ETHUSDT", "side": "BUY", "qty": qty,
           "avgOpenPrice": "2682.02", "leverage": "100", "margin": "150.7",
           "liqPrice": "2657.05"}
    if ctime:
        row["ctime"] = str(ctime)
    return row


def _manual_trade(**over):
    """Exakt wie services/position_watchdog._adopt sie anlegt (Prod-Daten ETH)."""
    t = {"id": "ETHUSDT-ext-1", "symbol": "ETHUSDT", "side": "LONG", "mode": "live",
         "status": "open", "entry": 2682.02, "sl": 2413.818, "initial_sl": 2413.818,
         "qty": 5.621, "qty_remaining": 5.621, "leverage": 100, "max_capital": 150.7,
         "strategy_id": "external", "strategy_name": "Manuell (Bitunix)",
         "manual_trade": True, "external_adopted": True,
         "bitunix_position_id": "8388865741619796225", "events": [],
         "opened_at": datetime.now(timezone.utc).isoformat()}
    t.update(over)
    return t


def _bot_trade(**over):
    t = _manual_trade(id="ETHUSDT-bot-1", strategy_id="ai_trader",
                      strategy_name="KI Trader", manual_trade=False)
    t.pop("external_adopted")
    t.update(over)
    return t


def _setup(trades, positions, **client_kw):
    client = _Client(positions, **client_kw)
    db = _DB(trades)
    at = AutoTradeManager(client)
    at.set_db(db)

    async def _avail():
        return 0.0

    async def _noop(*a, **kw):
        return None
    at._live_available_balance = _avail
    at._notify_reject = _noop
    wd = PositionWatchdog()
    wd.setup(db, client, at, telegram=None)
    wd.settings["adopt_grace_sec"] = 0
    wd._notify = _noop
    return wd, at, db, client


@pytest.fixture(autouse=True)
def _default_manage_external():
    old = watchdog.settings.get("manage_external", False)
    watchdog.settings["manage_external"] = False
    yield
    watchdog.settings["manage_external"] = old


# ----------------------------- Eigentums-Regel -----------------------------

def test_is_foreign_rules():
    assert trade_ownership.is_foreign(_manual_trade())
    assert not trade_ownership.is_foreign(_bot_trade())
    assert not trade_ownership.is_foreign(None)
    # Bot-Rest / registrierter KI-Limit-Fill gehören der Website
    assert not trade_ownership.is_foreign(_manual_trade(leftover=True))
    assert not trade_ownership.is_foreign(_manual_trade(adopted_from_limit=True))
    # 'Manuell (Website)' wurde von der Website eröffnet -> eigener Trade
    assert not trade_ownership.is_foreign(_bot_trade(strategy_id="external",
                                                     manual_trade=True))


def test_exchange_write_allowed_respects_manage_external_optin():
    assert not trade_ownership.exchange_write_allowed(_manual_trade())
    watchdog.settings["manage_external"] = True
    assert trade_ownership.exchange_write_allowed(_manual_trade())


# ----------------------------- exakter Bug-Ablauf --------------------------

def test_bug_watchdog_never_resets_sl_of_manual_trade():
    """Prod-Daten ETH: SL-Schätzung 2413 liegt hinter der Liq 2657 -> früher
    zog der Liq-Guard JEDEN Zyklus den Börsen-SL nach. Jetzt: kein Call."""
    wd, at, db, client = _setup([_manual_trade()], [_eth_pos()])
    for _ in range(3):  # Nutzer löscht/verschiebt SL zwischen den Zyklen
        client.tpsl_rows = []
        asyncio.run(wd.check())
        client.tpsl_rows = [{"id": "user-sl", "slPrice": "2500"}]
        asyncio.run(wd.check())
    assert _writes(client) == []
    doc = db.auto_trades.docs[0]
    assert not any("LIQ-SCHUTZ" in e for e in doc.get("events", []))
    assert "liq_guard" not in doc
    # Anzeige-Abgleich (nur DB) läuft weiter
    assert doc["liq_price"] == 2657.05


def test_bug_fresh_manual_position_adopted_and_untouched():
    wd, at, db, client = _setup([], [_eth_pos()])
    asyncio.run(wd.check())
    asyncio.run(wd.check())
    assert len(db.auto_trades.docs) == 1
    assert db.auto_trades.docs[0]["external_adopted"] is True
    assert _writes(client) == []


def test_website_trade_keeps_liq_protection():
    """Keine Regression: eigener Website-Trade mit SL hinter der Liq wird
    weiterhin geschützt (SL vor die Liq)."""
    wd, at, db, client = _setup([_bot_trade()], [_eth_pos()])
    moved = []

    async def _move(t, sl, qty):
        moved.append(sl)
        return True
    at._live_move_sl = _move
    asyncio.run(at.guard_fill_liq(db.auto_trades.docs[0], {
        "qty": 5.621, "entry": 2682.02, "liq_price": 2657.05, "margin": 150.7}))
    assert moved and moved[0] > 2657.05


def test_manage_external_optin_restores_liq_guard():
    watchdog.settings["manage_external"] = True
    wd, at, db, client = _setup([_manual_trade()], [_eth_pos()])
    moved = []

    async def _move(t, sl, qty):
        moved.append(sl)
        return True
    at._live_move_sl = _move
    asyncio.run(at.guard_fill_liq(db.auto_trades.docs[0], {
        "qty": 5.621, "entry": 2682.02, "liq_price": 2657.05, "margin": 150.7}))
    assert moved


# ----------------------------- Chokepoints ---------------------------------

def test_all_write_paths_blocked_for_manual_trade():
    wd, at, db, client = _setup([_manual_trade()], [_eth_pos()])
    t = db.auto_trades.docs[0]
    r = asyncio.run(at.sync_live_levels(dict(t), sl=2600))
    assert r["ok"] is False and r.get("foreign")
    r = asyncio.run(at.close_live_position(dict(t), 5.621))
    assert r["ok"] is False and r.get("foreign")
    assert "error" in asyncio.run(at.adjust_levels(t["id"], sl=2600))
    assert "error" in asyncio.run(at.manual_close(t["id"], 2690))
    assert "error" in asyncio.run(at.partial_close(t["id"], 50))
    assert "error" in asyncio.run(at.adjust_margin(t["id"], 10))
    assert "error" in asyncio.run(at.adjust_leverage(t["id"], 50))
    assert _writes(client) == []
    assert db.auto_trades.docs[0]["status"] == "open"


def test_write_paths_still_work_for_website_trade():
    wd, at, db, client = _setup([_bot_trade()], [_eth_pos()],
                                tpsl_rows=[{"id": "bot-sl", "slPrice": "2600"}])
    t = db.auto_trades.docs[0]
    asyncio.run(at.sync_live_levels(dict(t), sl=2610))
    assert ("modify_tpsl", "bot-sl", 2610) in client.calls
    r = asyncio.run(at.close_live_position(dict(t), 5.621))
    assert any(c[0] == "flash_close" for c in client.calls), r


# ----------------------------- Positions-Zuordnung -------------------------

def test_resolve_position_never_rebinds_known_id():
    wd, at, db, client = _setup([_bot_trade(bitunix_position_id="gone")],
                                [_eth_pos(pid="manual-p")])
    t = db.auto_trades.docs[0]
    assert asyncio.run(at._resolve_position(dict(t), refresh=True)) == "gone"
    assert ("resolve_position_id", "ETHUSDT", "LONG") not in client.calls


def test_resolve_position_without_id_skips_manual_position():
    wd, at, db, client = _setup(
        [_bot_trade(bitunix_position_id=None, qty=1.0, qty_remaining=1.0),
         _manual_trade(bitunix_position_id="manual-p")],
        [_eth_pos(pid="manual-p", qty="5.0"), _eth_pos(pid="bot-p", qty="1.0")])
    t = next(d for d in db.auto_trades.docs if d["id"] == "ETHUSDT-bot-1")
    assert asyncio.run(at._resolve_position(dict(t))) == "bot-p"
    # nur die manuelle Position offen -> keine Zuordnung
    client.positions = [_eth_pos(pid="manual-p", qty="5.0")]
    t2 = dict(t, bitunix_position_id=None)
    assert asyncio.run(at._resolve_position(t2)) is None


def test_stale_bot_position_never_closes_manual_position():
    """Bot-Position extern weg, manuelle Position gleicher Seite offen:
    der Close darf NICHT auf der manuellen Position landen."""
    wd, at, db, client = _setup(
        [_bot_trade(bitunix_position_id="gone"), _manual_trade(bitunix_position_id="manual-p")],
        [_eth_pos(pid="manual-p")])
    t = next(d for d in db.auto_trades.docs if d["id"] == "ETHUSDT-bot-1")
    assert asyncio.run(at._live_position_qty(t)) == 0.0
    r = asyncio.run(at.close_live_position(dict(t), 5.621))
    assert r["ok"] is True and "nicht mehr" in r["detail"]
    assert _writes(client) == []


def test_live_qty_counts_only_own_position():
    wd, at, db, client = _setup([_bot_trade(bitunix_position_id="bot-p", qty=1.0)],
                                [_eth_pos(pid="bot-p", qty="1.0"),
                                 _eth_pos(pid="manual-p", qty="5.0")])
    assert asyncio.run(at._live_position_qty(db.auto_trades.docs[0])) == 1.0
    # ohne positionId (Altverhalten): Summe über Symbol+Seite
    assert asyncio.run(at._live_position_qty({"symbol": "ETHUSDT", "side": "LONG"})) == 6.0


def test_new_position_resolution_excludes_manual_positions():
    wd, at, db, client = _setup([_manual_trade(bitunix_position_id="manual-p")],
                                [_eth_pos(pid="manual-p", qty="1.0")])
    assert asyncio.run(at._resolve_new_position_id("ETHUSDT", "LONG", qty=1.0)) is None
    assert asyncio.run(at._resolve_new_position_id("ETHUSDT", "LONG", qty=1.0,
                                                   strict=True)) is None
    # Bot-Position daneben wird korrekt gefunden
    client.positions.append(_eth_pos(pid="bot-new", qty="1.0"))
    assert asyncio.run(at._resolve_new_position_id("ETHUSDT", "LONG", qty=1.0)) == "bot-new"


def test_strict_resolution_skips_positions_of_other_website_trades():
    wd, at, db, client = _setup([_bot_trade(bitunix_position_id="bot-a")],
                                [_eth_pos(pid="bot-a", qty="1.0")])
    assert asyncio.run(at._resolve_new_position_id("ETHUSDT", "LONG", qty=1.0,
                                                   strict=True)) is None


def test_position_newer_than_trade():
    now_ms = datetime.now(timezone.utc).timestamp() * 1000
    old_trade = {"opened_at": "2020-01-01T00:00:00+00:00"}
    assert position_newer_than_trade({"opened_ms": now_ms}, old_trade)
    assert not position_newer_than_trade({"opened_ms": 0}, old_trade)
    assert not position_newer_than_trade({"opened_ms": now_ms}, {})
    fresh = {"opened_at": datetime.now(timezone.utc).isoformat()}
    assert not position_newer_than_trade({"opened_ms": now_ms}, fresh)


def test_watchdog_does_not_bind_old_trade_without_id_to_new_manual_position():
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    old = _bot_trade(bitunix_position_id=None, opened_at="2020-01-01T00:00:00+00:00")
    wd, at, db, client = _setup([old], [_eth_pos(pid="manual-p", ctime=now_ms)])
    asyncio.run(wd.check())
    bot = next(d for d in db.auto_trades.docs if d["id"] == "ETHUSDT-bot-1")
    assert not bot.get("bitunix_position_id")
    manual = [d for d in db.auto_trades.docs if d.get("external_adopted")]
    assert len(manual) == 1 and manual[0]["bitunix_position_id"] == "manual-p"
    assert _writes(client) == []
