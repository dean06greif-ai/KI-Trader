"""Regressionstests Edge-Register (services/setup_backtest/edges.py) und die
Edge-Entscheidung im Runner (_evaluate_setup / _optimize_setup).

Kernforderungen (Audit 09/2026):
  * Ein bestätigter Edge wird durch einen späteren Fehllauf NICHT mehr
    überschrieben/gelöscht (stale-Zähler, erst nach STALE_MAX kein Edge).
  * Ein schmalerer/knapp bestandener Satz ersetzt keinen breit bestätigten
    (Overfitting-Bremse, Mindest-Trades-Verhältnis, Robustheits-Marge).
  * Ein nachweislich robusterer Satz ersetzt den aktiven – der alte bleibt
    im Register (Rollback), ebenso rückwirkende Wiederherstellung aus dem Verlauf.

Reine Funktionen laufen ohne DB; die Fluss-Tests nutzen die lokale Mongo
(MONGO_URL aus backend/.env) mit einer Wegwerf-Datenbank.
"""
import asyncio
import os
import sys
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.setup_backtest import detectors, edges, runner, weights  # noqa: E402

pytestmark = pytest.mark.unit


def _stats(n, wr, pnl, windows=None):
    out = {"trades": n, "wins": round(n * wr / 100), "pnl": pnl, "winrate": wr, "margin": n * 100.0}
    if windows is not None:
        out["windows"] = windows
        out["windows_pos"] = sum(1 for w in windows if w["pnl"] > 0)
    return out


def _res(name="V1", vi=0, is_=(120, 58, 30.0), oos=(60, 57, 12.0), params=None, passed=True,
         pf=1.6, payoff=1.3, windows=None):
    """Kanonisches run_variant()-Ergebnis (nur die Felder, die Runner/Register lesen)."""
    is_st, oos_st = _stats(*is_), _stats(*oos, windows=windows)
    eff = dict(params or detectors.VARIANTS["breakout"][vi])
    eff.pop("name", None)
    trades = [{"symbol": "BTCUSDT", "setup": "breakout", "asset_class": "crypto", "realized_pnl": 0.3,
               "side": "LONG", "oos": True} for _ in range(min(oos[0], 5))]
    return {"setup": "breakout", "variant": vi, "variant_name": name, "variants_total": 3, "signals": 300,
            "is": is_st, "oos": oos_st, "passed": passed, "min_trades": {"is": 30, "oos": 15},
            "oos_trades": trades, "params": ({**eff, "name": name} if params else None), "effective_params": eff,
            "diag": {"is": {"n": is_[0], "pf": pf, "payoff": payoff, "max_dd": 4.0},
                     "oos": {"n": oos[0], "pf": pf, "payoff": payoff, "max_dd": 3.0}}}


# ------------------------------------------------------------------ rein
def test_fingerprint_ignores_name_and_is_stable():
    a = edges.fingerprint({"lookback": 20, "rr": 2.0, "name": "A"})
    b = edges.fingerprint({"rr": 2.0, "lookback": 20, "name": "B"})
    assert a == b and len(a) == 16
    assert a != edges.fingerprint({"lookback": 21, "rr": 2.0})


def test_overfit_flags_detect_is_oos_drop_pf_and_winrate_vs_crv():
    clean = edges.overfit_flags(_res())
    assert clean["hard"] == []
    # OOS/Trade bricht auf < 35 % des IS-Werts ein
    drop = edges.overfit_flags(_res(is_=(100, 60, 50.0), oos=(40, 52, 2.0)))
    assert any("OOS-Abfall" in f for f in drop["hard"])
    # Profit-Faktor zu dünn
    thin = edges.overfit_flags(_res(pf=1.05))
    assert any("Profit-Faktor" in f for f in thin["hard"])
    # Winrate deckt CRV kaum: Payoff 1.0 -> Break-even 50 %, WR 51 %
    wr = edges.overfit_flags(_res(oos=(60, 51, 1.0), payoff=1.0))
    assert any("Winrate" in f for f in wr["hard"])
    # Walk-Forward 2/3 nur Hinweis
    soft = edges.overfit_flags(_res(windows=[{"trades": 20, "pnl": 5}, {"trades": 20, "pnl": -1}, {"trades": 20, "pnl": 8}]))
    assert soft["hard"] == [] and any("Walk-Forward" in f for f in soft["soft"])


