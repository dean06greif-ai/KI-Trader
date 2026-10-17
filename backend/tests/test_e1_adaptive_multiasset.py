"""Regressionstests (E1, 06/2026): Retention-Erweiterung + Kompaktierung,
Multi-Asset-Historie (IBKR-Quelle, Markt-Öffnungs-Maske, Tages-Cap),
Aktivitäts-Wächter Stufe 4 (Ideen-Runde). Nur pure Logik – kein Netz/LLM."""
import asyncio
from datetime import datetime, timezone, timedelta

import numpy as np

from core import market_hours
from services import retention, history_sources
from services import activity_guard as ag
from services import activity_ideas as ideas
from services.candles import CandleArray
from services.setup_backtest import runner


# ---------------------------------------------------------------- Retention
def test_retention_policy_covers_previously_unbounded_collections():
    colls = {p["coll"] for p in retention.merged_policy({})}
    for c in ("app_notifications", "ai_ghost_trades", "regime_lab_runs",
              "dynamic_switch_log", "confluence_events", "local_jobs"):
        assert c in colls, f"{c} fehlt in der Retention-Policy"


def test_retention_still_never_touches_core_collections():
    colls = {p["coll"] for p in retention.merged_policy({})}
    for c in ("auto_trades", "settings", "ai_knowledge", "ai_playbook", "signals",
              "setup_backtest_trades", "strategies", "custom_strategies"):
        assert c not in colls


def test_retention_ghost_trades_floor():
    pol = {p["coll"]: p for p in retention.merged_policy({"ai_ghost_trades": {"days": 1}})}
    assert pol["ai_ghost_trades"]["days"] == retention.MIN_DAYS["ai_ghost_trades"]


def test_compact_error_kind_classifies_atlas_shared_tier():
    assert retention.compact_error_kind("command compact is not allowed on Atlas shared tier") == "unsupported"
    assert retention.compact_error_kind("not authorized on crypto_scanner to execute command") == "unsupported"
    assert retention.compact_error_kind("ns not found") == "error"


class _FakeColl:
    def __init__(self, docs=None):
        self.docs = docs or []
        self.deleted = []

    async def delete_many(self, flt):
        n = len(self.docs)
        self.deleted.append(flt)
        return type("R", (), {"deleted_count": n})()

    async def count_documents(self, flt):
        return len(self.docs)

    def find(self, *a, **k):
        return self

    def sort(self, *a):
        return self

    def skip(self, n):
        return self

    async def to_list(self, length=None):
        return []

    async def find_one(self, *a, **k):
        return None

    async def update_one(self, *a, **k):
        return None


class _FakeDB:
    def __init__(self, fail=None):
        self.settings = _FakeColl()
        self.fail = fail or {}
        self._colls = {}

    def __getitem__(self, name):
        return self._colls.setdefault(name, _FakeColl())

    async def list_collection_names(self):
        return ["big_a", "big_b"]

    async def command(self, cmd, name=None, **kw):
        if cmd == "collstats":
            return {"size": 50e6 if name == "big_a" else 10e6, "count": 5, "totalIndexSize": 1e6}
        if cmd == "compact":
            if name in self.fail:
                raise RuntimeError(self.fail[name])
            return {"bytesFreed": 1234}
        raise RuntimeError("unknown")


def test_compact_reports_unsupported_with_hint():
    db = _FakeDB(fail={"big_a": "not allowed on Atlas shared tier", "big_b": "not allowed on Atlas shared tier"})
    res = asyncio.run(retention.compact(db))
    assert res["ok"] == 0 and res["unsupported"] == 2
    assert res["hint"] == retention.ATLAS_SHARED_HINT
    assert [r["coll"] for r in res["collections"]] == ["big_a", "big_b"]  # größte zuerst


def test_compact_success_has_no_hint():
    res = asyncio.run(retention.compact(_FakeDB(), ["big_a"]))
    assert res["ok"] == 1 and res["hint"] is None
    assert res["collections"][0]["bytes_freed"] == 1234


# ------------------------------------------------------- Markt-Öffnungs-Maske
def _ts(y, m, d, hh, mm=0):
    return int(datetime(y, m, d, hh, mm, tzinfo=timezone.utc).timestamp() * 1000)


def test_open_mask_crypto_always_open():
    ts = np.array([_ts(2026, 6, 6, 12), _ts(2026, 6, 7, 3)])  # Sa, So
    assert market_hours.open_mask("BTCUSDT", ts).all()


