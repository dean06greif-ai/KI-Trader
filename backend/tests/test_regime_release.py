"""PLAN_REGIME_BRUECKE Baustein 1 – Nachweis-Gate, Stufen, Proposal-Pfad (rein + FakeDB)."""
import asyncio
from datetime import datetime, timezone, timedelta

from services import regime_release as rr


def _model(v2=True, mode=5):
    return {"engine": "v2" if v2 else "kmeans", "config": {"regime_mode": mode, "detector": "reactive"},
            "regimes": [{"id": 0, "label": "Abwärts"}, {"id": 3, "label": "Seitwärts"}, {"id": 6, "label": "Aufwärts"}],
            "norm_mean": [0], "norm_std": [1], "lookback_bars": 24, "timeframe": "1h"}


def _doc(kept=(0, 3, 6), segs=6, v2=True):
    segments = []
    for rid in (0, 3, 6):
        segments += [{"regime": rid, "from_ts": i, "to_ts": i + 1, "bars": 10} for i in range(segs)]
    return {"id": "ra_test", "symbols": ["BTCUSDT", "ETHUSDT"], "timeframe": "1h", "scope": "combined",
            "combined": {"model": _model(v2), "per_symbol": {"BTCUSDT": {"segments": segments}}},
            "kept": {f"combined:{r}": True for r in kept}, "release": {}}


def _jobs(calib=True, abl=True, delta=1.5):
    rows = [{"variant_key": "full", "holdout_direction_pct": 60.0},
            {"variant_key": "no_mtf", "holdout_direction_pct": 58.0},
            {"variant_key": "alt_ema", "holdout_direction_pct": 60.0 - delta}]
    return {"calibrations": [{"id": "c1", "symbols": ["BTCUSDT", "ETHUSDT"], "timeframe": "1h"}] if calib else [],
            "ablations": [{"id": "a1", "result": {"kind": "ablation", "symbols": ["BTCUSDT", "ETHUSDT"],
                                                  "timeframe": "1h", "rows": rows}}] if abl else []}


def test_validate_release_green_and_hash_stable():
    ok, reasons, ev = rr.validate_release(_doc(), "shadow", {}, _jobs())
    assert ok and reasons == []
    assert ev["evidence_hash"] and ev["ablation_delta_pct"] == 1.5 and ev["kept_regimes"] == 3
    _, _, ev2 = rr.validate_release(_doc(), "shadow", {}, _jobs())
    assert ev2["evidence_hash"] == ev["evidence_hash"]


def test_validate_release_each_missing_proof_one_reason():
    ok, reasons, _ = rr.validate_release(_doc(), "shadow", {}, _jobs(calib=False))
    assert not ok and sum("Kalibrierung" in r for r in reasons) == 1
    ok, reasons, _ = rr.validate_release(_doc(), "shadow", {}, _jobs(abl=False))
    assert not ok and sum("Ablation" in r for r in reasons) == 1
    ok, reasons, _ = rr.validate_release(_doc(), "shadow", {}, _jobs(delta=-0.5))
    assert not ok and any("verliert im Holdout" in r for r in reasons)
    ok, reasons, _ = rr.validate_release(_doc(segs=3), "shadow", {}, _jobs())
    assert not ok and sum("Abschnitte" in r for r in reasons) == 3
    ok, reasons, _ = rr.validate_release(_doc(kept=()), "shadow", {}, _jobs())
    assert not ok and any("behalten" in r for r in reasons)
    ok, reasons, _ = rr.validate_release(_doc(v2=False), "shadow", {}, _jobs())
    assert not ok and any("v2" in r for r in reasons)
    ok, reasons, _ = rr.validate_release(_doc(), "shadow", {"symbols": ["SOLUSDT"]}, _jobs())
    assert not ok and any("nicht Teil der Analyse" in r for r in reasons)
    ok, reasons, _ = rr.validate_release(_doc(), "none", {}, _jobs())
    assert not ok