def test_robust_score_rewards_trades_and_consistency_not_just_pnl():
    broad = edges.robust_score(_res(is_=(200, 58, 40.0), oos=(90, 58, 18.0)))     # 0.2/Trade, viele Trades
    narrow = edges.robust_score(_res(is_=(60, 80, 25.0), oos=(16, 81, 4.0)))     # 0.25/Trade, kaum Trades
    assert broad > 0 and narrow > 0
    assert broad > narrow, "wenige Trades mit hohem PnL/Trade dürfen nicht dominieren"
    assert edges.robust_score(_res(oos=(60, 40, -3.0), passed=False)) <= 0


def test_better_blocks_overfit_fewer_trades_and_small_margin():
    active = edges.summarize(_res("Aktiv", oos=(60, 57, 12.0)), "variant", 0)
    # 1) weniger als 70 % der Trades -> nein
    narrow = edges.summarize(_res("Schmal", vi=1, oos=(20, 85, 13.0)), "variant", 1)
    ok, why = edges.better(narrow, active)
    assert not ok and "OOS-Trades" in why
    # 2) marginal besser (< 10 %) -> nein
    marginal = edges.summarize(_res("Knapp", vi=1, oos=(60, 57, 12.6)), "variant", 1)
    ok, why = edges.better(marginal, active)
    assert not ok and "nicht robuster" in why
    # 3) klar robuster -> ja
    strong = edges.summarize(_res("Stark", vi=2, oos=(75, 60, 20.0), is_=(150, 60, 45.0)), "variant", 2)
    ok, why = edges.better(strong, active)
    assert ok and "robuster" in why
    # 4) robuster, aber Overfitting-Signal -> nein
    fitted = edges.summarize(_res("Fit", vi=2, is_=(150, 62, 90.0), oos=(75, 58, 15.0)), "variant", 2)
    assert fitted["flags"]["hard"]
    ok, why = edges.better(fitted, active)
    assert not ok and "Overfitting" in why
    assert not edges.better(edges.summarize(_res(passed=False), "variant", 0), active)[0]


def test_decide_keeps_active_on_failed_run_until_stale_max():
    active = edges.summarize(_res("Aktiv"), "variant", 0)
    failed = edges.summarize(_res("Aktiv", oos=(30, 45, -2.0), passed=False), "check", 0)
    d1 = edges.decide(active, failed, None, stale=0)
    assert d1["action"] == "stale_keep" and d1["stale"] == 1
    d2 = edges.decide(active, failed, None, stale=1)
    assert d2["action"] == "stale_keep" and d2["stale"] == 2
    d3 = edges.decide(active, failed, None, stale=2)
    assert d3["action"] == "stale" and d3["stale"] == edges.STALE_MAX
    # Bestätigung setzt den Zähler zurück
    ok = edges.summarize(_res("Aktiv"), "check", 0)
    assert edges.decide(active, ok, None, stale=2) == {"action": "confirm", "stale": 0,
                                                       "why": "aktiver Edge erneut bestätigt"}


def test_decide_candidate_paths():
    active = edges.summarize(_res("Aktiv", oos=(60, 57, 12.0)), "variant", 0)
    ok = edges.summarize(_res("Aktiv", oos=(60, 57, 12.0)), "check", 0)
    weak = edges.summarize(_res("Schmal", vi=1, oos=(20, 85, 13.0)), "variant", 1)
    strong = edges.summarize(_res("Stark", vi=2, oos=(80, 60, 22.0), is_=(150, 60, 45.0)), "variant", 2)
    # ohne aktiven Edge: erster Bestandener wird übernommen
    assert edges.decide(None, None, weak)["action"] == "adopt"
    assert edges.decide(None, None, None)["action"] == "none"
    # aktiv bestätigt + schwacher Kandidat -> bleibt
    assert edges.decide(active, ok, weak)["action"] == "confirm"
    # aktiv bestätigt + klar besserer Kandidat -> ersetzt
    assert edges.decide(active, ok, strong)["action"] == "replace"
    # aktiv durchgefallen, schmaler Kandidat -> aktiver bleibt (stale 1), kein Wechsel
    failed = edges.summarize(_res("Aktiv", oos=(30, 45, -2.0), passed=False), "check", 0)
    d = edges.decide(active, failed, weak, stale=0)
    assert d["action"] == "stale_keep" and d["stale"] == 1
    # ... aber nach STALE_MAX Fehlläufen übernimmt ein sauberer Kandidat
    d = edges.decide(active, failed, weak, stale=edges.STALE_MAX - 1)
    assert d["action"] == "replace" and "in Folge" in d["why"]
    # gleicher Fingerprint zählt nicht als Kandidat
    same = edges.summarize(_res("Aktiv", oos=(60, 57, 12.0)), "variant", 0)
    assert edges.decide(active, ok, same)["action"] == "confirm"


