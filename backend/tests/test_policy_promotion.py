"""Audit 3.3: Promotion-Regel + Rollback (rein) und Labor-Verdrahtung. Ohne Netzwerk."""
import asyncio

import pytest

from services import policy_lab as pl
from services import policy_promotion as pp
from tests.test_policy_lab import FakeDB, FakeEngine


@pytest.fixture()
def loop():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    yield loop
    loop.close()


def _lab(db):
    lab = pl.PolicyLab()
    lab.setup(FakeEngine(db, []))
    return lab


# ---------------- reine Funktionen ----------------

def test_trial_r():
    t = {"status": "closed", "entry": 100.0, "sl": 99.0, "net_pnl_pct": 2.0}
    assert pp.trial_r(t) == 2.0                      # 2% Gewinn / 1% SL-Distanz = 2R
    t["net_pnl_pct"] = -1.1
    assert pp.trial_r(t) == -1.1
    assert pp.trial_r({"status": "open", "entry": 100, "sl": 99}) is None
    assert pp.trial_r({"status": "closed", "entry": 100.0, "sl": 100.0,
                       "net_pnl_pct": 1.0}) is None  # keine SL-Distanz
    assert pp.trial_r({}) is None


def test_max_drawdown():
    assert pp.max_drawdown([1, -2, 1]) == 2.0
    assert pp.max_drawdown([1, 1, 1]) == 0.0
    assert pp.max_drawdown([-1, -1]) == 2.0
    assert pp.max_drawdown([]) == 0.0


def test_bootstrap_diff_lower_deterministic():
    cand = [1.0] * 50
    champ = [0.2] * 50
    lo1 = pp.bootstrap_diff_lower(cand, champ)
    lo2 = pp.bootstrap_diff_lower(cand, champ)
    assert lo1 == lo2 == 0.8                       # konstante Stichproben -> exakt
    assert pp.bootstrap_diff_lower([], champ) == float("-inf")
    mixed = [2.0, -1.0] * 25
    lo = pp.bootstrap_diff_lower(mixed, champ)
    assert lo < 0.3                                 # Streuung drückt die Untergrenze


def test_promotion_check_all_criteria():
    cfg = {"min_trials": 10, "champ_min_trades": 5, "bootstrap_n": 200}
    good = [1.0, 0.8, 1.2, 0.9] * 5                # 20 Trials, klar positiv
    champ = [0.1, -0.2, 0.0, 0.1] * 3
    rep = pp.promotion_check(good, champ, cfg)
    assert rep["promote"] is True
    assert rep["metrics"]["n_cand"] == 20 and rep["metrics"]["bootstrap_lower"] > 0
    # zu wenige Trials
    rep = pp.promotion_check(good[:5], champ, cfg)
    assert rep["promote"] is False
    assert not [c for c in rep["checks"] if c["name"] == "min_trials"][0]["ok"]
    # Kandidat schlechter -> mean_diff-Check fällt
    rep = pp.promotion_check([-0.5] * 20, champ, cfg)
    assert rep["promote"] is False
    # Drawdown schlechter trotz besserem Mittel
    dd_cand = [3.0, -2.0, -2.0, 3.0, 3.0] * 4      # DD 4R
    flat_champ = [0.05] * 10                        # DD 0R
    rep = pp.promotion_check(dd_cand, flat_champ, cfg)
    assert not [c for c in rep["checks"] if c["name"] == "drawdown_not_worse"][0]["ok"]
    assert rep["promote"] is False
    # leerer Champion -> champ_basis + bootstrap fallen
    rep = pp.promotion_check(good, [], cfg)
    assert rep["promote"] is False and rep["metrics"]["bootstrap_lower"] is None


