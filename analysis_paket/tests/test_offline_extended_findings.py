"""Erweiterte isolierte Offline-Prüfungen (T01-T05, R05, R08, R14)."""

import ast
import asyncio
import os
import sys
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest


SOURCE_DIR = Path(os.environ.get("KI_TRADER_SOURCE_DIR", "/app/review_source/backend")).resolve()
if str(SOURCE_DIR) not in sys.path:
    sys.path.insert(0, str(SOURCE_DIR))


def _source(rel_path: str) -> str:
    return (SOURCE_DIR / rel_path).read_text(encoding="utf-8")


def _extract_method(rel_path: str, class_name: str, method_name: str, globals_ns: dict):
    src = _source(rel_path)
    tree = ast.parse(src)
    target = None
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            for m in node.body:
                if isinstance(m, ast.AsyncFunctionDef) and m.name == method_name:
                    target = m
                    break
    assert target is not None, f"Methode {class_name}.{method_name} nicht gefunden"
    fn_src = ast.get_source_segment(src, target)
    ns = dict(globals_ns)
    exec(fn_src, ns)  # noqa: S102
    return ns[method_name]


def _extract_function(rel_path: str, fn_name: str, globals_ns: dict):
    src = _source(rel_path)
    tree = ast.parse(src)
    target = None
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == fn_name:
            target = node
            break
    assert target is not None, f"Funktion {fn_name} nicht gefunden"
    fn_src = ast.get_source_segment(src, target)
    ns = dict(globals_ns)
    exec(fn_src, ns)  # noqa: S102
    return ns[fn_name]


@pytest.mark.anyio
async def test_t01_watchdog_adopt_closes_full_foreign_position_without_identity_proof(monkeypatch):
    src = _source("services/position_watchdog.py")
    assert "flash_close(internal, pos[\"position_id\"]," in src
    assert "full=True" in src

    fake_logger = SimpleNamespace(warning=lambda *_a, **_k: None,
                                  error=lambda *_a, **_k: None,
                                  debug=lambda *_a, **_k: None)
    adopt = _extract_method(
        "services/position_watchdog.py",
        "PositionWatchdog",
        "_adopt",
        {
            "datetime": datetime,
            "timedelta": timedelta,
            "timezone": timezone,
            "time": __import__("time"),
            "logger": fake_logger,
            "Dict": dict,
            "Optional": __import__("typing").Optional,
            "_f": lambda v: float(v or 0),
        },
    )

    calls = {"flash_close": []}

    class FakeAutoTrades:
        async def find_one(self, _q):
            # Kürzlich geschlossener Bot-Trade mit ANDERER Position-ID.
            return {
                "symbol": "BTCUSDT",
                "side": "LONG",
                "status": "closed",
                "bitunix_position_id": "old-pos-1",
                "qty": 0.01,
                "strategy_name": "botA",
            }

        async def insert_one(self, _d):
            return None

    class FakeClient:
        async def get_mark_price(self, _internal):
            return 100.0

        async def flash_close(self, internal, position_id, side, qty, full=False):
            calls["flash_close"].append((internal, position_id, side, qty, full))
            return {"code": 0}

    fake_self = SimpleNamespace(
        db=SimpleNamespace(auto_trades=FakeAutoTrades()),
        client=FakeClient(),
        settings={"fallback_sl_percent": 2.0},
        _notify=(lambda *_a, **_k: asyncio.sleep(0)),
        _adopt_from_registry=(lambda *_a, **_k: asyncio.sleep(0)),
    )

    # Registry-Match erzwingen auf None (reg=None + find_match=None).
    stub_registry = types.ModuleType("entry_order_registry")

    async def _no_match(_db, _internal, _side):
        return None

    stub_registry.find_match = _no_match
    monkeypatch.setitem(sys.modules, "services.entry_order_registry", stub_registry)

    pos = {
        "entry": 100.0,
        "side": "LONG",
        "qty": 5.0,
        "position_id": "new-foreign-pos-99",
        "margin": 200.0,
        "leverage": 10,
    }
    out = await adopt(fake_self, "BTCUSDT", pos, None)
    assert out is None
    assert calls["flash_close"] == [(
        "BTCUSDT",
        "new-foreign-pos-99",
        "LONG",
        5.0,
        True,
    )]


