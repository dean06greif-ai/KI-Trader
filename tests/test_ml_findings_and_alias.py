"""Regressionstests: ML-Befunde entkoppelt (services/ml_findings.py), Alt-Artefakte-Aufräumen,
Alias-Check im Playbook-Prompt + Rückmeldung bei Alias-Ablehnung."""
import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
sys.path.insert(0, os.path.dirname(__file__))

from services import ai_memory, ai_playbook, boot_migrations, ml_findings  # noqa: E402
from test_data_recovery_and_trash import _Coll, _DB  # noqa: E402


def test_make_finding_shape_and_kind_fallback():
    f = ml_findings.make_finding("rule", "Titel", "x" * 3000, source="ml_lab/m", meta={"a": 1})
    assert f["kind"] == "rule" and len(f["content"]) == 2000 and f["id"].startswith("mlf_")
    assert ml_findings.make_finding("quatsch", "t", "c")["kind"] == "model"


def test_memory_default_kinds_exclude_ml_finding():
    import inspect
    src = inspect.getsource(ai_memory.AIMemory.context_text if hasattr(ai_memory, "AIMemory")
                            else ai_memory.memory.context_text)
    assert '"ml_finding", "idea"' not in src and '"research_insight", "idea"' in src


def test_migrate_from_memory_moves_and_classifies():
    db = _DB()
    db.ai_knowledge = _Coll([
        {"kind": "ml_finding", "title": "ML-Modell", "content": "AUC 0.9", "tags": ["ml"], "ts": "2026-09-01T00:00:00"},
        {"kind": "ml_finding", "title": "Regel", "content": "RSI<30", "tags": ["ml", "regel"], "ts": "2026-09-02T00:00:00"},
        {"kind": "ml_finding", "title": "Erkl", "content": "…", "tags": ["ml", "erklärung"], "ts": "2026-09-03T00:00:00"},
        {"kind": "idea", "title": "Idee", "content": "bleibt"}])
    db.ml_findings = _Coll()
    n = asyncio.run(ml_findings.migrate_from_memory(db))
    assert n == 3 and {r["kind"] for r in db.ai_knowledge.rows} == {"idea"}
    kinds = {r["title"]: r["kind"] for r in db.ml_findings.rows}
    assert kinds == {"ML-Modell": "model", "Regel": "rule", "Erkl": "explanation"}
    assert asyncio.run(ml_findings.migrate_from_memory(db)) == 0          # idempotent
    rec = asyncio.run(ml_findings.recent(db, limit=5))
    assert len(rec) == 3


def test_add_rules_and_recent_filter():
    db = _DB()
    db.ml_findings = _Coll()
    assert asyncio.run(ml_findings.add_rules(db, [{"title": "R1", "detail": "d"}, "kaputt", {"title": ""}], "src")) == 1
    asyncio.run(ml_findings.add(db, "explanation", "E", "text"))
    assert [r["kind"] for r in asyncio.run(ml_findings.recent(db, kind="rule"))] == ["rule"]


def test_cleanup_test_artifacts_only_orphans():
    db = _DB()
    db.strategies = _Coll([{"id": "custom_livetest"}])
    db.signals = _Coll([{"strategy_id": "custom_14f031fftest"}, {"strategy_id": "custom_14f031fftest"},
                        {"strategy_id": "custom_livetest"}, {"strategy_id": "ai_trader"}])
    dropped = []

    async def list_collection_names():
        return ["signals", "db.ai_lesson_candidates", "ai_lesson_candidates"]

    async def drop_collection(name):
        dropped.append(name)
    db.list_collection_names = list_collection_names
    db.drop_collection = drop_collection
    out = asyncio.run(boot_migrations.cleanup_test_artifacts(db))
    assert out["signals"] == 2 and out["strategies"] == ["custom_14f031fftest"]
    assert out["dropped"] == ["db.ai_lesson_candidates"] and dropped == ["db.ai_lesson_candidates"]
    assert {s["strategy_id"] for s in db.signals.rows} == {"custom_livetest", "ai_trader"}   # existierende bleibt


def test_alias_check_text_in_prompt():
    txt = ai_playbook.alias_check_text()
    assert txt.startswith("ALIAS-CHECK") and "trend_follow(trend/scalp)" in txt
    assert "mean_reversion(reversion/revert)" in txt and "fvg_fill(" in txt


def test_propose_alias_rejection_feeds_ki():
    db = _DB()
    db.ai_chat = _Coll()
    ai_playbook.set_custom_cache({})
    res = asyncio.run(ai_playbook.propose_custom_setup(db, "bb_trend_reversion",
                                                       "Preis kehrt nach Bandberührung zurück, Volumen bestätigt"))
    assert res["status"] == "rejected" and res["alias_of"] in ai_playbook.SETUPS
    assert len(db.ai_chat.rows) == 1 and db.ai_chat.rows[0]["kind"] == "alias_rejected"
    assert res["alias_of"] in db.ai_chat.rows[0]["text"]
    # echte neue Idee: kein Feed über Ablehnung
    db.ai_chat = _Coll()
    res = asyncio.run(ai_playbook.propose_custom_setup(db, "vol_climax_exit",
                                                       "Volumen-Klimax nach Trendlauf: Ausstieg gegen die Erschöpfung"))
    assert res["status"] == "ok" and not any(r.get("kind") == "alias_rejected" for r in db.ai_chat.rows)


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q", "-n", "0"]))