def test_entry_from_edge_and_active_params_of():
    edge = {"id": "e1", "params": {"lookback": 30, "rr": 2.5}, "name": "KI-Rev.17", "variant": 1,
            "is": {"trades": 100}, "oos": {"trades": 50}, "robust": 0.4, "flags": {"hard": [], "soft": []}}
    entry = edges.entry_from_edge({"status": "exhausted", "ai_proposal": {"x": 1}, "history": [1]}, edge)
    assert entry["status"] == "tuned" and entry["tuned"] == {"lookback": 30, "rr": 2.5, "name": "KI-Rev.17"}
    assert entry["edge_id"] == "e1" and "ai_proposal" not in entry and entry["history"] == [1]
    assert runner.active_params_of("breakout", entry, {"params": {"a": 1}, "name": "X"}) == {"a": 1, "name": "X"}
    assert runner.active_params_of("breakout", entry, None) == entry["tuned"]
    assert runner.active_params_of("breakout", {"status": "failed"}, None) is None
    base = runner.active_params_of("breakout", {"status": "passed", "variant": 1}, None)
    assert base and base["name"] == "Edge"


# ------------------------------------------------------------------ Fluss (lokale Mongo)
def _mongo_url():
    from dotenv import dotenv_values
    env = dotenv_values(Path(__file__).resolve().parent.parent / ".env")
    url = os.environ.get("MONGO_URL") or env.get("MONGO_URL")
    if not url or "mongodb+srv" in url:
        pytest.skip("lokale Mongo nötig")
    return url


def _with_db(fn):
    """Test-Coroutine mit Wegwerf-Datenbank in EINEM Event-Loop ausführen."""
    async def main():
        from motor.motor_asyncio import AsyncIOMotorClient
        client = AsyncIOMotorClient(_mongo_url(), serverSelectionTimeoutMS=2000)
        db = client[f"kitrader_test_{uuid.uuid4().hex[:8]}"]
        try:
            await fn(db)
        finally:
            await client.drop_database(db.name)
    asyncio.run(main())


async def _evaluate(db, entry, results, mode="loop"):
    """_evaluate_setup mit gefaktem run_variant: `results` mappt Varianten-Namen
    (bzw. 'params:<name>' für Parameter-Sätze) auf kanonische Ergebnisse."""
    calls = []

    def fake_run(setup, feats, vi, cls, split_ts, job_id, params=None):
        key = detectors.VARIANTS[setup][vi]["name"]
        if params:
            fp = edges.fingerprint(params)
            base = next((v["name"] for v in detectors.VARIANTS[setup] if edges.fingerprint(v) == fp), None)
            key = base or f"params:{params.get('name')}"
        calls.append(key)
        r = results.get(key) or _res(key, vi, passed=False, oos=(8, 40, -2.0))   # Feintuning-Kandidaten
        return {**r, "variant": vi, "params": dict(params) if params else None,
                "effective_params": {k: v for k, v in (params or detectors.VARIANTS[setup][vi]).items() if k != "name"}}

    orig = runner.run_variant
    runner.run_variant = fake_run
    try:
        job = {"id": "job", "params": {"days": 90}}
        ai = {"ai_revise": False, "ai_rounds": 0, "target_passed": 0}
        ev = await runner._evaluate_setup(job, db, "crypto", "breakout", entry, {"BTCUSDT": object()},
                                          0, mode, ai, 0)
    finally:
        runner.run_variant = orig
    return ev, calls


