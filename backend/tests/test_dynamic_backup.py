"""Dynamische Strategien: Backup-Export/Import/Duplizieren (services/dynamic_backup.py)
und automatische Sicherung von Analysen im Autopilot-Verlauf."""
import asyncio

from services import dynamic_backup as bk
from services import regime_history_import as imp
from tests.test_autopilot_history_import import _Db, _analysis


def _dyn(did="dyn_a", **kw):
    return {"id": did, "name": "Trend 9 Regime", "strategy_id": "custom_x", "timeframe": "1h",
            "symbols": ["BTCUSDT"], "model": {"regimes": [{"id": 0, "label": "Auf"}]},
            "configs": {"0": {"tp1_crv": 1.5}}, "regime_strategies": {"0": "custom_y"},
            "sub_strategies": {"0": {"id": "custom_z"}},
            "settings": {"analysis_id": "ra_1", "auto_check_enabled": True, "auto_apply_enabled": True},
            "verdict": {"dynamic_better": False}, "last_state": {"per_symbol": {"BTCUSDT": {"regime": 0}}},
            "pending_switch": {"x": 1}, "archived": True, "created_at": "2026-09-30T10:00:00+00:00", **kw}


def test_strategy_ids_of_collects_base_regime_and_sub():
    assert bk.strategy_ids_of(_dyn()) == ["custom_x", "custom_y", "custom_z"]


def test_clean_dynamic_strips_runtime_state():
    c = bk.clean_dynamic(_dyn())
    assert "last_state" not in c and "pending_switch" not in c and "archived" not in c
    assert c["configs"] == {"0": {"tp1_crv": 1.5}}


def test_validate_bundle():
    assert bk.validate_bundle({"type": "strategy_backup"})
    assert bk.validate_bundle({"type": bk.TYPE, "dynamic": {"strategy_id": "x"}})
    assert bk.validate_bundle({"type": bk.TYPE, "dynamic": _dyn()}) is None


def test_build_copy_safe_defaults():
    doc = bk.build_copy(_dyn(), "dyn_new", "Kopie", {"kind": "duplicate"})
    assert doc["id"] == "dyn_new" and doc["name"] == "Kopie"
    assert doc["settings"]["auto_check_enabled"] is False and doc["settings"]["auto_apply_enabled"] is False
    assert doc["settings"]["analysis_id"] == "ra_1"
    assert doc["last_state"] == {} and doc["release"]["status"] == "draft"
    assert doc["imported_from"]["kind"] == "duplicate"


# ---------------- Fake-DB für Import ----------------
class _Coll:
    def __init__(self, docs=None, key="id"):
        self.docs = {d[key]: d for d in (docs or [])}
        self.key = key

    async def find_one(self, flt, proj=None):
        d = self.docs.get(flt.get(self.key))
        if d and flt.get("archived", {}).get("$ne") is True and d.get("archived"):
            return None
        return d

    async def replace_one(self, flt, doc, upsert=False):
        self.docs[flt[self.key]] = doc

    async def update_one(self, flt, upd, upsert=False):
        self.docs.setdefault(flt[self.key], {}).update(upd.get("$set") or upd.get("$setOnInsert") or {})


class _Reg:
    def __init__(self, ids):
        self.ids = set(ids)

    def get(self, sid):
        return object() if sid in self.ids else None

    def upsert_custom(self, d):
        self.ids.add(d["id"])


class _ImpDb:
    def __init__(self, dyns=(), analyses=()):
        self.dynamic_strategies = _Coll(list(dyns))
        self.regime_analyses = _Coll(list(analyses))
        self.custom_strategies = _Coll()
        self.strategy_coin_configs = _Coll(key="_id")
        self.regime_lab_runs = _Db([]).regime_lab_runs


def _bundle(analysis=None):
    return {"type": bk.TYPE, "dynamic_id": "dyn_a", "dynamic": bk.clean_dynamic(_dyn()),
            "analysis": analysis, "custom_strategies": [{"id": "custom_y", "name": "Y", "long_rules": []}],
            "strategy_coin_configs": {"BTCUSDT": {"enabled": True}}}


