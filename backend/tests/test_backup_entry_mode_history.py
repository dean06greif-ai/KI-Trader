"""Regressionstests 09/2026: Backup (Supabase), Entry-Modus, Kerzen-Historie.

Reine Funktionen – kein Netzwerk, keine DB (Marker unit via conftest)."""
import time
from datetime import datetime, timezone

import numpy as np
import pytest
from bson import ObjectId

from core import instruments
from services import backup, candle_archive, candle_cache, entry_policy, retention
from services.candles import CandleArray
from services.setup_backtest import runner


# ---------------------------------------------------------------- Backup
def test_backup_name_roundtrip():
    at = datetime(2026, 9, 18, 3, 15, 0, tzinfo=timezone.utc)
    name = backup.backup_name(at)
    assert name == "backup_20260918_031500.json.gz"
    assert backup.parse_backup_date(name) == at
    assert backup.parse_backup_date("quatsch.gz") is None


def test_backup_expired_keeps_recent_and_unknown_names():
    now = datetime(2026, 9, 18, tzinfo=timezone.utc)
    names = ["backup_20260918_000000.json.gz", "backup_20260901_000000.json.gz",
             "backup_20260801_000000.json.gz", "readme.txt"]
    assert backup.expired(names, 30, now) == ["backup_20260801_000000.json.gz"]
    assert backup.expired(names, 10, now) == ["backup_20260901_000000.json.gz",
                                              "backup_20260801_000000.json.gz"]


def test_backup_dump_roundtrip_preserves_objectid_and_datetime():
    oid = ObjectId()
    dt = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)
    colls = {"settings": [{"_id": "ai_config", "enabled": True, "n": 1.5}],
             "auto_trades": [{"_id": oid, "opened_at": dt, "pnl": -3}]}
    blob = backup.encode_dump(colls, {"created_at": "x", "collections": {"settings": 1, "auto_trades": 1}})
    out = backup.decode_dump(blob)
    assert out["meta"]["collections"]["settings"] == 1
    assert out["collections"]["settings"][0] == {"_id": "ai_config", "enabled": True, "n": 1.5}
    restored = out["collections"]["auto_trades"][0]
    assert restored["_id"] == oid and restored["opened_at"] == dt and restored["pnl"] == -3


def test_backup_merged_config_defaults_and_clamps():
    cfg = backup.merged_config(None)
    assert cfg["enabled"] and cfg["retention_days"] == 30
    assert "settings" in cfg["collections"] and "auto_trades" in cfg["collections"]
    assert "ai_market_snapshots" not in cfg["collections"]   # Bulk bewusst nicht
    cfg = backup.merged_config({"enabled": False, "retention_days": 1, "collections": []})
    assert not cfg["enabled"] and cfg["retention_days"] == 3
    assert cfg["collections"] == backup.DEFAULT_COLLECTIONS


# ---------------------------------------------------------------- Retention
def test_retention_tightened_but_floors_hold():
    pol = {r["coll"]: r for r in retention.merged_policy(None)}
    assert pol["ai_decisions"]["days"] == 14
    assert pol["ai_chat_archive"]["days"] == 30
    assert pol["job_series"]["days"] == 60 and pol["ai_token_usage"]["days"] == 60
    # Untergrenzen gegen Fehlkonfiguration bleiben wirksam
    pol = {r["coll"]: r for r in retention.merged_policy({"ai_decisions": {"days": 1}})}
    assert pol["ai_decisions"]["days"] == retention.MIN_DAYS["ai_decisions"]


# ---------------------------------------------------------------- Entry-Modus
def test_entry_mode_default_is_unchanged_behaviour():
    cfg = dict(entry_policy.DEFAULTS)
    assert entry_policy.mode_of(cfg) == "ai"
    assert entry_policy.prompt_addendum(cfg) == ""
    dec = {"entry_type": "limit", "setup": "order_block", "limit_price": 100}
    assert entry_policy.apply(dec, cfg) == ("limit", None)
    assert entry_policy.apply({"entry_type": "market", "setup": "breakout"}, cfg) == ("market", None)


def test_entry_mode_conservative_forces_market_and_prompt():
    cfg = {"entry_mode": "conservative"}
    et, note = entry_policy.apply({"entry_type": "limit", "setup": "liquidity_sweep"}, cfg)
    assert et == "market" and "konservativ" in note
    assert "IMMER entry_type 'market'" in entry_policy.prompt_addendum(cfg)