@pytest.mark.anyio
async def test_t02_exception_recovery_branch_accepts_half_fill_but_keeps_planned_qty_semantics():
    src = _source("services/bitunix_trade.py")
    tree = ast.parse(src)
    branch = next(n for n in ast.walk(tree) if isinstance(n, ast.ExceptHandler)
                  and "Live order EXCEPTION" in (ast.get_source_segment(src, n) or ""))
    # Execute the actual exception suite, not a handwritten model of its logic.
    wrapper = ast.parse("async def probe(qty):\n    pass\n")
    wrapper.body[0].body = branch.body + ast.parse("return res, qty").body
    ast.fix_missing_locations(wrapper)

    async def run_branch(qty, untracked):
        notified = []
        async def untracked_qty(*_args):
            return untracked
        async def reject(*_args):
            notified.append("reject")
        async def no_sleep(*_args):
            return None
        ns = {"self": SimpleNamespace(_untracked_qty=untracked_qty,
                                      _notify_reject=reject),
              "symbol": "BTCUSDT", "side": "LONG", "e": RuntimeError("timeout"),
              "signal": {}, "cfg": {}, "asyncio": SimpleNamespace(sleep=no_sleep),
              "logger": SimpleNamespace(warning=lambda *_a, **_k: None,
                                        error=lambda *_a, **_k: None)}
        exec(compile(wrapper, str(SOURCE_DIR / "services/bitunix_trade.py"), "exec"), ns)
        result = await ns["probe"](qty)
        return (result[0] if result else None), notified, (result[1] if result else qty)

    res_60, notified_60, kept_qty_60 = await run_branch(10.0, 6.0)
    assert res_60 and res_60.get("code") == 0
    assert kept_qty_60 == 10.0  # geplanter Qty-Wert bleibt voll
    assert not notified_60

    res_40, notified_40, kept_qty_40 = await run_branch(10.0, 4.0)
    assert res_40 is None
    assert kept_qty_40 == 10.0
    assert notified_40 == ["reject"]


@pytest.mark.anyio
async def test_t03_sl_verification_none_does_not_set_sl_exchange_missing_and_countercases():
    src = _source("services/bitunix_trade.py")
    tree = ast.parse(src)
    branch = next(n for n in ast.walk(tree) if isinstance(n, ast.If)
                  and ast.unparse(n.test) == "position_id"
                  and "sl_ok = await self._ensure_live_sl" in (ast.get_source_segment(src, n) or ""))
    wrapper = ast.parse("async def probe():\n    sl_missing = False\n")
    wrapper.body[0].body += [branch] + ast.parse("return sl_missing").body
    ast.fix_missing_locations(wrapper)

    async def run_branch(sl_ok):
        async def ensure(*_a, **_k):
            return sl_ok
        async def close(*_a, **_k):
            return {"code": 1}  # emergency close not confirmed in countercase
        async def no_sleep(*_a, **_k):
            return None
        class Rows:
            async def to_list(self, _n):
                return []
        ns = {"position_id": "pid-1", "symbol": "BTCUSDT", "side": "LONG",
              "qty": 10.0, "sl": 90.0, "signal": {}, "cfg": {},
              "asyncio": SimpleNamespace(sleep=no_sleep),
              "logger": SimpleNamespace(warning=lambda *_a, **_k: None,
                                        error=lambda *_a, **_k: None),
              "self": SimpleNamespace(_ensure_live_sl=ensure, _notify_reject=no_sleep,
                                      client=SimpleNamespace(flash_close=close),
                                      db=SimpleNamespace(auto_trades=SimpleNamespace(find=lambda *_a: Rows())))}
        exec(compile(wrapper, str(SOURCE_DIR / "services/bitunix_trade.py"), "exec"), ns)
        return await ns["probe"]()

    assert await run_branch(None) is False
    assert await run_branch(True) is False
    assert await run_branch(False) is True


