"""Regressionstests: Setup-Reife-Gate (Live nur für Setups mit genug Daten).

Gewünschtes Verhalten (User-Anforderung 06/2026): Der KI-Trader macht Live-
Trades nur für Setups, zu denen bereits gute Daten gesammelt wurden
('bewährt'/'neutral' im Playbook). Neue/unreife Setups laufen – auch über der
Live-Konfidenz-Schwelle – zuerst als Paper-Datensammlung weiter.
"""
from services.ai_playbook import (MIN_TRADES_FOR_VERDICT, live_ready,
                                  verdict_for)


def test_new_setup_not_live_ready():
    ok, why = live_ready(None)
    assert not ok and "keine echten Daten" in why
    ok, _ = live_ready({"trades": 0, "wins": 0, "pnl": 0})
    assert not ok


def test_immature_setup_collects_first():
    stats = {"trades": 3, "wins": 2, "pnl": 4.0,
             "verdict": verdict_for(3, 2, 4.0)}
    assert stats["verdict"] == "test"
    ok, why = live_ready(stats)
    assert not ok
    assert f"3/{MIN_TRADES_FOR_VERDICT}" in why


def test_proven_setup_is_live_ready():
    stats = {"trades": 10, "wins": 7, "pnl": 25.0,
             "verdict": verdict_for(10, 7, 25.0)}
    assert stats["verdict"] == "bewährt"
    assert live_ready(stats) == (True, "bewährt")


def test_neutral_setup_is_live_ready():
    stats = {"trades": 8, "wins": 4, "pnl": 1.0,
             "verdict": verdict_for(8, 4, 1.0)}
    assert stats["verdict"] == "neutral"
    assert live_ready(stats)[0] is True


def test_weak_setup_not_live_ready():
    stats = {"trades": 10, "wins": 2, "pnl": -20.0,
             "verdict": verdict_for(10, 2, -20.0)}
    assert stats["verdict"] == "schwach"
    assert live_ready(stats)[0] is False


def test_live_ready_computes_verdict_if_missing():
    assert live_ready({"trades": 10, "wins": 7, "pnl": 25.0})[0] is True
    assert live_ready({"trades": 2, "wins": 2, "pnl": 5.0})[0] is False


# ---------------- Reife-Übersicht (UI) ----------------
def test_maturity_overview_all_setups_listed():
    from services.ai_playbook import SETUPS, maturity_overview
    stats = {"breakout": {"trades": 10, "wins": 7, "pnl": 25.0, "verdict": "bewährt"},
             "trend_follow": {"trades": 3, "wins": 2, "pnl": 4.0, "verdict": "test"}}
    rows = maturity_overview(stats, {})
    assert len(rows) == len(SETUPS)
    by = {r["setup"]: r for r in rows}
    assert by["breakout"]["live_ready"] is True
    assert by["breakout"]["winrate"] == 70
    assert by["trend_follow"]["live_ready"] is False
    assert "3/" in by["trend_follow"]["reason"]
    assert by["mean_reversion"]["trades"] == 0
    assert by["mean_reversion"]["live_ready"] is False
    # live-reife Setups zuerst
    assert rows[0]["setup"] == "breakout"


def test_maturity_overview_disabled_setup_not_live():
    from services.ai_playbook import maturity_overview
    stats = {"breakout": {"trades": 10, "wins": 7, "pnl": 25.0, "verdict": "bewährt"}}
    rows = maturity_overview(stats, {"breakout": {"reason": "10 Trades, Winrate 20%"}})
    by = {r["setup"]: r for r in rows}
    assert by["breakout"]["live_ready"] is False
    assert by["breakout"]["reason"].startswith("gesperrt")


# ---------------- Feed-Meldung bei Live-Freischaltung ----------------
class _Cursor:
    def __init__(self, docs):
        self._docs = docs

    async def to_list(self, *a, **kw):
        return list(self._docs)


class _Agg:
    def __init__(self, rows):
        self.rows = rows

    def aggregate(self, *a, **kw):
        return _Cursor(self.rows)


class _Settings:
    def __init__(self, doc):
        self.doc = doc

    async def find_one(self, *a, **kw):
        return dict(self.doc)

    async def update_one(self, q, upd, **kw):
        self.doc.update(upd.get("$set", {}))


class _Chat:
    def __init__(self):
        self.inserted = []

    async def insert_one(self, doc):
        self.inserted.append(doc)


class _DB:
    def __init__(self, agg_rows, state_doc):
        self.auto_trades = _Agg(agg_rows)
        self.settings = _Settings(state_doc)
        self.ai_chat = _Chat()


def test_refresh_posts_feed_message_on_transition():
    """Übergang 'nicht reif' -> 'reif' erzeugt genau EINE KI-Feed-Meldung
    (role='playbook'); erneuter Lauf ohne Änderung meldet nichts erneut."""
    import asyncio

    from services import ai_playbook

    # breakout hat jetzt 6 gute Trades, war vorher als NICHT reif bekannt
    rows = [{"_id": "breakout", "trades": 6, "wins": 4, "pnl": 12.4}]
    state = {"_id": "ai_playbook_state", "disabled": {},
             "live_ready": {"breakout": False}}
    db = _DB(rows, state)
    asyncio.run(ai_playbook.refresh(db))
    assert len(db.ai_chat.inserted) == 1
    msg = db.ai_chat.inserted[0]
    assert msg["role"] == "playbook" and msg["setup"] == "breakout"
    assert "LIVE-freigeschaltet" in msg["text"] and "6 Trades" in msg["text"]
    # Status persistiert -> zweiter Lauf meldet NICHT erneut
    asyncio.run(ai_playbook.refresh(db))
    assert len(db.ai_chat.inserted) == 1


def test_refresh_no_feed_message_on_first_init():
    """Beim allerersten Lauf (kein vorheriger Status) wird still initialisiert."""
    import asyncio

    from services import ai_playbook

    rows = [{"_id": "breakout", "trades": 6, "wins": 4, "pnl": 12.4}]
    db = _DB(rows, {"_id": "ai_playbook_state"})
    asyncio.run(ai_playbook.refresh(db))
    assert db.ai_chat.inserted == []
    assert db.settings.doc["live_ready"]["breakout"] is True
