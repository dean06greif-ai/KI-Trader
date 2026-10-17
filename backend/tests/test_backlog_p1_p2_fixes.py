"""Backlog-Fixes (P1+P2): Chart-Verfügbarkeit ohne Bitunix-Kline (Forex),
Kennzeichnung "Not-Fallback außerhalb des Teams" und record_result ohne
globalen Rollen-Fallback."""
import pytest

from core.instruments import kline_available
from core.utils import _enrich_trade
from services import ai_providers
from services.ai_roles import role_manager


@pytest.fixture(autouse=True)
def _clean_state():
    ai_providers._role_fallbacks.clear()
    ai_providers.set_current_role(None)
    yield
    ai_providers._role_fallbacks.clear()
    ai_providers.set_current_role(None)


def test_kline_available_per_instrument():
    assert kline_available("BTCUSDT") is True
    assert kline_available("GOLD") is True        # XAUUSDT-Kontrakt
    assert kline_available("GBPUSD") is False     # Forex: kein Bitunix-Kontrakt
    assert kline_available("EURUSD") is False
    assert kline_available("FOOBAR999") is True   # unbekannt = extern adoptiert


def test_enrich_trade_exposes_chart_available():
    base = {"symbol": "GBPUSD", "status": "closed", "entry": 1.1,
            "side": "LONG", "qty": 1}
    assert _enrich_trade(dict(base))["computed"]["chart_available"] is False
    assert _enrich_trade({**base, "symbol": "BTCUSDT"})["computed"]["chart_available"] is True


def test_team_chain_is_prefix_subset_of_full_chain():
    team = role_manager.team_chain("news_watcher", {})
    full = role_manager.chain("news_watcher", {})
    assert team, "Team-Kette darf nicht leer sein"
    assert set(team).issubset(set(full))
    assert full[:len(team)] == team  # Not-Kette hängt hinten dran


def test_outside_team_flag_in_health_status():
    ai_providers.record_result("groq", "modell-nicht-im-team", "ok",
                               key_index=0, role="news_watcher",
                               requested="wunsch-modell")
    h = ai_providers.health_status()
    fb = [f for f in h["active_fallbacks"] if f["role"] == "news_watcher"]
    assert fb, h["active_fallbacks"]
    assert fb[0].get("outside_team") is True
    assert fb[0].get("team_model")  # konfiguriertes Primärmodell für die UI


def test_inside_team_fallback_not_flagged():
    team = role_manager.configured_models("news_watcher", {})
    prov, model = team[-1]  # explizit gewähltes Team-Mitglied als Fallback
    ai_providers.record_result(prov, model, "ok", key_index=0,
                               role="news_watcher", requested="wunsch-modell")
    h = ai_providers.health_status()
    fb = [f for f in h["active_fallbacks"] if f["role"] == "news_watcher"]
    assert fb and fb[0].get("outside_team") is False


def test_provider_internal_substitute_is_flagged_outside_team():
    """User-Bug: Provider-interne Ersatzmodelle (gleicher Provider, aber nie
    vom Nutzer gewählt) galten fälschlich als 'im Team' – sie müssen als
    Not-Fallback markiert werden."""
    team = set(role_manager.configured_models("news_watcher", {}))
    internal = [pm for pm in role_manager.team_chain("news_watcher", {})
                if pm not in team]
    assert internal, "erwartet Provider-interne Ersatzmodelle in der Kette"
    prov, model = internal[0]
    ai_providers.record_result(prov, model, "ok", key_index=0,
                               role="news_watcher", requested="wunsch-modell")
    h = ai_providers.health_status()
    fb = [f for f in h["active_fallbacks"] if f["role"] == "news_watcher"]
    assert fb and fb[0].get("outside_team") is True


def test_configured_models_only_explicit_selection():
    conf = role_manager.configured_models("news_watcher", {})
    team = role_manager.team_chain("news_watcher", {})
    assert 1 <= len(conf) <= 3
    assert set(conf).issubset(set(team))