def test_import_restores_everything_with_original_id_when_free():
    db = _ImpDb()
    reg = _Reg({"custom_x", "custom_z"})
    an = _analysis("ra_1")
    res = asyncio.run(bk.import_bundle(db, reg, _bundle(an), lambda d, check_meta: d))
    assert res["id"] == "dyn_a" and res["same_id"] is True
    assert res["analysis"] == "restored" and "ra_1" in db.regime_analyses.docs
    assert res["custom_strategies"]["restored"] == ["custom_y"]
    assert db.strategy_coin_configs.docs["dyn_a_BTCUSDT"]["config"] == {"enabled": True}
    # Erkennung der wiederhergestellten Analyse ist im Autopilot-Verlauf gesichert
    assert "imp_ra_1" in db.regime_lab_runs.docs


def test_import_never_overwrites_existing_and_gets_new_id():
    existing = {**_dyn(), "archived": False, "name": "Original"}
    an = _analysis("ra_1")
    db = _ImpDb(dyns=[existing], analyses=[an])
    reg = _Reg({"custom_x", "custom_y", "custom_z"})
    res = asyncio.run(bk.import_bundle(db, reg, _bundle({**an, "name": "verändert"}), lambda d, check_meta: d))
    assert res["id"] != "dyn_a" and res["name"].endswith("(Import)")
    assert db.dynamic_strategies.docs["dyn_a"]["name"] == "Original"
    assert res["analysis"] == "exists" and db.regime_analyses.docs["ra_1"]["name"] == an["name"]
    assert res["custom_strategies"]["kept"] == ["custom_y"]


def test_duplicate_copy_keeps_analysis_link():
    db = _ImpDb(dyns=[{**_dyn(), "archived": False}], analyses=[_analysis("ra_1")])
    reg = _Reg({"custom_x", "custom_y", "custom_z"})
    b = _bundle(None)
    res = asyncio.run(bk.import_bundle(db, reg, b, lambda d, check_meta: d, name="Meine Kopie", copy=True))
    new = db.dynamic_strategies.docs[res["id"]]
    assert res["id"] != "dyn_a" and new["name"] == "Meine Kopie"
    assert new["settings"]["analysis_id"] == "ra_1" and res["analysis"] == "exists"


def test_import_fails_cleanly_when_strategy_missing():
    db = _ImpDb()
    b = {**_bundle(None), "custom_strategies": []}
    try:
        asyncio.run(bk.import_bundle(db, _Reg({"custom_x"}), b, lambda d, check_meta: d))
        assert False, "ValueError erwartet"
    except ValueError as e:
        assert "custom_y" in str(e)
    assert not db.dynamic_strategies.docs


def test_router_exposes_backup_endpoints():
    from routers import dynamic as rd
    paths = {r.path for r in rd.router.routes}
    assert {"/api/dynamic/{did}/export", "/api/dynamic/import", "/api/dynamic/{did}/duplicate"} <= paths


# ---------------- Autopilot-Verlauf: automatische Sicherung ----------------
def test_import_one_is_idempotent():
    db = _Db([])
    assert asyncio.run(imp.import_one(db, _analysis("a1"))) == "imp_a1"
    assert asyncio.run(imp.import_one(db, _analysis("a1"))) is None


def test_import_one_skips_when_unpinned_autopilot_run_shows_same_detection():
    db = _Db([])
    res = imp.analysis_to_run(_analysis("a1"))["result"]
    db.regime_lab_runs.docs["run1"] = {"result": {**res, "source": None}}
    assert asyncio.run(imp.import_one(db, _analysis("a1"), only_pinned=False)) is None
    assert asyncio.run(imp.import_one(db, _analysis("a1"), only_pinned=True)) == "imp_a1"


def test_boot_migration_registered():
    import inspect
    from services import boot_migrations as bm
    assert "migrate_regime_history_import" in inspect.getsource(bm.run_boot_migrations)


def test_persist_analysis_secures_detection_in_history():
    import inspect
    from services import regime_lab as lab
    src = inspect.getsource(lab.persist_analysis)
    assert "_secure_in_history" in src and src.index("_secure_in_history") < src.index("delete_one")