def test_flow_edge_survives_failed_run_and_is_replaced_only_by_robuster_set():
    async def body(db):
        names = [v["name"] for v in detectors.VARIANTS["breakout"]]
        good = _res(names[0], 0, oos=(60, 57, 12.0))
        # Lauf 1: Variante 0 besteht -> adopt, Trades gespeichert
        ev, _ = await _evaluate(db, {"status": "pending", "variant": 0},
                                {names[0]: good, names[1]: _res(names[1], 1, passed=False, oos=(20, 40, -3.0)),
                                 names[2]: _res(names[2], 2, passed=False, oos=(20, 40, -3.0))})
        e1 = ev["entry"]
        assert ev["edge"]["action"] == "adopt" and e1["status"] == "passed" and e1["edge_id"]
        active = await edges.get_active(db, "crypto", "breakout")
        assert active and active["name"] == names[0] and len(active["oos_trades"]) == 5
        assert ev["store"] == {"clear": True, "trades": good["oos_trades"]}
        # Lauf 2: aktiver Edge fällt durch (kurzes Datenfenster), andere Varianten auch
        fail = _res(names[0], 0, passed=False, oos=(12, 42, -1.5))
        ev, calls = await _evaluate(db, e1, {names[0]: fail, names[0]: fail,
                                             names[1]: _res(names[1], 1, passed=False, oos=(9, 40, -3.0)),
                                             names[2]: _res(names[2], 2, passed=False, oos=(9, 40, -3.0))})
        e2 = ev["entry"]
        assert calls[0] == names[0] and calls.count(names[0]) == 1, "aktiver Edge wird zuerst (und nur einmal) geprüft"
        assert ev["edge"]["action"] == "stale_keep" and e2["stale"] == 1
        assert e2["status"] == "tuned" and e2["tuned"]["name"] == names[0]
        assert e2["oos"]["trades"] == 60, "Anzeige bleibt beim bestätigten Edge"
        assert e2["last_check"]["oos"]["trades"] == 12
        assert ev["store"]["clear"] is False, "OOS-Trades des Edge bleiben fürs Reife-Gate"
        # Lauf 3: aktiver Edge besteht wieder, schmaler Kandidat besteht auch -> confirm, kein Wechsel
        ok = _res(names[0], 0, oos=(62, 58, 12.5))
        narrow = _res(names[2], 2, oos=(18, 88, 14.0))
        ev, _ = await _evaluate(db, e2, {names[0]: ok, names[1]: _res(names[1], 1, passed=False), names[2]: narrow})
        e3 = ev["entry"]
        assert ev["edge"]["action"] == "confirm" and e3["stale"] == 0 and e3["tuned"]["name"] == names[0]
        assert "OOS-Trades" in ev["edge"]["why"]
        active = await edges.get_active(db, "crypto", "breakout")
        assert active["confirmations"] == 2
        cands = await edges.list_edges(db, "crypto", "breakout")
        assert [c["status"] for c in cands] == ["active", "candidate"]
        # Lauf 4: klar robusterer Kandidat -> replace, alter Edge bleibt (retired) fürs Rollback
        strong = _res(names[1], 1, oos=(85, 61, 24.0), is_=(160, 60, 48.0))
        ev, _ = await _evaluate(db, e3, {names[0]: ok, names[1]: strong, names[2]: narrow})
        e4 = ev["entry"]
        assert ev["edge"]["action"] == "replace" and e4["name"] == names[1] and e4["status"] == "passed"
        all_edges = await edges.list_edges(db, "crypto", "breakout")
        assert {(c["name"], c["status"]) for c in all_edges} == {(names[1], "active"), (names[0], "retired"), (names[2], "candidate")}
        # Rollback auf den alten Edge
        old = next(c for c in all_edges if c["name"] == names[0])
        await db[weights.COLLECTION].insert_many([{**t, "run_at": "x"} for t in strong["oos_trades"]])
        out = await edges.activate(db, "crypto", "breakout", old["id"], reason="Test")
        assert out["entry"]["tuned"]["name"] == names[0] and out["restored_trades"] == 5
        assert await db[weights.COLLECTION].count_documents({"setup": "breakout"}) == 5
        st = await runner.load_state(db)
        assert st["classes"]["crypto"]["breakout"]["edge_action"] == "rollback"
        # Lauf 5: abgelöster Edge (names[1]) wird als Herausforderer mitgeprüft und
        # übernimmt wieder, wenn er auf aktuellen Daten klar robuster ist
        e5_in = st["classes"]["crypto"]["breakout"]
        huge = _res(names[1], 1, oos=(120, 63, 40.0), is_=(200, 62, 60.0))
        ev, calls = await _evaluate(db, e5_in, {names[0]: ok, names[1]: huge, names[2]: _res(names[2], 2, passed=False)})
        assert calls.count(names[1]) >= 1
        assert ev["edge"]["action"] == "replace" and ev["entry"]["name"] == names[1]
        assert any(h.get("challenger") for h in ev["entry"]["history"])
        statuses = {c["name"]: c["status"] for c in await edges.list_edges(db, "crypto", "breakout")}
        assert statuses[names[1]] == "active" and statuses[names[0]] == "retired"
    _with_db(body)


