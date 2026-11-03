"""Regressionstests: KI-Trader Gewinnschutz-Policy + KI-Provider-Priorisierung
+ clientId-Tagging der Entry-Orders (Nachweisbarkeit der Herkunft).

Anforderungen (23.08.):
  * Gewinnschutz (BE/SL-in-Gewinn, Marge-Freisetzung + Hebel-Maximierung) läuft
    für KI-Trader-Live-Trades INTERN per Default – ohne Coin-Einstellungen,
    dynamisch über settings['ai_profit_protection'] anpass-/abschaltbar.
  * Live-kritische KI-Rollen (Trade-Manager, Analyst, Markt-Beobachter,
    News-Wächter) werden beim Key-Kontingent priorisiert: Daten-/Lern-Rollen
    nutzen nur den Primär-Key, Backups bleiben für Live reserviert.
"""
import asyncio

from services import ai_providers
from services.bitunix_trade import AutoTradeManager, make_client_id


def run(coro):
    return asyncio.run(coro)


class FakeSettings:
    def __init__(self, doc=None):
        self.doc = doc

    async def find_one(self, q, *a, **kw):
        return dict(self.doc) if self.doc else None


class FakeDB:
    def __init__(self, doc=None):
        self.settings = FakeSettings(doc)


def _mgr(doc=None):
    m = AutoTradeManager.__new__(AutoTradeManager)
    m.db = FakeDB(doc)
    return m


class TestAiProtectionPolicy:
    def test_defaults_enabled(self):
        pol = run(_mgr().ai_protection_policy())
        assert pol["enabled"] is True
        assert pol["release_margin"] is True
        assert pol["max_leverage"] == 200.0
        assert pol["trigger_pct"] == 30.0
        assert pol["lock_pct"] == 50.0

    def test_settings_override(self):
        pol = run(_mgr({"enabled": False, "trigger_pct": 15.0,
                        "max_leverage": 100}).ai_protection_policy())
        assert pol["enabled"] is False
        assert pol["trigger_pct"] == 15.0
        assert pol["max_leverage"] == 100

    def test_unknown_keys_ignored(self):
        pol = run(_mgr({"hack": 1, "lock_pct": 70.0}).ai_protection_policy())
        assert "hack" not in pol
        assert pol["lock_pct"] == 70.0

    def test_cache_and_force(self):
        m = _mgr({"trigger_pct": 11.0})
        assert run(m.ai_protection_policy())["trigger_pct"] == 11.0
        m.db.settings.doc = {"trigger_pct": 22.0}
        assert run(m.ai_protection_policy())["trigger_pct"] == 11.0  # gecacht
        assert run(m.ai_protection_policy(force=True))["trigger_pct"] == 22.0


class TestProviderPriority:
    def test_live_roles_critical(self):
        for role in ("trade_manager", "analyst", "market_observer", "news_watcher"):
            assert ai_providers.role_priority(role) == "critical"

    def test_collection_roles_low(self):
        for role in ("learner", "research_analyst", "summarizer"):
            assert ai_providers.role_priority(role) == "low"

    def test_unknown_role_normal(self):
        assert ai_providers.role_priority(None) == "normal"
        assert ai_providers.role_priority("irgendwas") == "normal"

    def test_low_priority_uses_only_primary_key(self):
        idxs = list(range(16))
        assert ai_providers.restrict_indices_for_priority(idxs, "low") == [0]
        # Primär-Key im Cooldown -> low bekommt gar keinen Key (Backups tabu)
        assert ai_providers.restrict_indices_for_priority([3, 7], "low") == []

    def test_critical_and_normal_keep_all_keys(self):
        idxs = list(range(5))
        assert ai_providers.restrict_indices_for_priority(idxs, "critical") == idxs
        assert ai_providers.restrict_indices_for_priority(idxs, "normal") == idxs


class TestClientIdTagging:
    def test_prefix_and_strategy_tag(self):
        cid = make_client_id("ai_trader")
        assert cid.startswith("KIT-aitrader-")
        assert len(cid) <= 32

    def test_sanitizes_and_falls_back(self):
        assert make_client_id(None).startswith("KIT-bot-")
        assert make_client_id("!!##").startswith("KIT-bot-")
        assert make_client_id("custom_23a30b65").startswith("KIT-custom23a3-")
