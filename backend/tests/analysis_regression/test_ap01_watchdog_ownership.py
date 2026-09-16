"""AP01-Solltests (Befund T01): Watchdog-Ownership – kein Close ohne
Eigentums-/Mengenbeleg. Ersetzt die Charakterisierungstests, die den Fehler
erwarteten (Plan-Regel 4)."""
import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from services.position_watchdog import PositionWatchdog, leftover_evidence

from _fakes import FakeDB

pytestmark = pytest.mark.unit


def _pos(qty=1.0, pid="P-NEW", opened_ms=0.0):
    return {"bitunix_symbol": "ADAUSDT", "side": "LONG", "qty": qty,
            "entry": 0.5, "position_id": pid, "leverage": 10, "margin": 5,
            "opened_ms": opened_ms}


def _closed_trade(qty=10.0, pid="P-OLD", closed_min_ago=5):
    return {"symbol": "ADA", "side": "LONG", "mode": "live", "status": "closed",
            "strategy_id": "trend", "strategy_name": "Trend", "qty": qty,
            "bitunix_position_id": pid,
            "closed_at": (datetime.now(timezone.utc)
                          - timedelta(minutes=closed_min_ago)).isoformat()}


class TestLeftoverEvidence:
    def test_no_closed_trade_is_no_leftover(self):
        ok, _ = leftover_evidence(_pos(), None)
        assert ok is False

    def test_same_position_id_is_leftover(self):
        ok, why = leftover_evidence(_pos(qty=0.4, pid="P-OLD"), _closed_trade())
        assert ok is True and "Position-ID" in why

    def test_different_known_position_ids_never_leftover(self):
        # Alter kleiner Bot-Trade, neue GROSSE manuelle Position gleicher Seite
        ok, why = leftover_evidence(_pos(qty=0.1, pid="P-NEW"), _closed_trade(pid="P-OLD"))
        assert ok is False and "fremde" in why

    def test_large_qty_without_id_is_not_leftover(self):
        big = _pos(qty=50.0, pid="P-NEW")
        ok, _ = leftover_evidence(big, _closed_trade(qty=10.0, pid=""))
        assert ok is False

    def test_opened_after_close_is_not_leftover(self):
        after_ms = (datetime.now(timezone.utc) + timedelta(minutes=1)).timestamp() * 1000
        ok, why = leftover_evidence(_pos(qty=0.5, pid="P-NEW", opened_ms=after_ms),
                                    _closed_trade(qty=10.0, pid=""))
        assert ok is False and "Positions-ID" in why

    def test_small_qty_without_id_match_is_not_closed(self):
        """Prüfbericht-Kleinfix: kleine Menge + Zeitfenster ist KEIN
        Identitätsbeleg mehr – ohne übereinstimmende Positions-ID wird die
        Position nicht als vermeintlicher Rest geschlossen."""
        before_ms = (datetime.now(timezone.utc) - timedelta(hours=2)).timestamp() * 1000
        ok, why = leftover_evidence(_pos(qty=0.5, pid="P-NEW", opened_ms=before_ms),
                                    _closed_trade(qty=10.0, pid=""))
        assert ok is False and "Identitätsbeleg" in why

    def test_both_ids_missing_is_not_closed(self):
        ok, why = leftover_evidence(_pos(qty=0.1, pid=""),
                                    _closed_trade(qty=10.0, pid=""))
        assert ok is False and "Positions-ID" in why

    def test_unknown_source_qty_is_no_evidence(self):
        ok, _ = leftover_evidence(_pos(qty=0.1, pid=""), _closed_trade(qty=0, pid=""))
        assert ok is False


class _FakeClient:
    def __init__(self):
        self.flash_close_calls = []

    def configured(self):
        return True

    async def flash_close(self, *a, **k):
        self.flash_close_calls.append(a)
        return {"code": 0}

    async def get_mark_price(self, *_a):
        return 0.5


def _watchdog_with(db, client):
    wd = PositionWatchdog()
    wd.db, wd.client = db, client

    async def _noop(_text):
        return None
    wd._notify = _noop
    return wd


class TestAdoptForeignPositionNotClosed:
    def test_foreign_large_position_after_bot_close_is_adopted_not_closed(self):
        """T01-Kernfall: kurz nach einem Bot-Close taucht eine ANDERE, große
        Position gleicher Seite auf -> sichtbar übernehmen, NIE schließen."""
        db = FakeDB()
        asyncio.get_event_loop_policy()
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(
                db.auto_trades.insert_one(_closed_trade(qty=10.0, pid="P-OLD")))
            client = _FakeClient()
            wd = _watchdog_with(db, client)
            pos = _pos(qty=50.0, pid="P-NEW")
            trade = loop.run_until_complete(wd._adopt("ADA", pos))
            assert client.flash_close_calls == []            # KEIN Close!
            assert trade is not None
            assert trade["strategy_name"] == "Manuell (Bitunix)"
            assert trade["manual_trade"] is True
        finally:
            loop.close()

    def test_true_rest_same_position_id_is_cleaned(self):
        db = FakeDB()
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(
                db.auto_trades.insert_one(_closed_trade(qty=10.0, pid="P-OLD")))
            client = _FakeClient()
            wd = _watchdog_with(db, client)
            pos = _pos(qty=0.3, pid="P-OLD")
            trade = loop.run_until_complete(wd._adopt("ADA", pos))
            assert len(client.flash_close_calls) == 1        # Rest bereinigt
            assert trade is None
        finally:
            loop.close()

    def test_double_run_is_idempotent_no_second_close(self):
        db = FakeDB()
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(
                db.auto_trades.insert_one(_closed_trade(qty=10.0, pid="P-OLD")))
            client = _FakeClient()
            wd = _watchdog_with(db, client)
            foreign = _pos(qty=50.0, pid="P-NEW")
            loop.run_until_complete(wd._adopt("ADA", foreign))
            loop.run_until_complete(wd._adopt("ADA", foreign))
            assert client.flash_close_calls == []
        finally:
            loop.close()
