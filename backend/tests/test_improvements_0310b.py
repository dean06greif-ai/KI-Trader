"""Regressionstests 03.10.2026 (b): Note-Schutz im Autopilot, 3-Jahres-Zeiträume,
KI-Modell-Katalog (Bewertung, Auto-Aufnahme, Entfernen/Wiederherstellen,
Mitteilung), Worker-Fehlertext. Rein offline (keine DB/kein Netz)."""
import asyncio
from pathlib import Path

import pytest

from services import ai_model_news, ai_model_rating, ai_providers, regime_autopilot as ap
from services import history_sources
from services.setup_backtest import auto, runner

# Echte Kennzahlen des Prod-Laufs 3cf6d7f85df8 (sehr gut 8/8 -> gut 5/8, Score 63,7 -> 65,2)
BASE_M = {"direction_pct": 92.9, "holdout_direction_pct": 92.5, "inner_direction_pct": 93.1,
          "train_direction_pct": 93.0, "holdout_bars": 77970, "avg_live_phase_days": 4.4,
          "validation_passed": True, "reference_pct": 70.8, "holdout_reference_pct": 74.0,
          "reference_lag_days": 1.8, "holdout_reference_f1_pct": 61.0,
          "holdout_reference_bal_pct": 60.3, "holdout_skill_pct": 6.8,
          "live_direction_phase_days": 5.4, "reference_missed_pct": 14.3}
BEST_M = {"direction_pct": 90.3, "holdout_direction_pct": 90.1, "inner_direction_pct": 90.2,
          "train_direction_pct": 90.3, "holdout_bars": 77970, "avg_live_phase_days": 3.37,
          "validation_passed": True, "reference_pct": 68.7, "holdout_reference_pct": 71.3,
          "reference_lag_days": 1.7, "holdout_reference_f1_pct": 61.2,
          "holdout_reference_bal_pct": 63.8, "holdout_skill_pct": -3.8,
          "live_direction_phase_days": 4.0, "reference_missed_pct": 6.1}


# ---------------- Autopilot: Note darf nicht sinken ----------------
def test_grade_compare_real_run():
    gc = ap.grade_compare(BEST_M, BASE_M)
    assert gc["before"] == "sehr gut" and gc["after"] == "gut" and gc["regressed"]
    assert "Ø Richtungs-Phase im Sweet Spot" in gc["newly_failed"]


def test_adopt_blocked_when_grade_drops():
    res = {"improved": True, "best": {"metrics": BEST_M}, "baseline": {"metrics": BASE_M}}
    assert not ap.holdout_regressed(BEST_M, BASE_M)  # alter Schutz griff hier nicht
    assert ap.grade_regressed(BEST_M, BASE_M)
    assert ap.adopt_recommended(res) is False


def test_adopt_allowed_same_grade():
    res = {"improved": True, "best": {"metrics": dict(BASE_M)}, "baseline": {"metrics": BASE_M}}
    assert ap.adopt_recommended(res) is True


def test_rate_result_mentions_grade_drop():
    res = {"best": {"metrics": BEST_M, "score": 65.2}, "baseline": {"metrics": BASE_M}}
    r = ap.rate_result(res)
    assert r["label"] == "gut" and "Note gesunken" in r["why"]


# ---------------- Zeiträume bis 3 Jahre ----------------
def test_setup_backtest_max_days_3y_with_ram_guard():
    assert runner.MAX_DAYS == 1095
    assert runner.class_days(1095, 3) == 1095      # Rohstoffe
    assert runner.class_days(1095, 2) == 1095      # Indizes
    assert runner.class_days(1095, 13) == 365      # Krypto in der Cloud gekappt
    assert runner.class_days(90, 13) == 90         # kurze Läufe unverändert
    assert auto.normalize({"days": 5000})["days"] == 1095


def test_history_caps_3y():
    assert history_sources.BACKUP_MAX_DAYS == 1095
    from core import instruments
    assert instruments.BITUNIX_HIST_CAP_DAYS == 1095


# ---------------- Modell-Bewertung ----------------
CAT = {"gemini": ["gemini-3.5-flash", "gemini-3.6-flash"], "mistral": ["mistral-small-latest"],
       "openrouter": ["deepseek/deepseek-v4-flash"]}


