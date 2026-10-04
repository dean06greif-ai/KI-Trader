"""Regressionstests 09/2026: Revisions-Rückstufung vs. Backtest-Edge.

Befund (Prod-DB 19.09.): Fast alle Setups (session_open, momentum_news, htf_range,
divergence, ...) standen in allen Anlageklassen mit 'Revision v1: Validierung neu
gestartet' und 0 Paper-Trades seit Revision dauerhaft in der Rückstufung. Zwei
Sicherheitsmechanismen liefen gegeneinander:
  * die Rückstufung verlangt Paper-Trades seit Revision – die kamen ohne
    Auslöser (Setup-Trigger) nie zusammen;
  * das Backtest-Seeding war für rückgestufte/eval_since-Setups ausgeschlossen,
    obwohl der Backtester dem Setup (z.B. session_open Krypto, 230 OOS-Trades)
    einen Edge bestätigt hatte.

Abgedeckt:
  * setup_lifecycle.revision_demotion (rein)
  * refresh(): Revisions-Rückstufung + Backtest-Edge + 2 profitable Paper-Trades
    seit Revision -> aufgehoben, bt_validated gesetzt, live_ready True
  * refresh(): ohne echte Paper-Trades bleibt die Rückstufung (Backtest ist
    Beschleunigung, kein Ersatz)
  * refresh(): Rückstufung wegen SCHWACHER Ergebnisse wird NICHT per Backtest
    aufgehoben
  * erneute Rückstufung wegen Schwäche löscht bt_validated
  * setup_trigger.paper_wanted / _handle_hit: rückgestufte Setups bekommen
    Paper-Sammel-Trades auch ohne Backtest-Edge
"""
import asyncio
import importlib.util
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

_TESTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(_TESTS_DIR.parent))

from services import ai_playbook, setup_lifecycle as lifecycle  # noqa: E402
from services import setup_trigger as st  # noqa: E402
from services.ai_engine import DEFAULT_AI_CONFIG  # noqa: E402
from services.setup_backtest import weights as bt_weights  # noqa: E402


