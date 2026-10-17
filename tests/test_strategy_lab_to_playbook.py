"""Regressionstests: Strategie-Labor -> Playbook (Aufräumen 06.09.2026)."""
import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
sys.path.insert(0, os.path.dirname(__file__))

from services import ai_playbook, ai_strategy_lab as lab  # noqa: E402
from test_data_recovery_and_trash import _Coll, _DB  # noqa: E402


def test_candidate_setup_id_and_desc():
    assert lab.candidate_setup_id("Bollinger‑Band‑Trend‑Volume Reversion") == "bollinger_band_trend_vol"
    assert lab.candidate_setup_id("RSI < 30 kaufen!") == "rsi_30_kaufen"
    assert lab.candidate_setup_id("42 Strategie") == "ki_42_strategie"
    assert lab.candidate_setup_id("Übergröße") == "uebergroesse"
    # Alias eines bestehenden Setups wird vom Playbook (korrekt) abgelehnt
    sid = lab.candidate_setup_id("Bollinger‑Band‑Trend‑Volume Reversion")
    assert not ai_playbook.valid_custom_setup(sid, "Preis kehrt nach Band-Berührung zurück, Volumen bestätigt")[0]
    assert ai_playbook.valid_custom_setup("vol_climax_exit", "Lücke zur Vortages-Schlusskerze wird am Open gefüllt")[0]
    d = lab.candidate_setup_desc({"thesis": "These A.", "rules_text": "Regel B"})
    assert d == "These A. Regeln: Regel B"
    assert len(lab.candidate_setup_desc({"thesis": "x" * 500})) == 300
    assert lab.is_test_candidate({"name": "TEST_RSI_Rule"}) and lab.is_test_candidate({"name": "QA_x"})
    assert not lab.is_test_candidate({"name": "Testlauf Momentum"})


def test_migrate_to_playbook_moves_active_and_deletes_test_candidates():
    db = _DB()
    db.ai_strategy_candidates = _Coll([
        {"id": "c1", "name": "Vol Climax Exit", "stage": "ghost",
         "thesis": "Lücke zur Vortages-Schlusskerze wird nach dem Open gefüllt, Einstieg gegen den Gap."},
        {"id": "c5", "name": "Bollinger‑Band‑Trend‑Volume Reversion", "stage": "live_pending",
         "thesis": "Preis kehrt nach Berührung der Bollinger-Bänder zurück, Volumen bestätigt."},
        {"id": "c2", "name": "TEST_RSI_Rule", "stage": "rejected", "thesis": "RSI<30 kaufen"},
        {"id": "c3", "name": "Alte Idee", "stage": "rejected", "thesis": "..."},
        {"id": "c4", "name": "Trader Live", "stage": "live", "thesis": "vom Trader freigegeben"},
    ])
    db.ai_ghost_trades = _Coll([{"id": "g1", "candidate_id": "c2"}])
    db.ai_chat = _Coll()
    ai_playbook.set_custom_cache({})
    store = lab.StrategyLab()
    store.setup(type("Eng", (), {"db": db})())
    res = asyncio.run(store.migrate_to_playbook())
    assert res["deleted_test"] == 1 and len(res["migrated"]) == 1 and len(res["skipped"]) == 1
    assert res["migrated"][0]["setup"] == "vol_climax_exit"
    assert res["skipped"][0]["alias_of"] in ai_playbook.SETUPS          # Alias -> bestehendes Setup benannt
    ids = {c["id"]: c for c in db.ai_strategy_candidates.rows}
    assert "c2" not in ids and ids["c1"]["stage"] == "rejected" and ids["c1"]["migrated_to"] == "vol_climax_exit"
    assert ids["c5"]["stage"] == "rejected" and "entspricht dem bestehenden" in ids["c5"]["decision_note"]
    assert ids["c3"]["stage"] == "rejected" and ids["c4"]["stage"] == "live"      # unangetastet
    assert db.ai_ghost_trades.rows == []
    pb = asyncio.run(db.settings.find_one({"_id": ai_playbook.STATE_ID}))
    assert "vol_climax_exit" in pb["custom"] and "bollinger_band_trend_vol" not in pb["custom"]
    assert store.settings["allow_ai_create"] is False and store.settings["auto_develop_enabled"] is False
    # Prompt-Block: Labor stillgelegt, keine 'new_strategies' mehr
    txt = asyncio.run(store.context_text())
    assert "STILLGELEGT" in txt and "new_setups" in txt and "new_strategies" in txt
    assert "Trader Live" in txt


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q", "-n", "0"]))
