"""Regressionstests: Setup-Lebenszyklus OHNE harte Sperren (06/2026).

Nutzer-Vorgabe: Schwache Setups werden nicht mehr gesperrt, sondern in die
Paper-Datensammlung zurückgestuft und automatisch wieder live geschaltet,
sobald seit der Rückstufung genug gute Paper-Trades vorliegen. Abgedeckt:
  * live_divergent / demotion_candidates / migrate_disabled (rein)
  * refresh(): 'schwach' -> live_blocked (nicht disabled), Feed-Meldung
  * refresh(): Alt-Sperren werden migriert, disabled bleibt leer
  * Wieder-Freischaltung über Paper-Trades seit Rückstufung; danach zählt
    nur noch die Statistik seit der Rückstufung (kein Ping-Pong)
  * live_ready_for nutzt den Reife-Cache; disabled_reason liefert None
  * Prompt-Kontext spricht von Rückstufung, nicht von Sperre
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
from datetime import datetime, timedelta, timezone

import pytest

from services import ai_playbook, setup_lifecycle as lifecycle
_lg = _load("test_setup_live_gate")
_Chat, _Settings = _lg._Chat, _lg._Settings

WEAK = {"trades": 10, "wins": 2, "pnl": -20.0, "margin": 100.0, "verdict": "schwach"}
GOOD = {"trades": 10, "wins": 7, "pnl": 25.0, "margin": 100.0, "verdict": "bewährt"}


class _Trades:
    """aggregate() liefert je nach Filter (mode/since) andere Zeilen."""

    def __init__(self, overall, paper_since=None, live_only=None, since_all=None):
        self.overall = overall
        self.paper_since = paper_since or []
        self.live_only = live_only or []
        self.since_all = since_all or []

    def aggregate(self, pipeline, *a, **kw):
        match = pipeline[0]["$match"]
        cutoff = match.get("opened_at", {}).get("$gte", "")
        recent = cutoff > (datetime.now(timezone.utc) - timedelta(days=ai_playbook.LOOKBACK_DAYS - 1)).isoformat()
        if match.get("mode") == "live":
            rows = self.live_only
        elif "$or" in match:
            rows = self.paper_since
        elif recent:
            rows = self.since_all
        else:
            rows = self.overall
        if match.get("setup") and not isinstance(match["setup"], dict):
            rows = [r for r in rows if r["_id"] == match["setup"]]

        class _C:
            async def to_list(self_, n=None):
                return list(rows)
        return _C()

    def find(self, *a, **kw):
        class _C:
            async def to_list(self_, n=None):
                return []
        return _C()


class _DB:
    def __init__(self, trades, state):
        self.auto_trades = trades
        self.settings = _Settings(state)
        self.ai_chat = _Chat()


@pytest.fixture(autouse=True)
def _reset_caches():
    ai_playbook._disabled_cache.clear()
    ai_playbook._live_blocked_cache.clear()
    ai_playbook._ready_cache.clear()
    yield
    ai_playbook._disabled_cache.clear()
    ai_playbook._live_blocked_cache.clear()
    ai_playbook._ready_cache.clear()


# ---------------- rein ----------------
def test_live_divergent_reports_weak_overall_verdict():
    why = ai_playbook.live_divergent(None, WEAK)
    assert why and "schwach" in why and "10 Trades" in why
    assert ai_playbook.live_divergent(None, GOOD) is None
    # Paper/Live-Divergenz weiter erkannt
    live = {"trades": 15, "wins": 1, "pnl": -30.0, "margin": 100.0}
    assert ai_playbook.live_divergent(live, GOOD) == lifecycle.demotion_reason(live)


def test_demotion_candidates_only_library_setups():
    stats = {"momentum_news": WEAK, "breakout": GOOD, "unknown_setup": WEAK}
    out = ai_playbook.demotion_candidates(stats, {}, ai_playbook.all_setups())
    assert set(out) == {"momentum_news"}


def test_migrate_disabled_moves_locks_to_demotion():
    disabled = {"breakout": {"at": "2026-06-01T00:00:00+00:00",
                             "retest_at": "2026-06-15T00:00:00+00:00", "reason": "8 Trades, WR 25%"}}
    d, lb, changed = ai_playbook.migrate_disabled(disabled, {"pullback": {"at": "x"}})
    assert changed and d == {}
    assert set(lb) == {"breakout", "pullback"}
    assert lb["breakout"]["reason"].startswith("migriert aus Sperre")
    assert lb["breakout"]["retest_at"] == "2026-06-15T00:00:00+00:00"
    assert ai_playbook.migrate_disabled({}, {}) == ({}, {}, False)


# ---------------- refresh ----------------
def _rows(**by):
    return [{"_id": sid, "trades": s["trades"], "wins": s["wins"], "pnl": s["pnl"],
             "margin": s.get("margin", 0)} for sid, s in by.items()]


def test_refresh_demotes_instead_of_locking():
    db = _DB(_Trades(_rows(momentum_news=WEAK, breakout=GOOD)),
             {"_id": "ai_playbook_state", "disabled": {}, "live_ready": {}})
    data = asyncio.run(ai_playbook.refresh(db))
    assert data["disabled"] == {}
    assert "momentum_news" in data["live_blocked"]
    assert data["live_ready"]["momentum_news"] is False
    assert data["live_ready"]["breakout"] is True
    # Feed-Meldung spricht von Rückstufung/Datensammlung, nicht von Sperre
    msgs = [m["text"] for m in db.ai_chat.inserted if m["setup"] == "momentum_news"]
    assert msgs and "Paper-Datensammlung" in msgs[0] and "keine Sperre" in msgs[0]
    # Caches: keine harte Sperre, aber Rückstufungs-Grund fürs Live-Gate
    assert ai_playbook.disabled_reason("momentum_news") is None
    lb = ai_playbook.live_block_reason("momentum_news")
    assert lb and "rückgestuft" in lb and "Paper-Datensammlung" in lb
    assert ai_playbook.live_ready_for("momentum_news", WEAK)[0] is False
    assert ai_playbook.live_ready_for("breakout", GOOD) == (True, "bewährt")
    # Persistenz
    assert db.settings.doc["disabled"] == {} and "momentum_news" in db.settings.doc["live_blocked"]


def test_refresh_migrates_old_locks():
    state = {"_id": "ai_playbook_state",
             "disabled": {"momentum_news": {"at": "2026-06-01T00:00:00+00:00",
                                            "retest_at": "2026-06-15T00:00:00+00:00",
                                            "reason": "8 Trades, Winrate 25%"}},
             "live_ready": {"momentum_news": False}}
    # Setup ist inzwischen wieder gut – wäre unter der alten Sperre trotzdem blockiert
    db = _DB(_Trades(_rows(momentum_news=GOOD)), state)
    data = asyncio.run(ai_playbook.refresh(db))
    assert data["disabled"] == {}
    # Migration -> Rückstufung; Re-Test-Datum liegt in der Vergangenheit und
    # die Gesamtstatistik ist gut -> sofort wieder freigeschaltet
    assert "momentum_news" not in data["live_blocked"]
    assert data["live_ready"]["momentum_news"] is True
    assert ai_playbook.disabled_reason("momentum_news") is None


def test_refresh_repromotes_after_good_paper_trades_and_no_pingpong():
    since = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
    paper_since = _rows(momentum_news={"trades": 6, "wins": 4, "pnl": 8.0})
    state = {"_id": "ai_playbook_state", "disabled": {},
             "live_blocked": {"momentum_news": {"at": since, "reason": "schwach",
                                                "retest_at": (datetime.now(timezone.utc)
                                                              + timedelta(days=10)).isoformat(),
                                                "paper_since": {"trades": 0}}},
             "live_ready": {"momentum_news": False}}
    # 30-Tage-Gesamtstatistik weiterhin schwach, seit Rückstufung aber gut
    trades = _Trades(_rows(momentum_news=WEAK), paper_since=paper_since,
                     since_all=paper_since)
    db = _DB(trades, state)
    data = asyncio.run(ai_playbook.refresh(db))
    assert "momentum_news" not in data["live_blocked"], "Paper-Erfolg muss wieder live schalten"
    assert data["live_ready"]["momentum_news"] is True
    assert data["eval_since"]["momentum_news"] == since
    assert ai_playbook.live_ready_for("momentum_news", WEAK)[0] is True
    # zweiter Lauf: alte 30-Tage-Verluste dürfen NICHT sofort erneut zurückstufen
    data2 = asyncio.run(ai_playbook.refresh(db))
    assert "momentum_news" not in data2["live_blocked"]
    assert data2["live_ready"]["momentum_news"] is True


def test_context_text_mentions_demotion_not_lock():
    db = _DB(_Trades(_rows(momentum_news=WEAK)),
             {"_id": "ai_playbook_state", "disabled": {}, "live_ready": {}})
    text = asyncio.run(ai_playbook.context_text(db))
    assert "RÜCKGESTUFT" in text and "GESPERRT" not in text
    assert "RÜCKGESTUFTE SETUPS" in text and "KEINE Sperre" in text


def test_maturity_overview_uses_demotion_phase_and_judge_stats():
    rows = ai_playbook.maturity_overview(
        {"momentum_news": WEAK}, {},
        live_blocked={"momentum_news": {"reason": "schwach", "paper_since": {"trades": 2}}})
    by = {r["setup"]: r for r in rows}
    assert by["momentum_news"]["phase"] == "rückgestuft"
    assert by["momentum_news"]["reason"].startswith("rückgestuft")
    assert by["momentum_news"]["paper_since_demotion"] == 2
    # judge_stats (seit Rückstufung gut) macht das Setup live-reif
    rows = ai_playbook.maturity_overview({"momentum_news": WEAK}, {},
                                         judge_stats={"momentum_news": GOOD})
    assert {r["setup"]: r for r in rows}["momentum_news"]["live_ready"] is True


def test_status_reports_no_hard_locks():
    db = _DB(_Trades([]), {"_id": "ai_playbook_state"})
    st = asyncio.run(ai_playbook.status(db))
    assert st["hard_locks"] is False and st["disabled"] == {}