def test_open_mask_forex_weekend_and_daily_break():
    ts = np.array([
        _ts(2026, 6, 3, 12),      # Mi Mittag -> offen
        _ts(2026, 6, 5, 21, 30),  # Fr nach 21:00 -> zu
        _ts(2026, 6, 6, 12),      # Sa -> zu
        _ts(2026, 6, 7, 20),      # So vor 21:15 -> zu
        _ts(2026, 6, 7, 22, 30),  # So nach Öffnung -> offen
        _ts(2026, 6, 3, 21, 30),  # Mi tägliche Pause 21:00-22:05 -> zu
    ])
    m = market_hours.open_mask("EURUSD", ts)
    assert m.tolist() == [True, False, False, False, True, False]


def test_open_mask_matches_scalar_is_market_closed():
    base = datetime(2026, 6, 1, tzinfo=timezone.utc)
    stamps = [base + timedelta(hours=h) for h in range(0, 24 * 7, 3)]
    ts = np.array([int(s.timestamp() * 1000) for s in stamps])
    vec = market_hours.open_mask("XAUUSDT", ts)
    scalar = [not market_hours.is_market_closed("XAUUSDT", s)[0] for s in stamps]
    assert vec.tolist() == scalar


def test_market_open_only_filters_candles_for_forex_and_keeps_crypto():
    ts = np.array([_ts(2026, 6, 3, 12), _ts(2026, 6, 6, 12), _ts(2026, 6, 3, 13)], dtype=np.int64)
    one = np.ones(3)
    ca = CandleArray(ts, one, one, one, one, one)
    fx = runner.market_open_only("EURUSD", ca)
    assert len(fx) == 2 and _ts(2026, 6, 6, 12) not in fx.ts.tolist()
    assert runner.market_open_only("BTCUSDT", ca) is ca


# ---------------------------------------------------- Historien-Quelle Forex
def test_resolve_forex_falls_back_to_yahoo_without_ibkr(monkeypatch):
    monkeypatch.setattr(history_sources, "_ibkr_available", lambda: False)
    assert history_sources._resolve("EURUSD") == ("yahoo", "EURUSD=X")
    assert history_sources.days_cap("EURUSD", 365) == 30


def test_resolve_forex_uses_ibkr_when_configured(monkeypatch):
    monkeypatch.setattr(history_sources, "_ibkr_available", lambda: True)
    assert history_sources._resolve("EURUSD") == ("ibkr", "EURUSD")
    assert history_sources.days_cap("EURUSD", 3650) == history_sources.IBKR_MAX_DAYS
    assert not history_sources.supports_parallel("EURUSD")
    assert "ibkr" in history_sources.SOURCES
    # Krypto/Indizes unverändert
    assert history_sources._resolve("BTCUSDT")[0] == "binance"
    assert history_sources.days_cap("BTCUSDT", 90) == 90


def test_fetch_ibkr_falls_back_to_yahoo_without_conid(monkeypatch):
    from services.ibkr_client import ibkr_client

    async def _no_conid(sym):
        return None

    async def _yahoo(session, ref, s, e, job=None, pace=0.25):
        return [np.zeros((1, 6))]

    monkeypatch.setattr(ibkr_client, "forex_conid", _no_conid)
    monkeypatch.setattr(history_sources, "fetch_yahoo", _yahoo)
    blocks = asyncio.run(history_sources.fetch_ibkr(None, "EURUSD", 0, 10 * 86400_000))
    assert len(blocks) == 1


def test_fetch_ibkr_chunks_and_orders_ascending(monkeypatch):
    from services.ibkr_client import ibkr_client
    calls = []

    async def _conid(sym):
        return 42

    async def _bars(conid, start_ms, end_ms, bar="5min"):
        calls.append((start_ms, end_ms, bar))
        t = start_ms // 1000
        return [{"time": t, "open": 1.0, "high": 1.1, "low": 0.9, "close": 1.05}]

    monkeypatch.setattr(ibkr_client, "forex_conid", _conid)
    monkeypatch.setattr(ibkr_client, "history_bars", _bars)
    end = 5 * 86400_000
    blocks = asyncio.run(history_sources.fetch_ibkr(None, "EURUSD", 0, end, pace=0))
    assert all(c[2] == "1min" for c in calls)
    assert len(calls) == 3  # 5 Tage / 48h-Chunks
    ts = [b[0, 0] for b in blocks]
    assert ts == sorted(ts)
    assert blocks[0][0, 5] > 0  # Aktivitäts-Volumen statt 0


