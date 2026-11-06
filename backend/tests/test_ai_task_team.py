import asyncio

import pytest

from services import ai_edge_report, ai_providers, ai_roles

pytestmark = pytest.mark.unit

DEAD = "nvidia/nemotron-3-super-120b-a12b:free"


class _Settings:
    def __init__(self, doc=None):
        self.doc, self.saved = doc, None

    async def find_one(self, _q):
        return dict(self.doc) if self.doc else None

    async def update_one(self, _q, update, upsert=False):
        self.saved = update.get("$set")


class _DB:
    def __init__(self, doc=None):
        self.settings = _Settings(doc)


def _slots(cfg):
    return [cfg.get(k) for k in ai_roles.MODEL_SLOTS if k.endswith("model")]


def test_presets_valid_no_dead_model_and_mixed_providers():
    for role, p in ai_roles.ROLE_PRESETS.items():
        for pk, mk in (("provider", "model"), ("fallback_provider", "fallback_model"),
                       ("fallback2_provider", "fallback2_model")):
            assert p.get(mk) in ai_providers.allowed_models(p.get(pk)), (role, mk)
        assert DEAD not in _slots(p)
        assert len({p["provider"], p.get("fallback_provider"), p.get("fallback2_provider")}) >= 2, role
    assert ai_roles.ROLE_PRESETS["analyst"]["model"] == "openai/gpt-6-luna-pro"
    assert "openai/gpt-6-luna-pro" in ai_providers.PAID_MODELS_NO_FALLBACK


def test_dead_slug_migrates_to_lightning():
    assert ai_providers.migrate_model("openrouter", DEAD) == ("openrouter", "nvidia/nemotron-3.5-lightning:free")
    assert DEAD not in ai_providers.allowed_models("openrouter")


def test_task_team_applied_once_keeps_switches_and_schedule():
    stored = {"_cost_migration_v1": True,
              "analyst": {"provider": "openrouter", "model": DEAD, "user_configured": True, "enabled": False},
              "deep_analyst": {"provider": "mistral", "model": "mistral-small-latest",
                               "user_configured": True, "schedule_times": ["07:30"]}}
    db = _DB(stored)
    mgr = ai_roles.AIRoleManager()
    asyncio.run(mgr.load(db))
    a, d = mgr.role_cfg("analyst"), mgr.role_cfg("deep_analyst")
    assert a["model"] == "openai/gpt-6-luna-pro" and a["enabled"] is False
    assert d["model"] == ai_roles.ROLE_PRESETS["deep_analyst"]["model"] and d["schedule_times"] == ["07:30"]
    assert a["user_configured"] is True
    assert db.settings.saved[ai_roles.TASK_TEAM_MARKER] is True


def test_task_team_not_reapplied_after_marker_or_user_update():
    stored = {"_cost_migration_v1": True, ai_roles.TASK_TEAM_MARKER: True,
              "analyst": {"provider": "mistral", "model": "mistral-small-latest", "user_configured": True}}
    mgr = ai_roles.AIRoleManager()
    asyncio.run(mgr.load(_DB(stored)))
    assert mgr.role_cfg("analyst")["model"] == "mistral-small-latest"
    db = _DB()
    asyncio.run(mgr.update(db, {"chat": {"provider": "groq", "model": "openai/gpt-oss-20b"}}))
    assert db.settings.saved[ai_roles.TASK_TEAM_MARKER] is True


def test_edge_report_model_key():
    decs = {"d1": {"model": "openai/gpt-6-luna-pro"}}
    assert ai_edge_report.model_key({"ai_model": "x"}, decs) == "x"
    assert ai_edge_report.model_key({"decision_id": "d1"}, decs) == "openai/gpt-6-luna-pro"
    assert ai_edge_report.model_key({}, decs) is None
