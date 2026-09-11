"""Regressionstests: Entry-Order-Registry + korrekte KI-Zuordnung im Watchdog.

Bug-Report (22.08.): KI-Limit-Orders, die erst nach dem Warte-Timeout gefüllt
wurden, übernahm der Watchdog fälschlich als 'Manuell (Bitunix)' – ohne
Telegram-Signal. Diese Tests decken ab:
  * Registry: register / find_match / resolve / mark_orphan / cleanup
  * Watchdog._adopt ordnet registrierte Limit-Fills dem KI-Trader zu
  * Ohne Registry-Eintrag bleibt die bisherige 'Manuell (Bitunix)'-Übernahme
"""
import asyncio
from datetime import datetime, timedelta, timezone

from services import entry_order_registry
from services.position_watchdog import PositionWatchdog


# --------------------------- Fakes ---------------------------------------

def _iso(dt):
    return dt.isoformat()


class FakeCollection:
    def __init__(self, docs=None):
        self.docs = [dict(d) for d in (docs or [])]
        self.inserted = []

    def _match(self, d, q):
        for k, v in (q or {}).items():
            if isinstance(v, dict):
                if "$gte" in v and not (d.get(k) is not None and d[k] >= v["$gte"]):
                    return False
                if "$lt" in v and not (d.get(k) is not None and d[k] < v["$lt"]):
                    return False
                if "$ne" in v and d.get(k) == v["$ne"]:
                    return False
                continue
            if d.get(k) != v:
                return False
        return True

    async def find_one(self, q, *a, **kw):
        for d in self.docs:
            if self._match(d, q):
                return dict(d)
        return None

    async def insert_one(self, doc):
        self.inserted.append(dict(doc))
        self.docs.append(dict(doc))

    async def update_one(self, q, upd, upsert=False, **kw):
        for d in self.docs:
            if self._match(d, q):
                d.update(upd.get("$set", {}))
                return None
        if upsert:
            doc = dict(q)
            doc.update(upd.get("$set", {}))
            self.docs.append(doc)
        return None

    async def delete_one(self, q):
        for i, d in enumerate(self.docs):
            if self._match(d, q):
                self.docs.pop(i)
                break
        return None

    def find(self, q=None, *a, **kw):
        rows = [dict(d) for d in self.docs if self._match(d, q or {})]

        class _Cursor:
            def __init__(self, rows):
                self.rows = rows

            def sort(self, key, direction=1):
                self.rows.sort(key=lambda r: r.get(key) or "",
                               reverse=direction < 0)
                return self

            def limit(self, *a, **kw):
                return self

            async def to_list(self, n=None):
                return self.rows[:n] if n else self.rows

            def __aiter__(self):
                self._it = iter(self.rows)
                return self

            async def __anext__(self):
                try:
                    return next(self._it)
                except StopIteration:
                    raise StopAsyncIteration
        return _Cursor(rows)


class FakeDB:
    def __init__(self):
        self.pending_entry_orders = FakeCollection()
        self.auto_trades = FakeCollection()
        self.settings = FakeCollection()


class FakeClient:
    def __init__(self):
        self.cancelled = []

    def configured(self):
        return True

    async def cancel_orders(self, symbol, ids):
        self.cancelled.append((symbol, list(ids)))
        return {"code": 0}

    async def get_mark_price(self, symbol):
        return 100.0


def run(coro):
    return asyncio.run(coro)


META = {"strategy_id": "ai_trader", "strategy_name": "KI-Trader",
        "sl": 98.0, "tp1": 103.0, "tpf": 106.0, "leverage": 20.0,
        "capital": 25.0, "mode": "live", "fee_percent": 0.06,
        "tp1_close_percent": 50.0, "timeframe": "5m", "horizon": "swing"}

POS = {"bitunix_symbol": "BTCUSDT", "side": "LONG", "qty": 0.01,
       "entry": 100.0, "position_id": "p-1", "leverage": 20.0, "margin": 25.0}


# --------------------------- Registry ------------------------------------