def test_rollback_check():
    cfg = {"rollback_window_trades": 10, "rollback_min_trades": 4}
    assert pp.rollback_check([-1, -1], 0.5, cfg)["rollback"] is False   # zu früh
    res = pp.rollback_check([-1.0, -0.5, -0.8, -0.2], 0.3, cfg)
    assert res["rollback"] is True and res["post_mean_r"] < 0
    # negativ, aber besser als vorher -> kein Rollback
    assert pp.rollback_check([-0.1] * 5, -0.5, cfg)["rollback"] is False
    # positiv -> kein Rollback
    assert pp.rollback_check([0.5] * 5, 0.1, cfg)["rollback"] is False


# ---------------- Labor-Verdrahtung ----------------

def test_promotion_flow_ready_promote_rollback(loop, monkeypatch):
    db = FakeDB()
    lab = _lab(db)
    loop.run_until_complete(lab.create_candidate(
        {"name": "T", "system_suffix": "", "config_overrides": {"min_confidence": 75}}))
    lab.promotion_cfg.update({"min_trials": 5, "champ_min_trades": 3,
                              "rollback_window_trades": 10, "rollback_min_trades": 3})
    cid = lab.candidate["id"]
    for i in range(6):
        db[pl.TRIALS_COLL].rows.append({
            "id": f"t{i}", "candidate_id": cid, "status": "closed",
            "entry": 100.0, "sl": 99.0, "net_pnl_pct": 1.0})

    async def fake_champ(since):
        return [0.1, -0.1, 0.05, 0.0]
    monkeypatch.setattr(lab, "_champion_rs", fake_champ)

    loop.run_until_complete(lab._check_promotion())
    assert lab.candidate["status"] == "promotion_ready"
    assert db.ai_chat.rows and "Promotion" in db.ai_chat.rows[0]["text"]

    # Promotion: Override landet in der Engine-Config, Snapshot fürs Rollback
    updates_seen = {}

    async def fake_update_config(u):
        updates_seen.update(u)
        lab.engine.config.update(u)
        return dict(lab.engine.config)
    lab.engine.update_config = fake_update_config
    res = loop.run_until_complete(lab.promote())
    assert res["status"] == "ok" and lab.candidate["status"] == "promoted"
    assert updates_seen == {"min_confidence": 75}
    assert lab.candidate["promotion_snapshot"]["prev_config"] == {"min_confidence": 65}

    # Verschlechterung im Fenster -> automatischer Rollback stellt Config wieder her
    async def bad_champ(since):
        return [-1.0, -0.8, -0.9, -0.7]
    monkeypatch.setattr(lab, "_champion_rs", bad_champ)
    loop.run_until_complete(lab._check_rollback())
    assert lab.candidate is None
    assert updates_seen == {"min_confidence": 65}
    row = db[pl.CAND_COLL].rows[0]
    assert row["status"] == "rolled_back" and "AUTO" in row["rollback_reason"]


def test_promotion_window_survived_finalizes(loop, monkeypatch):
    db = FakeDB()
    lab = _lab(db)
    loop.run_until_complete(lab.create_candidate({"name": "T", "system_suffix": "x"}))
    lab.promotion_cfg.update({"rollback_window_trades": 3, "rollback_min_trades": 2})
    cid = lab.candidate["id"]
    lab.candidate.update({"status": "promoted", "promoted_at": pl._now_iso(),
                          "rollback_baseline_r": 0.1,
                          "promotion_snapshot": {"prev_config": {}}})
    db[pl.CAND_COLL].rows[0].update(lab.candidate)

    async def good_champ(since):
        return [0.5, 0.4, 0.6]
    monkeypatch.setattr(lab, "_champion_rs", good_champ)
    loop.run_until_complete(lab._check_rollback())
    assert lab.candidate is None
    assert db[pl.CAND_COLL].rows[0]["status"] == "promoted_final"
    assert lab.begin_cycle() is False  # kein Shadow mehr nach Abschluss


def test_promote_requires_ready_or_force(loop):
    db = FakeDB()
    lab = _lab(db)
    loop.run_until_complete(lab.create_candidate({"name": "T", "system_suffix": "x"}))
    res = loop.run_until_complete(lab.promote())
    assert res["status"] == "rejected" and "force" in res["reason"]
    assert loop.run_until_complete(lab.rollback())["status"] == "rejected"
