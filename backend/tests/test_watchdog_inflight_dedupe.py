"""Regressionstests: Doppel-Trade-Schutz Watchdog <-> KI-Entry.

Bug-Report: Bei KI-Limit-Orders erschien auf der Website EIN KI-Trade und
zusätzlich derselbe Trade als 'Manuell (Bitunix)' (Watchdog-Übernahme im
Zeitfenster zwischen Börsen-Fill und lokalem DB-Insert). Abgedeckt:
  * entry_inflight: begin/end/is_inflight, duplicate_of (rein)
  * Watchdog übernimmt KEINE Position, solange ein Entry für Symbol+Seite läuft
  * Karenz (adopt_grace_sec): frische Positionen (Börsen-ctime) erst später
  * Karenz-Fallback ohne ctime: erste Sichtung merken, zweiter Zyklus übernimmt
  * registrierte KI-Entry-Order wird trotz Karenz sofort als KI-Trade übernommen
  * Registry-Abgleich hat Vorrang vor der Rest-Bereinigung (kein Flash-Close
    eines KI-Wiedereinstiegs)
  * Dedupe: KI-Trade + Watchdog-Übernahme an derselben Position-ID -> Übernahme weg
  * on_signal-Wrapper: In-Flight-Markierung + Duplikat-Bereinigung nach Insert
"""
import asyncio
import importlib.util
import sys
from pathlib import Path

_TESTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(_TESTS_DIR.parent))