def test_validate_activation_boundaries():
    rows = [{"regime": "strukturell bär", "trades": 30, "avg_reward": -0.42},
            {"regime": "strukturell bulle", "trades": 30, "avg_reward": -0.17}]
    ok, reasons, info = rr.validate_activation(rows)
    assert ok and info["reward_delta_r"] == 0.25 and info["shadow_trades"] == 60
    rows[0]["trades"] = 29
    ok, reasons, _ = rr.validate_activation(rows)
    assert not ok and any("29/30" in r for r in reasons)
    rows[0]["trades"] = 30
    rows[1]["avg_reward"] = -0.18
    ok, reasons, _ = rr.validate_activation(rows)
    assert not ok and any("0.24" in r for r in reasons)
    ok, reasons, _ = rr.validate_activation([{"regime": "unbekannt", "trades": 100, "avg_reward": 0}])
    assert not ok and any("Stichprobe fehlt" in r for r in reasons)


def test_apply_stage_history_grows_and_revoke():
    doc = _doc()
    _, _, ev = rr.validate_release(doc, "shadow", {}, _jobs())
    rel = rr.apply_stage(doc, "shadow", ev, "Admin", "manuell")
    assert rel["stage"] == "shadow" and len(rel["history"]) == 1 and rel["asset_classes"] == ["crypto"]
    doc["release"] = rel
    rel2 = rr.apply_stage(doc, "active", {}, "ki_trader", "Rewards", proposal_id="p1")
    assert rel2["stage"] == "active" and len(rel2["history"]) == 2
    assert rel2["history"][1]["proposal_id"] == "p1" and rel2["evidence"] == ev
    doc["release"] = rel2
    rel3 = rr.apply_stage(doc, "none", {}, "Admin", "Widerruf")
    assert rel3["stage"] == "none" and len(rel3["history"]) == 3 and rel3["since"] is None


def test_proposal_paths():
    rec = {"asset_class": "crypto", "aid": "ra_1", "verdict": "activate", "confidence": 80, "reason": "r"}
    p = rr.proposal_for(rec, True, [], "suggest")
    assert p["status"] == "pending" and p["scope"] == "regime_release"
    assert p["changes"]["structural_regime_stage"] == "active"
    p = rr.proposal_for(rec, True, [], "auto")
    assert p["status"] == "auto_applied" and p["pending_auto_until"] and p["applied_at"] is None
    assert not rr.grace_due(p)
    p["pending_auto_until"] = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    assert rr.grace_due(p)
    p = rr.proposal_for(rec, False, ["Shadow erst 12/30"], "auto")
    assert p["status"] == "needs_data" and p["gate_reasons"]
    assert rr.proposal_for({**rec, "confidence": 60}, True, [], "suggest") is None
    assert rr.proposal_for(rec, True, [], "off") is None
    assert rr.proposal_for({**rec, "verdict": "keep_shadow"}, True, [], "suggest") is None
    rv = rr.proposal_for({**rec, "verdict": "revoke", "confidence": 50}, False, ["x"], "auto")
    assert rv["status"] == "auto_applied" and rv["applied_at"] and rv["changes"]["structural_regime_stage"] == "shadow"


class _Coll:
    def __init__(self, docs=None):
        self.docs = docs or []

    def _m(self, d, q):
        for k, v in q.items():
            cur = d
            for part in k.split("."):
                cur = (cur or {}).get(part) if isinstance(cur, dict) else None
            if isinstance(v, dict):
                if "$in" in v and cur not in v["$in"]:
                    return False
                if "$gte" in v and not (str(cur or "") >= v["$gte"]):
                    return False
                if "$ne" in v and cur == v["$ne"]:
                    return False
            elif isinstance(cur, list):
                if v not in cur:
                    return False
            elif cur != v:
                return False
        return True

    async def find_one(self, q, *_):
        return next((dict(d) for d in self.docs if self._m(d, q)), None)

    def find(self, q, *_):
        docs = [dict(d) for d in self.docs if self._m(d, q)]

        class _C:
            def sort(self, *_): return self
            async def to_list(self, *_): return docs
        return _C()

    async def update_one(self, q, u, upsert=False):
        for d in self.docs:
            if self._m(d, q):
                d.update(u.get("$set", {}))
                return

    async def insert_one(self, d):
        self.docs.append(dict(d))