def test_rating_successor_free_is_top():
    r = ai_model_rating.rate("gemini", "gemini-3.8-flash", None, CAT)
    assert r["tier"] == "top" and r["successor_of"] == "gemini-3.6-flash" and not r["paid"]


def test_rating_paid_cheap_successor_top_expensive_good():
    cheap = ai_model_rating.rate("openrouter", "deepseek/deepseek-v4.1-flash",
                                 {"price_in": 0.1, "price_out": 1.2}, CAT)
    assert cheap["tier"] == "top" and cheap["paid"] and cheap["successor_of"] == "deepseek/deepseek-v4-flash"
    pricey = ai_model_rating.rate("openrouter", "anthropic/claude-sonnet-5.5",
                                  {"price_in": 2, "price_out": 10}, CAT)
    assert pricey["tier"] == "good"


def test_rating_skips_small_and_special():
    assert ai_model_rating.rate("mistral", "ministral-3b-latest", None, CAT)["tier"] == "low"
    assert ai_model_rating.rate("mistral", "codestral-latest", None, CAT)["tier"] == "low"
    assert ai_model_rating.rate("openrouter", "qwen/qwen3-8b", {"price_out": 0.4}, CAT)["tier"] == "low"


def test_newest_only_and_paid_candidate():
    rs = [ai_model_rating.rate("openrouter", m, {"price_out": 1}, CAT)
          for m in ("x-ai/grok-4.3", "x-ai/grok-4.7")]
    assert [r["model"] for r in ai_model_rating.newest_only(rs)] == ["x-ai/grok-4.7"]
    now = 2_000_000_000
    fresh = {"price_out": 1.0, "context": 200_000, "created": now - 86400}
    assert ai_model_rating.paid_candidate("x-ai/grok-4.7", fresh, now)
    assert not ai_model_rating.paid_candidate("x-ai/grok-4.7", {**fresh, "created": now - 400 * 86400}, now)
    assert not ai_model_rating.paid_candidate("~openai/gpt-luna-latest", fresh, now)
    assert not ai_model_rating.paid_candidate("unknown/model-x", fresh, now)


# ---------------- Katalog: entfernen / dynamisch / bezahlt ----------------
@pytest.fixture
def clean_catalog():
    yield
    ai_providers.set_removed_models({})
    ai_providers.set_dynamic_models({})


def test_removed_models_disappear_from_allowed(clean_catalog):
    ai_providers.set_removed_models({"mistral": ["ministral-8b-latest"]})
    assert "ministral-8b-latest" not in ai_providers.allowed_models("mistral")
    assert "ministral-8b-latest" not in ai_providers.catalog()["mistral"]
    chain = ai_providers.same_provider_chain("mistral", "ministral-8b-latest")
    assert ("mistral", "ministral-8b-latest") not in chain


def test_dynamic_paid_model_never_fallback(clean_catalog):
    ai_providers.set_dynamic_models({"openrouter": ["openai/gpt-6-luna"]},
                                    {"openrouter/openai/gpt-6-luna": {"paid": True, "weight": 3}})
    assert "openai/gpt-6-luna" in ai_providers.allowed_models("openrouter")
    assert "openai/gpt-6-luna" in ai_providers.PAID_MODELS_NO_FALLBACK
    assert ai_providers.model_weight("openai/gpt-6-luna") == 3


def test_dead_groq_qwen_migrated():
    assert "qwen/qwen3.8-27b" in ai_providers.ALLOWED_MODELS["groq"]
    assert ai_providers.MODEL_MIGRATIONS["qwen/qwen3.6-27b"] == ("groq", "qwen/qwen3.8-27b")


# ---------------- Modell-Wächter mit Fake-DB ----------------
class FakeSettings:
    def __init__(self):
        self.docs = {}

    async def find_one(self, q):
        d = self.docs.get(q["_id"])
        return dict(d) if d else None

    async def update_one(self, q, upd, upsert=False):
        self.docs.setdefault(q["_id"], {"_id": q["_id"]}).update(upd.get("$set") or {})


class FakeDB:
    def __init__(self):
        self.settings = FakeSettings()


