"""Regression 07.09.2026 (Runde 4): Aktiv-Filter für Paper-Badge/Strategie-
Vergleich, Lektions-Regel 'Coin-Ranglisten', tote OpenRouter-Slugs,
Rollen-Presets ohne Cerebras + Fallback-2-Ergänzung. Reine Unit-Tests."""
from services import active_scope
from services import ai_master_prompt as mp
from services import ai_providers, ai_roles


# ---------------- active_scope ----------------
DOCS = [
    {"_id": "ai_trader_BTCUSDT", "config": {"mode": "paper"}},
    {"_id": "ai_trader_ETHUSDT", "config": {"mode": "off"}},
    {"_id": "scalping_BTCUSDT", "config": {"mode": "live"}},
    {"_id": "ema_pullback_scalping_SOLUSDT", "config": {"mode": "paper"}},
    {"_id": "broken", "config": None},
]


def test_active_keys_from_docs_only_paper_or_live():
    keys = active_scope.active_keys_from_docs(DOCS)
    assert keys == {"ai_trader_BTCUSDT", "scalping_BTCUSDT", "ema_pullback_scalping_SOLUSDT"}


def test_cache_overrides_db_state():
    cached = {"ai_trader_ETHUSDT": {"mode": "paper"}, "ai_trader_BTCUSDT": {"mode": "off"}}
    keys = active_scope.active_keys_from_docs(DOCS, cached)
    assert "ai_trader_ETHUSDT" in keys and "ai_trader_BTCUSDT" not in keys


def test_is_active_handles_underscore_strategy_ids():
    keys = active_scope.active_keys_from_docs(DOCS)
    assert active_scope.is_active(keys, "ema_pullback_scalping", "SOLUSDT")
    assert not active_scope.is_active(keys, "scalping", "SOLUSDT")
    assert not active_scope.is_active(keys, "ai_trader", "ETHUSDT")
    assert not active_scope.is_active(keys, None, "BTCUSDT")


def test_filter_active_keeps_manual_trades_and_drops_inactive():
    keys = active_scope.active_keys_from_docs(DOCS)
    trades = [
        {"strategy_id": "ai_trader", "symbol": "BTCUSDT", "realized_pnl": 1},
        {"strategy_id": "ai_trader", "symbol": "ETHUSDT", "realized_pnl": -5},
        {"strategy_id": "ai_trader", "symbol": "XRPUSDT", "realized_pnl": -7},
        {"strategy_id": "external", "symbol": "QQQUSDT", "manual_trade": True},
        {"strategy_id": "scalping", "symbol": "BTCUSDT", "external_adopted": True},
    ]
    kept = active_scope.filter_active(trades, keys)
    assert [t["symbol"] for t in kept] == ["BTCUSDT", "QQQUSDT", "BTCUSDT"]
    strict = active_scope.filter_active(trades, keys, keep_manual=False)
    assert len(strict) == 2


# ---------------- Lektions-Regel: Coin-Ranglisten ----------------
def test_coin_ranking_lesson_detected_and_blocked():
    title = "AVAX, ETH, POL, DOT, DOGE, BNB priorisieren – ADA, SOL, QQQ, SPY meiden"
    detail = "Aktualisierte 60-Tage-Daten: PnL positiv für AVAX (+124,59) ..."
    assert mp.is_coin_ranking(title, detail)
    ok, why = mp.check_lesson_rules(mp.DEFAULT_RULES, title, detail)
    assert not ok and "Coin-Rangliste" in why


def test_coin_ranking_rule_can_be_disabled():
    rules = {**mp.DEFAULT_RULES, "block_coin_ranking_lessons": False}
    ok, _ = mp.check_lesson_rules(rules, "AVAX, ETH, POL priorisieren – SOL meiden", "")
    assert ok
    assert mp.normalize_rules({"block_coin_ranking_lessons": False})["block_coin_ranking_lessons"] is False
    assert mp.normalize_rules({})["block_coin_ranking_lessons"] is True


def test_asset_lessons_with_mechanism_are_not_rankings():
    # Belege im Detail (viele Ticker) dürfen nicht anschlagen – nur der Titel zählt.
    title = "Forex- und Nicht-Krypto-Signale nur mit Zusatzbestätigung"
    detail = "SILVER LONG -11,46 USDT, OIL SHORT -2,46 USDT, EURUSD LONG -5,80, USDJPY ..."
    assert not mp.is_coin_ranking(title, detail)
    assert mp.check_lesson_rules(mp.DEFAULT_RULES, title, detail)[0]
    # Ein einzelner Coin mit Mechanismus ist erlaubt (kein Ranking)
    assert not mp.is_coin_ranking("DOGE in der Asia-Session meiden (Spread > 0.1 %)")
    # Indikator-Kürzel sind keine Ticker
    assert not mp.is_coin_ranking("Bei ATR, RSI und MACD-Divergenz Longs bevorzugen")


def test_rules_text_and_labels_mention_new_rule():
    assert "Coin-Ranglisten" in mp.rules_text(mp.DEFAULT_RULES)
    assert "block_coin_ranking_lessons" in mp.RULE_LABELS