def test_set_stage_unique_per_class_and_revoke_immediate():
    a, b = _doc(), _doc()
    b["id"] = "ra_other"
    _, _, ev = rr.validate_release(a, "shadow", {}, _jobs())

    class _DB:
        regime_analyses = _Coll([a, b])
    db = _DB()
    asyncio.run(rr.set_stage(db, "ra_test", "shadow", ev, "Admin", "m"))
    assert a["release"]["stage"] == "shadow"
    asyncio.run(rr.set_stage(db, "ra_other", "shadow", ev, "Admin", "m2"))
    assert b["release"]["stage"] == "shadow" and a["release"]["stage"] == "none"
    assert any("abgelöst" in h["reason"] for h in a["release"]["history"])
    asyncio.run(rr.set_stage(db, "ra_other", "none", {}, "Admin", "Widerruf"))
    assert b["release"]["stage"] == "none" and len(b["release"]["history"]) == 2


def test_handle_recommendation_cooldown_and_needs_data(monkeypatch):
    a = _doc()
    a["release"] = {"stage": "shadow", "asset_classes": ["crypto"]}
    inserted = []

    class _Engine:
        config = {"structural_regime_autonomy": "suggest"}

        async def _insert_proposal(self, p):
            inserted.append(p)

    class _DB:
        regime_analyses = _Coll([a])
        ai_proposals = _Coll([])

    from services import ai_rewards
    async def _by(db, days):
        return [{"regime": "strukturell bär", "trades": 5, "avg_reward": 0.1}]
    monkeypatch.setattr(ai_rewards, "by_structural_regime", _by)
    db = _DB()
    rec = {"asset_class": "crypto", "verdict": "activate", "confidence": 90, "reason": "x"}
    p = asyncio.run(rr.handle_recommendation(db, _Engine(), rec))
    assert p["status"] == "needs_data"
    db.ai_proposals.docs.append(p)
    assert asyncio.run(rr.handle_recommendation(db, _Engine(), rec)) is None   # Cooldown 7 Tage
    _Engine.config["structural_regime_autonomy"] = "off"
    db.ai_proposals.docs.clear()
    assert asyncio.run(rr.handle_recommendation(db, _Engine(), rec)) is None


def test_validate_release_scope_both_normalized_to_combined():
    """Analyse-Scope `both` (kombiniert + je Coin) wird für die Freigabe als
    `combined` behandelt – kein `both` im release-Dokument."""
    d = _doc()
    d["scope"] = "both"
    ok, reasons, ev = rr.validate_release(d, "shadow", {}, _jobs())
    assert ok and ev["scope"] == "combined"
    rel = rr.apply_stage(d, "shadow", ev, "Admin", "m")
    assert rel["scope"] == "combined"


def test_apply_proposal_reruns_activation_gate(monkeypatch):
    """Trader klickt `Übernehmen` auf einen regime_release-Vorschlag: das
    Stichproben-Gate wird erneut geprüft – ohne grünes Gate keine Umschaltung."""
    from services.ai_engine_governance import AIEngineGovernanceMixin as GovernanceMixin
    a = _doc()
    a["release"] = {"stage": "shadow", "asset_classes": ["crypto"]}

    class _DB:
        regime_analyses = _Coll([a])

    class _Eng(GovernanceMixin):
        db = _DB()
        config = {}
    from services import ai_rewards
    thin = [{"regime": "strukturell bär", "trades": 3, "avg_reward": 0.1}]
    monkeypatch.setattr(ai_rewards, "by_structural_regime", lambda db, days: _aw(thin))
    changes = {"structural_regime_stage": "active", "asset_class": "crypto", "aid": "ra_test"}
    try:
        asyncio.run(_Eng()._apply_changes("regime_release", None, changes))
        assert False, "Gate hätte greifen müssen"
    except ValueError as e:
        assert "Stichproben-Gate" in str(e)
    assert a["release"]["stage"] == "shadow"
    good = [{"regime": "strukturell bär", "trades": 40, "avg_reward": -0.4},
            {"regime": "strukturell bulle", "trades": 40, "avg_reward": 0.3}]
    monkeypatch.setattr(ai_rewards, "by_structural_regime", lambda db, days: _aw(good))
    asyncio.run(_Eng()._apply_changes("regime_release", None, changes))
    assert a["release"]["stage"] == "active"


async def _aw(v):
    return v