def test_watch_remove_in_use_rejected_then_restore(clean_catalog):
    from services.ai_model_watch import ModelWatch
    w, db = ModelWatch(), FakeDB()
    res = asyncio.run(w.remove(db, "mistral", "mistral-small-latest", ["Chat (Modell)"]))
    assert res["status"] == "error" and "verwendet" in res["detail"]
    assert asyncio.run(w.remove(db, "mistral", "mistral-small-latest", []))["status"] == "ok"
    assert "mistral-small-latest" not in ai_providers.allowed_models("mistral")
    assert asyncio.run(w.restore(db, "mistral", "mistral-small-latest"))["status"] == "ok"
    assert "mistral-small-latest" in ai_providers.allowed_models("mistral")


def test_watch_auto_adopt_top_only(clean_catalog, monkeypatch):
    from services.ai_model_watch import ModelWatch
    w = ModelWatch()

    async def ok(p, m):
        return True
    monkeypatch.setattr(w, "_smoke_test", ok)
    discovered = {"gemini": ["gemini-3.8-flash", "gemini-3.7-flash", "gemini-robotics-er-2-preview"],
                  "mistral": ["ministral-3b-latest"]}
    approved, meta, rated = {}, {}, {}
    news = asyncio.run(w._auto_adopt(discovered, approved, meta, rated, {}, set()))
    assert [n["model"] for n in news] == ["gemini-3.8-flash"]   # nur neueste Linie
    assert approved == {"gemini": ["gemini-3.8-flash"]}
    assert meta["gemini/gemini-3.8-flash"]["auto"] is True
    assert rated["mistral/ministral-3b-latest"]["tier"] == "low"


def test_watch_dismissed_never_auto(clean_catalog, monkeypatch):
    from services.ai_model_watch import ModelWatch
    w = ModelWatch()
    news = asyncio.run(w._auto_adopt({"gemini": ["gemini-3.8-flash"]}, {}, {}, {}, {},
                                     {"gemini/gemini-3.8-flash"}))
    assert news == []


def test_announcement_dismiss():
    from services.ai_model_watch import ModelWatch
    w, db = ModelWatch(), FakeDB()
    db.settings.docs["model_watch"] = {"_id": "model_watch", "announcements": [{"id": "a1", "dismissed": False}]}
    assert asyncio.run(w.dismiss_announcement(db, "a1"))["status"] == "ok"
    assert db.settings.docs["model_watch"]["announcements"][0]["dismissed"] is True
    assert asyncio.run(w.dismiss_announcement(db, "zz"))["status"] == "error"


def test_news_merge_llm_tolerant():
    r = ai_model_rating.rate("gemini", "gemini-3.8-flash", None, CAT)
    labels = {"analyst": "Analyst – x", "chat": "Chat – y"}
    base = ai_model_news.rule_models([r], {"analyst": {"model": "gemini-3.6-flash"}}, labels)
    assert base[0]["roles"][0]["role"] == "analyst"  # direkter Nachfolger
    data = {"models": [{"model": "gemini/gemini-3.8-flash", "why": "schneller & besser",
                        "roles": [{"role": "chat", "replaces": "a"}, {"role": "evil", "replaces": "b"}]}]}
    out = ai_model_news.merge_llm(base, data, labels)
    assert out[0]["why"] == "schneller & besser" and [x["role"] for x in out[0]["roles"]] == ["chat"]


# ---------------- Lokaler Worker: lesbarer 502-Grund ----------------
def _worker_src():
    return (Path(__file__).resolve().parents[2] / "local_worker" / "worker.py").read_text(encoding="utf-8")


def test_worker_http_reason_html_502():
    """http_reason isoliert ausführen (Worker-Import fragt sonst interaktiv die Server-URL ab)."""
    import ast
    src = _worker_src()
    fn = next(n for n in ast.parse(src).body if isinstance(n, ast.FunctionDef) and n.name == "http_reason")
    ns = {}
    exec(compile(ast.Module(body=[fn], type_ignores=[]), "worker", "exec"), ns)

    class R:
        status_code, text = 502, "<!DOCTYPE html><html lang=\"en\">"

    class J:
        status_code, text = 409, '{"detail":"x"}'
    assert "Render-Proxy" in ns["http_reason"](R)
    assert ns["http_reason"](J) == '{"detail":"x"}'
    assert "def http_reason" in src
    assert "Server wieder erreichbar" in src


