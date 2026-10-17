"""Regressionstests: Multi-Asset-Tiefenanalyse (Iteration 06/2026).

1. News-Wächter bewertet ALLE Anlageklassen (Prompt-Framing) und löst
   `affects`-Tokens auch für Gold/Öl/Indizes/Forex korrekt auf.
2. Analyse-/Tiefenanalyse-Prompts sind multi-asset-gerahmt: Setups als
   gewichteter Faktor (SETUP-WAHL statt SETUP-PFLICHT), Volatilität relativ
   zur eigenen Baseline statt absolutem BTC-Vergleich.
"""
import pytest

from services.ai_news_watcher import WATCHER_SYSTEM, news_symbols


SYMBOLS = ["BTCUSDT", "ETHUSDT", "GOLD", "SILVER", "OIL",
           "SPYUSDT", "QQQUSDT", "EURUSD", "GBPUSD", "USDJPY"]


class TestNewsWatcherMultiAsset:
    def test_watcher_prompt_covers_all_classes(self):
        for kw in ("Multi-Asset", "GOLD", "SPYUSDT", "EURUSD", "OPEC"):
            assert kw in WATCHER_SYSTEM, f"'{kw}' fehlt im News-Wächter-Prompt"

    def test_affects_resolve_commodities_and_indices(self):
        events = [{"affects": ["XAU", "WTI", "SPX"]}]
        out = news_symbols(events, SYMBOLS)
        assert out == ["GOLD", "OIL", "SPYUSDT"]

    def test_affects_resolve_forex_aliases(self):
        events = [{"affects": ["EURO", "YEN", "CHF"]}]
        out = news_symbols(events, SYMBOLS)
        assert "EURUSD" in out and "USDJPY" in out
        # USDCHF nicht handelbar in dieser Liste -> kein Fantasie-Treffer
        assert "USDCHF" not in out

    def test_generic_tokens_ignored(self):
        assert news_symbols([{"affects": ["USD", "CRYPTO", "ALL"]}], SYMBOLS) == []


class TestPromptFraming:
    def test_analysis_prompts_setup_as_weighted_factor(self):
        from services.ai_engine import ANALYSIS_SYSTEM, ANALYSIS_SYSTEM_LEAN
        assert "SETUP-PFLICHT" not in ANALYSIS_SYSTEM
        assert "SETUP-WAHL" in ANALYSIS_SYSTEM
        assert "Multi-Asset" in ANALYSIS_SYSTEM
        assert "gelernte Setup-Bilanz" in ANALYSIS_SYSTEM_LEAN

    def test_deep_prompts_multiasset_and_own_baseline(self):
        from services.ai_engine import DEEP_ANALYSIS_SYSTEM, DEEP_UPDATE_SYSTEM
        assert "Multi-Asset" in DEEP_ANALYSIS_SYSTEM
        assert "EIGENEN Volatilitäts-Baseline" in DEEP_ANALYSIS_SYSTEM
        assert "Multi-Asset" in DEEP_UPDATE_SYSTEM
        assert "Krypto-Daytrading-Plattform" not in DEEP_ANALYSIS_SYSTEM

    def test_playbook_context_mentions_weighting_rule(self):
        import inspect
        from services import ai_playbook
        src = inspect.getsource(ai_playbook.context_text)
        assert "GEWICHTUNG" in src and "setup_weighting" in src