@pytest.mark.anyio
async def test_t04_setup_live_gate_allows_missing_setup_exception_path_and_default_bypass(monkeypatch):
    live_gate_bypass_ok = _extract_function(
        "services/ai_engine.py",
        "live_gate_bypass_ok",
        {"Dict": dict},
    )
    assert live_gate_bypass_ok(70, 65, 0, {}) is True

    fake_logger = SimpleNamespace(info=lambda *_a, **_k: None, warning=lambda *_a, **_k: None)
    setup_live_gate = _extract_method(
        "services/ai_engine.py",
        "AIEngine",
        "_setup_live_gate",
        {
            "Dict": dict,
            "Optional": __import__("typing").Optional,
            "datetime": datetime,
            "timezone": timezone,
            "setup_asset_class": SimpleNamespace(asset_class_of=lambda _s: "crypto"),
            "setup_capital": SimpleNamespace(allocation=lambda *_a, **_k: {"suspended": False},
                                             gate_p_win=lambda _d: 0.5),
            "ai_playbook": None,
            "live_gate_bypass_ok": live_gate_bypass_ok,
            "logger": fake_logger,
        },
    )

    # Fake imports innerhalb der Methode
    core_pkg = types.ModuleType("core")
    core_state = types.ModuleType("core.state")
    core_state.autotrader = SimpleNamespace(effective_mode=lambda *_a, **_k: "live")
    core_state.telegram = object()
    core_pkg.state = core_state
    monkeypatch.setitem(sys.modules, "core", core_pkg)
    monkeypatch.setitem(sys.modules, "core.state", core_state)

    services_pkg = sys.modules.get("services") or types.ModuleType("services")
    notifications_mod = types.ModuleType("notifications")

    async def _noop_notify(*_a, **_k):
        return None

    notifications_mod.telegram_notify = _noop_notify
    services_pkg.notifications = notifications_mod
    monkeypatch.setitem(sys.modules, "services", services_pkg)
    monkeypatch.setitem(sys.modules, "services.notifications", notifications_mod)

    class _DB:
        class _Auto:
            async def count_documents(self, _q):
                return 0

        auto_trades = _Auto()

    # Fall 1: missing setup -> durchgelassen
    engine = SimpleNamespace(config={"setup_live_gate": True, "min_confidence": 65}, db=_DB())
    dec_no_setup = {"symbol": "BTCUSDT", "confidence": 70}
    assert await setup_live_gate(engine, dec_no_setup) is None

    # Fall 2: Ausnahme in cached_setup_stats -> durchgelassen
    bad_playbook = SimpleNamespace(
        live_block_reason=lambda *_a, **_k: None,
        cached_setup_stats=(lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("db fail"))),
        live_ready_for=lambda *_a, **_k: (False, "not-ready"),
        class_stats=lambda *_a, **_k: {},
        asset_stats=lambda *_a, **_k: {},
    )
    setup_live_gate.__globals__["ai_playbook"] = bad_playbook
    dec_exc = {"setup": "ema_pullback", "symbol": "BTCUSDT", "confidence": 70, "action": "BUY"}
    assert await setup_live_gate(engine, dec_exc) is None

    # Fall 3: default bypass (70/65/opened=0) -> live_gate_bypass gesetzt
    good_playbook = SimpleNamespace(
        live_block_reason=lambda *_a, **_k: None,
        cached_setup_stats=(lambda *_a, **_k: {}),
        live_ready_for=lambda *_a, **_k: (False, "zu wenig live trades"),
        class_stats=lambda *_a, **_k: {},
        asset_stats=lambda *_a, **_k: {},
    )

    async def _cached(_db):
        return {}

    good_playbook.cached_setup_stats = _cached
    setup_live_gate.__globals__["ai_playbook"] = good_playbook
    dec = {"setup": "ema_pullback", "symbol": "BTCUSDT", "confidence": 70, "action": "BUY"}
    assert await setup_live_gate(engine, dec) is None
    assert dec.get("live_gate_bypass") is True


def test_t05_policy_fingerprint_ignores_min_conf_fee_guard_and_regime_config_but_tracks_real_sizing_change():
    from services import policy_fingerprint as pf

    base = {
        "sizing_mode": "risk",
        "risk_per_trade_pct": 2.0,
        "lev_mode": "coin",
        "min_confidence": 65,
        "fee_guard_enabled": True,
        "regime_mode": 9,
    }
    h_base = pf.sizing_hash(base)
    h_min_conf = pf.sizing_hash({**base, "min_confidence": 70})
    h_fee_guard = pf.sizing_hash({**base, "fee_guard_enabled": False})
    h_regime = pf.sizing_hash({**base, "regime_mode": 5})
    assert h_min_conf == h_base
    assert h_fee_guard == h_base
    assert h_regime == h_base

    fp_base = pf.build(prompt_hash="p", lessons_h="l", playbook_version="1", model="m", gate_version=1,
                       sizing_h=h_base)
    fp_changed_non_sizing = pf.build(prompt_hash="p", lessons_h="l", playbook_version="1", model="m",
                                     gate_version=1, sizing_h=h_min_conf)
    assert fp_changed_non_sizing["combined"] == fp_base["combined"]

    h_real_change = pf.sizing_hash({**base, "risk_per_trade_pct": 3.0})
    assert h_real_change != h_base