def test_lesson_policy_default_consistent_and_legacy_recognised():
    pol = mp.DEFAULT_LESSON_POLICY
    assert "10. KEINE Coin-Ranglisten" in pol and "11. Lektionen sind Handlungsregeln" in pol
    # Regel 4 widerspricht Regel 9 nicht mehr (Hebel ausgenommen)
    rule4 = [l for l in pol.split("\n") if l.startswith("4.")][0]
    assert "Hebel ist davon ausgenommen" in rule4
    assert pol.strip() not in mp.LEGACY_LESSON_POLICIES
    legacy = next(iter(mp.LEGACY_LESSON_POLICIES))
    assert legacy.startswith("1. Datenbasis") and "9. KEINE Hebel-Deckel" in legacy


# ---------------- Provider-Katalog / Migrationen ----------------
def test_dead_openrouter_free_slugs_removed_and_migrated():
    for dead in ("openai/gpt-oss-20b:free", "nvidia/nemotron-nano-9b-v2:free"):
        assert dead not in ai_providers.ALLOWED_MODELS["openrouter"]
        assert dead not in ai_providers.FALLBACK_ORDER["openrouter"]
        prov, model = ai_providers.migrate_model("openrouter", dead)
        assert prov == "groq" and model in ai_providers.ALLOWED_MODELS["groq"]
    # lebende Slugs unverändert
    assert ai_providers.migrate_model("openrouter", "google/gemma-4-31b-it:free") == \
        ("openrouter", "google/gemma-4-31b-it:free")


def test_role_presets_avoid_cerebras_and_have_valid_models():
    for role, cfg in ai_roles.ROLE_PRESETS.items():
        for pp, pm in (("provider", "model"), ("fallback_provider", "fallback_model"),
                       ("fallback2_provider", "fallback2_model")):
            prov, model = cfg.get(pp), cfg.get(pm)
            if not model:
                continue
            assert prov != "cerebras", f"{role}: Cerebras (402) in Voreinstellung"
            assert model in ai_providers.allowed_models(prov), f"{role}: {prov}/{model}"
    for role in ai_roles.FALLBACK2_REQUIRED_ROLES:
        assert ai_roles.ROLE_PRESETS[role].get("fallback2_model")


def test_fill_missing_fallback2_only_touches_empty_slots():
    mgr = ai_roles.AIRoleManager()
    tm = mgr.config["trade_manager"]
    tm.update({"provider": "openrouter", "model": "deepseek/deepseek-v4-flash",
               "fallback_provider": "groq", "fallback_model": "openai/gpt-oss-120b",
               "fallback2_provider": None, "fallback2_model": None, "user_configured": True})
    nw = mgr.config["news_watcher"]
    nw.update({"provider": "mistral", "model": "mistral-small-latest",
               "fallback_provider": "mistral", "fallback_model": "ministral-8b-latest",
               "fallback2_provider": None, "fallback2_model": None})
    # bereits vollständig konfigurierte Rolle bleibt unverändert
    before_learner = dict(mgr.config["learner"])
    filled = mgr._fill_missing_fallback2()
    assert set(filled) == {"trade_manager", "news_watcher"}
    assert tm["fallback2_model"] and (tm["fallback2_provider"], tm["fallback2_model"]) not in {
        ("openrouter", "deepseek/deepseek-v4-flash"), ("groq", "openai/gpt-oss-120b")}
    assert tm["provider"] == "openrouter" and tm["fallback_model"] == "openai/gpt-oss-120b"
    # Preset-Fallback2 von news_watcher ist mistral/ministral-8b = schon Fallback 1
    # -> nächstes freies Preset-Modell (gemini) statt Doppelung
    assert nw["fallback2_provider"] != "mistral"
    assert mgr.config["learner"] == before_learner
    assert mgr._fill_missing_fallback2() == []


def test_groq_replaced_in_large_prompt_roles_only():
    mgr = ai_roles.AIRoleManager()
    an = mgr.config["analyst"]
    an.update({"provider": "openrouter", "model": "nvidia/nemotron-3-super-120b-a12b:free",
               "fallback_provider": "groq", "fallback_model": "openai/gpt-oss-120b",
               "fallback2_provider": "gemini", "fallback2_model": "gemini-3.5-flash-lite"})
    tm_before = dict(mgr.config["trade_manager"])
    swapped = mgr._replace_small_budget_models()
    assert "analyst" in swapped
    assert an["fallback_provider"] != "groq" and an["fallback_model"]
    # kein Duplikat in der Kette
    chain = [(an["provider"], an["model"]), (an["fallback_provider"], an["fallback_model"]),
             (an["fallback2_provider"], an["fallback2_model"])]
    assert len(set(chain)) == 3
    assert mgr.config["trade_manager"] == tm_before  # kleine Prompts: Groq bleibt
    for role in ai_roles.LARGE_PROMPT_ROLES:
        for pp in ("provider", "fallback_provider", "fallback2_provider"):
            assert ai_roles.ROLE_PRESETS[role].get(pp) != "groq"