def test_entry_mode_aggressive_prompt_and_note_only():
    cfg = {"entry_mode": "aggressive"}
    add = entry_policy.prompt_addendum(cfg)
    assert "liquidity_sweep" in add and "breakout" in add and "'limit'" in add
    # Limit bleibt Limit; Market bei Level-Setup bleibt Market (kein Level erfindbar)
    assert entry_policy.apply({"entry_type": "limit", "setup": "range_fade"}, cfg) == ("limit", None)
    et, note = entry_policy.apply({"entry_type": "market", "setup": "range_fade"}, cfg)
    assert et == "market" and "Modus A" in note
    assert entry_policy.apply({"entry_type": "market", "setup": "breakout"}, cfg) == ("market", None)


def test_entry_mode_clamp_rejects_unknown():
    cfg = {}
    entry_policy.clamp_updates({"entry_mode": "Aggressive"}, cfg)
    assert cfg["entry_mode"] == "aggressive"
    entry_policy.clamp_updates({"entry_mode": "yolo"}, cfg)
    assert cfg["entry_mode"] == "ai"


def test_every_playbook_setup_has_entry_style():
    from services import ai_playbook
    missing = [s for s in ai_playbook.SETUPS if s not in entry_policy.SETUP_STYLE]
    assert missing == [], f"Setups ohne Entry-Stil: {missing}"


# ---------------------------------------------------------------- Kerzen-Historie
def test_bitunix_instruments_no_longer_capped_below_backtester_max():
    for sym in ("GOLD", "SILVER", "OIL", "QQQUSDT", "SPYUSDT"):
        assert instruments.history_days_cap(sym, 365) == 365, sym
    assert instruments.history_days_cap("EURUSD", 365) == 30   # Yahoo-Forex unverändert


def _ca(days: float, step_min: int = 1) -> CandleArray:
    n = int(days * 1440 / step_min)
    ts = np.arange(n, dtype=np.float64) * step_min * 60000 + 1_700_000_000_000
    ones = np.ones(n)
    return CandleArray(ts, ones, ones, ones, ones, ones)


def test_history_note_reports_actual_days():
    assert runner.history_note("GOLD", 365, 365, _ca(364.5), "bitunix") is None
    note = runner.history_note("GOLD", 365, 365, _ca(217), "bitunix")
    assert note.startswith("GOLD: 217 von 365 Tagen") and "Listing" in note
    note = runner.history_note("EURUSD", 365, 30, _ca(29), "yahoo")
    assert "max. 30 Tage" in note
    assert runner.history_note("X", 90, 90, CandleArray.empty(), "binance") is None


def test_head_exhausted_memo_expires():
    candle_cache._HEAD_EXHAUSTED.clear()
    now = time.time()
    assert not candle_cache._head_exhausted("GOLD", now)
    candle_cache._HEAD_EXHAUSTED["GOLD"] = now
    assert candle_cache._head_exhausted("GOLD", now + 3600)
    assert not candle_cache._head_exhausted("GOLD", now + candle_cache.HEAD_RETRY_SEC + 1)
    candle_cache._HEAD_EXHAUSTED.clear()


def test_candle_archive_roundtrip_and_throttle():
    ca = _ca(2)
    out = candle_archive.decode(candle_archive.encode(ca))
    assert len(out) == len(ca) and int(out.ts[0]) == int(ca.ts[0])
    assert candle_archive.decode(b"kaputt") is None
    candle_archive._LAST.clear()
    assert candle_archive.upload_due("GOLD", 100)
    candle_archive._LAST["GOLD"] = {"at": 1000.0, "count": 100}
    assert not candle_archive.upload_due("GOLD", 100, now=1000.0 + 10 * 3600)   # nicht gewachsen
    assert not candle_archive.upload_due("GOLD", 200, now=1000.0 + 60)          # zu früh
    assert candle_archive.upload_due("GOLD", 200, now=1000.0 + 10 * 3600)
    candle_archive._LAST.clear()


@pytest.mark.parametrize("sym", ["XAUUSDT", "QQQUSDT"])
def test_bitunix_source_still_resolves(sym):
    from services import history_sources
    inst = instruments.get({"XAUUSDT": "GOLD", "QQQUSDT": "QQQUSDT"}[sym])
    assert inst.hist_source == "bitunix" and inst.hist_ref == sym
    assert history_sources.days_cap(inst.symbol, 400) == 365