class _DummyStrategy:
    STRATEGY_NAME = "dummy"

    def check_signal(self, _window, _symbol, _settings):
        return None


def _candles_for_r08():
    candles = []
    for i in range(120):
        candles.append({
            "timestamp": i * 60_000,
            "open": 100.0,
            "high": 100.2,
            "low": 99.8,
            "close": 100.0,
            "volume": 1.0,
        })
    return candles


@pytest.mark.parametrize("side,sl,tp1,tpf,bar_high,bar_low", [
    ("LONG", 90.0, 101.0, 103.0, 104.0, 99.0),
    ("SHORT", 110.0, 99.0, 97.0, 101.0, 96.0),
])
def test_r08_same_bar_tp1_tpf_characterizes_optimistic_pnl(side, sl, tp1, tpf, bar_high, bar_low, monkeypatch):
    from services import backtester
    from services.backtester import simulate_pair

    candles = _candles_for_r08()
    for i in range(86, len(candles)):
        candles[i]["high"] = bar_high
        candles[i]["low"] = bar_low

    def provider(i):
        if i == 85:
            return {"type": side, "entry_price": 100.0}
        return None

    monkeypatch.setattr(
        backtester,
        "compute_levels",
        lambda *_a, **_k: (sl, tp1, tpf, 10.0, 2.0),
    )

    res = simulate_pair(
        _DummyStrategy(),
        candles,
        "BTCUSDT",
        {},
        {
            "max_capital": 100,
            "leverage": 2,
            "sl_mode": "fixed",
            "sl_fixed_percent": 20,
            "tp_mode": "percent",
            "tp1_percent": 1,
            "tp_full_percent": 3,
            "tp1_close_percent": 50,
            "fee_percent": 0.0,
        },
        None,
        True,
        None,
        provider,
    )
    assert res["trades"] == 1
    # Charakterisierung: aktueller Code liefert 6 statt sequenzieller 4.
    assert res["pnl"] == pytest.approx(6.0)


@pytest.mark.xfail(strict=True, reason="Soll-Abnahme: sequenzielles TP1+TPF in gleicher Kerze")
@pytest.mark.parametrize("side,sl,tp1,tpf,bar_high,bar_low", [
    ("LONG", 90.0, 101.0, 103.0, 104.0, 99.0),
    ("SHORT", 110.0, 99.0, 97.0, 101.0, 96.0),
])
def test_r08_acceptance_same_bar_tp_should_be_sequential_not_optimistic(side, sl, tp1, tpf, bar_high, bar_low, monkeypatch):
    from services import backtester
    from services.backtester import simulate_pair

    candles = _candles_for_r08()
    for i in range(86, len(candles)):
        candles[i]["high"] = bar_high
        candles[i]["low"] = bar_low

    def provider(i):
        if i == 85:
            return {"type": side, "entry_price": 100.0}
        return None

    monkeypatch.setattr(backtester, "compute_levels", lambda *_a, **_k: (sl, tp1, tpf, 10.0, 2.0))
    res = simulate_pair(_DummyStrategy(), candles, "BTCUSDT", {}, {
        "max_capital": 100,
        "leverage": 2,
        "sl_mode": "fixed",
        "sl_fixed_percent": 20,
        "tp_mode": "percent",
        "tp1_percent": 1,
        "tp_full_percent": 3,
        "tp1_close_percent": 50,
        "fee_percent": 0.0,
    }, None, True, None, provider)
    assert res["pnl"] == pytest.approx(4.0)


def test_r08_open_trade_with_floating_loss_and_entry_fee_still_reported_as_zero_metrics():
    from services.backtester import simulate_pair

    candles = _candles_for_r08()
    for i in range(86, len(candles)):
        candles[i]["open"] = 99.0
        candles[i]["close"] = 99.0  # unter Entry, SL bleibt unberührt
        candles[i]["high"] = 99.5
        candles[i]["low"] = 98.8

    def provider(i):
        if i == 85:
            return {"type": "LONG", "entry_price": 100.0}
        return None

    res = simulate_pair(
        _DummyStrategy(),
        candles,
        "BTCUSDT",
        {},
        {
            "max_capital": 100,
            "leverage": 2,
            "sl_mode": "fixed",
            "sl_fixed_percent": 20,
            "tp_mode": "percent",
            "tp1_percent": 15,
            "tp_full_percent": 25,
            "tp1_close_percent": 50,
            "fee_percent": 0.1,
        },
        None,
        True,
        None,
        provider,
    )
    assert res["trades"] == 0
    assert res["pnl"] == 0
    assert res["fees"] == 0