class TestRegistry:
    def test_register_and_find_match(self):
        db = FakeDB()
        run(entry_order_registry.register(
            db, order_id="o-1", symbol="BTCUSDT", side="LONG",
            qty=0.01, price=100.0, meta=META))
        hit = run(entry_order_registry.find_match(db, "BTCUSDT", "LONG"))
        assert hit and hit["order_id"] == "o-1"
        assert hit["meta"]["strategy_id"] == "ai_trader"
        # falsche Seite / falsches Symbol -> kein Treffer
        assert run(entry_order_registry.find_match(db, "BTCUSDT", "SHORT")) is None
        assert run(entry_order_registry.find_match(db, "ETHUSDT", "LONG")) is None

    def test_resolve_removes_entry(self):
        db = FakeDB()
        run(entry_order_registry.register(
            db, order_id="o-2", symbol="ETHUSDT", side="SHORT",
            qty=1.0, price=50.0))
        run(entry_order_registry.resolve(db, "o-2"))
        assert run(entry_order_registry.find_match(db, "ETHUSDT", "SHORT")) is None

    def test_mark_orphan_keeps_entry_findable(self):
        db = FakeDB()
        run(entry_order_registry.register(
            db, order_id="o-3", symbol="SOLUSDT", side="LONG",
            qty=2.0, price=10.0))
        run(entry_order_registry.mark_orphan(db, "o-3", "Cancel nicht bestätigt"))
        hit = run(entry_order_registry.find_match(db, "SOLUSDT", "LONG"))
        assert hit and hit["status"] == "orphan"

    def test_old_entries_not_matched_and_cleaned(self):
        db = FakeDB()
        old = _iso(datetime.now(timezone.utc) - timedelta(hours=48))
        db.pending_entry_orders.docs.append(
            {"order_id": "o-old", "symbol": "BTCUSDT", "side": "LONG",
             "qty": 0.01, "price": 90.0, "meta": {}, "status": "waiting",
             "created_at": old})
        assert run(entry_order_registry.find_match(db, "BTCUSDT", "LONG")) is None
        client = FakeClient()
        removed = run(entry_order_registry.cleanup(db, client))
        assert removed == 1
        assert client.cancelled == [("BTCUSDT", ["o-old"])]
        assert db.pending_entry_orders.docs == []


# --------------------------- Watchdog-Zuordnung ---------------------------

def _watchdog(db):
    wd = PositionWatchdog()
    wd.db = db
    wd.client = FakeClient()
    wd.telegram = None
    notes = []

    async def _notify(text):
        notes.append(text)
    wd._notify = _notify
    wd._notes = notes
    return wd


class TestWatchdogAttribution:
    def test_adopt_registered_limit_fill_as_ai_trade(self):
        db = FakeDB()
        run(entry_order_registry.register(
            db, order_id="o-9", symbol="BTCUSDT", side="LONG",
            qty=0.01, price=100.0, meta=META))
        wd = _watchdog(db)
        trade = run(wd._adopt("BTCUSDT", dict(POS)))
        assert trade is not None
        assert trade["strategy_id"] == "ai_trader"
        assert trade["strategy_name"] == "KI-Trader"
        assert trade["manual_trade"] is False
        assert trade["adopted_from_limit"] is True
        assert trade["external_adopted"] is False  # wird normal gemanagt
        assert trade["sl"] == 98.0 and trade["tpf"] == 106.0
        assert trade["bitunix_position_id"] == "p-1"
        # Registry-Eintrag ist aufgelöst -> kein Doppel-Adopt
        assert run(entry_order_registry.find_match(db, "BTCUSDT", "LONG")) is None
        assert db.auto_trades.inserted[0]["strategy_id"] == "ai_trader"

    def test_adopt_without_registry_stays_manual(self):
        db = FakeDB()
        wd = _watchdog(db)
        trade = run(wd._adopt("BTCUSDT", dict(POS)))
        assert trade is not None
        assert trade["strategy_id"] == "external"
        assert trade["strategy_name"] == "Manuell (Bitunix)"
        assert trade["manual_trade"] is True
        assert trade["external_adopted"] is True

    def test_ai_adopt_without_tp_levels_not_managed_locally(self):
        db = FakeDB()
        meta = dict(META, tp1=None, tpf=None)
        run(entry_order_registry.register(
            db, order_id="o-10", symbol="BTCUSDT", side="LONG",
            qty=0.01, price=100.0, meta=meta))
        wd = _watchdog(db)
        trade = run(wd._adopt("BTCUSDT", dict(POS)))
        assert trade["strategy_id"] == "ai_trader"
        # Ohne TP-Levels: sichtbar, aber nicht lokal gemanagt (kein Fehl-Close)
        assert trade["external_adopted"] is True