def test_flow_stale_after_max_then_candidate_takes_over():
    async def body(db):
        names = [v["name"] for v in detectors.VARIANTS["breakout"]]
        fail = _res(names[0], 0, passed=False, oos=(12, 42, -1.5))
        nope = {names[1]: _res(names[1], 1, passed=False), names[2]: _res(names[2], 2, passed=False)}
        ev, _ = await _evaluate(db, {"status": "pending", "variant": 0}, {names[0]: _res(names[0], 0), **nope})
        entry = ev["entry"]
        for k in range(1, edges.STALE_MAX):
            ev, _ = await _evaluate(db, entry, {names[0]: fail, **nope})
            entry = ev["entry"]
            assert entry["status"] == "tuned" and entry["stale"] == k
        ev, _ = await _evaluate(db, entry, {names[0]: fail, **nope})
        entry = ev["entry"]
        assert ev["edge"]["action"] == "stale" and entry["status"] == "stale" and "tuned" not in entry
        assert ev["store"]["clear"] is True
        assert runner.effective_params_of("breakout", entry) is None, "stale = kein Edge fürs Trading"
        active = await edges.get_active(db, "crypto", "breakout")
        assert active and active["stale"] == edges.STALE_MAX, "Edge bleibt reaktivierbar"
        # Edge besteht später wieder -> zurück auf tuned, stale 0
        ev, _ = await _evaluate(db, entry, {names[0]: _res(names[0], 0), **nope})
        assert ev["edge"]["action"] == "confirm" and ev["entry"]["status"] == "tuned" and ev["entry"]["stale"] == 0
    _with_db(body)


def test_recover_from_history_reactivates_best_lost_edge():
    async def body(db):
        p17 = {"lookback": 30, "rr": 2.5, "name": "KI-Rev.17"}
        p20 = {"lookback": 12, "rr": 1.2, "name": "KI-Rev.20"}
        hist = [{"variant": 1, "name": "KI-Rev.17", "passed": True, "params": {k: v for k, v in p17.items() if k != "name"},
                 "is": _stats(400, 61, 50.0), "oos": _stats(222, 60, 23.0), "at": "2026-09-15T10:00:00+00:00",
                 "diag": {"is": {"pf": 1.7, "payoff": 1.2}, "oos": {"pf": 1.6, "payoff": 1.2}}},
                {"variant": 1, "name": "KI-Rev.20", "passed": False, "params": {k: v for k, v in p20.items() if k != "name"},
                 "is": _stats(90, 48, 1.0), "oos": _stats(38, 45, -0.24), "at": "2026-09-16T20:00:00+00:00"}]
        state = {"classes": {"crypto": {"session_open": {"status": "exhausted", "variant": 2, "history": hist,
                                                         "ai_proposal": p20, "days": 90},
                                        "breakout": {"status": "failed", "history": []}}}}
        await runner.save_state(db, state)
        out = await edges.recover_from_history(db)
        assert out == {"imported": 1, "activated": ["session_open@crypto"]}
        st = await runner.load_state(db)
        e = st["classes"]["crypto"]["session_open"]
        assert e["status"] == "tuned" and e["tuned"] == p17 and e["edge_action"] == "recovered"
        assert "ai_proposal" not in e
        assert runner.effective_params_of("session_open", e)["rr"] == 2.5
        # idempotent
        assert await edges.recover_from_history(db) == {"imported": 0, "activated": []}
        assert len(await edges.list_edges(db)) == 1
    _with_db(body)