def _synthetic_regime_series():
    ts0 = 1_700_000_000_000
    day_ms = 86_400_000
    closes = []
    v = 100.0
    for i in range(320):  # Aufwärtstrend
        v *= 1.0012
        closes.append(v)
    for i in range(200):  # Umkehr
        v *= 0.9988
        closes.append(v)
    out = []
    for i, c in enumerate(closes):
        out.append({
            "timestamp": ts0 + i * day_ms,
            "open": c,
            "high": c * 1.002,
            "low": c * 0.998,
            "close": c,
            "volume": 1000.0,
        })
    return out


def test_r05_prefix_invariance_causal_vs_retrospective_labels_with_fixed_model_and_config():
    from services import regime as rg
    from services import regime_engine as eng

    full = _synthetic_regime_series()
    prefix = full[:335]
    model = rg.detect_regimes(
        {"BTCUSDT": prefix},
        "24h",
        engine="v2",
        engine_config={
            "detector": "reactive",
            "regime_mode": 9,
            "confidence_min": 0.7,
            "min_hold_days": 2.0,
            "auto_adapt": False,
            "adapt_profile": "off",
        },
    )
    assert model and model.get("engine") == "v2"

    causal_prefix = eng.classify_series(model, prefix, conf_min=0.7, min_hold_days=2.0)
    causal_full_prefix = eng.classify_series(model, full, conf_min=0.7, min_hold_days=2.0)[: len(prefix)]
    assert causal_prefix == causal_full_prefix

    final_prefix = eng.final_labels(model, prefix)
    final_full_prefix = eng.final_labels(model, full)[: len(prefix)]
    # retrospektive Final-Labels dürfen sich durch Future-Append ändern
    if final_prefix == final_full_prefix:
        pytest.xfail("R05-Forschung auf diesem synthetischen Datensatz nicht nachweisbar")
    assert final_prefix != final_full_prefix


@pytest.mark.anyio
async def test_r14_candle_cache_tail_start_skips_last_open_minute_update(monkeypatch):
    from services import candle_cache
    from services.candles import CandleArray
    from core import instruments

    candle_cache.clear()
    base = CandleArray.from_dicts([
        {"timestamp": 10_000, "open": 100, "high": 101, "low": 99, "close": 100, "volume": 1},
        {"timestamp": 70_000, "open": 100, "high": 101, "low": 99, "close": 100, "volume": 1},
    ])
    candle_cache._MEM["BTCUSDT"] = {"candles": base, "last_refresh": 0.0, "used_at": 0.0}

    monkeypatch.setattr(instruments, "history_days_cap", lambda _s, d: d)

    # Deterministischer Zeitrahmen + erzwungene Tail-Aktualisierung.
    t_now = 200.0
    monkeypatch.setattr(candle_cache.time, "time", lambda: t_now)
    monkeypatch.setattr(candle_cache, "TAIL_TTL_SEC", 0)

    tail_calls = {}

    async def _fake_fetch_range_parallel(_session, _symbol, _start, _end, job=None):
        return CandleArray.empty()

    async def _fake_fetch_range(_session, _symbol, start_ms, end_ms, job=None, pace=None):
        tail_calls["start_ms"] = start_ms
        tail_calls["end_ms"] = end_ms
        # Quelle hätte letzte Minute aktualisiert, wird hier aber gar nicht mehr angefragt.
        return CandleArray.from_dicts([
            {"timestamp": 130_000, "open": 101, "high": 102, "low": 100, "close": 101, "volume": 1},
        ])

    monkeypatch.setattr(candle_cache, "_fetch_range_parallel", _fake_fetch_range_parallel)
    monkeypatch.setattr(candle_cache, "_fetch_range", _fake_fetch_range)

    out = await candle_cache.get_candles(None, "BTCUSDT", 1)
    assert len(out) >= 2
    assert tail_calls["start_ms"] == 70_000 + 60_000
    # Stale/aktualisierte letzte offene Minute (ts=70_000) bleibt unverändert.
    assert int(candle_cache._MEM["BTCUSDT"]["candles"].cl[1]) == 100