def _load(name):
    spec = importlib.util.spec_from_file_location(f"_helper_{name}", _TESTS_DIR / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_lg = _load("test_setup_live_gate")
_Chat, _Settings = _lg._Chat, _lg._Settings

NOW = datetime.now(timezone.utc)
SINCE = (NOW - timedelta(days=2)).isoformat()
RETEST_FUTURE = (NOW + timedelta(days=10)).isoformat()
WEAK = {"trades": 10, "wins": 2, "pnl": -20.0, "margin": 100.0}


def _rows(**by):
    return [{"_id": sid, "trades": s["trades"], "wins": s["wins"], "pnl": s["pnl"],
             "margin": s.get("margin", 0)} for sid, s in by.items()]


class _Cursor:
    def __init__(self, rows):
        self.rows = rows

    async def to_list(self, n=None):
        return list(self.rows)


class _Trades:
    """auto_trades.aggregate(): je nach Filter (mode/since/$or) andere Zeilen."""

    def __init__(self, overall=None, paper_since=None, since_all=None):
        self.overall = overall or []
        self.paper_since = paper_since or []
        self.since_all = since_all or []

    def aggregate(self, pipeline, *a, **kw):
        match = pipeline[0]["$match"]
        cutoff = match.get("opened_at", {}).get("$gte", "")
        recent = cutoff > (NOW - timedelta(days=ai_playbook.LOOKBACK_DAYS - 1)).isoformat()
        if match.get("mode") == "live":
            rows = []
        elif "$or" in match:
            rows = self.paper_since if recent else self.overall
        elif recent:
            rows = self.since_all
        else:
            rows = self.overall
        if match.get("setup") and not isinstance(match["setup"], dict):
            rows = [r for r in rows if r["_id"] == match["setup"]]
        return _Cursor(rows)

    def find(self, *a, **kw):
        return _Cursor([])


class _BacktestTrades:
    """setup_backtest_trades.aggregate(): OOS-Trades je Setup der Klasse."""

    def __init__(self, by_setup):
        self.by_setup = by_setup

    def aggregate(self, pipeline, *a, **kw):
        cls = pipeline[0]["$match"].get("asset_class")
        rows = [{"_id": sid, **v} for sid, v in self.by_setup.get(cls, {}).items()]
        return _Cursor(rows)


class _DB:
    def __init__(self, trades, state, backtest=None):
        self.auto_trades = trades
        self.settings = _Settings(state)
        self.ai_chat = _Chat()
        self._bt = _BacktestTrades(backtest or {})

    def __getitem__(self, name):
        assert name == bt_weights.COLLECTION, name
        return self._bt


@pytest.fixture(autouse=True)
def _reset_caches():
    for c in (ai_playbook._disabled_cache, ai_playbook._live_blocked_cache,
              ai_playbook._ready_cache, ai_playbook._class_cache):
        c.clear()
    yield
    for c in (ai_playbook._disabled_cache, ai_playbook._live_blocked_cache,
              ai_playbook._ready_cache, ai_playbook._class_cache):
        c.clear()


def _state(reason, **extra):
    return {"_id": "ai_playbook_state", "disabled": {}, "live_ready": {},
            "classes": {"crypto": {"live_blocked": {"session_open": {
                "at": SINCE, "reason": reason, "retest_at": RETEST_FUTURE,
                "paper_since": {"trades": 0}, **extra}}}}}


BT_EDGE = {"crypto": {"session_open": {"trades": 230, "wins": 130, "pnl": 410.0, "margin": 2000.0}}}


# ---------------- rein ----------------
def test_revision_demotion_detects_kind_or_legacy_reason():
    assert lifecycle.revision_demotion({"kind": "revision", "reason": "x"}) is True
    assert lifecycle.revision_demotion({"reason": "Revision v1: Validierung neu gestartet"}) is True
    assert lifecycle.revision_demotion({"reason": "Gesamt-Urteil schwach: 24 Trades"}) is False
    assert lifecycle.revision_demotion({"reason": "chronisch negativ: 13 Trades"}) is False
    assert lifecycle.revision_demotion(None) is False


def test_revise_setup_marks_demotion_kind():
    state = {"_id": "ai_playbook_state", "classes": {"crypto": {
        "live_blocked": {"session_open": {"at": SINCE, "reason": "schwach"}}}}}
    db = _DB(_Trades(), state)
    res = asyncio.run(ai_playbook.revise_setup(
        db, "crypto", "session_open", "London-Open Breakout mit Volumenfilter und Retest",
        reason="Test", source="test"))
    assert res["status"] == "ok"
    lb = db.settings.doc["classes"]["crypto"]["live_blocked"]["session_open"]
    assert lb["kind"] == "revision" and lb["reason"].startswith("Revision v1")
    assert lifecycle.revision_demotion(lb)


# ---------------- refresh ----------------
def test_revision_demotion_without_paper_trades_stays_blocked_despite_edge():
    db = _DB(_Trades(), _state("Revision v1: Validierung neu gestartet", kind="revision"),
             backtest=BT_EDGE)
    data = asyncio.run(ai_playbook.refresh(db))
    cls = data["classes"]["crypto"]
    assert "session_open" in cls["live_blocked"], "Backtest allein darf nicht freischalten"
    assert cls["live_ready"]["session_open"] is False
    assert "session_open" not in cls.get("bt_validated", {})


def test_revision_demotion_lifted_by_backtest_edge_plus_two_paper_trades():
    paper = _rows(session_open={"trades": 2, "wins": 2, "pnl": 6.0, "margin": 20.0})
    db = _DB(_Trades(paper_since=paper, since_all=paper),
             _state("Revision v1: Validierung neu gestartet", kind="revision"),
             backtest=BT_EDGE)
    data = asyncio.run(ai_playbook.refresh(db))
    cls = data["classes"]["crypto"]
    assert "session_open" not in cls["live_blocked"]
    assert cls["bt_validated"].get("session_open")
    # Seeding zählt nach eval_since weiter -> live-reif (backtest-seeded)
    assert cls["live_ready"]["session_open"] is True
    assert ai_playbook.live_ready_for("session_open", None, asset_class="crypto")[0] is True
    assert ai_playbook.live_block_reason("session_open", asset_class="crypto") is None
    msgs = [m["text"] for m in db.ai_chat.inserted if m.get("setup") == "session_open"]
    assert any("backtest-validiert" in m for m in msgs)
    # Persistenz + zweiter Lauf stabil (kein Ping-Pong)
    assert db.settings.doc["classes"]["crypto"]["bt_validated"].get("session_open")
    data2 = asyncio.run(ai_playbook.refresh(db))
    assert "session_open" not in data2["classes"]["crypto"]["live_blocked"]
    assert data2["classes"]["crypto"]["live_ready"]["session_open"] is True


def test_legacy_revision_entry_without_kind_is_lifted_too():
    paper = _rows(session_open={"trades": 2, "wins": 1, "pnl": 3.0, "margin": 20.0})
    db = _DB(_Trades(paper_since=paper, since_all=paper),
             _state("Revision v1: Validierung neu gestartet"), backtest=BT_EDGE)
    data = asyncio.run(ai_playbook.refresh(db))
    assert "session_open" not in data["classes"]["crypto"]["live_blocked"]


def test_weak_demotion_is_not_lifted_by_backtest():
    paper = _rows(session_open={"trades": 2, "wins": 2, "pnl": 6.0, "margin": 20.0})
    db = _DB(_Trades(overall=_rows(session_open=WEAK), paper_since=paper, since_all=paper),
             _state("Gesamt-Urteil schwach: 10 Trades, Winrate 20%"), backtest=BT_EDGE)
    data = asyncio.run(ai_playbook.refresh(db))
    cls = data["classes"]["crypto"]
    assert "session_open" in cls["live_blocked"], "schwache Setups brauchen echte Paper-Trades"
    assert cls["live_ready"]["session_open"] is False


def test_redemotion_for_weakness_clears_bt_validated():
    weak_live = {"trades": 12, "wins": 2, "pnl": -60.0, "margin": 100.0}
    state = {"_id": "ai_playbook_state", "disabled": {}, "live_ready": {},
             "classes": {"crypto": {"live_blocked": {}, "bt_validated": {"session_open": SINCE},
                                    "eval_since": {"session_open": SINCE}}}}
    db = _DB(_Trades(overall=_rows(session_open=weak_live), since_all=_rows(session_open=weak_live)),
             state, backtest=BT_EDGE)
    data = asyncio.run(ai_playbook.refresh(db))
    cls = data["classes"]["crypto"]
    assert "session_open" in cls["live_blocked"]
    assert "session_open" not in cls["bt_validated"]
    assert not lifecycle.revision_demotion(cls["live_blocked"]["session_open"])


# ---------------- Setup-Trigger: Paper-Trades für rückgestufte Setups ----------------
def test_paper_wanted_rules():
    assert st.paper_wanted("breakout", True, False, False) is True
    assert st.paper_wanted("breakout", False, True, False) is True
    assert st.paper_wanted("momentum_news", False, False, False) is True
    assert st.paper_wanted("breakout", False, False, False) is False
    assert st.paper_wanted("breakout", False, False, True) is True


class _Engine:
    def __init__(self):
        self.config = {**DEFAULT_AI_CONFIG, "enabled": True, "collection_enabled": True}
        self.db = type("DB", (), {"ai_decisions": type("C", (), {
            "insert_one": staticmethod(_noop)})()})()
        self.key = None
        self._analyzing = False
        self.emitted = []

    async def _emit_signal(self, dec, collection=False):
        self.emitted.append((dec, collection))
        return True


async def _noop(*a, **kw):
    return None


def test_handle_hit_paper_trade_for_demoted_setup_without_edge():
    ai_playbook._class_cache["crypto"] = {"live_blocked": {"session_open": {
        "at": SINCE, "reason": "Revision v1: Validierung neu gestartet", "retest_at": RETEST_FUTURE}}}
    trig = st.SetupTrigger()
    eng = _Engine()
    trig.setup(eng)
    hit = {"setup": "session_open", "side": "LONG", "entry": 100.0, "sl": 99.0, "tp1": 101.0,
           "tpf": 102.0, "note": "London-Open", "has_edge": False}
    res = asyncio.run(trig._handle_hit("BTCUSDT", "crypto", hit))
    assert res["paper"] is True and res["ai"] is False
    dec, collection = eng.emitted[0]
    assert collection is True and dec["setup"] == "session_open"
    # nicht rückgestuft + kein Edge + kein paper_all -> weiterhin kein Paper-Trade
    hit2 = {**hit, "setup": "breakout"}
    res2 = asyncio.run(trig._handle_hit("ETHUSDT", "crypto", hit2))
    assert res2["paper"] is False and len(eng.emitted) == 1