# ---------------- Note-Schutz in der Suche (grade_lock) ----------------
def test_search_benchmark_and_grade_lock():
    base = {**BASE_M, "inner_reference_f1_pct": 55.5}
    best = {**BEST_M, "inner_reference_f1_pct": 57.9}
    sb, sx = ap.search_benchmark(base), ap.search_benchmark(best)
    assert sb["passed"] == 6 and sx["passed"] == 4
    # höherer Score, aber weniger Kriterien -> nicht besser
    assert not ap.grade_lock_better(best, 65.2, {"metrics": base, "score": 63.7}, True)
    # mehr Kriterien gewinnt auch bei niedrigerem Score; gleiche Stufe -> Score entscheidet
    assert ap.grade_lock_better(base, 60.0, {"metrics": best, "score": 65.2}, False)
    assert ap.grade_lock_better(base, 64.0, {"metrics": base, "score": 63.7}, True)


# ---------------- KI-Trader-Lab lokal: MemDB + Replay ----------------
def test_memdb_replay_matches_real_mongo():
    """Edges-Operationen gegen die MemDB protokollieren, auf Mongo nachspielen ->
    gleicher Endzustand wie direkt in der MemDB (inkl. neu eingefügter IDs)."""
    import os
    from motor.motor_asyncio import AsyncIOMotorClient
    from services.setup_backtest import edges, local_run

    async def go():
        client = AsyncIOMotorClient(os.environ.get("MONGO_URL") or "mongodb://localhost:27017",
                                    serverSelectionTimeoutMS=2000)
        try:
            await client.server_info()
        except Exception:  # noqa: BLE001
            pytest.skip("keine lokale Mongo")
        real = client["test_memdb_replay"]
        await real[edges.COLLECTION].delete_many({})
        await real[edges.COLLECTION].insert_one({"id": "old1", "asset_class": "crypto", "setup": "s1",
                                                 "fingerprint": "f0", "status": "active", "best_robust": 1.0})
        snap = await real[edges.COLLECTION].find({}).to_list(100)
        for d in snap:
            d["_id"] = str(d["_id"])
        mem = local_run.MemDB({edges.COLLECTION: snap})
        summ = {"fingerprint": "f1", "is": {}, "oos": {}, "robust": 2.0, "passed": True, "params": {},
                "name": "v", "variant": 1, "source": "check", "diag": {}, "flags": {}}
        doc = await edges.record(mem, "crypto", "s1", summ, days=1095, job_id="j", oos_trades=[{"x": 1}])
        await edges.set_active(mem, "crypto", "s1", doc["id"], reason="test")
        await edges.prune(mem, "crypto", "s1")
        await mem.settings.update_one({"_id": "setup_backtest_state"}, {"$set": {"classes": {"crypto": {}}}}, upsert=True)
        n = await local_run.replay(real, mem.log)
        got = {d["id"]: d["status"] for d in await real[edges.COLLECTION].find({}).to_list(100)}
        want = {d["id"]: d["status"] for d in mem[edges.COLLECTION].docs}
        st = await real.settings.find_one({"_id": "setup_backtest_state"})
        await client.drop_database("test_memdb_replay")
        return n, got, want, st
    n, got, want, st = asyncio.run(go())
    assert n >= 4 and got == want and got["old1"] == "retired"
    assert st["classes"] == {"crypto": {}}


def test_ai_seed_wired_into_local_exec():
    from services import local_exec
    from services.setup_backtest import runner
    assert "ai_seed" in local_exec._KIND_STORES and local_exec.AI_SEED_MIN_VERSION == (1, 21)
    runner.JOBS["t_seed"] = {"id": "t_seed", "status": "running"}
    assert local_exec._get_job("t_seed", "ai_seed") is runner.JOBS.pop("t_seed")
    src = _worker_src()
    assert 'VERSION = "1.23.0"' in src and 'kind == "ai_seed"' in src