# ------------------------------------------------- Aktivitäts-Wächter Stufe 4
def test_guard_normalize_idea_defaults_and_clamp():
    cfg = ag.normalize({"idea_gap_hours": 1, "idea_rounds": False})
    assert cfg["idea_gap_hours"] == 6 and cfg["idea_rounds"] is False
    assert ag.normalize({})["idea_rounds"] is True


def test_idea_round_due_only_after_max_steps_and_gap():
    st = ag.normalize({"steps": 2, "max_steps": 3})
    assert not ag.idea_round_due(st, rate=0.5)
    st["steps"] = 3
    assert ag.idea_round_due(st, rate=0.5)
    assert not ag.idea_round_due(st, rate=3.0)
    st["last_idea_at"] = datetime.now(timezone.utc).isoformat()
    assert not ag.idea_round_due(st, rate=0.5)
    st["last_idea_at"] = (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat()
    assert ag.idea_round_due(st, rate=0.5)
    st["idea_rounds"] = False
    assert not ag.idea_round_due(st, rate=0.5)


def test_parse_idea_validates_actions():
    known = {"trend_follow": "x", "breakout": "y"}
    assert ideas.parse_idea({"action": "new_setup"}, known) is None  # keine Diagnose
    ok = ideas.parse_idea({"diagnosis": "d", "action": "new_setup", "setup_id": "Range_Fade_EU",
                           "desc": "Entry am Range-Hoch mit RSI>70, SL 0.4%, TP 0.8%", "trade_target": "7.5",
                           "asset_class": "forex"}, known)
    assert ok["action"] == "new_setup" and ok["setup_id"] == "range_fade_eu"
    assert ok["trade_target"] == 7 and ok["asset_class"] == "forex"
    # revise auf unbekanntes Setup -> none
    rv = ideas.parse_idea({"diagnosis": "d", "action": "revise", "setup_id": "unknown",
                           "desc": "lange Beschreibung mit mehr als 20 Zeichen"}, known)
    assert rv["action"] == "none"
    # zu kurze desc -> none
    short = ideas.parse_idea({"diagnosis": "d", "action": "new_setup", "setup_id": "abc", "desc": "kurz"}, known)
    assert short["action"] == "none"
    # ungültige Klasse -> crypto
    assert ideas.parse_idea({"diagnosis": "d", "action": "none", "asset_class": "bonds"}, known)["asset_class"] == "crypto"


def test_summarize_holds_dedupes_reasons():
    holds = [{"symbol": "BTCUSDT", "reasoning": "Range zu eng, kein Setup"},
             {"symbol": "BTCUSDT", "reasoning": "Range zu eng, kein Setup"},
             {"symbol": "ETHUSDT", "reasoning": "Konfidenz unter Schwelle"}]
    s = ideas.summarize_holds(holds)
    assert s["count"] == 3 and s["symbols"]["BTCUSDT"] == 2 and len(s["reasons"]) == 2


def test_build_prompt_contains_evidence():
    ev = {"moves": [{"symbol": "SOLUSDT", "asset_class": "crypto",
                     "features": {"change_60m_pct": 3.2, "regime": "trend", "volume_ratio": 2.1},
                     "analysis": {"cause": "News", "missed_reason": "Schwelle", "setup_match": "breakout"}}],
          "holds": {"count": 4, "symbols": {"BTCUSDT": 4}, "reasons": ["zu ruhig"]}}
    p = ideas.build_prompt(ev, {"breakout": "Ausbruch"}, {"min_confidence": 55}, 0.5, 3)
    for needle in ("SOLUSDT", "+3.20%", "Schwelle", "breakout", "zu ruhig", "min_confidence"):
        assert needle in p


def test_chat_text_variants():
    base = {"diagnosis": "zu wenig Vola", "action": "none", "reason": "marktbedingt"}
    assert "marktbedingt" in ideas.chat_text(base)
    applied = {**base, "action": "new_setup", "setup_id": "x_y", "desc": "regel",
               "action_result": {"kind": "new_setup", "status": "ok"}}
    assert "Neues Datensammel-Setup" in ideas.chat_text(applied)
    rejected = {**applied, "action_result": {"kind": "new_setup", "status": "rejected", "reason": "Alias"}}
    assert "nicht angewendet" in ideas.chat_text(rejected)