def _load(name):
    """Nachbar-Testmodul laden, ohne vom Paket-Namen 'tests' abzuhängen
    (im Repo-Root existiert ein zweites 'tests'-Paket)."""
    spec = importlib.util.spec_from_file_location(f"_helper_{name}", _TESTS_DIR / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod
import time

import pytest

from services import entry_inflight
from services.bitunix_trade import AutoTradeManager
from services.position_watchdog import (PositionWatchdog, parse_positions,
                                        position_age_sec)
_pw = _load("test_position_watchdog")
FakeClient, FakeCollection, FakeDB = _pw.FakeClient, _pw.FakeCollection, _pw.FakeDB


@pytest.fixture(autouse=True)
def _clear_inflight():
    entry_inflight.clear()
    yield
    entry_inflight.clear()


class _Coll(FakeCollection):
    """FakeCollection + delete_one (Dedupe) + $push-Unterstützung."""

    async def delete_one(self, q):
        before = len(self.docs)
        self.docs = [d for d in self.docs if not self._match(d, q)]
        return type("R", (), {"deleted_count": before - len(self.docs)})()

    async def update_one(self, q, upd, **kw):
        await super().update_one(q, upd, **kw)
        push = upd.get("$push") or {}
        for d in self.docs:
            if self._match(d, q):
                for k, spec in push.items():
                    vals = spec.get("$each", [spec]) if isinstance(spec, dict) else [spec]
                    d[k] = (d.get(k) or []) + list(vals)


class _DB(FakeDB):
    def __init__(self, trades=None):
        super().__init__(trades)
        self.auto_trades = _Coll(trades)
        self.pending_entry_orders = _Coll()


def _pos_row(pid="p1", ctime_ms=None, sym="ADAUSDT", side="BUY"):
    row = {"symbol": sym, "side": side, "qty": "500", "avgOpenPrice": "0.5",
           "positionId": pid, "leverage": 10, "margin": 25}
    if ctime_ms is not None:
        row["ctime"] = str(int(ctime_ms))
    return row


def _wd(db, client, grace=90):
    at = AutoTradeManager(client)
    at.set_db(db)
    wd = PositionWatchdog()
    wd.setup(db, client, at, telegram=None)
    wd.settings["adopt_grace_sec"] = grace

    async def _noop(text):
        pass
    wd._notify = _noop
    return wd


# ---------------- reine Funktionen ----------------
def test_parse_positions_reads_ctime():
    rows = parse_positions({"code": 0, "data": [_pos_row(ctime_ms=1788408985000)]})
    assert rows[0]["opened_ms"] == 1788408985000.0
    assert position_age_sec(rows[0], now=1788408985000 / 1000 + 42) == 42.0
    assert position_age_sec({"opened_ms": 0}) is None


def test_inflight_begin_end_and_expiry():
    key = entry_inflight.begin("ADAUSDT", "LONG", "ai_trader")
    assert entry_inflight.is_inflight("ADAUSDT", "LONG")
    assert entry_inflight.is_inflight("adausdt", "long")  # case-insensitiv
    assert not entry_inflight.is_inflight("ADAUSDT", "SHORT")
    assert entry_inflight.started_iso("ADAUSDT", "LONG")
    entry_inflight.end(key)
    assert not entry_inflight.is_inflight("ADAUSDT", "LONG")
    # referenzgezählt: zwei parallele Entries -> erst nach dem letzten end() frei
    k1 = entry_inflight.begin("SOLUSDT", "LONG")
    k2 = entry_inflight.begin("SOLUSDT", "LONG")
    entry_inflight.end(k1)
    assert entry_inflight.is_inflight("SOLUSDT", "LONG")
    entry_inflight.end(k2)
    assert not entry_inflight.is_inflight("SOLUSDT", "LONG")
    # verwaiste Markierung verfällt
    entry_inflight.begin("ETHUSDT", "SHORT")
    assert not entry_inflight.is_inflight("ETHUSDT", "SHORT", max_age_sec=-1)


def test_duplicate_of_rules():
    ki = {"id": "ki", "symbol": "ADAUSDT", "side": "LONG", "mode": "live",
          "status": "open", "bitunix_position_id": "p1"}
    ext = {"id": "ext", "symbol": "ADAUSDT", "side": "LONG", "mode": "live",
           "status": "open", "strategy_id": "external", "external_adopted": True,
           "bitunix_position_id": "p1", "opened_at": "2026-06-10T10:00:05+00:00"}
    assert entry_inflight.duplicate_of(ki, ext, None)
    # andere Position-ID -> echte Manuell-Position, kein Duplikat
    assert not entry_inflight.duplicate_of(ki, {**ext, "bitunix_position_id": "p9"},
                                           "2026-06-10T10:00:00+00:00")
    # ohne Position-ID, aber während unseres Entrys übernommen -> Duplikat
    assert entry_inflight.duplicate_of(ki, {**ext, "bitunix_position_id": None},
                                       "2026-06-10T10:00:00+00:00")
    # vor unserem Entry eröffnet -> kein Duplikat
    assert not entry_inflight.duplicate_of(ki, {**ext, "bitunix_position_id": None},
                                           "2026-06-10T10:00:09+00:00")
    # KI-/Website-Trades sind nie Duplikate anderer KI-Trades
    assert not entry_inflight.duplicate_of(ki, {**ext, "strategy_id": "ai_trader",
                                                "external_adopted": False}, None)
    # anderer Modus/Seite/Status
    assert not entry_inflight.duplicate_of(ki, {**ext, "side": "SHORT"}, None)
    assert not entry_inflight.duplicate_of(ki, {**ext, "status": "closed"}, None)


# ---------------- Watchdog: In-Flight & Karenz ----------------
def test_watchdog_skips_position_while_entry_inflight():
    old = (time.time() - 3600) * 1000
    client = FakeClient(positions=[_pos_row(ctime_ms=old)])
    db = _DB()
    wd = _wd(db, client)
    entry_inflight.begin("ADAUSDT", "LONG", "ai_trader")
    status = asyncio.run(wd.check())
    assert status["adopted"] == 0 and status["adopt_deferred"] == 1
    assert db.auto_trades.inserted == []
    # Entry beendet (z.B. abgebrochen) -> nächster Zyklus übernimmt normal
    entry_inflight.clear()
    status = asyncio.run(wd.check())
    assert status["adopted"] == 1
    assert db.auto_trades.inserted[0]["strategy_name"] == "Manuell (Bitunix)"


def test_watchdog_grace_defers_fresh_position_by_ctime():
    fresh = (time.time() - 10) * 1000
    client = FakeClient(positions=[_pos_row(ctime_ms=fresh)])
    db = _DB()
    wd = _wd(db, client, grace=90)
    status = asyncio.run(wd.check())
    assert status["adopted"] == 0 and status["adopt_deferred"] == 1
    # Position ist inzwischen alt genug
    client.positions = [_pos_row(ctime_ms=(time.time() - 120) * 1000)]
    status = asyncio.run(wd.check())
    assert status["adopted"] == 1


def test_watchdog_grace_fallback_first_seen_without_ctime():
    client = FakeClient(positions=[_pos_row()])  # keine ctime
    db = _DB()
    wd = _wd(db, client, grace=90)
    status = asyncio.run(wd.check())
    assert status["adopted"] == 0 and status["adopt_deferred"] == 1
    assert "p1" in wd._first_seen
    # zweiter Zyklus nach Ablauf der Karenz
    wd._first_seen["p1"] -= 200
    status = asyncio.run(wd.check())
    assert status["adopted"] == 1
    assert "p1" not in wd._first_seen
    # Karenz 0 = Altverhalten (sofort)
    wd2 = _wd(_DB(), FakeClient(positions=[_pos_row(pid="p2")]), grace=0)
    assert asyncio.run(wd2.check())["adopted"] == 1


def test_registered_ki_order_adopted_immediately_despite_grace():
    fresh = (time.time() - 5) * 1000
    client = FakeClient(positions=[_pos_row(ctime_ms=fresh)])
    db = _DB()
    db.pending_entry_orders.docs.append({
        "order_id": "o1", "symbol": "ADAUSDT", "side": "LONG", "qty": 500,
        "price": 0.5, "status": "waiting", "created_at": "2099-01-01T00:00:00+00:00",
        "meta": {"strategy_id": "ai_trader", "strategy_name": "KI-Trader",
                 "sl": 0.49, "tp1": 0.51, "tpf": 0.52, "leverage": 10}})

    async def _find_match(_db, symbol, side, max_age_h=36.0):
        return db.pending_entry_orders.docs[0] if symbol == "ADAUSDT" else None

    from services import entry_order_registry
    orig = entry_order_registry.find_match
    entry_order_registry.find_match = _find_match
    try:
        wd = _wd(db, client, grace=90)
        status = asyncio.run(wd.check())
    finally:
        entry_order_registry.find_match = orig
    assert status["adopted"] == 1
    t = db.auto_trades.inserted[0]
    assert t["strategy_id"] == "ai_trader" and t["manual_trade"] is False
    assert t["adopted_from_limit"] is True and t["bitunix_order_id"] == "o1"


def test_registry_match_beats_leftover_flash_close():
    """KI-Wiedereinstieg kurz nach einem Close darf NICHT als 'Rest' an der
    Börse geschlossen werden, wenn die Position zu einer registrierten
    KI-Order gehört."""
    client = FakeClient(positions=[_pos_row()])
    db = _DB()
    wd = _wd(db, client, grace=0)
    reg = {"order_id": "o7", "meta": {"strategy_id": "ai_trader", "sl": 0.49,
                                      "tp1": 0.51, "tpf": 0.52}}
    pos = parse_positions({"code": 0, "data": [_pos_row()]})[0]
    trade = asyncio.run(wd._adopt("ADAUSDT", pos, reg=reg))
    assert trade is not None and trade["strategy_id"] == "ai_trader"
    assert not [c for c in client.calls if c[0] == "flash_close"]


# ---------------- Dedupe an derselben Position-ID ----------------
def test_watchdog_removes_duplicate_adoption_bound_to_same_position():
    ki = {"id": "ADAUSDT-1", "symbol": "ADAUSDT", "side": "LONG", "mode": "live",
          "status": "open", "strategy_id": "ai_trader", "strategy_name": "KI-Trader",
          "qty": 500, "qty_remaining": 500, "sl": 0.49, "entry": 0.5,
          "bitunix_position_id": "p1", "events": []}
    dup = {"id": "ADAUSDT-ext-1", "symbol": "ADAUSDT", "side": "LONG", "mode": "live",
           "status": "open", "strategy_id": "external", "strategy_name": "Manuell (Bitunix)",
           "external_adopted": True, "manual_trade": True, "qty": 500,
           "bitunix_position_id": "p1"}
    client = FakeClient(positions=[_pos_row()], tpsl_rows=[{"slPrice": 0.49}])
    db = _DB([dup, ki])  # Duplikat steht absichtlich VOR dem KI-Trade
    wd = _wd(db, client)
    status = asyncio.run(wd.check())
    ids = [d["id"] for d in db.auto_trades.docs]
    assert "ADAUSDT-1" in ids and "ADAUSDT-ext-1" not in ids
    assert status["deduped"] == 1 and status["adopted"] == 0
    ki_doc = next(d for d in db.auto_trades.docs if d["id"] == "ADAUSDT-1")
    assert any("doppelte Übernahme" in e for e in ki_doc["events"])


def test_watchdog_keeps_real_manual_position_with_other_position_id():
    ki = {"id": "ADAUSDT-1", "symbol": "ADAUSDT", "side": "LONG", "mode": "live",
          "status": "open", "strategy_id": "ai_trader", "qty": 500, "qty_remaining": 500,
          "sl": 0.49, "entry": 0.5, "bitunix_position_id": "p1"}
    manual = {"id": "ADAUSDT-ext-2", "symbol": "ADAUSDT", "side": "LONG", "mode": "live",
              "status": "open", "strategy_id": "external", "external_adopted": True,
              "qty": 100, "bitunix_position_id": "p2"}
    client = FakeClient(positions=[_pos_row("p1"), _pos_row("p2")],
                        tpsl_rows=[{"slPrice": 0.49}])
    db = _DB([ki, manual])
    wd = _wd(db, client)
    status = asyncio.run(wd.check())
    assert len(db.auto_trades.docs) == 2 and status["deduped"] == 0


# ---------------- Zuordnung der neuen Position (Hedge: mehrere Positionen) ----------------
def test_pick_new_position_skips_bound_and_prefers_qty_then_newest():
    from services.bitunix_trade import pick_new_position
    rows = [
        {"positionId": "old", "side": "SELL", "qty": "1.719", "ctime": "1788397192000"},
        {"positionId": "new", "side": "SELL", "qty": "1.558", "ctime": "1788405196000"},
        {"positionId": "long", "side": "BUY", "qty": "1.558", "ctime": "1788409999000"},
    ]
    # ohne Hinweise: jüngste Short-Position
    assert pick_new_position(rows, "SHORT") == "new"
    # Menge entscheidet
    assert pick_new_position(rows, "SHORT", qty=1.72) == "old"
    # bereits gebundene Position wird übersprungen
    assert pick_new_position(rows, "SHORT", qty=1.72, exclude={"old"}) == "new"
    # alle gebunden -> trotzdem beste Wahl statt None
    assert pick_new_position(rows, "SHORT", qty=1.72, exclude={"old", "new"}) == "old"
    assert pick_new_position(rows, "LONG") == "long"
    assert pick_new_position([], "LONG") is None
    assert pick_new_position([{"positionId": "x", "side": "BUY"}], "SHORT") is None


def test_resolve_new_position_id_excludes_positions_of_other_trades():
    other = {"id": "t-old", "symbol": "BTCUSDT", "side": "SHORT", "mode": "live",
             "status": "open", "bitunix_position_id": "old"}
    client = FakeClient(positions=[
        {"positionId": "old", "symbol": "BTCUSDT", "side": "SELL", "qty": "1.719",
         "avgOpenPrice": "4400", "ctime": "1788397192000"},
        {"positionId": "new", "symbol": "BTCUSDT", "side": "SELL", "qty": "1.558",
         "avgOpenPrice": "4433", "ctime": "1788405196000"}])
    calls = []

    async def _legacy(symbol, side):
        calls.append((symbol, side))
        return None
    client.resolve_position_id = _legacy
    at = AutoTradeManager(client)
    at.set_db(_DB([other]))
    assert asyncio.run(at._resolve_new_position_id("BTCUSDT", "SHORT", qty=1.558)) == "new"
    assert calls == []
    # Fallback auf klassisches resolve_position_id, wenn keine Zeilen passen
    assert asyncio.run(at._resolve_new_position_id("BTCUSDT", "LONG", qty=1.0)) is None
    assert calls == [("BTCUSDT", "LONG")]


def test_watchdog_symbol_fallback_prefers_qty_match():
    t_small = {"id": "small", "symbol": "BTCUSDT", "side": "SHORT", "mode": "live",
               "status": "open", "strategy_id": "ai_trader", "qty": 1.558,
               "qty_remaining": 1.558, "sl": 4500, "entry": 4433}
    t_big = {"id": "big", "symbol": "BTCUSDT", "side": "SHORT", "mode": "live",
             "status": "open", "strategy_id": "ai_trader", "qty": 1.719,
             "qty_remaining": 1.719, "sl": 4500, "entry": 4400}
    client = FakeClient(positions=[
        {"positionId": "p-big", "symbol": "BTCUSDT", "side": "SELL", "qty": "1.719",
         "avgOpenPrice": "4400"},
        {"positionId": "p-small", "symbol": "BTCUSDT", "side": "SELL", "qty": "1.558",
         "avgOpenPrice": "4433"}], tpsl_rows=[{"slPrice": 4500}])
    db = _DB([t_small, t_big])
    wd = _wd(db, client)
    asyncio.run(wd.check())
    by = {d["id"]: d for d in db.auto_trades.docs}
    assert by["big"]["bitunix_position_id"] == "p-big"
    assert by["small"]["bitunix_position_id"] == "p-small"


# ---------------- on_signal-Wrapper ----------------
class _Mgr(AutoTradeManager):
    def __init__(self, db, trade):
        super().__init__(FakeClient())
        self.set_db(db)
        self._trade = trade
        self.seen_inflight = None

    async def _on_signal_impl(self, signal, candles):
        self.seen_inflight = entry_inflight.is_inflight(signal["symbol"], signal["type"])
        if self._trade:
            await self.db.auto_trades.insert_one(dict(self._trade))
        return self._trade


def test_on_signal_marks_inflight_and_removes_duplicates():
    dup = {"id": "ADAUSDT-ext-9", "symbol": "ADAUSDT", "side": "LONG", "mode": "live",
           "status": "open", "strategy_id": "external", "external_adopted": True,
           "bitunix_position_id": "p1", "opened_at": "2099-01-01T00:00:00+00:00"}
    trade = {"id": "ADAUSDT-9", "symbol": "ADAUSDT", "side": "LONG", "mode": "live",
             "status": "open", "strategy_id": "ai_trader", "bitunix_position_id": "p1",
             "events": []}
    db = _DB([dup])
    mgr = _Mgr(db, trade)
    out = asyncio.run(mgr.on_signal({"symbol": "ADAUSDT", "type": "LONG",
                                     "strategy_id": "ai_trader"}, []))
    assert out is trade
    assert mgr.seen_inflight is True
    assert not entry_inflight.is_inflight("ADAUSDT", "LONG")
    ids = [d["id"] for d in db.auto_trades.docs]
    assert ids == ["ADAUSDT-9"]


def test_on_signal_clears_inflight_on_exception_and_paper_untouched():
    class _Boom(_Mgr):
        async def _on_signal_impl(self, signal, candles):
            raise RuntimeError("boom")

    mgr = _Boom(_DB(), None)
    with pytest.raises(RuntimeError):
        asyncio.run(mgr.on_signal({"symbol": "ETHUSDT", "type": "SHORT"}, []))
    assert not entry_inflight.is_inflight("ETHUSDT", "SHORT")
    # Paper-Trade: keine Duplikat-Bereinigung (Manuell-Trades bleiben)
    dup = {"id": "x", "symbol": "ETHUSDT", "side": "SHORT", "mode": "live",
           "status": "open", "strategy_id": "external", "external_adopted": True,
           "opened_at": "2099-01-01T00:00:00+00:00"}
    db = _DB([dup])
    mgr = _Mgr(db, {"id": "p", "symbol": "ETHUSDT", "side": "SHORT", "mode": "paper",
                    "status": "open"})
    asyncio.run(mgr.on_signal({"symbol": "ETHUSDT", "type": "SHORT"}, []))
    assert len(db.auto_trades.docs) == 2
