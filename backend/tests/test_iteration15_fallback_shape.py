"""Iteration 15: Shape-/Klassifizierungs-Tests für active_fallbacks
(outside_team + team_model) sowie API-Shape von GET /api/ai/status."""
import os
import sys

import pytest
import requests
from dotenv import dotenv_values

sys.path.insert(0, "/app/backend")

frontend_env = dotenv_values("/app/frontend/.env")
BASE_URL = (os.environ.get("REACT_APP_BACKEND_URL")
            or frontend_env.get("REACT_APP_BACKEND_URL")).rstrip("/")


# --- API shape -------------------------------------------------------------
def test_ai_status_active_fallbacks_shape():
    r = requests.get(f"{BASE_URL}/api/ai/status", timeout=60)
    assert r.status_code == 200, r.text[:300]
    ph = r.json().get("providers_health")
    assert isinstance(ph, dict)
    af = ph.get("active_fallbacks")
    assert isinstance(af, list)
    for entry in af:
        assert "outside_team" in entry, entry
        assert "team_model" in entry, entry
        assert "role" in entry and "provider" in entry and "model" in entry


# --- In-Process: record_result -> health_status ----------------------------
def test_provider_internal_substitute_flagged_live():
    from services import ai_providers
    from services.ai_roles import role_manager
    from services.ai_engine import ai_engine

    role = "news_watcher"
    team = role_manager.configured_models(role, ai_engine.config or {})
    assert team, "news_watcher hat keine konfigurierten Modelle"

    ai_providers.record_result("openrouter",
                               "nvidia/nemotron-3-super-120b-a12b:free",
                               "ok", key_index=0, role=role, requested="x")
    af = ai_providers.health_status()["active_fallbacks"]
    entry = next((e for e in af if e.get("role") == role), None)
    assert entry is not None, af
    assert entry["outside_team"] is True, entry
    assert entry["team_model"] == f"{team[0][0]}/{team[0][1]}", entry
