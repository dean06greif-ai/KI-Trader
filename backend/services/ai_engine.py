"""
AI Trading Engine ("KI Trader")
- Periodically sends multi-timeframe market snapshots + crypto news + user chat
  directives to a configurable LLM (Gemini, Groq, OpenRouter/Grok, Mistral).
- The LLM returns structured trade decisions (LONG/SHORT/HOLD + confidence +
  SL/TP suggestions + reasoning). Actionable decisions are emitted as signals
  through the normal signal/auto-trade pipeline (strategy_id "ai_trader").
- Provides a multi-turn chat so the user can give the AI instructions
  ("achte auf BTC-Support bei 60k") that flow into the next analysis.

Provider (alle kostenlos in ihren Free-Tiers, deploybar auf Render):
  - Google Gemini      -> GEMINI_API_KEY  (google-genai SDK)
  - Groq (Llama, Qwen) -> GROQ_API_KEY    (OpenAI-kompatibel)
  - OpenRouter (Grok, DeepSeek, Llama Free) -> OPENROUTER_API_KEY
  - Mistral            -> MISTRAL_API_KEY (OpenAI-kompatibel)

Der Fallback bei Rate-Limit bleibt innerhalb des ausgewählten Providers.
"""
import os
import json
import re
import time
import uuid
import hashlib
import asyncio
import logging
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Callable
try:
    from zoneinfo import ZoneInfo
except ImportError:  # Python <3.9 fallback (nicht relevant für Render, aber safe)
    from backports.zoneinfo import ZoneInfo  # type: ignore

from dotenv import load_dotenv
load_dotenv()

from core import timeutil
from core import market_hours
from services.timeframes import aggregate_candles
from services import session_levels
from services import range_analysis
from services import runner_policy
from services.technical_indicators import TechnicalIndicators
from services.news_feed import news_feed
from services import macro_context
from services import liquidity_data
from services import liquidity_levels
from services import key_level_limits
from services.ai_knowledge import PLATFORM_KNOWLEDGE, tunable_spec_text, validate_changes
from services.ai_master_prompt import master_prompt
from services.ai_strategy_lab import strategy_lab
from services import ai_schedule
from services import ai_validation
from services.ai_validation import validation_gate
from services import ai_providers
from services import policy_fingerprint
from services.policy_lab import policy_lab
from services import ai_playbook
from services import setup_asset_class
from services import setup_capital
from services import setup_weighting
from services import position_sizing
from services import slippage_guard
from services import smc_zones
from services import sweep_trigger
from services import tf2_signal
from services import setup_review
from services.ai_roles import role_manager
from services.ai_json import parse_json_lenient
from services.ai_engine_context import AIEngineContextMixin
from services.ai_engine_governance import AIEngineGovernanceMixin, OPINION_SYSTEM  # noqa: F401 (Re-Export)
from services.ai_engine_housekeeping import AIEngineHousekeepingMixin, SUMMARY_SYSTEM  # noqa: F401 (Re-Export)

logger = logging.getLogger(__name__)

BERLIN_TZ = ZoneInfo("Europe/Berlin")

DEFAULT_AI_CONFIG = {
    "enabled": False,
    "interval_min": 10,
    # Zeitplan der regelmäßigen Analyse: Fenster mit eigenem Intervall,
    # z.B. nachts alle 30 min, 15-18 Uhr alle 5 min (services/ai_schedule.py).
    "schedule": [],
    "min_confidence": 65,
    "provider": "gemini",
    "model": "gemini-3.5-flash",
    "news_enabled": True,
    # Externer Makro-Kontext (Key-Levels, Funding/OI, Makro-Kalender, DXY/Yield,
    # BTC-Dominanz, Trump/Truth-Social) — pro Analyse-Zyklus über get_macro_context().
    "macro_enabled": True,
    # Coins, für die pro Zyklus Key-Levels + Funding/OI geholt werden (kompakt ~2 KB).
    "macro_symbols": ["BTCUSDT", "ETHUSDT", "SOLUSDT"],
    # Liquiditäts-/Liquidations-Kontext (Eigenbau-Heatmap, Orderbook-Wände,
    # Long/Short-Ratio, OI-Trend, eigene "Liquidity Levels") – frei & keyless.
    "liquidity_enabled": True,
    "liquidity_symbols": ["BTCUSDT", "ETHUSDT", "SOLUSDT"],
    # Feinsteuerung der Liquiditäts-Daten:
    # use_liquidation_data = ECHTE Daten (Long/Short-Ratio, OI, Orderbook-Wände,
    #   Live-Liquidationen der Börsen) – Standard AN.
    # use_heatmap_data = MODELLIERTE Liq-Cluster (reine Formel Preis ± 1/Hebel,
    #   KEINE gemessenen Daten) – Standard AUS: sie existieren rechnerisch für
    #   jeden Coin und haben die KI in Fade-Trades an erfundenen Levels gelockt.
    "use_liquidation_data": True,
    "use_heatmap_data": False,
    "cooldown_min": 45,
    # ---- Autonomie-Leitplanken (Self-Tuning-Guard): Spanne, in der die KI
    # ihre eigenen Engine-Werte (min_confidence, cooldown_min) bei
    # autonomy=auto selbst anwenden darf. Außerhalb wird die Änderung nur
    # Vorschlag (needs_confirmation). Die Spanne selbst darf NUR der Trader
    # ändern (nicht in der KI-Whitelist). Hintergrund: RCA 14.08. – die KI
    # hatte min_confidence per Self-Tuning auf 85 geschraubt und sich damit
    # selbst stranguliert (Prompts kalibrieren A-Setups auf 70–85).
    "tune_conf_min": 55,
    "tune_conf_max": 75,
    "tune_cooldown_max": 45,
    # Autonomie-Spanne Richtungs-Guard: KI darf max_same_direction nur
    # innerhalb dieser Grenzen selbst setzen (außerhalb/0 = nur Vorschlag)
    "tune_guard_min": 1,
    "tune_guard_max": 6,
    # Maker-Order-Modus: unkritische KI-Entries als Post-Only-Limit (Maker-Fee)
    "maker_mode": False,
    "maker_wait_sec": 45,
    "maker_suspended_until": None,
    # ---- Datensammel-Modus (Phase 4): Entscheidungen unterhalb der
    # Live-Schwelle (aber >= collection_min_confidence) werden als PAPER-
    # Trades ausgeführt und mit data_collection=true markiert – nie live,
    # kein Kapital, keine Telegram-Meldungen. Ziel: deutlich mehr gelabelte
    # Trades für das ML-Training (separat gewichtet, keine Vermischung).
    "collection_enabled": True,
    "collection_min_confidence": 60,
    "collection_cooldown_min": 30,
    "collection_max_same_direction": 5,
    "collection_max_per_coin": 2,
    # Live-Gate-Bypass: hochkonfidente Setups dürfen begrenzt live gehen, auch
    # wenn das Setup noch nicht 'live-reif' ist (paar Live-Trades pro Tag,
    # statt alles in die Paper-Datensammlung umzuleiten).
    "live_gate_bypass_enabled": False,  # T04: Default AUS – Bypass ist Opt-in
    "live_gate_bypass_margin": 5,    # Konfidenz-Aufschlag über min_confidence
    "live_gate_bypass_per_day": 2,   # max. Bypass-Live-Trades pro Tag
    # ---- Low-Vol-Market-Block (Baustein A): liegt die ATR des Signals (% vom
    # Preis) unter der Schwelle, sind Market-Entries live gesperrt – es wird
    # ein Maker-Entry erzwungen, OHNE Market-Fallback. Schwelle 0 = aus.
    "low_vol_market_block_enabled": True,
    "low_vol_atr_threshold_pct": 0.10,
    # ---- Setup-Gewichtung mit Live-Vorrang (Audit 2.5): ab 5 echten Live-
    # Trades zählt die Live-Bilanz des Setups fürs Konfidenz-Gewicht; ohne
    # genug Live-Daten wirkt die Paper-/Misch-Bilanz nur als Prior mit Abzug
    # (kann dämpfen, wertet nie über neutral auf). Aus = altes Verhalten.
    "setup_weight_live_pref": True,
    # ---- Erwartungswert-Gewichtung (Audit 2.6): Gewicht aus dem geschrumpften
    # Netto-R-Mittel je riskiertem USDT (pnl/risk_usdt) statt Trefferquote;
    # WR nur Tiebreaker. Aus = alte WR-basierte Gewichtung.
    "setup_weight_ev": True,
    # Setup-Reife-Gate: LIVE nur für Setups mit genug echten Daten (Playbook-
    # Urteil 'bewährt'/'neutral'). Neue/unreife Setups laufen auch bei hoher
    # Konfidenz zuerst als Paper-Datensammlung weiter. Greift nur, wenn der
    # Trade wirklich live liefe (Modus live) – Paper-Modus bleibt unverändert.
    "setup_live_gate": True,
    # ---- Fee-Wächter: Physik-Grenze statt Stil-Vorgabe. Ein KI-Trade wird
    # nur eröffnet, wenn seine SL-Distanz mind. fee_guard_mult × Roundtrip-
    # Fees (2 × fee_percent des Coins, Standard 0,12%) beträgt. Blockt nur
    # mathematisch garantierte Fee-Verlierer – Scalpen bleibt sonst frei.
    # Gilt für alle KI-Trades inkl. Sammel-Trades; NICHT in der KI-Whitelist.
    # Default 2.5× (vorher 4×): reale Bitunix-Gebühren liegen durch VIP-Level
    # und Discount-Voucher meist unter der Standard-Taker-Fee.
    "fee_guard_enabled": True,
    "fee_guard_mult": 2.5,
    # V2: zusätzliches dynamisches SL-Minimum = fee_guard_atr_mult × 1m-ATR%.
    # Verhindert Stops im Markt-Rauschen (KI klebte SLs ans 0,5%-Fee-Minimum).
    "fee_guard_atr_mult": 2.5,
    # V3: bei hohem CRV (>=2 / >=3) darf das Fee-Minimum um 15% / 25%
    # unterschritten werden – knappe, aber fette Setups werden fair bewertet.
    "fee_guard_crv_relax": True,
    # Stale-Price-Guard: Entry ablehnen, wenn die letzte Kerze älter ist (min; 0=aus).
    # RCA 17.08.: EURUSD-Doppel-Trade nutzte einen ~45 min alten Preis (Feed hing).
    "stale_price_max_min": 10,
    # Rohstoff-Feeds (GOLD/SILVER/OIL) liefern nur ~alle 11 min einen Kurs und
    # liefen mit dem 10-min-Limit in systematische Blocks (Befund 26.08.: 87x
    # "11 min alt, Limit 10"). Eigenes, weiteres Limit je Rohstoff (min; 0=aus).
    "stale_price_max_min_commodity": 20,
    # Duplikat-Guard: kein identischer Symbol+Richtung-Einstieg, solange ein
    # offener KI-Trade jünger als dieses Fenster ist (Befund 26.08.: zwei fast
    # identische USDJPY-SHORTs 15 min auseinander; der Sammel-Cooldown ist nur
    # In-Memory und überlebt keinen Deploy). 0 = aus.
    "dup_entry_window_min": 30,
    # Momentum-News-Bremse (datenbasiert 26.08.): momentum_news ist gesamt das
    # stärkste Setup (+190 USDT), verliert aber GENAU im Konfidenzband 70-74
    # massiv (n=75, 43% Win, -201 USDT; 60-64: +185, 75-79: +157). Live-Entries
    # in diesem Band werden ausgelassen, Sammel-Trades messen weiter. [] = aus.
    "momentum_news_conf_block": [70, 74],
    # Regime-Sperrfilter (Default AUS, erst nach 1-2 Wochen sauberer Nach-Guard-
    # Daten aktivieren): blockt LIVE-Neueinstiege in den gelisteten Regimen;
    # Sammel-Trades laufen bewusst weiter, damit die Statistik den Filter beweist.
    "regime_block_enabled": False,
    "regime_block_list": ["range_ruhig"],
    # Max. gleichzeitig offene KI-Trader-Trades pro Coin (1–5). Default 1 =
    # bisheriges Verhalten (strikt ein Trade pro Coin). Nur der KI-Trader nutzt
    # dieses Limit; alle anderen Strategien bleiben bei strikt 1 Trade pro Coin.
    "max_trades_per_coin": 1,
    # Max. Kapital (USDT Margin) pro KI-Trade. 0 = aus -> Coin-Trade-Settings
    # gelten wie bisher. Wenn > 0, entscheidet die KI PRO TRADE selbst, wie viel
    # Kapital (10-100% dieses Betrags) sie einsetzt – nicht automatisch immer das Maximum.
    "max_capital_per_trade": 0,
    # Positionsgröße (services/position_sizing.py): "legacy" = Coin-max_capital ×
    # capital_pct × ML-Faktor (altes Verhalten) | "risk" = Risiko-Budget in % der
    # Equity / SL-Abstand, Hebel mit Liq hinter dem SL (Boot-Migration setzt risk).
    **position_sizing.DEFAULTS,
    # Slippage-Wächter: Momentum-Setups live nur, wenn die gemessene
    # Entry-Slippage des Coins im Rahmen bleibt (Befund 02.09.: squeeze_breakout
    # Paper 48 % / live 7 % Winrate – Ausführung, nicht Strategie).
    **slippage_guard.DEFAULTS,
    # Sweep-Trigger (services/sweep_trigger.py): lokaler 1m-Wick-Sweep-Detektor
    # -> gezielte Einzel-Symbol-Analyse mit Tagesbudget + Symbol-Cooldown.
    **sweep_trigger.DEFAULTS,
    # TF2-Trigger (services/tf2_signal.py): Trendfolge-2-Signal (MACD-Kreuz +
    # Impuls + Volumen, 5m) -> gezielte Einzel-Symbol-Analyse für trend_follow2.
    **tf2_signal.DEFAULTS,
    # Einstellungs-Autonomie: darf die KI ihre Trade-Settings ändern?
    # off = nie | suggest = Vorschläge, Trader bestätigt | auto = sofort anwenden
    "autonomy": "suggest",
    # Selbst-Lernen aus Signal-/Trade-Ergebnissen
    "learning_enabled": True,
    "learn_on_trade_close": True,
    "learning_lookback_days": 14,
    "max_lessons": 10,
    # KI-berechnete SL/TP-Levels direkt für die Order nutzen (statt Coin-Trade-Settings)
    "use_ai_levels": False,
    # Übergeordnete Swing-Trades: eigene Kategorie mit niedrigem Hebel und
    # weiten Zielen, parallel zu kurzfristigen (auch gegenläufigen) Scalps.
    "swing_enabled": True,
    "swing_max_leverage": 8,
    # Runner-Absicherung: Runner-Trades (nur Teil-TP, Rest läuft mit Trailing)
    # sichern sich nach der Gewinnsicherung automatisch ab: SL im Gewinn +
    # Marge maximal freisetzen (Hebel steigt auf den Deckel) -> risikofreier
    # Runner bei voller Positionsgröße, Kapital wird für andere Trades frei.
    "runner_secure_enabled": True,
    "runner_scalp_enabled": True,        # Runner auch für Scalp-/News-Trades
    "runner_scalp_news_only": True,      # ... nur wenn News-getrieben (news_impact != neutral)
    "runner_trend_setups": True,         # Runner-Test bei trend_follow/trend_follow2 auch ohne News
    "runner_trend_share": 0.5,           # ... A/B-Anteil der Trendfolge-Scalps, die als Runner laufen
    "runner_secure_trigger_pct": 30.0,   # ab X% Gewinn auf die Marge
    "runner_secure_max_leverage": 100,   # Ziel-Hebel beim Freisetzen (max 200)
    # Gruppen-Analyse: Krypto / Forex / Indizes+Rohstoffe in getrennten
    # LLM-Läufen für tiefere, asset-spezifischere Begründungen.
    "group_analysis": True,
    # Lean-Prompt: statische Info-Blöcke (Plattform-Wissen, Parameter der
    # anderen Strategien) aus jedem Analyse-Lauf weglassen – spart Tokens/Kosten
    # ohne die Entscheidungsqualität zu beeinflussen.
    "lean_prompt": True,
    # Smart-Skip: geplanten LLM-Lauf einer Gruppe überspringen, wenn sich der
    # Markt seit der letzten Analyse kaum bewegt hat, keine Position offen ist
    # und die letzte Entscheidung überall HOLD war (max. 2 Skips in Folge;
    # manuelle Analysen laufen immer). Spart LLM-Calls/Kosten.
    "smart_skip": True,
    "smart_skip_move_pct": 0.15,
    # Trade-Rahmen (global fürs ganze KI-Team, gilt für jeden Trade):
    # CRV-Spanne (TP1 vs. SL) in der sich die KI frei bewegen darf.
    # CRV-Rahmen (TP1 relativ zum SL), von der KI pro Trade frei innerhalb der
    # Spanne wählbar. crv_max Standard 4 (User 15.06.): deckelt unrealistisch
    # weite TPs; 0 = keine Obergrenze bleibt wählbar. Prod-Hinweis: eine bereits
    # gespeicherte 0 in der Prod-Config bleibt 0 – einmal im Setup umstellen.
    "crv_min": 1.2,
    "crv_max": 4.0,
    # Hebel-Modus: "coin" = Coin-Trade-Settings entscheiden (bisheriges
    # Verhalten) | "auto" = KI wählt pro Trade frei bis lev_auto_max |
    # "fixed" = immer fester Hebel lev_fixed.
    "lev_mode": "coin",
    "lev_auto_max": 25,
    "lev_fixed": 10,
    # Diversifikations-Guards (technisch erzwungen, gegen Klumpen-Trades):
    # max. gleichzeitig offene KI-Trades in DIESELBE Richtung (0 = aus) und
    # Mindestabstand (%) zwischen Entries auf demselben Symbol + Richtung.
    "max_same_direction": 3,
    "min_entry_distance_pct": 0.5,
    # BTC/ETH/SOL als EIN Richtungs-Risiko zählen (Korrelations-Guard)
    "correlation_guard": True,
}

# Kataloge, Keys (inkl. Backup-Keys) & Modell-Gewichte leben zentral in
# services/ai_providers.py – hier nur Aliase für Rückwärtskompatibilität.
ALLOWED_MODELS = ai_providers.ALLOWED_MODELS
OPENAI_COMPAT_PROVIDERS = ai_providers.OPENAI_COMPAT_PROVIDERS
FALLBACK_ORDER = ai_providers.FALLBACK_ORDER

DEEP_ANALYSIS_SYSTEM = (
    "Du bist der 'Tiefen-Analyst' im KI-Team einer Multi-Asset-Trading-Plattform "
    "(Krypto-Perps, Indizes, Rohstoffe, Forex). "
    "Du erstellst eine SEHR gründliche Marktanalyse (kein direkter Trade-Auftrag): "
    "Makro-Lage, Schlüssel-Levels, Szenarien pro Asset, Risiken, konkrete Empfehlungen "
    "für den regulären Analysten (der periodisch tradet). Behandle Nicht-Krypto-Assets "
    "GLEICHWERTIG: Bewerte ihre Handelbarkeit an der EIGENEN Volatilitäts-Baseline des "
    "Assets und den Klassen-Grenzen (Forex/Indizes bewegen sich in Prozent klein, sind "
    "aber mit engen Klassen-SLs handelbar) – NICHT am absoluten ATR-Vergleich mit BTC. "
    "Meldet der Volatilitäts-Radar oder ein News-Ereignis erhöhte Aktivität bei Gold/Öl/"
    "Indizes/Forex, analysiere diese Assets genauso tief wie Krypto. "
    "Nutze ALLE übergebenen Daten "
    "inkl. Wirtschaftskalender, News-Wächter-Ereignisse und Performance der anderen "
    "Strategien der Plattform. WICHTIG: Das 'outlook'-Array MUSS für JEDES im Prompt "
    "übergebene Asset genau einen Eintrag enthalten – kein Asset auslassen. "
    "Antworte AUSSCHLIESSLICH mit validem JSON ohne Markdown:\n"
    '{"report": "8-15 Sätze tiefe Marktanalyse auf Deutsch", '
    '"outlook": [{"symbol": "BTCUSDT", "bias": "bullish|bearish|neutral", '
    '"key_levels": "kompakt", "szenario": "1-2 Sätze"}, "… ein Eintrag PRO Asset"], '
    '"risks": ["Risiko 1", "Risiko 2"], '
    '"recommendations": ["konkrete Empfehlung für den Analysten"]}'
)

DEEP_UPDATE_SYSTEM = (
    "Du bist der 'Tiefen-Analyst' im KI-Team einer Multi-Asset-Trading-Plattform "
    "(Krypto, Indizes, Rohstoffe, Forex). "
    "Dies ist ein KOMPAKTES UPDATE zu deiner letzten Tiefenanalyse – KEINE neue "
    "Komplettanalyse. Analysiere AUSSCHLIESSLICH, was sich durch das übergebene "
    "News-Ereignis GEÄNDERT hat (Bias-Wechsel, neue Levels, neue Risiken). "
    "Was unverändert ist, erwähnst du gar nicht oder in maximal einem Halbsatz. "
    "Halte dich extrem kurz – Qualität vor Länge. "
    "WICHTIG: Das 'outlook'-Array MUSS für JEDES im Prompt übergebene Asset genau "
    "einen Eintrag enthalten. Antworte AUSSCHLIESSLICH mit validem JSON ohne Markdown:\n"
    '{"report": "3-6 Sätze Update auf Deutsch: was hat sich geändert und warum", '
    '"outlook": [{"symbol": "BTCUSDT", "bias": "bullish|bearish|neutral", '
    '"key_levels": "kompakt", "szenario": "1 Satz"}], '
    '"risks": ["nur NEUE Risiken"], '
    '"recommendations": ["nur geänderte Empfehlungen"]}'
)

ANALYSIS_SYSTEM = (
    "Du bist ein erfahrener Multi-Asset-Daytrading-Analyst (Krypto, Indizes, Rohstoffe, "
    "Forex) und triffst eigenständige "
    "Trading-Entscheidungen für ein automatisiertes System. Du bekommst Multi-Timeframe-"
    "Marktdaten, aktuelle News-Schlagzeilen, offene Positionen und Anweisungen des Traders. "
    "Sei diszipliniert: Trade NUR bei klarer Edge, sonst HOLD. Sei ehrlich mit der Konfidenz. "
    "Bestimme je Asset ZUERST die aktuelle Marktphase (Trend/Range/Squeeze/News-getrieben) und "
    "suche den dazu passenden Edge. RANGE-TRADING: Liefern die Marktdaten einen 'Range-Check'-"
    "Block (Seitwärtsrange mit mehrfachen Touches), ist ein range_fade-Setup valide, sobald eine "
    "Wick-Rejection an der Range-Grenze vorliegt: Entry an der Grenze, SL knapp dahinter, "
    "TP zur Range-Mitte bzw. Gegenseite. Ohne Wick-Rejection oder mitten in der Range: HOLD. "
    "Bei HOLD nenne im reasoning kurz den konkreten Grund "
    "(z.B. 'schlechtes Handelsfenster', 'Range ohne klares Level', 'Trade-Sperre bis klarer Edge') – "
    "confidence darf dann bewusst niedrig oder 0 sein. "
    "Berücksichtige Anweisungen des Traders IMMER mit höchster Priorität. "
    "Der MASTERPROMPT des Traders steht über allem – auch über deinen Lektionen. "
    "Du bist EINE Strategie von vielen auf dieser Plattform: dein Auftrag ist, den Markt "
    "perfekt zu kennen, passende Strategien anzuwenden oder neu zu entwickeln und dabei von "
    "den anderen Strategien und deren Parametern zu lernen. "
    "Antworte AUSSCHLIESSLICH mit validem JSON ohne Markdown, exakt in diesem Schema:\n"
    '{"market_overview": "2-4 Sätze Marktlage auf Deutsch", '
    '"decisions": [{"symbol": "BTCUSDT", "action": "LONG|SHORT|HOLD", '
    '"confidence": 0-100, "horizon": "scalp|swing", "runner": false, '
    '"setup": "' + ai_playbook.SETUP_ENUM + '", '
    '"sl_pct": 0.2-3.0, "tp1_pct": 0.3-4.0, "tpf_pct": 0.5-8.0, '
    '"capital_pct": 10-100, '
    '"entry_type": "market|limit", "limit_price": null, "limit_valid_min": "15-480 (scalp) | bis 2880 (swing)", '
    '"cancel_limit": false, '
    '"news_impact": "positive|negative|neutral", "strategy_candidate_id": null, '
    '"reasoning": "1-2 Sätze auf Deutsch", '
    '"size_reason": "1 kurzer Satz: warum diese Positionsgröße (capital_pct/Hebel)", '
    '"levels_reason": "1 kurzer Satz: warum SL und TP genau dort liegen"}], '
    '"new_strategies": [{"name": "...", "thesis": "...", "rules_text": "...", '
    '"symbols": ["BTCUSDT"], "learned_from": "..."}], '
    '"new_setups": [{"id": "snake_case_id", "desc": "Einstieg/SL/TP/Timeframe in 1-2 Sätzen", "trade_target": Trades/Woche}], '
    '"setup_revisions": [{"setup": "id", "asset_class": "crypto|indices|resources|forex", "desc": "...", "reason": "kurz", "trade_target": Trades/Woche}], '
    '"config_changes": [{"symbol": "BTCUSDT", "changes": {"leverage": 8}, "reason": "kurz"}]}\n'
    "TOKEN-SPARSAMKEIT: Bei HOLD nur symbol, action, confidence und ein reasoning von max. 8 Wörtern – "
    "alle anderen Felder (setup, sl/tp, capital_pct, size_reason, levels_reason, entry_type) WEGLASSEN. "
    "Leere Listen (new_strategies, new_setups, setup_revisions, config_changes) komplett weglassen. "
    "setup_revisions nur, wenn das Playbook ein rückgestuftes Setup für deine Klasse nennt. "
    "Regeln: sl_pct/tp1_pct/tpf_pct sind Prozent-Abstände vom aktuellen Preis. "
    "tp1_pct > sl_pct (CRV mind. 1.2), tpf_pct > tp1_pct. Für JEDES übergebene Symbol genau eine Entscheidung. "
    "ENTRY-ART: entry_type 'market' (Standard) = sofortiger Einstieg zum aktuellen Preis. "
    "entry_type 'limit' NUR, wenn ein klares Key-Level (Order-Block, POC/VAH/VAL, Range-Grenze, "
    "Liquidity Level aus den Marktdaten) in Reichweite liegt und ein Rücklauf dorthin wahrscheinlich "
    "ist: limit_price = das Level (LONG unter dem aktuellen Preis, SHORT darüber; max. 2.5% entfernt, "
    "bei swing max. 8%). sl_pct/tp1_pct/tpf_pct gelten dann ab limit_price. limit_valid_min = "
    "Gültigkeit in Minuten (scalp 15-480; bei horizon swing bis 2880 = 48h, ideal für Haupt-"
    "Unterstützungen/-Widerstände der 4h/1d-Zonen) – wähle sie selbst passend zum Setup; "
    "danach verfällt die Order automatisch und du bewertest im nächsten Zyklus neu. Pro Symbol und "
    "Richtung wartet höchstens EINE Limit-Order (eine neue ersetzt die alte). Steht unter WARTENDE "
    "LIMIT-ORDERS eine Order, die nicht mehr zur Marktlage passt, storniere sie mit cancel_limit=true "
    "(auch bei HOLD möglich). "
    "SETUP-WAHL: Deine EIGENE Analyse (Marktstruktur, Orderflow, Tiefenanalyse-Outlook) "
    "entscheidet – das Feld 'setup' benennt bei LONG/SHORT das Playbook-Setup, das deine Analyse "
    "am besten beschreibt (ein Label, KEIN Ersatz für die Analyse). Die gelernte Erfolgsbilanz "
    "des Setups justiert deine Konfidenz automatisch (bewährt hebt, schwach senkt – siehe "
    "GELERNTE SETUP-GEWICHTE im Playbook). Bevorzuge bewährte Setups, meide gesperrte, variiere "
    "statt immer dasselbe Muster; beschreibt KEIN Setup deine Idee, erzwinge keines, sondern "
    "schlage per 'new_setups' ein passendes vor; bei HOLD 'setup' weglassen. "
    "SL und TP gehören ZUM Setup (Mean-Reversion enge Ziele, "
    "Breakout/Squeeze weite) – nicht immer dieselben Standardwerte. "
    "HORIZON: 'scalp' (Standard) = kurz-/mittelfristig mit den normalen Bereichen oben. "
    "'swing' = übergeordneter, langfristiger Trade: niedriger Hebel (wird automatisch gedeckelt), "
    "weite Ziele erlaubt (sl_pct 0.5-12, tp1_pct 0.8-25, tpf_pct bis 60), 'runner': true bedeutet "
    "nach TP1 läuft der Rest ohne festes Endziel mit Trailing-Stop weiter; zusätzlich sichert sich "
    "ein Runner-Trade nach Erreichen der Gewinnschwelle automatisch ab (SL in den Gewinn + Marge "
    "wird maximal freigesetzt, der Hebel steigt bei gleicher Positionsgröße -> risikofreier Runner). "
    "Nutze runner=true gezielt, nur wo echtes Weiterlauf-Potenzial besteht. Auch bei horizon 'scalp' "
    "ist runner=true erlaubt, wenn der Trade News-getrieben ist (news_impact positive/negative): dann "
    "wird kein voller TP genommen, das Endziel liegt sehr weit und der SL wird an Key-Levels "
    "nachgezogen (mit Mindestabstand gegen Rauschen und vor der Liq). Ein Swing-Trade und "
    "kurzfristige Gegen-Scalps auf demselben Asset schließen sich NICHT aus – du darfst z.B. einen "
    "übergeordneten LONG halten und zwischenzeitliche Abwärtsbewegungen mit SHORT-Scalps handeln. "
    "Nutze swing nur bei klarer übergeordneter Struktur (Higher-Timeframe-Trend, Makro-These), "
    "nicht als Ausrede für weite Stops.\n"
    "DIVERSIFIKATION (weiche Regel): Eröffne nicht reflexartig viele Trades in dieselbe Richtung "
    "mit derselben Begründung – das bündelt das Risiko auf eine einzige Prognose. Prüfe die "
    "offenen Positionen und staffle/variiere bewusst; wenn du dennoch mehrere gleichgerichtete "
    "Trades willst, begründe das explizit. Kein hartes Verbot.\n"
    "BEGRÜNDUNGEN: Jedes 'reasoning' muss ASSET-SPEZIFISCH sein (konkrete Level, Struktur, "
    "Besonderheit des Assets) – KEINE Copy-Paste-Sätze über viele Assets hinweg. Wenn ein "
    "übergreifender Grund (z.B. Makro-Event) alle Assets betrifft, nenne ihn EINMAL in "
    "market_overview und schreibe im reasoning nur die asset-spezifischen Details. "
    "Staffle auch die confidence-Werte ehrlich pro Asset statt pauschal denselben Wert zu geben. "
    "capital_pct = wie viel Prozent deines Max-Kapitals pro Trade du einsetzen willst (10-100). "
    "Wähle capital_pct proportional zu deiner Überzeugung und dem Setup-Risiko – setze NICHT "
    "reflexartig immer 100, sondern staffle: schwächere Setups kleiner, A+-Setups größer. "
    "strategy_candidate_id nur setzen, wenn die Entscheidung zu einem Strategie-Kandidaten aus "
    "deinem Strategie-Labor gehört (Kandidaten in der Ghost-Phase werden automatisch nur "
    "simuliert). new_strategies nur bei einer wirklich neuen, begründeten Idee (sonst leere Liste). "
    "config_changes ist optional und NUR erlaubt, wenn der Prompt-Abschnitt EINSTELLUNGS-AUTONOMIE aktiv ist – "
    "sonst leere Liste. Nutze deine Performance-Statistik und gelernten Lektionen aktiv für bessere Entscheidungen.\n"
    "TIMEFRAME-DISZIPLIN: Gewichte 5m/15m-Struktur stärker als den 1m-Chart. RSI auf 1m ist "
    "Rauschen und allein KEIN Trade-Grund – nutze ihn höchstens fürs Entry-Timing; die Bestätigung "
    "kommt vom 5m/15m (Scalps) bzw. 1h+ (Swings). Läuft ein Setup wiederholt schlecht, wechsle "
    "bewusst die Auswertungs-Ebene (höherer Timeframe, andere Trigger) oder setze das Setup aus – "
    "das Playbook sperrt schwache Setups zusätzlich automatisch.\n"
    "SESSION-LEVELS & ZONEN: Die Marktdaten enthalten Highs/Lows der Asia-/London-/NY-Session und "
    "Umverteilungszonen (Volumen-Cluster). Nutze sie aktiv: Sweeps von Session-Hochs/-Tiefs sind "
    "bevorzugte liquidity_sweep-Trigger, Ausbrüche aus Umverteilungszonen bevorzugte "
    "breakout-Trigger; Entries mitten in einer Zone haben schlechtes CRV und sind zu meiden.\n"
    "KONFIDENZ-KALIBRIERUNG: HOLD ohne Edge ist richtig – aber wenn Struktur, Level und Trigger "
    "zusammenpassen, benenne das Setup und handle es mit ehrlicher Konfidenz (sauberes A-Setup = "
    "70-85, nicht chronisch 50-60). Dauer-HOLD über viele Zyklen trotz klarer Setups ist genauso "
    "ein Fehler wie Overtrading."
)

# Kompakte Variante des Analyse-Systemprompts (aktiv bei lean_prompt=AN):
# identisches JSON-Schema und identische Kernregeln, aber ohne ausschweifende
# Erklärtexte – spart pro Gruppen-Lauf mehrere hundert Tokens ohne die
# Entscheidungsqualität zu verändern.
ANALYSIS_SYSTEM_LEAN = (
    "Du bist ein disziplinierter Multi-Asset-Daytrading-Analyst (Krypto, Indizes, Rohstoffe, "
    "Forex) eines automatisierten Systems. "
    "Anweisungen des Traders und der MASTERPROMPT stehen über allem. "
    "Bestimme je Asset ZUERST die Marktphase (Trend/Range/Squeeze/News) und suche den dazu "
    "passenden Edge – trade NUR bei klarer Edge, sonst HOLD mit kurzem Grund im reasoning "
    "(z.B. 'schlechtes Handelsfenster', 'kein klares Level', 'Trade-Sperre bis klarer Edge'); "
    "confidence darf bei HOLD bewusst 0 sein. "
    "Range-Trading: 'Range-Check'-Block + Wick-Rejection an der Range-Grenze = valides "
    "range_fade-Setup (Entry an der Grenze, SL knapp dahinter, TP Range-Mitte/Gegenseite). "
    "Antworte AUSSCHLIESSLICH mit validem JSON ohne Markdown, exakt in diesem Schema:\n"
    '{"market_overview": "2-3 Sätze Marktlage auf Deutsch", '
    '"decisions": [{"symbol": "BTCUSDT", "action": "LONG|SHORT|HOLD", '
    '"confidence": 0-100, "horizon": "scalp|swing", "runner": false, '
    '"setup": "' + ai_playbook.SETUP_ENUM + '", '
    '"sl_pct": 0.2-3.0, "tp1_pct": 0.3-4.0, "tpf_pct": 0.5-8.0, '
    '"capital_pct": 10-100, '
    '"entry_type": "market|limit", "limit_price": null, "limit_valid_min": "15-480 (scalp) | bis 2880 (swing)", '
    '"cancel_limit": false, '
    '"news_impact": "positive|negative|neutral", "strategy_candidate_id": null, '
    '"reasoning": "1-2 Sätze auf Deutsch", '
    '"size_reason": "1 kurzer Satz: warum diese Positionsgröße (capital_pct/Hebel)", '
    '"levels_reason": "1 kurzer Satz: warum SL und TP genau dort liegen"}], '
    '"new_strategies": [{"name": "...", "thesis": "...", "rules_text": "...", '
    '"symbols": ["BTCUSDT"], "learned_from": "..."}], '
    '"new_setups": [{"id": "snake_case_id", "desc": "Einstieg/SL/TP/Timeframe in 1-2 Sätzen", "trade_target": Trades/Woche}], '
    '"setup_revisions": [{"setup": "id", "asset_class": "crypto|indices|resources|forex", "desc": "...", "reason": "kurz", "trade_target": Trades/Woche}], '
    '"config_changes": [{"symbol": "BTCUSDT", "changes": {"leverage": 8}, "reason": "kurz"}]}\n'
    "TOKEN-SPARSAMKEIT: Bei HOLD nur symbol, action, confidence + reasoning (max. 8 Wörter) – alle "
    "anderen Felder weglassen. Leere Listen weglassen. setup_revisions nur für ein im Playbook als "
    "RÜCKGESTUFT genanntes Setup deiner Klasse. "
    "Regeln: sl/tp-Prozente = Abstand vom aktuellen Preis; tp1_pct > sl_pct; tpf_pct > tp1_pct; "
    "für JEDES übergebene Symbol genau EINE Entscheidung. "
    "entry_type 'limit' nur mit klarem Key-Level (Order-Block/POC/VAH/VAL/Range-Grenze): "
    "limit_price = das Level (LONG darunter, SHORT darüber; max 2.5%, swing max 8% entfernt), "
    "sl/tp-Prozente gelten dann ab limit_price, limit_valid_min (scalp 15-480, swing bis 2880) "
    "wählst du selbst – danach "
    "Verfall + Neubewertung; sonst 'market'. cancel_limit=true storniert die wartende Limit-Order "
    "des Symbols (auch bei HOLD; siehe WARTENDE LIMIT-ORDERS). "
    "setup = das Playbook-Setup, das deine EIGENE Analyse am besten beschreibt (Pflichtfeld bei "
    "LONG/SHORT, aber Label statt Fessel: die gelernte Setup-Bilanz justiert deine Konfidenz "
    "automatisch; gesperrte nicht nutzen; passt keines: per 'new_setups' vorschlagen; "
    "SL/TP passend zum Setup wählen statt Standardwerte; bei HOLD weglassen). "
    "horizon 'swing' = übergeordneter Trade (Hebel wird automatisch gedeckelt, weite Ziele erlaubt: "
    "sl_pct 0.5-12, tp1_pct 0.8-25, tpf_pct bis 60); runner=true bei swing oder bei News-getriebenen "
    "Scalps (news_impact positive/negative): Rest läuft nach TP1 "
    "mit Trailing weiter und wird nach der Gewinnschwelle automatisch risikofrei gestellt: SL im "
    "Gewinn + Marge freigesetzt). Nur nutzen, wo echtes Weiterlauf-Potenzial besteht. "
    "Swing-Position und gegenläufige Scalps auf demselben Asset sind erlaubt. "
    "Kein reflexartiges Bündeln vieler gleichgerichteter Trades mit derselben Begründung. "
    "reasoning IMMER asset-spezifisch (konkrete Level/Struktur), keine Copy-Paste-Sätze; "
    "confidence ehrlich pro Asset staffeln. capital_pct proportional zur Überzeugung (nicht immer 100). "
    "strategy_candidate_id nur für Kandidaten aus deinem Strategie-Labor. "
    "new_strategies nur bei wirklich neuer, begründeter Idee (sonst []). "
    "config_changes NUR wenn der Abschnitt EINSTELLUNGS-AUTONOMIE aktiv ist – sonst []. "
    "Nutze Performance-Statistik und Lektionen aktiv. "
    "TIMEFRAMES: 5m/15m-Struktur schlägt 1m; RSI(1m) ist nur Entry-Timing, nie der Trade-Grund; "
    "Swings auf 1h+ bestätigen; schlecht laufende Setups auf höhere Timeframes umstellen oder aussetzen. "
    "SESSION-LEVELS & ZONEN: Sweeps der Asia-/London-/NY-Session-Hochs/-Tiefs = bevorzugte "
    "liquidity_sweep-Trigger; Ausbrüche aus Umverteilungszonen (Volumen-Cluster) = bevorzugte "
    "breakout-Trigger; Entries mitten in einer Zone meiden. "
    "KONFIDENZ: sauberes A-Setup ehrlich mit 70-85 bewerten (nicht chronisch 50-60) – "
    "Dauer-HOLD trotz klarer Setups ist genauso ein Fehler wie Overtrading."
)

# --- Fix 0.4: Prompt-Versionierung -------------------------------------------
# Kurz-Hashes der statischen Analyse-Systemprompts (ändern sich automatisch bei
# jeder Prompt-Code-Änderung/Deploy). Zusammen mit master_prompt.version_hash()
# wird jede ai_decision einem exakten Prompt-Stand zuordenbar -> ML-Daten
# lassen sich nach Prompt-Version segmentieren.


def _prompt_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:10]


ANALYSIS_PROMPT_HASHES = {
    "full": _prompt_hash(ANALYSIS_SYSTEM),
    "lean": _prompt_hash(ANALYSIS_SYSTEM_LEAN),
}


def prompt_version_info(variant: str) -> Dict:
    """Versions-Fingerprint des kompletten Entscheidungs-Prompts.

    combined = ML-Groupby-Key; Einzelteile für gezielte Analysen:
    analysis = Hash des statischen Systemprompts (variant lean|full),
    master/master_v = Inhalts-Hash + laufende Version des Trader-MasterPrompts.
    """
    a_hash = ANALYSIS_PROMPT_HASHES.get(variant, "")
    m_hash = master_prompt.version_hash()
    return {
        "analysis": a_hash,
        "variant": variant,
        "master": m_hash,
        "master_v": master_prompt.version,
        "combined": f"{variant}-{a_hash}+{m_hash}",
    }


CHAT_SYSTEM_TEMPLATE = (
    "Du bist der 'KI Trader' – die integrierte Trading-KI einer Krypto-Daytrading-Plattform. "
    "Du analysierst periodisch alle Coins (Multi-Timeframe + News) und kannst automatisch Trades auslösen. "
    "Der Nutzer chattet hier mit dir, um dir Anweisungen zu geben (z.B. 'achte auf BTC-Support bei 60k', "
    "'sei heute defensiv', 'keine Shorts auf SOL'). Alle Nutzer-Nachrichten fließen automatisch als "
    "Direktiven in deine nächste Analyse ein – bestätige das, wenn dir jemand eine Anweisung gibt. "
    "Antworte kompakt, präzise und auf Deutsch. Nutze die Live-Daten unten für fundierte Antworten. "
    "Erfinde keine Zahlen.\n"
    "WICHTIG – ECHTE AKTIONEN: Gibt der Trader eine ausführbare Anweisung (Positionen schließen, "
    "Trade anpassen, Lektion anlegen/ändern/löschen, Einstellung ändern), führt das System sie REAL "
    "aus, BEVOR du antwortest. Die echten Ergebnisse stehen dann im Block 'SOEBEN REAL AUSGEFÜHRTE "
    "AKTIONEN'. Berichte EXAKT diese Ergebnisse. Behaupte NIEMALS, etwas geschlossen oder geändert "
    "zu haben, das dort nicht mit ✅ gelistet ist – fehlt der Block, wurde NICHTS ausgeführt: sage "
    "das ehrlich und bitte um eine präzisere Anweisung.\n\n"
    "=== AKTUELLER KONTEXT ===\n{context}\n\n"
    "=== BISHERIGER CHAT-VERLAUF ===\n{history}"
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _is_rate_limit_error(err: Exception) -> bool:
    """True wenn Gemini 429 / RESOURCE_EXHAUSTED / Quota-Fehler wirft."""
    s = str(err).lower()
    return any(k in s for k in ("429", "resource_exhausted", "quota", "rate limit", "ratelimit"))


def live_gate_bypass_ok(confidence, min_confidence, opened_today, cfg: Dict) -> bool:
    """Setup-Gate-Bypass (rein, testbar): hochkonfidente Setups dürfen begrenzt
    live gehen, auch wenn das Setup noch nicht 'live-reif' ist.
    T04: Default AUS – kontrollierte Live-Exploration nur nach explizitem Opt-in."""
    if not cfg.get("live_gate_bypass_enabled", False):
        return False
    try:
        margin = float(cfg.get("live_gate_bypass_margin", 5) or 0)
        per_day = int(cfg.get("live_gate_bypass_per_day", 2) or 0)
        conf = float(confidence or 0)
    except (TypeError, ValueError):
        return False
    return (per_day > 0 and int(opened_today) < per_day
            and conf >= float(min_confidence or 0) + margin)


class AIEngine(AIEngineContextMixin, AIEngineGovernanceMixin,
               AIEngineHousekeepingMixin):
    def __init__(self):
        self.config = dict(DEFAULT_AI_CONFIG)
        self.db = None
        self.scanner = None
        self.signal_cb: Optional[Callable] = None
        self.toggle_check: Optional[Callable] = None
        self.symbols: List[str] = []
        self.decisions: Dict[str, Dict] = {}
        self.last_run: Optional[str] = None
        self.next_run: Optional[str] = None
        self.active_window: Optional[str] = None
        self._day_risk_cache: Dict = {}
        self.last_error: Optional[str] = None
        self.running = False
        self._analyzing = False
        self._next_due = 0.0
        self._last_signal_ts: Dict[str, float] = {}
        self._group_skips: Dict[str, int] = {}
        self._review_last_check = 0.0
        self._last_ghost_ts: Dict[str, float] = {}
        # Eigener Cooldown für Datensammel-Trades (Phase 4)
        self._last_collection_ts: Dict[str, float] = {}
        # Modell, das aktuell benutzt wird (nach Fallback ggf. abweichend von cfg.model)
        self._effective_model: Optional[str] = None
        self._effective_provider: Optional[str] = None
        # Deep-Analysis-Scheduling: Slot ("HH:MM") -> Berlin-Datum des letzten Laufs
        self._deep_ran: Dict[str, str] = {}
        self.deep_last: Optional[str] = None
        self.deep_last_error: Optional[str] = None
        # Housekeeping-State (Europe/Berlin) – wird in settings/ai_trader_housekeeping persistiert.
        # Hour-Key im Format "YYYYMMDDHH", Date-Key "YYYY-MM-DD".
        self._last_cleanup_hour: Optional[str] = None
        self._last_reset_date: Optional[str] = None
        self._housekeeping_lock = asyncio.Lock()
        # Retry-Backoff für den täglichen Reset. Zählt Fehlversuche pro anstehendem
        # Vortag, damit ein LLM-/DB-Ausfall den Reset nicht dauerhaft verhindert –
        # aber auch nicht die Engine in einer Endlosschleife blockiert.
        self._reset_retry_day: Optional[str] = None
        self._reset_retry_count: int = 0
        # Lern-Modul (wird in setup() initialisiert, braucht db)
        self.learning = None

    @property
    def key(self) -> Optional[str]:
        """API-Key des aktuell konfigurierten Providers (primärer Key).

        Fällt auf den Key eines beliebigen konfigurierten Providers zurück –
        die Modell-Kette (services/ai_roles.chain) nutzt ohnehin alle Provider
        mit Key als letzte Fallback-Stufe. So blockiert eine Voreinstellung für
        einen Provider ohne Key den Betrieb nicht."""
        direct = ai_providers.primary_key(self.config.get("provider", "gemini"))
        if direct:
            return direct
        for prov, has_key in ai_providers.available_providers().items():
            if has_key:
                return ai_providers.primary_key(prov)
        return None

    @staticmethod
    def _provider_key(provider: str) -> Optional[str]:
        return ai_providers.primary_key(provider)

    def _available_providers(self) -> Dict[str, bool]:
        """True, wenn für den Provider ein API-Key gesetzt ist."""
        return ai_providers.available_providers()

    def setup(self, db, scanner, signal_cb, toggle_check, symbols: List[str]):
        self.db = db
        self.scanner = scanner
        self.signal_cb = signal_cb
        self.toggle_check = toggle_check
        self.symbols = symbols
        from services.ai_learning import AILearning  # lazy: vermeidet Zyklen
        self.learning = AILearning(self)

    # ---------------- config ----------------
    async def load_config(self):
        doc = await self.db.settings.find_one({"_id": "ai_trader_config"})
        if doc:
            doc.pop("_id", None)
            for k in DEFAULT_AI_CONFIG:
                if k in doc:
                    self.config[k] = doc[k]
            # Migration: unbekannten Provider oder ungültiges Modell -> zuerst
            # tote Slugs auf Nachfolger mappen, sonst Default (Gemini Flash)
            prov = self.config.get("provider")
            mod = self.config.get("model")
            if prov not in ALLOWED_MODELS or mod not in ALLOWED_MODELS.get(prov, []):
                new_prov, new_mod = ai_providers.migrate_model(prov, mod)
                if new_mod not in ALLOWED_MODELS.get(new_prov or "", []):
                    new_prov, new_mod = "gemini", "gemini-3.5-flash"
                logger.info(f"AI config: Modell migriert {prov}/{mod} -> {new_prov}/{new_mod}")
                self.config["provider"] = new_prov
                self.config["model"] = new_mod
                await self.db.settings.update_one(
                    {"_id": "ai_trader_config"},
                    {"$set": {"provider": new_prov, "model": new_mod}},
                    upsert=True,
                )
            # Einmalige Migration (User 15.06.): gespeichertes crv_max=0 (alte
            # Voreinstellung "keine Obergrenze") -> neuer Standard 4. Wer danach
            # bewusst wieder 0 wählt, bleibt bei 0 (Marker verhindert Wiederholung).
            if not doc.get("crv_max_migrated_v1") and float(doc.get("crv_max", 0) or 0) == 0:
                self.config["crv_max"] = 4.0
                await self.db.settings.update_one(
                    {"_id": "ai_trader_config"},
                    {"$set": {"crv_max": 4.0, "crv_max_migrated_v1": True}},
                    upsert=True)
                logger.info("AI config: crv_max 0 -> 4.0 migriert (Deckel gegen unrealistisch weite TPs)")
            elif not doc.get("crv_max_migrated_v1"):
                await self.db.settings.update_one(
                    {"_id": "ai_trader_config"},
                    {"$set": {"crv_max_migrated_v1": True}}, upsert=True)
            # Einmalige Migration (User-Freigabe 08/2026): smart_skip_move_pct
            # 0.02 (alte Voreinstellung – skippt praktisch nie, unnötige
            # Analyst-Calls) -> 0.10. Wer danach bewusst wieder niedriger
            # wählt, bleibt dabei (Marker verhindert Wiederholung).
            if (not doc.get("smart_skip_migrated_v1")
                    and float(doc.get("smart_skip_move_pct", 0.15) or 0) <= 0.02):
                self.config["smart_skip_move_pct"] = 0.10
                await self.db.settings.update_one(
                    {"_id": "ai_trader_config"},
                    {"$set": {"smart_skip_move_pct": 0.10,
                              "smart_skip_migrated_v1": True}},
                    upsert=True)
                logger.info("AI config: smart_skip_move_pct 0.02 -> 0.10 migriert "
                            "(spart unnötige Analyst-Calls ohne Bewegung)")
            elif not doc.get("smart_skip_migrated_v1"):
                await self.db.settings.update_one(
                    {"_id": "ai_trader_config"},
                    {"$set": {"smart_skip_migrated_v1": True}}, upsert=True)
        else:
            await self.db.settings.insert_one({"_id": "ai_trader_config", **self.config})
        # Self-Tuning-Guard: von der KI selbst gesetzte Werte außerhalb der
        # Leitplanken einmalig zurückholen (heilt Prod nach Deploy, ohne einen
        # manuell vom Trader gesetzten Wert anzufassen).
        try:
            await self._normalize_auto_tuned()
        except Exception as e:
            logger.warning(f"Self-Tuning-Guard Normalisierung fehlgeschlagen: {e}")
        # load last decisions for continuity after restart
        try:
            rows = await self.db.ai_decisions.find().sort("ts", -1).limit(60).to_list(60)
            for r in rows:
                sym = r.get("symbol")
                if sym and sym not in self.decisions:
                    r.pop("_id", None)
                    self.decisions[sym] = r
        except Exception:
            pass
        # Housekeeping-Marker laden. Beim allerersten Start werden sie mit dem
        # aktuellen Berlin-Zeitstempel initialisiert, damit weder Cleanup noch
        # Reset direkt nach dem Boot feuern (sondern erst zur nächsten vollen
        # Stunde bzw. zum nächsten 00:00 Uhr Berlin).
        try:
            hk = await self.db.settings.find_one({"_id": "ai_trader_housekeeping"})
            now_berlin = datetime.now(BERLIN_TZ)
            if hk:
                self._last_cleanup_hour = hk.get("last_cleanup_hour")
                self._last_reset_date = hk.get("last_reset_date")
            if not self._last_cleanup_hour:
                self._last_cleanup_hour = now_berlin.strftime("%Y%m%d%H")
            if not self._last_reset_date:
                self._last_reset_date = now_berlin.strftime("%Y-%m-%d")
            await self.db.settings.update_one(
                {"_id": "ai_trader_housekeeping"},
                {"$set": {
                    "last_cleanup_hour": self._last_cleanup_hour,
                    "last_reset_date": self._last_reset_date,
                }},
                upsert=True,
            )
        except Exception as e:
            logger.warning(f"AI housekeeping init failed: {e}")
        if self.learning:
            await self.learning.load_state()
        # KI-Team-Rollen (Modelle, Handelszeiten, Fallback-KI) laden
        try:
            await role_manager.load(self.db)
        except Exception as e:
            logger.warning(f"AI roles load failed: {e}")

    async def update_config(self, updates: Dict) -> Dict:
        was_enabled = self.config.get("enabled")
        if "enabled" in updates:
            self.config["enabled"] = bool(updates["enabled"])
        if "interval_min" in updates:
            self.config["interval_min"] = max(2, min(120, int(updates["interval_min"])))
        if "schedule" in updates:
            self.config["schedule"] = ai_schedule.normalize_schedule(updates["schedule"])
        if "min_confidence" in updates:
            self.config["min_confidence"] = max(0, min(100, int(updates["min_confidence"])))
        if "cooldown_min" in updates:
            self.config["cooldown_min"] = max(0, min(720, int(updates["cooldown_min"])))
        if "tune_conf_min" in updates:
            self.config["tune_conf_min"] = max(0, min(100, int(updates["tune_conf_min"])))
        if "tune_conf_max" in updates:
            self.config["tune_conf_max"] = max(0, min(100, int(updates["tune_conf_max"])))
        if self.config.get("tune_conf_min", 55) > self.config.get("tune_conf_max", 75):
            self.config["tune_conf_min"] = self.config["tune_conf_max"]
        if "tune_cooldown_max" in updates:
            self.config["tune_cooldown_max"] = max(0, min(720, int(updates["tune_cooldown_max"])))
        if "tune_guard_min" in updates:
            self.config["tune_guard_min"] = max(1, min(10, int(updates["tune_guard_min"])))
        if "tune_guard_max" in updates:
            self.config["tune_guard_max"] = max(1, min(10, int(updates["tune_guard_max"])))
        if self.config.get("tune_guard_min", 1) > self.config.get("tune_guard_max", 6):
            self.config["tune_guard_min"] = self.config["tune_guard_max"]
        if "maker_mode" in updates:
            self.config["maker_mode"] = bool(updates["maker_mode"])
        if "maker_wait_sec" in updates:
            self.config["maker_wait_sec"] = max(10, min(300, int(updates["maker_wait_sec"])))
        if "maker_suspended_until" in updates:
            v = updates["maker_suspended_until"]
            self.config["maker_suspended_until"] = str(v) if v else None
        if "maker_suspend_hours" in updates:
            # virtueller Key (KI/Autonomie): X Stunden aussetzen, 0 = aufheben
            try:
                h = max(0.0, min(72.0, float(updates["maker_suspend_hours"])))
            except (TypeError, ValueError):
                h = 0.0
            self.config["maker_suspended_until"] = (
                (datetime.now(timezone.utc) + timedelta(hours=h)).isoformat()
                if h > 0 else None)
        if "fee_guard_enabled" in updates:
            self.config["fee_guard_enabled"] = bool(updates["fee_guard_enabled"])
        if "fee_guard_mult" in updates:
            try:
                self.config["fee_guard_mult"] = max(0.0, min(30.0, float(updates["fee_guard_mult"])))
            except (TypeError, ValueError):
                pass
        if "fee_guard_atr_mult" in updates:
            try:
                self.config["fee_guard_atr_mult"] = max(0.0, min(30.0, float(updates["fee_guard_atr_mult"])))
            except (TypeError, ValueError):
                pass
        if "fee_guard_crv_relax" in updates:
            self.config["fee_guard_crv_relax"] = bool(updates["fee_guard_crv_relax"])
        if "stale_price_max_min" in updates:
            try:
                self.config["stale_price_max_min"] = max(0.0, min(120.0, float(updates["stale_price_max_min"])))
            except (TypeError, ValueError):
                pass
        if "stale_price_max_min_commodity" in updates:
            try:
                self.config["stale_price_max_min_commodity"] = max(
                    0.0, min(120.0, float(updates["stale_price_max_min_commodity"])))
            except (TypeError, ValueError):
                pass
        if "dup_entry_window_min" in updates:
            try:
                self.config["dup_entry_window_min"] = max(
                    0.0, min(720.0, float(updates["dup_entry_window_min"])))
            except (TypeError, ValueError):
                pass
        if "momentum_news_conf_block" in updates:
            v = updates["momentum_news_conf_block"]
            if isinstance(v, (list, tuple)) and len(v) == 0:
                self.config["momentum_news_conf_block"] = []
            elif isinstance(v, (list, tuple)) and len(v) == 2:
                try:
                    lo = max(0.0, min(100.0, float(v[0])))
                    hi = max(0.0, min(100.0, float(v[1])))
                    if lo <= hi:
                        self.config["momentum_news_conf_block"] = [lo, hi]
                except (TypeError, ValueError):
                    pass
        if "regime_block_enabled" in updates:
            self.config["regime_block_enabled"] = bool(updates["regime_block_enabled"])
        if "regime_block_list" in updates and isinstance(updates["regime_block_list"], list):
            self.config["regime_block_list"] = [
                str(x).strip()[:40] for x in updates["regime_block_list"] if str(x).strip()][:10]
        if "collection_enabled" in updates:
            self.config["collection_enabled"] = bool(updates["collection_enabled"])
        if "collection_min_confidence" in updates:
            self.config["collection_min_confidence"] = max(0, min(100, int(updates["collection_min_confidence"])))
        if "collection_cooldown_min" in updates:
            self.config["collection_cooldown_min"] = max(0, min(720, int(updates["collection_cooldown_min"])))
        if "collection_max_same_direction" in updates:
            self.config["collection_max_same_direction"] = max(0, min(10, int(updates["collection_max_same_direction"])))
        if "collection_max_per_coin" in updates:
            self.config["collection_max_per_coin"] = max(1, min(5, int(updates["collection_max_per_coin"])))
        if "live_gate_bypass_enabled" in updates:
            self.config["live_gate_bypass_enabled"] = bool(updates["live_gate_bypass_enabled"])
        if "live_gate_bypass_margin" in updates:
            self.config["live_gate_bypass_margin"] = max(0, min(30, int(updates["live_gate_bypass_margin"])))
        if "live_gate_bypass_per_day" in updates:
            self.config["live_gate_bypass_per_day"] = max(0, min(10, int(updates["live_gate_bypass_per_day"])))
        if "low_vol_market_block_enabled" in updates:
            self.config["low_vol_market_block_enabled"] = bool(updates["low_vol_market_block_enabled"])
        if "low_vol_atr_threshold_pct" in updates:
            try:
                self.config["low_vol_atr_threshold_pct"] = max(
                    0.0, min(2.0, float(updates["low_vol_atr_threshold_pct"] or 0)))
            except (TypeError, ValueError):
                pass
        if "setup_live_gate" in updates:
            self.config["setup_live_gate"] = bool(updates["setup_live_gate"])
        if "setup_weight_live_pref" in updates:
            self.config["setup_weight_live_pref"] = bool(updates["setup_weight_live_pref"])
        if "setup_weight_ev" in updates:
            self.config["setup_weight_ev"] = bool(updates["setup_weight_ev"])
        if "max_trades_per_coin" in updates:
            self.config["max_trades_per_coin"] = max(1, min(5, int(updates["max_trades_per_coin"])))
        if "max_capital_per_trade" in updates:
            try:
                self.config["max_capital_per_trade"] = max(0.0, min(100000.0, float(updates["max_capital_per_trade"] or 0)))
            except (TypeError, ValueError):
                pass
        position_sizing.clamp_updates(updates, self.config)
        slippage_guard.clamp_updates(updates, self.config)
        sweep_trigger.clamp_updates(updates, self.config)
        tf2_signal.clamp_updates(updates, self.config)
        if "runner_trend_setups" in updates:
            self.config["runner_trend_setups"] = bool(updates["runner_trend_setups"])
        if "runner_trend_share" in updates:
            try:
                self.config["runner_trend_share"] = max(0.0, min(1.0, float(updates["runner_trend_share"])))
            except (TypeError, ValueError):
                pass
        if "news_enabled" in updates:
            self.config["news_enabled"] = bool(updates["news_enabled"])
        if "macro_enabled" in updates:
            self.config["macro_enabled"] = bool(updates["macro_enabled"])
        if "macro_symbols" in updates and isinstance(updates["macro_symbols"], list):
            syms = [str(s).upper() for s in updates["macro_symbols"] if str(s).strip()]
            self.config["macro_symbols"] = syms[:6] or macro_context.DEFAULT_SYMBOLS
        if "liquidity_enabled" in updates:
            self.config["liquidity_enabled"] = bool(updates["liquidity_enabled"])
        if "liquidity_symbols" in updates and isinstance(updates["liquidity_symbols"], list):
            syms = [str(s).upper() for s in updates["liquidity_symbols"] if str(s).strip()]
            self.config["liquidity_symbols"] = syms[:6] or list(liquidity_data.DEFAULT_SYMBOLS)
        for flag in ("use_liquidation_data", "use_heatmap_data", "lean_prompt", "smart_skip"):
            if flag in updates:
                self.config[flag] = bool(updates[flag])
        if "smart_skip_move_pct" in updates:
            try:
                self.config["smart_skip_move_pct"] = max(0.02, min(2.0, float(updates["smart_skip_move_pct"])))
            except (TypeError, ValueError):
                pass
        if "autonomy" in updates and updates["autonomy"] in ("off", "suggest", "auto"):
            self.config["autonomy"] = updates["autonomy"]
        if "learning_enabled" in updates:
            self.config["learning_enabled"] = bool(updates["learning_enabled"])
        if "learn_on_trade_close" in updates:
            self.config["learn_on_trade_close"] = bool(updates["learn_on_trade_close"])
        if "learning_lookback_days" in updates:
            self.config["learning_lookback_days"] = max(3, min(90, int(updates["learning_lookback_days"])))
        if "max_lessons" in updates:
            self.config["max_lessons"] = max(3, min(100, int(updates["max_lessons"])))
        if "use_ai_levels" in updates:
            self.config["use_ai_levels"] = bool(updates["use_ai_levels"])
        if "swing_enabled" in updates:
            self.config["swing_enabled"] = bool(updates["swing_enabled"])
        if "runner_secure_enabled" in updates:
            self.config["runner_secure_enabled"] = bool(updates["runner_secure_enabled"])
        if "runner_scalp_enabled" in updates:
            self.config["runner_scalp_enabled"] = bool(updates["runner_scalp_enabled"])
        if "runner_scalp_news_only" in updates:
            self.config["runner_scalp_news_only"] = bool(updates["runner_scalp_news_only"])
        if "runner_secure_trigger_pct" in updates:
            try:
                self.config["runner_secure_trigger_pct"] = max(
                    5.0, min(300.0, float(updates["runner_secure_trigger_pct"])))
            except (TypeError, ValueError):
                pass
        if "runner_secure_max_leverage" in updates:
            try:
                self.config["runner_secure_max_leverage"] = max(
                    2, min(200, int(updates["runner_secure_max_leverage"])))
            except (TypeError, ValueError):
                pass
        if "group_analysis" in updates:
            self.config["group_analysis"] = bool(updates["group_analysis"])
        if "swing_max_leverage" in updates:
            try:
                self.config["swing_max_leverage"] = max(1, min(20, int(updates["swing_max_leverage"])))
            except (TypeError, ValueError):
                pass
        if "crv_min" in updates:
            try:
                self.config["crv_min"] = max(1.0, min(10.0, float(updates["crv_min"])))
            except (TypeError, ValueError):
                pass
        if "crv_max" in updates:
            try:
                v = float(updates["crv_max"] or 0)
                self.config["crv_max"] = 0 if v <= 0 else max(
                    float(self.config.get("crv_min", 1.2) or 1.2), min(20.0, v))
            except (TypeError, ValueError):
                pass
        if "lev_mode" in updates and updates["lev_mode"] in ("coin", "auto", "fixed"):
            self.config["lev_mode"] = updates["lev_mode"]
        if "lev_auto_max" in updates:
            try:
                self.config["lev_auto_max"] = max(1, min(200, int(updates["lev_auto_max"])))
            except (TypeError, ValueError):
                pass
        if "lev_fixed" in updates:
            try:
                self.config["lev_fixed"] = max(1, min(100, int(updates["lev_fixed"])))
            except (TypeError, ValueError):
                pass
        if "max_same_direction" in updates:
            try:
                self.config["max_same_direction"] = max(0, min(20, int(updates["max_same_direction"])))
            except (TypeError, ValueError):
                pass
        if "min_entry_distance_pct" in updates:
            try:
                self.config["min_entry_distance_pct"] = max(0.0, min(5.0, float(updates["min_entry_distance_pct"])))
            except (TypeError, ValueError):
                pass
        if "correlation_guard" in updates:
            self.config["correlation_guard"] = bool(updates["correlation_guard"])
        if "provider" in updates and "model" in updates:
            prov, mod = updates["provider"], updates["model"]
            if prov in ALLOWED_MODELS and mod in ai_providers.allowed_models(prov):
                self.config["provider"], self.config["model"] = prov, mod
                # Wechselt der Nutzer das Modell manuell, reset des Fallback-States.
                self._effective_model = None
        elif "model" in updates:
            mod = updates["model"]
            # Finde Provider automatisch anhand des Modells (inkl. neu entdeckter)
            prov = ai_providers.provider_for_model(mod)
            if prov:
                self.config["model"] = mod
                self.config["provider"] = prov
                self._effective_model = None
        await self.db.settings.update_one({"_id": "ai_trader_config"},
                                          {"$set": dict(self.config)}, upsert=True)
        if self.config.get("enabled") and not was_enabled:
            self._next_due = 0  # run analysis immediately after enabling
        elif "schedule" in updates or "interval_min" in updates:
            # Neues (kürzeres) Intervall soll sofort greifen, nicht erst nach dem
            # alten Wartefenster – ebenfalls am Voll-Stunden-Raster ausgerichtet.
            self._next_due = min(self._next_due, ai_schedule.next_aligned_ts(
                time.time(), max(1, self.current_interval()[0]), BERLIN_TZ))
        return dict(self.config)

    # ---------------- market context ----------------
    def _snapshot(self, symbol: str) -> Optional[Dict]:
        candles = self.scanner.candle_buffer.get(symbol, [])
        if len(candles) < 60:
            return None
        ti = TechnicalIndicators
        price = candles[-1]["close"]
        # Frischester Live-Preis: die formende (noch offene) Kerze des Scanners
        # nutzen, solange sie aktuell ist (<4 min) – sonst letzter Schlusskurs.
        forming = (getattr(self.scanner, "forming", None) or {}).get(symbol)
        if isinstance(forming, dict) and forming.get("close"):
            age_ms = (datetime.now(timezone.utc).timestamp() * 1000
                      - float(forming.get("timestamp") or 0))
            if 0 <= age_ms < 4 * 60 * 1000:
                price = forming["close"]
        lines = []
        rsi_1m = 0
        rsi_5m = 0
        # 5m ergänzt: RSI/Struktur auf 1m ist überwiegend Rauschen ("Gambling"),
        # auf 5m/15m deutlich aussagekräftiger – die KI bekommt beide Ebenen.
        for tf in ("1m", "5m", "15m", "1h"):
            agg = candles if tf == "1m" else aggregate_candles(candles, tf, drop_partial=True)
            if len(agg) < 20:
                continue
            cl = [c["close"] for c in agg][-120:]
            rsi_arr = ti.calculate_rsi(cl, 14)
            rsi = rsi_arr[-1] if rsi_arr and rsi_arr[-1] is not None else 50
            if tf == "1m":
                rsi_1m = rsi
            elif tf == "5m":
                rsi_5m = rsi
            ema20 = ti.calculate_ema(cl, 20)[-1]
            ema50 = ti.calculate_ema(cl, 50)[-1] if len(cl) >= 50 else None
            trend = "aufwärts" if (ema50 and ema20 > ema50) else ("abwärts" if ema50 else "unklar")
            chg = (cl[-1] - cl[0]) / cl[0] * 100 if cl[0] else 0
            hi = max(c["high"] for c in agg[-60:])
            lo = min(c["low"] for c in agg[-60:])
            noise = " (nur Timing)" if tf == "1m" else ""
            lines.append(f"{tf}: RSI {rsi:.0f}{noise}, Trend {trend}, Δ{chg:+.2f}%, Range {lo:g}-{hi:g}")
        try:
            atr = ti.calculate_atr(candles, 14)[-1] or 0
            vols = [c.get("volume", 0) for c in candles]
            v_recent = sum(vols[-5:]) / 5
            v_base = (sum(vols[-60:]) / 60) or 1
            atr_pct = atr / price * 100 if price else 0.0
            lines.append(f"ATR(1m) {atr_pct:.3f}% | Volumen x{v_recent / v_base:.2f}")
            # Niedrig-Volatilitäts-Modus: statt "kein Trade" (Lektion 8) die
            # 5m/15m-ATR mitliefern, damit die KI auf den höheren TF wechselt.
            lowvol_thr = float(self.config.get("lowvol_atr_pct", 0.10) or 0)
            if self.config.get("lowvol_tf_switch", True) and 0 < atr_pct < lowvol_thr:
                htf = []
                for _tf in ("5m", "15m"):
                    _agg = aggregate_candles(candles, _tf, drop_partial=True)
                    if len(_agg) >= 15:
                        _a = ti.calculate_atr(_agg, 14)[-1] or 0
                        htf.append(f"{_tf}-ATR {_a / price * 100:.3f}%")
                if htf:
                    lines.append(
                        f"⚠ NIEDRIGE VOLATILITÄT (1m-ATR {atr_pct:.3f}% < {lowvol_thr:g}%): "
                        f"NICHT pauschal HOLD – wechsle auf 5m/15m-Struktur ({', '.join(htf)}) "
                        f"und leite SL/TP aus der 5m/15m-ATR ab (größere Bewegung, weiterer Stop)")
        except Exception:
            pass
        # Orderflow: bevorzugt ECHTE Bitunix-Trades (Tick-Delta, CVD,
        # Großaufträge, Sweep-Erkennung); Fallback ist der alte Kerzen-Proxy.
        real_orderflow = None
        try:
            from services.orderflow import orderflow
            from core import instruments as _instr
            _inst = _instr.get(symbol)
            # Rohstoffe/Indizes handeln bei Bitunix unter eigenem Kontrakt
            # (GOLD -> XAUUSDT usw.) – dort gibt es ECHTE Tick-Daten.
            _of_sym = _inst.bitunix if (_inst and _inst.bitunix) else symbol
            real_orderflow = orderflow.snapshot_text(_of_sym)
        except Exception as e:
            logger.debug(f"Orderflow (echt) für {symbol} nicht verfügbar: {e}")
        if not real_orderflow:
            # Forex/Metalle: Näherung aus ECHTEM CME-Futures-Volumen (besser
            # als der synthetische Kerzen-Proxy, Spot-FX hat kein Volumen).
            try:
                from services.fx_orderflow import fx_orderflow
                real_orderflow = fx_orderflow.snapshot_text(symbol)
            except Exception as e:
                logger.debug(f"FX-Orderflow für {symbol} nicht verfügbar: {e}")
        if real_orderflow:
            lines.append(real_orderflow)
        else:
            # Orderflow-Proxy (1m): Käufer-/Verkäufer-Druck aus Kerzen-Volumen-Deltas –
            # Näherung, falls der echte Tick-Stream (noch) keine Daten hat.
            try:
                def _vol_delta(cs):
                    buy = sum(c.get("volume", 0) or 0 for c in cs if c["close"] >= c["open"])
                    sell = sum(c.get("volume", 0) or 0 for c in cs if c["close"] < c["open"])
                    tot = buy + sell
                    return (buy - sell) / tot if tot else 0.0
                d15 = _vol_delta(candles[-15:])
                cvd_prev = _vol_delta(candles[-60:-30])
                cvd_now = _vol_delta(candles[-30:])
                cvd_trend = ("steigend" if cvd_now > cvd_prev + 0.05
                             else "fallend" if cvd_now < cvd_prev - 0.05 else "neutral")
                side = "Käufer" if d15 > 0.05 else ("Verkäufer" if d15 < -0.05 else "ausgeglichen")
                lines.append(f"Orderflow-Proxy(1m): Delta15 {d15:+.2f} ({side}), CVD-Trend {cvd_trend}")
            except Exception:
                pass
        # Session-Highs/-Lows (Asia/London/NY) + Umverteilungszonen für die KI
        try:
            sess = session_levels.levels_text(candles, price)
            if sess:
                lines.append(sess)
            zones = session_levels.zones_text(candles, price)
            if zones:
                lines.append(zones)
        except Exception as e:
            logger.debug(f"Session-Levels für {symbol} nicht berechenbar: {e}")
        # Range-/Wick-Analyse (15m/1h): Datenbasis für range_fade / mean_reversion
        try:
            rng = range_analysis.range_text(candles, price)
            if rng:
                lines.append(rng)
        except Exception as e:
            logger.debug(f"Range-Analyse für {symbol} nicht berechenbar: {e}")
        # Smart-Money-Zonen (Order-Blocks/FVG auf 5m/15m/1h): Datenbasis für
        # order_block / fvg_fill / htf_range – lokal, ohne Netzwerk.
        try:
            smc = smc_zones.zones_text(candles, price)
            if smc:
                lines.append(smc)
        except Exception as e:
            logger.debug(f"SMC-Zonen für {symbol} nicht berechenbar: {e}")
        # Trendfolge-2-Signal (MACD-Kreuz + Impuls + Volumen, 5m): Datenbasis für
        # trend_follow2 – Zeile erscheint NUR bei frischem Kreuz (Token-Budget).
        try:
            tf2 = tf2_signal.context_line(candles, price)
            if tf2:
                lines.append(tf2)
        except Exception as e:
            logger.debug(f"TF2-Signal für {symbol} nicht berechenbar: {e}")
        return {"symbol": symbol, "price": price,
                "rsi": round(rsi_5m or rsi_1m, 1), "rsi_1m": round(rsi_1m, 1),
                "text": f"{symbol}: Preis {price:g} | " + " | ".join(lines)}

    @staticmethod
    def _safe_lev(raw) -> int:
        try:
            return max(0, min(200, int(float(raw or 0))))
        except (TypeError, ValueError):
            return 0

    def _apply_crv_frame(self, sl_pct: float, tp1_pct: float, tpf_pct: float,
                         is_swing: bool) -> tuple:
        """CRV-Spanne aus dem Trade-Rahmen technisch erzwingen (TP1 relativ zu SL)."""
        crv_min = max(1.0, float(self.config.get("crv_min", 1.2) or 1.2))
        crv_max = float(self.config.get("crv_max", 0) or 0)
        cap = 0.25 if is_swing else 0.10
        tp1_pct = max(tp1_pct, min(cap, sl_pct * crv_min))
        if crv_max >= crv_min:
            tp1_pct = min(tp1_pct, sl_pct * crv_max)
            # TP Full ebenfalls deckeln (User 15.06.: keine unrealistisch weiten
            # Ziele mehr): max. 2× crv_max, bei Standard 4 also 8R.
            tpf_pct = min(tpf_pct, sl_pct * crv_max * 2)
        return tp1_pct, max(tpf_pct, tp1_pct)

    def _frame_leverage(self, dec: Dict, is_swing: bool) -> float:
        """Hebel gemäß Hebel-Modus des Trade-Rahmens (0 = Coin-Settings entscheiden)."""
        lev_mode = str(self.config.get("lev_mode", "coin") or "coin")
        swing_cap = float(self.config.get("swing_max_leverage", 8) or 8)
        ai_lev = 0.0
        if lev_mode == "fixed":
            ai_lev = max(1.0, min(100.0, float(self.config.get("lev_fixed", 10) or 10)))
        elif lev_mode == "auto":
            lev_max = max(1.0, min(200.0, float(self.config.get("lev_auto_max", 25) or 25)))
            chosen = float(dec.get("leverage") or 0)
            # KI hat keinen Hebel angegeben -> Coin-Settings entscheiden (kein Zwang)
            ai_lev = min(chosen, lev_max) if chosen > 0 else 0.0
        if is_swing:
            ai_lev = min(ai_lev, swing_cap) if ai_lev > 0 else swing_cap
        return ai_lev

    @staticmethod
    def _parse_json(text: str) -> Dict:
        # Tolerant gegenüber Markdown-Zäunen, Kommentaren und abgeschnittenen
        # Antworten – gültiges JSON wird unverändert gelesen (siehe ai_json.py).
        return parse_json_lenient(text)

    @staticmethod
    def _trigger_hint(trigger: str) -> str:
        """Prüfauftrag je Ereignis-Typ (Sweep / TF2-Signal / News-Impuls) – rein."""
        t = trigger.upper()
        if t.startswith("TF2"):
            return ("Prüfe NUR das genannte Symbol: Ist das TF2-Signal (MACD-Kreuz + Impuls + Volumen) ein "
                    "valides trend_follow2-Setup in Richtung des 15m-Trends? Dann setup=trend_follow2, "
                    "Entry Market, SL am genannten Struktur-Level (sl_pct entsprechend), TP1 1R/TP-Full 2R, "
                    "runner=true erlaubt. Wenn nicht: HOLD mit kurzem Grund.")
        if t.startswith("NEWS"):
            return ("Prüfe NUR die genannten Symbole: Läuft ein News-/Momentum-Impuls (Volumen, Kerzenkörper, "
                    "Richtung passend zur Nachricht), der ein momentum_news-Setup rechtfertigt? Dann "
                    "setup=momentum_news, news_impact positive/negative, Entry in Impulsrichtung nach erster "
                    "Konsolidierung, SL hinter Impuls-Basis. Impuls ausgereizt/eingepreist: HOLD mit kurzem Grund.")
        return ("Prüfe NUR das/die genannten Symbole: Ist der Sweep + Reclaim ein valides "
                "liquidity_sweep-/order_block-Setup (5m/15m-Bestätigung, Level, CRV)? "
                "Wenn nicht: HOLD mit kurzem Grund.")

    def is_fresh(self, decision: Optional[Dict]) -> bool:
        if not decision or not decision.get("ts"):
            return False
        try:
            ts = datetime.fromisoformat(decision["ts"].replace("Z", "+00:00"))
            max_age = max(self.config.get("interval_min", 10) * 2.5, 20)
            return (datetime.now(timezone.utc) - ts) < timedelta(minutes=max_age)
        except Exception:
            return False

    async def generate_for_role(self, role: str, prompt: str, system: str,
                                temperature: float = 0.4,
                                json_mode: bool = True) -> tuple[str, str, str]:
        """Generierung über die Modell-Kette einer KI-Team-Rolle.

        Kette = Rollen-Modell (bzw. Haupt-Modell) + Provider-Fallbacks +
        Rollen-Fallback-KI; pro Provider werden primärer + Backup-Key probiert.
        Der Analyst kann pro Zeitplan-Fenster ein eigenes Modell haben
        (z.B. starkes Modell zur US-Eröffnung). Rückgabe: (text, provider, model)."""
        chain = role_manager.chain(role, self.config)
        ai_providers.set_current_role(role)
        if role == "analyst":
            w = self.current_window()
            wm = (w or {}).get("model")
            if wm:
                wp = (w or {}).get("provider") or ai_providers.provider_for_model(wm)
                if wp:
                    prepend = ai_providers.same_provider_chain(wp, wm)
                    chain = prepend + [c for c in chain if c not in prepend]
        text, provider, model = await ai_providers.generate_chain(
            chain, prompt, system, temperature=temperature, json_mode=json_mode,
            priority=ai_providers.role_priority(role), role=role)
        self._effective_model = model
        self._effective_provider = provider
        try:
            await self._track_tokens(role, model,
                                     (len(prompt) + len(system) + len(text)) // 4)
        except Exception:
            pass
        if model != self.config.get("model"):
            logger.info(f"AI [{role}]: nutzt {provider}/{model} (Haupt-Modell: {self.config.get('model')})")
        return text, provider, model

    def current_window(self) -> Optional[Dict]:
        """Aktives Zeitplan-Fenster (Berlin-Zeit) oder None = Standard."""
        try:
            now = self.scanner.berlin_now()
            minutes = now.hour * 60 + now.minute
        except Exception:
            minutes = timeutil.berlin_minutes()
        return ai_schedule.effective_window(self.config.get("schedule"), minutes)

    async def _track_tokens(self, role: str, model: str, est_tokens: int):
        """Geschätzte Tokens pro Rolle und Tag (deutsche Zeit) mitschreiben –
        Basis für das Kosten-Dashboard (GET /api/ai/token-usage)."""
        if self.db is None or est_tokens <= 0:
            return
        day = timeutil.berlin_date()
        await self.db.ai_token_usage.update_one(
            {"date": day, "role": role},
            {"$inc": {"tokens": int(est_tokens), "calls": 1},
             "$set": {"model": model, "updated_at": _now_iso()}},
            upsert=True)
        try:
            await self._check_token_alert(role, day)
        except Exception:  # noqa: BLE001 – Wächter darf Analysen nie stören
            pass

    # Token-Kosten-Wächter: absolute Untergrenze + Vielfaches des eigenen Schnitts
    TOKEN_ALERT_MIN = 120_000     # unter ~120k Tokens/Tag je Rolle nie warnen
    TOKEN_ALERT_FACTOR = 2.5      # warnen ab 2.5x des 7-Tage-Schnitts der Rolle

    async def _check_token_alert(self, role: str, day: str):
        """Warnt (Glocke + Telegram-Toggle `token_alert`), wenn eine Rolle heute
        ungewöhnlich viele Tokens verbraucht – max. 1x pro Rolle und Tag."""
        doc = await self.db.ai_token_usage.find_one({"date": day, "role": role})
        today = int((doc or {}).get("tokens") or 0)
        if today < self.TOKEN_ALERT_MIN:
            return
        cursor = (self.db.ai_token_usage
                  .find({"role": role, "date": {"$lt": day}})
                  .sort("date", -1).limit(7))
        hist = [int(d.get("tokens") or 0) for d in await cursor.to_list(length=7)]
        baseline = int(sum(hist) / len(hist)) if hist else 0
        if baseline > 0 and today < baseline * self.TOKEN_ALERT_FACTOR:
            return
        from services.notifications import notify_token_spike
        await notify_token_spike(role, today, baseline, len(hist))

    async def _generate_json(self, prompt: str, system: str,
                             role: str = "analyst") -> tuple[str, str]:
        """JSON-Generierung für eine Rolle. Gibt (raw_text, effektives_model) zurück."""
        text, _provider, model = await self.generate_for_role(role, prompt, system)
        return text, model

    def _analysis_groups(self, symbols: List[str]) -> List[tuple]:
        """Symbole für die Analyse gruppieren: Krypto / Forex / Indizes+Rohstoffe.
        Getrennte LLM-Läufe liefern differenziertere, asset-spezifische
        Begründungen als ein einzelner Batch über alle ~20 Assets."""
        if not self.config.get("group_analysis", True):
            return [("Alle Assets", list(symbols))]
        from core import instruments
        by_group = {i.symbol: i.group for i in instruments.INSTRUMENTS}
        buckets = {"Krypto": [], "Forex": [], "Indizes & Rohstoffe": []}
        for s in symbols:
            g = by_group.get(s)
            if g == instruments.GROUP_FOREX:
                buckets["Forex"].append(s)
            elif g == instruments.GROUP_CRYPTO:
                buckets["Krypto"].append(s)
            else:
                buckets["Indizes & Rohstoffe"].append(s)
        return [(k, v) for k, v in buckets.items() if v]

    def _should_skip_group(self, g_label: str, g_syms, snaps, open_syms, manual: bool) -> bool:
        """Smart-Skip: LLM-Lauf einer Gruppe auslassen, wenn seit der letzten
        Analyse praktisch nichts passiert ist.

        Konservativ: nie bei manuellen Läufen, nie bei offenen Positionen in der
        Gruppe, nur wenn die letzte (frische) Entscheidung überall HOLD war und
        sich kein Preis um mehr als `smart_skip_move_pct` bewegt hat. Max. 2
        Skips in Folge – jeder 3. Lauf analysiert IMMER."""
        if manual or not self.config.get("smart_skip", True):
            return False
        if open_syms is None or any(s in open_syms for s in g_syms):
            return False
        if self._group_skips.get(g_label, 0) >= 2:
            return False
        try:
            thr = max(0.02, float(self.config.get("smart_skip_move_pct", 0.15) or 0.15))
        except (TypeError, ValueError):
            thr = 0.15
        for s in g_syms:
            dec = self.decisions.get(s)
            if not self.is_fresh(dec) or not dec.get("price"):
                return False
            if dec.get("action") != "HOLD":
                return False
            try:
                move = abs(float(snaps[s]["price"]) - float(dec["price"])) \
                    / float(dec["price"]) * 100
            except (TypeError, ValueError, ZeroDivisionError, KeyError):
                return False
            if move >= thr:
                return False
        return True

    async def run_analysis(self, manual: bool = False,
                           only_symbols: Optional[List[str]] = None,
                           trigger: Optional[str] = None) -> Dict:
        """Regulärer Analyse-Zyklus. only_symbols/trigger = gezielte Einzel-Symbol-
        Analyse (Sweep-Trigger, services/sweep_trigger.py): nur diese Symbole,
        kein Smart-Skip, Ereignis-Block im Prompt."""
        if self._analyzing:
            return {"status": "busy", "detail": "Analyse läuft bereits"}
        from core.config import local_engine_disabled
        if not manual and not trigger and local_engine_disabled():
            return {"status": "skipped",
                    "detail": "AI_TRADER_LOCAL_DISABLE aktiv (Preview-Instanz)"}
        if not self.key:
            self.last_error = f"API-Key für Provider '{self.config.get('provider')}' fehlt (Render EnvVars setzen)"
            return {"status": "error", "detail": self.last_error}
        self._analyzing = True
        if trigger:
            manual = True  # kein Smart-Skip für Ereignis-Läufe
        try:
            symbols = [s for s in self.symbols
                       if (not self.toggle_check or self.toggle_check("ai_trader", s))
                       and len(self.scanner.candle_buffer.get(s, [])) >= 60]
            if only_symbols:
                wanted = {str(x).upper() for x in only_symbols}
                symbols = [s for s in symbols if s.upper() in wanted]
            if not symbols:
                return {"status": "error", "detail": "Keine Coins mit ausreichend Kursdaten"}
            # Nur Coins analysieren, die für den KI Trader freigeschaltet sind
            # (Trade-Modus paper ODER live – 'off' wird übersprungen, spart Tokens)
            try:
                docs = await self.db.strategy_coin_configs.find(
                    {"_id": {"$regex": "^ai_trader_"}}).to_list(200)
                modes = {d["_id"].replace("ai_trader_", "", 1):
                         (d.get("config") or {}).get("mode") for d in docs}
                from core.defaults import DEFAULT_STRATEGY_COIN_CFG
                default_mode = DEFAULT_STRATEGY_COIN_CFG.get("mode", "off")
                active = [s for s in symbols if (modes.get(s) or default_mode) != "off"]
                if active:
                    symbols = active
                else:
                    return {"status": "error",
                            "detail": "Kein Coin für den KI Trader freigeschaltet "
                                      "(Trade-Modus überall 'off')"}
            except Exception as fe:
                logger.warning(f"AI Coin-Freigabe-Filter fehlgeschlagen: {fe}")
            # Wochenend-Guard: Symbole mit geschlossenem realen Markt (Forex/
            # Rohstoffe/Indizes Fr 21:00 - So ~22:00 UTC) überspringen – spart
            # Tokens und verhindert Analysen/Entscheidungen auf eingefrorenen
            # Kursen (Entries blockt _emit_signal zusätzlich hart).
            open_market = [s for s in symbols if not market_hours.is_weekend_closed(s)[0]]
            if len(open_market) < len(symbols):
                skipped_closed = [s for s in symbols if s not in open_market]
                logger.info(f"AI Wochenend-Guard: {len(skipped_closed)} Symbole mit "
                            f"geschlossenem Markt übersprungen ({', '.join(skipped_closed)})")
            if open_market:
                symbols = open_market
            snaps = {s: self._snapshot(s) for s in symbols}
            snaps = {s: v for s, v in snaps.items() if v}

            news_block = "(News deaktiviert)"
            if self.config.get("news_enabled"):
                news = await news_feed.get_headlines(18)
                news_block = "\n".join(f"- {n['title']} ({n['source']})" for n in news) or "(keine News verfügbar)"

            directives = await self._user_directives()
            open_trades = await self._open_trades_text()
            open_syms = None
            try:
                from services.ai_trade_manager import exposure_text
                _open_rows = await self.db.auto_trades.find({"status": "open"}) \
                    .limit(60).to_list(60)
                open_syms = {str(r.get("symbol")) for r in _open_rows}
                open_trades += f"\nExposure: {exposure_text(_open_rows)}"
            except Exception:
                pass
            extra_blocks = await self._analysis_extra_blocks(purpose="analysis_base")
            liq_block = ""
            try:
                liq_block = await self._liquidity_block() or ""
            except Exception as e:
                logger.warning(f"AI liquidity block failed: {e}")
            berlin = self.scanner.berlin_now().strftime("%d.%m.%Y %H:%M")

            capital_block = ""
            max_cap = float(self.config.get("max_capital_per_trade") or 0)
            if str(self.config.get("sizing_mode", "legacy")) == "risk":
                r_pct = float(self.config.get("risk_per_trade_pct", 2.0) or 2.0)
                floor = float(self.config.get("risk_conviction_floor", 0.5) or 0.5)
                capital_block = (
                    f"=== POSITIONSGRÖSSE (RISIKO-MODUS) ===\n"
                    f"Die Positionsgröße wird automatisch aus dem Risiko-Budget berechnet: "
                    f"{r_pct:g}% der Equity pro Trade / SL-Abstand; der Hebel wird so gewählt, "
                    f"dass die Liquidation hinter dem SL liegt. 'capital_pct' ist NUR deine "
                    f"Überzeugung: 100 = volles Budget, 10 = {floor * 100:.0f}% des Budgets. "
                    f"Reduziere also NICHT reflexartig 'defensiv' – ein enger SL macht die "
                    f"Position automatisch größer (gleiches Risiko), ein weiter SL kleiner. "
                    f"Lektionen über 'Marge reduzieren' oder 'Paper-Größe' sind hier "
                    f"gegenstandslos.\n\n")
            elif max_cap > 0:
                capital_block = (
                    f"=== KAPITAL PRO TRADE ===\n"
                    f"Max. Kapital pro Trade: {max_cap:.2f} USDT Margin. Du entscheidest "
                    f"pro Trade über 'capital_pct' (10-100), wie viel davon du einsetzt. "
                    f"Staffle nach Überzeugung – nutze NICHT automatisch immer 100%.\n\n")

            # Trade-Rahmen (global, vom Trader im AI-Panel vorgegeben): CRV-Spanne
            # und Hebel-Modus, in denen sich die KI pro Trade frei bewegen darf.
            crv_min = float(self.config.get("crv_min", 1.2) or 1.2)
            crv_max = float(self.config.get("crv_max", 0) or 0)
            lev_mode = str(self.config.get("lev_mode", "coin") or "coin")
            frame_lines = [
                "CRV (tp1_pct / sl_pct): mind. " + f"{crv_min:g}"
                + (f", max. {crv_max:g}" if crv_max > 0 else "")
                + " – wähle SL/TP innerhalb dieser Spanne frei passend zum Setup "
                  "(wird technisch erzwungen)."]
            if lev_mode == "auto":
                lam = int(self.config.get("lev_auto_max", 25) or 25)
                frame_lines.append(
                    f"HEBEL: Auto – gib pro Entscheidung zusätzlich das Feld \"leverage\" "
                    f"(ganze Zahl 1-{lam}) an, passend zu Setup-Qualität und SL-Abstand "
                    f"(enger SL + hoher Hebel = hohes Liquidationsrisiko).")
            elif lev_mode == "fixed":
                frame_lines.append(
                    f"HEBEL: fest {int(self.config.get('lev_fixed', 10) or 10)}x "
                    f"(vom Trader vorgegeben – nicht wählbar).")
            # Fee-Wächter in den Prompt: sonst schlägt die KI weiter Stops vor,
            # die technisch geblockt werden (gleiche Logik wie SL-Ratchet-Regel).
            if self.config.get("fee_guard_enabled", True):
                fg_mult = float(self.config.get("fee_guard_mult", 2.5) or 0)
                if fg_mult > 0:
                    fg_atr = float(self.config.get("fee_guard_atr_mult", 2.5) or 0)
                    atr_note = (f" Zusätzlich gilt ein dynamisches Minimum von {fg_atr:g}× der "
                                f"aktuellen 1m-ATR des Coins (es zählt das GRÖSSERE Minimum)."
                                if fg_atr > 0 else "")
                    crv_note = (" AUSNAHME (knappe Setups): Bei CRV >= 2 darf der SL das "
                                "Minimum um bis zu 15% unterschreiten, bei CRV >= 3 um bis zu "
                                "25% – ein starkes CRV deckt die Gebühren. Gib solche knappen, "
                                "aber hochwertigen Setups also NICHT reflexartig als HOLD aus."
                                if self.config.get("fee_guard_crv_relax", True) else "")
                    frame_lines.append(
                        f"FEE-WÄCHTER: sl_pct muss mind. {fg_mult * 0.12:.2f}% betragen "
                        f"({fg_mult:g}× Roundtrip-Fees ~0.12%).{atr_note} Engere Stops werden "
                        f"technisch geblockt.{crv_note} WICHTIG: Das Minimum ist eine UNTERGRENZE, KEIN "
                        f"Zielwert – setze den SL IMMER an die Marktstruktur (Swing-Punkt, "
                        f"Range-Grenze), NIEMALS pauschal auf das Minimum. Liegt dein "
                        f"Struktur-SL unter dem Minimum, gib HOLD statt den SL künstlich zu "
                        f"schrumpfen oder aufzuweiten – solche Trades stoppt das Rauschen aus.")
            if self.config.get("lowvol_tf_switch", True):
                frame_lines.append(
                    "NIEDRIG-VOLATILITÄTS-MODUS: Fällt die 1m-ATR eines Assets unter "
                    f"{float(self.config.get('lowvol_atr_pct', 0.10) or 0.10):g}% (Marker "
                    "'⚠ NIEDRIGE VOLATILITÄT' in den Marktdaten), gib NICHT reflexartig HOLD: "
                    "wechsle stattdessen auf den 5m-/15m-Chart, trade dort die größere Bewegung "
                    "und setze sl_pct/tp1_pct anhand der mitgelieferten 5m/15m-ATR (weiterer "
                    "Stop, entsprechend größere Ziele – Fee-Wächter und CRV-Rahmen bleiben gültig).")
            frame_block = ("=== TRADE-RAHMEN (vom Trader vorgegeben) ===\n"
                           + "\n".join(frame_lines) + "\n\n")

            trigger_block = ""
            if trigger:
                trigger_block = (
                    "=== EREIGNIS (gezielte Sofort-Analyse) ===\n"
                    f"{str(trigger)[:400]}\n"
                    "Diese Analyse wurde außerplanmäßig durch einen lokalen Detektor "
                    f"ausgelöst. {self._trigger_hint(str(trigger))}\n\n")
            # FOMC-Protokoll (services/fomc_event.py): Phasen-Anweisungen rund um
            # den Zinsentscheid (leer außerhalb des Fensters / >24h vor Termin).
            fomc_block = ""
            try:
                from services import fomc_event
                _fb = fomc_event.prompt_block()
                if _fb:
                    fomc_block = _fb + "\n\n"
            except Exception as fe:
                logger.warning(f"FOMC prompt block failed: {fe}")
            # CPI-/NFP-Protokoll (services/econ_event.py): gleiches Muster für
            # US-Datenveröffentlichungen (leer außerhalb der Fenster).
            try:
                from services import econ_event
                _eb = econ_event.prompt_blocks()
                if _eb:
                    fomc_block += _eb + "\n\n"
            except Exception as fe:
                logger.warning(f"Econ prompt block failed: {fe}")
            prompt_base = (
                f"Zeit (Berlin): {berlin}\n\n"
                f"{extra_blocks}\n\n"
                + fomc_block +
                f"=== AKTUELLE NEWS ===\n{news_block}\n\n"
                + trigger_block + capital_block + frame_block +
                f"=== ANWEISUNGEN DES TRADERS (höchste Priorität) ===\n{directives}\n\n"
                f"=== OFFENE POSITIONEN ===\n{open_trades}\n\n"
            )

            groups = self._analysis_groups(list(snaps.keys()))
            from services.ai_market_observer import market_observer as _observer
            now = _now_iso()
            emitted = []
            stored = []
            overview_parts = []
            group_errors = []
            skipped_groups = []
            token_estimate = 0
            all_new_strategies = []
            all_config_changes = []
            all_new_setups = []
            all_setup_revisions = []
            model_used = None
            # Policy-Fingerprint (Audit 3.1): zyklusweite Bestandteile einmal berechnen
            try:
                _pol_lessons = policy_fingerprint.lessons_hash(
                    await self.learning.get_lessons() if self.learning else [])
            except Exception:
                _pol_lessons = ""
            _pol_playbook = policy_fingerprint.short_hash(ai_playbook.revision_versions())
            _pol_sizing = policy_fingerprint.sizing_hash(self.config)
            try:
                from services.ml_gate import ml_gate as _pol_gate
                _pol_gate_v = (_pol_gate.model_meta or {}).get("version")
            except Exception:
                _pol_gate_v = None
            # Policy-Labor (Audit 3.2): fährt DIESER Zyklus den Kandidaten mit?
            _pol_shadow = policy_lab.begin_cycle()
            for g_label, g_syms in groups:
                if self._should_skip_group(g_label, g_syms, snaps, open_syms, manual):
                    self._group_skips[g_label] = self._group_skips.get(g_label, 0) + 1
                    skipped_groups.append(g_label)
                    logger.info(f"AI Smart-Skip: Gruppe {g_label} praktisch unverändert – "
                                f"LLM-Lauf gespart ({self._group_skips[g_label]}. in Folge)")
                    continue
                self._group_skips[g_label] = 0
                # Liquiditäts-/Liquidations-Daten betreffen nur Krypto – Forex-
                # und Indizes-Läufe bekommen den Block nicht (spart Tokens).
                g_liq = (liq_block + "\n\n") if (liq_block and g_label in ("Krypto", "Alle Assets")) else ""
                # BTC-Korrelations-Filter: nur für Krypto-Gruppen relevant
                g_btc = ""
                if g_label in ("Krypto", "Alle Assets"):
                    g_btc = self._btc_corr_block()
                    if g_btc:
                        g_btc += "\n\n"
                g_classes = setup_asset_class.classes_for_group(g_label)
                g_base = await self.resolve_group_blocks(prompt_base, g_classes, list(g_syms))
                g_prompt = (
                    g_base + g_liq + g_btc
                    + f"=== MARKTDATEN (Multi-Timeframe) – FOKUS-GRUPPE: {g_label} ===\n"
                    + "\n".join(snaps[s]["text"] for s in g_syms)
                    + f"\n\nDieser Lauf behandelt NUR die Gruppe {g_label} "
                      f"({', '.join(g_syms)}). Analysiere jedes dieser Symbole in der "
                      "Tiefe (Struktur, Level, Korrelationen INNERHALB der Gruppe) und "
                      "gib für jedes genau eine Entscheidung mit individueller, "
                      "asset-spezifischer Begründung als JSON zurück."
                )
                try:
                    lean = bool(self.config.get("lean_prompt", True))
                    sys_prompt = ANALYSIS_SYSTEM_LEAN if lean else ANALYSIS_SYSTEM
                    # Fix 0.4: Prompt-Stand dieses Laufs (wird an jede Decision gebunden)
                    pv = prompt_version_info("lean" if lean else "full")
                    raw, model_used = await self._generate_json(g_prompt, sys_prompt)
                    data = self._parse_json(raw)
                except Exception as ge:
                    group_errors.append(f"{g_label}: {str(ge)[:120]}")
                    logger.error(f"AI Gruppen-Analyse {g_label} fehlgeschlagen: {ge}")
                    continue
                ov = str(data.get("market_overview", "")).strip()
                if ov:
                    overview_parts.append(f"[{g_label}] {ov}" if len(groups) > 1 else ov)
                all_new_strategies += list(data.get("new_strategies") or [])
                all_new_setups += list(data.get("new_setups") or [])
                # Setup-Revisionen gelten nur für die Klassen dieses Gruppen-Laufs
                for rev in list(data.get("setup_revisions") or [])[:1]:
                    if isinstance(rev, dict):
                        all_setup_revisions.append({**rev, "_classes": g_classes})
                all_config_changes += list(data.get("config_changes") or [])
                for d in data.get("decisions", []):
                    sym = d.get("symbol")
                    if sym not in snaps or sym not in g_syms:
                        continue
                    action = str(d.get("action", "HOLD")).upper()
                    if action not in ("LONG", "SHORT", "HOLD"):
                        action = "HOLD"
                    horizon = "swing" if (str(d.get("horizon") or "").lower() == "swing"
                                          and self.config.get("swing_enabled", True)) else "scalp"
                    setup = (ai_playbook.normalize_setup(d.get("setup"))
                             if action in ("LONG", "SHORT") else None)
                    dec = {
                        "id": str(uuid.uuid4()),
                        "symbol": sym,
                        "action": action,
                        "confidence": max(0, min(100, int(d.get("confidence", 0) or 0))),
                        "horizon": horizon,
                        "setup": setup,
                        "runner": bool(d.get("runner")) and horizon == "swing",
                        "sl_pct": float(d.get("sl_pct", 0.6) or 0.6),
                        "tp1_pct": float(d.get("tp1_pct", 0.9) or 0.9),
                        "tpf_pct": float(d.get("tpf_pct", 1.8) or 1.8),
                        **setup_asset_class.clamp_levels(
                            setup_asset_class.asset_class_of(sym),
                            float(d.get("sl_pct", 0.6) or 0.6),
                            float(d.get("tp1_pct", 0.9) or 0.9),
                            float(d.get("tpf_pct", 1.8) or 1.8), horizon == "swing"),
                        "capital_pct": max(10, min(100, int(d.get("capital_pct", 100) or 100))),
                        # Key-Level-Limit-Orders (services/key_level_limits.py)
                        **key_level_limits.parse_decision_fields(d),
                        "leverage": self._safe_lev(d.get("leverage")),
                        "news_impact": d.get("news_impact", "neutral"),
                        "reasoning": str(d.get("reasoning", ""))[:500],
                        "size_reason": (str(d.get("size_reason", "") or "")[:200] or None),
                        "levels_reason": (str(d.get("levels_reason", "") or "")[:200] or None),
                        "strategy_candidate_id": str(d.get("strategy_candidate_id") or "") or None,
                        "price": snaps[sym]["price"],
                        "rsi": snaps[sym]["rsi"],
                        "ts": now,
                        "signaled": False,
                        "model": model_used,
                        "model_weight": ai_providers.model_weight(model_used),
                        "prompt_version": pv,
                        # Audit 3.1: exakter Policy-Stand dieser Entscheidung
                        "policy_version": policy_fingerprint.build(
                            prompt_hash=pv.get("combined"), lessons_h=_pol_lessons,
                            playbook_version=_pol_playbook, model=model_used,
                            gate_version=_pol_gate_v, sizing_h=_pol_sizing),
                        "entry_market_snapshot": _observer.entry_snapshot(sym),
                    }
                    if dec.get("levels_clamp") and action in ("LONG", "SHORT"):
                        logger.info(f"{sym}: Klassen-Grenze angewendet – {dec['levels_clamp']}")
                        dec["levels_reason"] = f"[Clamp: {dec['levels_clamp']}] {dec.get('levels_reason') or ''}"[:200]
                    # Dynamische Setup-Gewichtung (services/setup_weighting.py):
                    # die gelernte Erfolgsbilanz des gewählten Setups (Klasse +
                    # Asset) justiert die Konfidenz in engen Grenzen – Setups
                    # wirken als gewichteter Faktor neben der Eigenanalyse,
                    # nie als harte Fessel.
                    if action in ("LONG", "SHORT") and setup:
                        _w_cls = setup_asset_class.asset_class_of(sym)
                        _w = setup_weighting.combined_weight(
                            ai_playbook.class_stats(_w_cls, setup),
                            ai_playbook.asset_stats(_w_cls, setup, sym),
                            class_live_stats=ai_playbook.class_live_stats(_w_cls, setup),
                            live_pref=bool(self.config.get("setup_weight_live_pref", True)),
                            ev_mode=bool(self.config.get("setup_weight_ev", True)))
                        _w_conf, _w_note = setup_weighting.adjust_confidence(
                            dec["confidence"], _w)
                        if _w_note:
                            dec["confidence_raw"] = dec["confidence"]
                            dec["confidence"] = _w_conf
                            dec["setup_weight"] = _w
                            dec["setup_weight_note"] = _w_note
                            logger.info(f"{sym}: {_w_note} ({setup}@{_w_cls})")
                    # Gate v1 (Phase 5): Shadow-Prediction nur loggen, nie blocken
                    if action in ("LONG", "SHORT"):
                        from services.ml_gate import ml_gate
                        dec["gate_shadow"] = ml_gate.shadow_predict(dec)
                    self.decisions[sym] = dec
                    stored.append(dec)
                    if (action in ("LONG", "SHORT")
                            and dec["confidence"] >= self.config["min_confidence"]
                            and self.scanner.is_trading_session("ai_trader")):
                        gate_note = await self._setup_live_gate(dec)
                        if gate_note is None:
                            ok = await self._emit_signal(dec)
                            if ok:
                                dec["signaled"] = True
                                emitted.append(f"{sym} {action}")
                        else:
                            # Setup noch nicht live-reif -> fällt unten in die
                            # Paper-Datensammlung statt live zu gehen
                            dec["live_gate"] = gate_note
                    # Datensammel-Modus (Phase 4): unterhalb der Live-Schwelle,
                    # aber über der Sammel-Schwelle -> Paper-Trade (data_collection)
                    if (not dec["signaled"]
                            and action in ("LONG", "SHORT")
                            and bool(self.config.get("collection_enabled"))
                            and dec["confidence"] >= int(self.config.get(
                                "collection_min_confidence", 60) or 0)
                            and self.scanner.is_trading_session("ai_trader")):
                        ok = await self._emit_signal(dec, collection=True)
                        if ok:
                            dec["signaled"] = True
                            dec["data_collection"] = True
                            emitted.append(f"{sym} {action} (Datensammlung)")
                    # Key-Level-Limit-Orders: wartende Orders des Symbols je
                    # Zyklus neu bewerten (cancel_limit / Gegenrichtung /
                    # obsolet durch sofortigen Market-Entry).
                    try:
                        await key_level_limits.reevaluate(self.db, sym, dec)
                    except Exception as e:
                        logger.warning(f"{sym}: Limit-Order-Neubewertung fehlgeschlagen: {e}")
                # Policy-Labor (Audit 3.2): Kandidaten-Policy als Shadow-Lauf auf
                # DERSELBEN Gruppe (eigener LLM-Call, nur jeder k-te Zyklus).
                if _pol_shadow:
                    try:
                        await policy_lab.shadow_group(
                            self, g_label, g_prompt, sys_prompt, snaps, g_syms,
                            champion_prompt_hash=pv.get("combined"),
                            lessons_h=_pol_lessons, playbook_version=_pol_playbook,
                            gate_version=_pol_gate_v)
                    except Exception as ple:
                        logger.warning(f"Policy-Lab Shadow ({g_label}) fehlgeschlagen: {ple}")
            if group_errors and not stored:
                self.last_error = "Gruppen-Analyse fehlgeschlagen: " + "; ".join(group_errors)[:280]
                return {"status": "error", "detail": self.last_error}
            if skipped_groups and not stored and not group_errors:
                # Kompletter Zyklus per Smart-Skip gespart: kein Feed-Eintrag (kein Spam)
                self.last_run = now
                self.last_error = None
                logger.info(f"AI Smart-Skip: Zyklus ohne LLM-Call ({', '.join(skipped_groups)})")
                return {"status": "ok", "decisions": 0, "signals": [],
                        "skipped_groups": skipped_groups,
                        "overview": "Smart-Skip: Markt seit letzter Analyse praktisch "
                                    "unverändert – LLM-Analyse gespart"}
            if stored:
                await self.db.ai_decisions.insert_many([dict(x) for x in stored])

            # Neue Strategie-Ideen der KI -> Strategie-Labor (Ghost-Phase)
            new_candidates = []
            # Setup-Entdeckung: neue Setup-Ideen der KI -> Playbook (Shadow-Test
            # als Paper-Datensammlung, Live erst über das Reife-Gate)
            for spec in all_new_setups[:2]:
                if not isinstance(spec, dict):
                    continue
                try:
                    res = await ai_playbook.propose_custom_setup(
                        self.db, spec.get("id"), spec.get("desc"), source="ki",
                        trade_target=spec.get("trade_target"))
                    if res.get("status") == "rejected":
                        logger.info(f"KI-Setup-Vorschlag abgelehnt: {res.get('reason')}")
                except Exception as se:
                    logger.error(f"KI-Setup konnte nicht angelegt werden: {se}")

            # Setup-Überarbeitung je Anlageklasse: nur rückgestufte Setups, max.
            # eine Revision pro Setup und Re-Test-Zeitraum (ai_playbook.revise_setup)
            for rev in all_setup_revisions[:2]:
                cls = str(rev.get("asset_class") or "").strip().lower()
                if cls not in rev.get("_classes", []):
                    cls = rev["_classes"][0] if len(rev.get("_classes", [])) == 1 else cls
                try:
                    res = await ai_playbook.revise_setup(
                        self.db, cls, rev.get("setup"), rev.get("desc"),
                        reason=str(rev.get("reason") or ""), source="ki",
                        trade_target=rev.get("trade_target"))
                    if res.get("status") == "rejected":
                        logger.info(f"KI-Setup-Revision abgelehnt ({rev.get('setup')}@{cls}): {res.get('reason')}")
                except Exception as se:
                    logger.error(f"KI-Setup-Revision fehlgeschlagen: {se}")

            for spec in all_new_strategies[:3]:
                if not isinstance(spec, dict):
                    continue
                try:
                    res = await strategy_lab.create_candidate(spec, source="ki")
                    if res.get("status") == "ok":
                        new_candidates.append(res["candidate"]["id"])
                except Exception as se:
                    logger.error(f"Strategie-Kandidat konnte nicht angelegt werden: {se}")

            # Self-Tuning: von der KI gewünschte Einstellungs-Änderungen verarbeiten
            cfg_results = []
            try:
                cfg_results = await self._handle_config_changes(
                    all_config_changes, source="analysis")
            except Exception as ce:
                logger.error(f"AI config changes failed: {ce}")
            # Autonomie "auto": zurückgestellte Wünsche erneut prüfen und
            # anwenden, sobald die Datenlage sie bestätigt.
            try:
                await self.review_parked_proposals()
            except Exception as re_:
                logger.error(f"Autonomie-Review fehlgeschlagen: {re_}")

            feed_entry = {
                "id": str(uuid.uuid4()),
                "role": "analysis",
                "text": "\n\n".join(overview_parts)[:1800],
                "group_errors": group_errors or None,
                "decisions": [{"symbol": x["symbol"], "action": x["action"],
                               "confidence": x["confidence"], "reasoning": x["reasoning"],
                               "horizon": x.get("horizon", "scalp"),
                               "setup": x.get("setup"),
                               "signaled": x["signaled"]} for x in stored],
                "emitted": emitted,
                "config_changes": [{"symbol": p["symbol"], "changes": p["changes"],
                                    "status": p["status"]} for p in cfg_results],
                "new_candidates": new_candidates,
                "skipped_groups": skipped_groups or None,
                "token_estimate": token_estimate or None,
                "manual": manual,
                "model": model_used,
                "ts": now,
            }
            await self.db.ai_chat.insert_one(dict(feed_entry))
            self.last_run = now
            self.last_error = None
            logger.info(f"AI analysis done ({model_used}): {len(stored)} decisions, "
                        f"{len(emitted)} signals ({emitted}), ~{token_estimate} Tokens (Schätzung)")
            return {"status": "ok", "decisions": len(stored), "signals": emitted,
                    "overview": feed_entry["text"], "model": model_used}
        except Exception as e:
            self.last_error = str(e)[:300]
            logger.error(f"AI analysis failed: {e}")
            return {"status": "error", "detail": self.last_error}
        finally:
            self._analyzing = False

    async def _open_ai_trades_count(self, collection: bool) -> Optional[int]:
        """Offene KI-Trades der jeweiligen Welt – Sammel getrennt von echtem
        Risiko, damit max_open_trades (MasterPrompt) nicht vermischt zählt."""
        try:
            return await self.db.auto_trades.count_documents(
                {"status": "open", "strategy_id": "ai_trader",
                 "data_collection": {"$eq": True} if collection else {"$ne": True}})
        except Exception:
            return None

    async def _today_risk(self) -> tuple:
        """Realisierter PnL und Trade-Anzahl der KI für den heutigen Handelstag.
        Zählt nur echte Risiko-Trades – Sammel-Trades (Paper, ML-Daten) dürfen
        die Tageslimits (max_daily_loss/max_trades_per_day) nicht verbrauchen."""
        try:
            day = self.scanner.berlin_date()
            rows = await self.db.auto_trades.find(
                {"strategy_id": "ai_trader", "trade_date": day,
                 "data_collection": {"$ne": True}},
                {"realized_pnl": 1, "status": 1}).to_list(300)
        except Exception as e:
            logger.warning(f"Tages-Risiko nicht ermittelbar: {e}")
            return None, None
        pnl = sum(float(r.get("realized_pnl") or 0) for r in rows)
        self._day_risk_cache = {"date": day, "realized_pnl": round(pnl, 4),
                                "trades": len(rows),
                                "limit_usdt": master_prompt.rules.get("max_daily_loss_usdt") or None,
                                "max_trades": master_prompt.rules.get("max_trades_per_day") or None}
        return round(pnl, 4), len(rows)

    async def _diversification_gate(self, sym: str, dec: Dict,
                                    collection: bool = False) -> tuple:
        """Diversifikations- & Playbook-Guards (technisch erzwungen):
        Richtungs-Klumpen, Entry-Cluster in derselben Zone, gesperrte Setups.
        GETRENNTE WELTEN (Bug-Report 26.08.): Sammel-Trades (Paper, ML-Daten)
        und echte Risiko-Trades belegen sich gegenseitig KEINE Guard-Slots mehr –
        10 offene Sammel-LONGs hatten Live-Einstiege blockiert ("Limit 3").
        Live-Einstiege prüfen nur echte Risiko-Trades; Sammel-Einstiege prüfen
        nur andere Sammel-Trades mit eigenem Limit (collection_max_same_direction,
        vorher tote Einstellung). Korrelations-Guard und Playbook-Sperren gelten
        weiterhin nur für Live (Paper-Daten über gesperrte Setups sind fürs ML
        wertvoll)."""
        try:
            open_rows = await self.db.auto_trades.find(
                {"status": "open", "strategy_id": "ai_trader"},
                {"symbol": 1, "side": 1, "entry": 1, "data_collection": 1}).to_list(200)
        except Exception as e:
            logger.warning(f"Diversifikations-Guard: offene Trades nicht lesbar: {e}")
            open_rows = []
        if collection:
            rows_checked = [t for t in open_rows if t.get("data_collection")]
            max_same = int(self.config.get("collection_max_same_direction", 5) or 0)
        else:
            rows_checked = [t for t in open_rows if not t.get("data_collection")]
            max_same = int(self.config.get("max_same_direction", 3) or 0)
        allowed, why = ai_playbook.diversification_check(
            rows_checked, sym, dec["action"], float(dec.get("price") or 0),
            max_same_direction=max_same,
            min_dist_pct=float(self.config.get("min_entry_distance_pct", 0.5) or 0),
            setup=dec.get("setup"),
            correlation_guard=(not collection) and bool(self.config.get("correlation_guard", True)))
        if not allowed:
            return False, why
        # FOMC-Gate (Paper UND Live): fomc_event nur im Event-Fenster, im Lock
        # (erste Minuten nach dem Entscheid) gar keine neuen Einstiege.
        from services import fomc_event
        fomc_reason = fomc_event.entry_block_reason(dec.get("setup"))
        if fomc_reason:
            return False, fomc_reason
        # CPI-/NFP-Gate: gleiches Muster für die Daten-Events (econ_event.py)
        from services import econ_event
        econ_reason = econ_event.entry_block_reason(dec.get("setup"))
        if econ_reason:
            return False, econ_reason
        if not collection:
            blocked = ai_playbook.disabled_reason(dec.get("setup"))
            if blocked:
                return False, blocked
        return True, ""


    async def _setup_live_gate(self, dec: Dict) -> Optional[str]:
        """Setup-Reife-Gate: None = live ok, sonst Begründung fürs Umleiten in
        die Datensammlung. Greift NUR, wenn der Trade wirklich live liefe –
        im Paper-Modus bleibt alles wie bisher."""
        if not bool(self.config.get("setup_live_gate", True)):
            return None
        setup = dec.get("setup")
        if not setup:
            return None
        try:
            from core.state import autotrader
            if autotrader.effective_mode("ai_trader", dec.get("symbol")) != "live":
                return None
            # Reife-Gate gilt je ANLAGEKLASSE (services/setup_asset_class.py)
            cls = setup_asset_class.asset_class_of(dec.get("symbol"))
            dec["asset_class"] = cls
            # Paper/Live-Divergenz: Setup läuft live nachweislich schlechter als
            # im Paper -> kein Live (auch kein Bypass), Datensammlung läuft weiter.
            lb = ai_playbook.live_block_reason(setup, asset_class=cls)
            if lb:
                logger.info(f"{dec.get('symbol')}: Live-Gate – {lb}")
                return lb
            stats = await ai_playbook.cached_setup_stats(self.db)
            ok, why = ai_playbook.live_ready_for(setup, stats.get(setup), asset_class=cls)
            if ok:
                # Kapital-Zuweisung je Setup × Asset (services/setup_capital.py):
                # letzte Eskalationsstufe = Live-Einstieg für DIESES Setup auf
                # DIESEM Asset ausgesetzt -> Paper-Datensammlung läuft weiter.
                alloc = setup_capital.allocation(
                    ai_playbook.class_stats(cls, setup),
                    ai_playbook.asset_stats(cls, setup, dec.get("symbol")),
                    dec.get("confidence"), self.config.get("min_confidence", 65),
                    setup_capital.gate_p_win(dec))
                dec["setup_alloc"] = alloc
                if alloc.get("suspended"):
                    note = (f"Setup '{setup}' auf {dec.get('symbol')} ausgesetzt – "
                            f"{alloc.get('note', '')}")
                    logger.info(f"{dec.get('symbol')}: Live-Gate – {note}")
                    return note
                return None
            # Bypass: hochkonfidente Setups dürfen begrenzt live gehen (paar
            # Live-Trades/Tag), statt ALLES in die Datensammlung umzuleiten.
            try:
                day_start = datetime.now(timezone.utc).strftime("%Y-%m-%dT00:00:00")
                opened_today = await self.db.auto_trades.count_documents(
                    {"strategy_id": "ai_trader", "mode": "live",
                     "data_collection": {"$ne": True},
                     "opened_at": {"$gte": day_start}})
            except Exception:
                opened_today = 999
            if live_gate_bypass_ok(dec.get("confidence"),
                                   self.config.get("min_confidence", 65),
                                   opened_today, self.config):
                dec["live_gate_bypass"] = True
                dec["live_gate_bypass_reason"] = why
                per_day = int(self.config.get("live_gate_bypass_per_day", 2) or 0)
                logger.info(f"{dec.get('symbol')}: Live-Gate-BYPASS – Setup "
                            f"'{setup}' noch nicht live-reif, aber Konfidenz "
                            f"{dec.get('confidence')} und erst {opened_today} "
                            "KI-Live-Trades heute")
                try:
                    from core import state
                    from services import notifications
                    await notifications.telegram_notify(
                        self.db, state.telegram, "live_gate_bypass",
                        f"⚡ *LIVE-BYPASS* {dec.get('symbol')} {dec.get('action')}\n"
                        f"Setup `{setup}` ist noch nicht live-reif ({why}), "
                        f"aber Konfidenz {dec.get('confidence')} ≥ Schwelle+"
                        f"{self.config.get('live_gate_bypass_margin', 5)} – "
                        f"KI eröffnet Live-Trade {opened_today + 1}/{per_day} heute.")
                except Exception as e:
                    logger.warning(f"Live-Bypass-Notify fehlgeschlagen: {e}")
                return None
            note = f"Setup '{setup}' noch nicht live-reif: {why}"
            logger.info(f"{dec.get('symbol')}: Live-Gate -> Datensammlung ({note})")
            return note
        except Exception as e:
            # T04: fail-closed – wenn die Reife-Prüfung selbst scheitert, geht
            # der Trade in die Paper-Datensammlung statt ungeprüft live.
            logger.warning(f"Setup-Live-Gate-Prüfung fehlgeschlagen – fail-closed: {e}")
            return (f"Live-Gate-Prüfung fehlgeschlagen ({str(e)[:120]}) – "
                    "Trade geht in die Datensammlung")

    def _stale_limit_for(self, sym: str) -> float:
        """Stale-Limit je Symbol: Rohstoffe bekommen ihr eigenes, weiteres Limit."""
        try:
            limit = float(self.config.get("stale_price_max_min", 10) or 0)
        except (TypeError, ValueError):
            limit = 10.0
        try:
            from core import instruments as _instr
            if getattr(_instr.BY_SYMBOL.get(sym), "group", None) == _instr.GROUP_RESOURCES:
                limit = float(self.config.get("stale_price_max_min_commodity", 20) or 0)
        except (TypeError, ValueError):
            limit = 20.0
        except Exception:
            pass
        return limit

    def _momentum_brake_block(self, dec: Dict) -> Optional[str]:
        """Momentum-News-Bremse: blockt Live-Entries des Setups momentum_news im
        konfigurierten Konfidenzband (Default 70-74, messbares Verlustband)."""
        rng = self.config.get("momentum_news_conf_block") or []
        if str(dec.get("setup")) != "momentum_news" or len(rng) != 2:
            return None
        try:
            lo, hi = float(rng[0]), float(rng[1])
            conf = float(dec.get("confidence"))
        except (TypeError, ValueError):
            return None
        if lo <= conf <= hi:
            return (f"Momentum-News-Bremse: Konfidenz {conf:g} liegt im messbaren "
                    f"Verlustband {lo:g}-{hi:g} (bisher 43% Winrate, -201 USDT) – "
                    "Live-Entry ausgelassen, Sammel-Messung läuft weiter")
        return None

    async def _dup_entry_block(self, sym: str, action: str) -> Optional[str]:
        """Duplikat-Guard: offener KI-Trade gleicher Richtung jünger als Fenster?"""
        try:
            win = float(self.config.get("dup_entry_window_min", 30) or 0)
        except (TypeError, ValueError):
            win = 30.0
        if win <= 0:
            return None
        cutoff = (datetime.now(timezone.utc) - timedelta(minutes=win)).isoformat()
        try:
            dup = await self.db.auto_trades.find_one(
                {"status": "open", "strategy_id": "ai_trader", "symbol": sym,
                 "side": action, "opened_at": {"$gte": cutoff}}, {"id": 1})
        except Exception:
            return None
        if dup:
            return (f"Duplikat-Guard: offener {action}-Trade auf {sym} ist jünger als "
                    f"{win:g} min – kein identischer Doppel-Einstieg")
        return None

    async def _emit_signal(self, dec: Dict, collection: bool = False) -> bool:
        sym = dec["symbol"]
        # ---- Wochenend-Guard: reale Märkte zu -> kein KI-Einstieg ----
        # Eingefrorene Wochenend-Kurse (Forex/Rohstoffe/Indizes) erzeugen
        # Zombie-Trades, die tagelang Richtungs-Slots blockieren und die
        # ML-Labels/Regime-Statistik verschmutzen. Kein Feed-Eintrag (am
        # Wochenende käme sonst alle 15 min derselbe Spam).
        wk_closed, wk_why = market_hours.is_weekend_closed(sym)
        if wk_closed:
            logger.info(f"AI-Signal blockiert ({sym} {dec['action']}): {wk_why}")
            dec["blocked_by"] = wk_why
            return False
        # ---- Stale-Price-Guard: kein Einstieg auf veralteten Kursen ----
        # RCA 17.08.: zwei EURUSD-Trades nutzten denselben ~45 min alten Preis
        # (hängender Forex-Feed). Gilt für Live- UND Sammel-Trades (veraltete
        # Preise sind auch als ML-Label wertlos). 0 = aus.
        try:
            max_age_min = self._stale_limit_for(sym)
        except (TypeError, ValueError):
            max_age_min = 10.0
        if max_age_min > 0 and self.scanner is not None:
            buf = (getattr(self.scanner, "candle_buffer", {}) or {}).get(sym) or []
            last_ts = float((buf[-1].get("timestamp") or 0) if buf else 0)
            if last_ts > 0:
                age_min = (time.time() * 1000.0 - last_ts) / 60000.0
                if age_min > max_age_min:
                    why = (f"Stale-Price-Guard: letzter Kurs von {sym} ist {age_min:.0f} min "
                           f"alt (Limit {max_age_min:g} min) – kein Einstieg auf veralteten Daten")
                    logger.info(f"AI-Signal blockiert ({sym} {dec['action']}): {why}")
                    dec["blocked_by"] = why
                    return False
        # ---- Duplikat-Guard: kein identischer Symbol+Richtung-Doppeleinstieg ----
        dup_why = await self._dup_entry_block(sym, dec["action"])
        if dup_why:
            logger.info(f"AI-Signal blockiert ({sym} {dec['action']}): {dup_why}")
            dec["blocked_by"] = dup_why
            return False
        # ---- Momentum-News-Bremse (nur Live – Sammel-Trades messen weiter) ----
        if not collection:
            mb_why = self._momentum_brake_block(dec)
            if mb_why:
                logger.info(f"AI-Signal blockiert ({sym} {dec['action']}): {mb_why}")
                dec["blocked_by"] = mb_why
                return False
        # ---- Slippage-Wächter (nur Live): Momentum-Setups brauchen saubere Fills ----
        if not collection and slippage_guard.applies_to(dec.get("setup"), self.config):
            try:
                sl_stats = await slippage_guard.symbol_stats(self.db, sym, self.config)
                sg_why = slippage_guard.block_reason(sym, dec.get("setup"), sl_stats, self.config)
            except Exception as e:  # noqa: BLE001
                logger.debug(f"Slippage-Wächter übersprungen ({sym}): {e}")
                sg_why = None
            if sg_why:
                logger.info(f"AI-Signal blockiert ({sym} {dec['action']}): {sg_why}")
                dec["blocked_by"] = sg_why
                return False
        # ---- Regime-Sperrfilter (Default AUS) ----
        # Blockt LIVE-Neueinstiege in unklaren Regimen (Befund 17.08.:
        # range_ruhig nur ~10% Winrate). Sammel-Trades laufen bewusst weiter,
        # damit die Statistik den Filter später beweisen/widerlegen kann.
        if not collection and self.config.get("regime_block_enabled", False):
            try:
                from services.ai_market_observer import market_observer
                snap = market_observer.snapshots.get(sym) or {}
                reg = str(((snap.get("features") or {}).get("regime")) or "")
            except Exception:
                reg = ""
            block_list = [str(x) for x in (self.config.get("regime_block_list") or [])]
            if reg and reg in block_list:
                why = (f"Regime-Sperrfilter: {sym} ist im Regime '{reg}' – Neueinstiege in "
                       f"unklaren Marktphasen gesperrt (Sammel-Trades laufen weiter)")
                logger.info(f"AI-Signal blockiert ({sym} {dec['action']}): {why}")
                dec["blocked_by"] = why
                try:
                    await self.db.ai_chat.insert_one({
                        "id": str(uuid.uuid4()), "role": "governance",
                        "text": f"Trade {dec['action']} {sym} blockiert – {why}",
                        "ts": _now_iso()})
                except Exception:
                    pass
                return False
        # ---- MasterPrompt: oberstes Gebot, technisch erzwungen ----
        # max_open_trades zählt je Welt getrennt (Sammel vs. echtes Risiko).
        open_ai_trades = await self._open_ai_trades_count(collection)
        allowed, why = master_prompt.check_trade(
            sym, dec["action"], confidence=dec.get("confidence"), open_trades=open_ai_trades)
        if allowed and not collection:
            # Tages-Risiko-Limits zielen auf echtes Kapital – Sammel-Trades
            # sind immer Paper und werden davon nicht gebremst.
            day_pnl, day_trades = await self._today_risk()
            allowed, why = master_prompt.check_day(day_pnl, day_trades)
        if allowed:
            # ---- Diversifikation & Playbook (Richtungs-/Cluster-Guard) ----
            allowed, why = await self._diversification_gate(sym, dec, collection)
        if not allowed:
            logger.info(f"AI-Signal blockiert ({sym} {dec['action']}): {why}"
                        + (" [Datensammlung]" if collection else ""))
            dec["blocked_by"] = why
            if not collection:
                try:
                    await self.db.ai_chat.insert_one({
                        "id": str(uuid.uuid4()), "role": "governance",
                        "text": f"Trade {dec['action']} {sym} blockiert – {why}",
                        "ts": _now_iso()})
                except Exception:
                    pass
            return False
        if collection:
            cd = int(self.config.get("collection_cooldown_min", 30) or 0) * 60
            if cd and (time.time() - self._last_collection_ts.get(sym, 0)) < cd:
                return False
            cooldown = 0
        else:
            cooldown = self.config.get("cooldown_min", 45) * 60
        if cooldown:
            max_per_coin = max(1, min(5, int(self.config.get("max_trades_per_coin", 1) or 1)))
            if max_per_coin > 1:
                # KI-Trader mit mehreren Slots: Cooldown gilt PRO TRADE statt pro
                # Coin. Solange auf dem Coin noch freie Slots (max_trades_per_coin)
                # offen sind, wird der Coin-Cooldown übersprungen, damit die Slots
                # zeitnah gefüllt werden. Erst wenn die Slots voll sind, bremst der
                # Cooldown (die Slot-Obergrenze setzt on_signal ohnehin durch).
                try:
                    open_count = await self.db.auto_trades.count_documents(
                        {"symbol": sym, "status": "open", "strategy_id": "ai_trader"})
                except Exception:
                    open_count = 0
                if open_count >= max_per_coin and \
                        (time.time() - self._last_signal_ts.get(sym, 0)) < cooldown:
                    return False
            elif (time.time() - self._last_signal_ts.get(sym, 0)) < cooldown:
                return False
        entry = float(dec["price"])
        if entry <= 0:
            return False
        # Key-Level-Limit-Order: Entry wartet am Level (Order-Block/POC) statt
        # sofort Market zu füllen – SL/TP werden ab dem Limit-Preis gerechnet.
        # Ungültige Limit-Vorgaben (falsche Seite/zu weit weg) fallen auf
        # Market zurück, damit kein valides Signal verloren geht.
        limit_req = False
        if not collection and str(dec.get("entry_type") or "market") == "limit":
            ok_l, why_l = key_level_limits.validate(
                dec["action"], dec.get("limit_price"), entry, dec.get("horizon"))
            if ok_l:
                limit_req = True
                entry = float(dec["limit_price"])
            else:
                dec["limit_reject"] = why_l
                logger.info(f"AI-Signal {sym}: Limit-Entry verworfen ({why_l}) -> Market")
        # Makro-Parameter des Strategie-Kandidaten haben Vorrang vor den
        # spontanen Prozentwerten der Analyse (individuelle Feinjustierung
        # je eigener Strategie, siehe services/ai_strategy_lab.py).
        macro = strategy_lab.macro_params(dec.get("strategy_candidate_id"))
        is_swing = str(dec.get("horizon") or "scalp") == "swing"
        # Runner auch für News-/Scalp-Trades (services/runner_policy.py)
        runner = runner_policy.runner_allowed(dec, self.config)
        sl_input = macro.get("sl_fixed_percent", dec["sl_pct"])
        if is_swing:
            # Swing: eigene, weite Grenzen (niedriger Hebel wird unten gedeckelt)
            sl_pct = max(0.005, min(0.12, float(sl_input) / 100))
            tp1_pct = max(sl_pct * 1.2, min(0.25, dec["tp1_pct"] / 100))
            tpf_pct = max(tp1_pct, min(0.60, dec["tpf_pct"] / 100))
            if runner:
                tpf_pct = max(tpf_pct, 0.50)  # Endziel sehr weit -> Trailing übernimmt
        else:
            sl_pct = max(0.15, min(5.0, float(sl_input))) / 100
            if macro.get("tp1_crv"):
                tp1_pct = min(0.08, sl_pct * float(macro["tp1_crv"]))
            else:
                tp1_pct = max(sl_pct * 1.2, min(0.08, dec["tp1_pct"] / 100))
            if macro.get("tpf_crv"):
                tpf_pct = min(0.15, max(tp1_pct, sl_pct * float(macro["tpf_crv"])))
            else:
                tpf_pct = max(tp1_pct, min(0.15, dec["tpf_pct"] / 100))
        # Trade-Rahmen: CRV-Spanne (global vorgegeben) technisch erzwingen –
        # TP1 wird in [SL*crv_min, SL*crv_max] geklemmt, TP-Full folgt.
        tp1_pct, tpf_pct = self._apply_crv_frame(sl_pct, tp1_pct, tpf_pct, is_swing)
        if runner and not is_swing:
            tpf_pct = runner_policy.scalp_runner_tpf(sl_pct, tpf_pct)
        sign = 1 if dec["action"] == "LONG" else -1
        sl = entry * (1 - sign * sl_pct)
        tp1 = entry * (1 + sign * tp1_pct)
        tpf = entry * (1 + sign * tpf_pct)
        crv = round(abs(tp1 - entry) / abs(entry - sl), 2) if entry != sl else 0
        # ---- Strategie-Labor: Kandidaten erst nach Ghost-Phase + Freigabe live ----
        cand_id = dec.get("strategy_candidate_id")
        stage = strategy_lab.execution_stage(cand_id) if cand_id else None
        if cand_id and stage in ("ghost", "live_pending", "unknown", "rejected", None):
            if stage in ("unknown", "rejected", None):
                logger.info(f"AI-Signal {sym}: Kandidat {cand_id} unbekannt/abgelehnt "
                            "-> Kandidaten-Bezug verworfen")
                cand_id, stage = None, None
            else:
                if (time.time() - self._last_ghost_ts.get(sym, 0)) < cooldown:
                    return False
                try:
                    await strategy_lab.record_ghost_trade(
                        cand_id, sym, dec["action"], entry, sl, tp1,
                        reason=dec.get("reasoning", ""))
                    # Eigener Cooldown für Ghost-Trades: ein simulierter Test darf
                    # echte Signale auf dem Coin nicht blockieren.
                    self._last_ghost_ts[sym] = time.time()
                except Exception as ge:
                    logger.error(f"Ghost-Trade fehlgeschlagen: {ge}")
                return False
        now = self.scanner.berlin_now()
        rules_met = {"ai_active": True, "ai_direction": True, "ai_confidence": True, "ai_news": True}
        signal = {
            "symbol": sym,
            "type": dec["action"],
            "signal_class": "SIGNAL",
            "entry_price": round(entry, 6),
            "stop_loss": round(sl, 6),
            "take_profit_1": round(tp1, 6),
            "take_profit_full": round(tpf, 6),
            "crv": crv,
            "rsi": dec.get("rsi", 0),
            "ema_fast": 0,
            "ema_slow": 0,
            "rules_met": rules_met,
            "rules_met_count": 4,
            "rules_total": 4,
            "timestamp": _now_iso(),
            "trade_date": self.scanner.berlin_date(),
            "hour": now.hour,
            "weekday": now.weekday(),
            "session": self.scanner.get_current_session(),
            "strategy_id": "ai_trader",
            "strategy_name": "KI Trader",
            "status": "active",
            "ai_confidence": dec["confidence"],
            "ai_reasoning": dec["reasoning"],
            "ai_news_impact": dec.get("news_impact", "neutral"),
            "ai_size_reason": dec.get("size_reason"),
            "ai_levels_reason": dec.get("levels_reason"),
            "decision_id": dec.get("id"),
            "policy_version": dec.get("policy_version"),
            "use_ai_levels": bool(self.config.get("use_ai_levels")) or is_swing,
            "ai_horizon": "swing" if is_swing else "scalp",
            "ai_setup": dec.get("setup"),
            "ai_runner": runner,
            "ai_secure_runner": bool(runner and self.config.get("runner_secure_enabled", True)),
            "ai_secure_runner_trigger": float(self.config.get("runner_secure_trigger_pct", 30.0) or 30.0),
            "ai_secure_runner_max_lev": float(self.config.get("runner_secure_max_leverage", 100) or 100),
            "ai_candidate_id": cand_id,
            "cfg_overrides": strategy_lab.trade_overrides(cand_id) if cand_id else None,
            "force_paper": bool(cand_id and stage == "paper"),
            "force_paper_reason": ("Strategie-Kandidat noch nicht für Live freigegeben"
                                  if cand_id and stage == "paper" else None),
        }
        if collection:
            signal["data_collection"] = True
            signal["force_paper"] = True
            signal["force_paper_reason"] = "Datensammel-Modus (nur Paper)"
            signal["collection_reason"] = (
                "below_live_conf"
                if dec["confidence"] < int(self.config.get("min_confidence", 65) or 0)
                else "live_blocked")
        if is_swing:
            # Swing: eigener Timeframe-Schlüssel (Anti-Stacking blockiert Scalps nicht)
            signal["timeframe"] = "swing"
        # Maker-Order-Modus: unkritische KI-Entries als Post-Only-Limit;
        # Datensammel-Trades ausgenommen (sollen sofort füllen -> Lernen).
        if not collection and self.config.get("maker_mode"):
            await self._maker_check_performance()
            if not self._maker_suspended():
                signal["ai_maker_ok"] = True
                signal["ai_maker_wait_sec"] = int(self.config.get("maker_wait_sec", 45) or 45)
        # Hebel-Modus (global, AI-Panel): coin = Coin-Settings entscheiden
        # (bisheriges Verhalten) | auto = KI wählt pro Trade bis lev_auto_max |
        # fixed = fester Hebel. Swing bleibt immer auf swing_max_leverage gedeckelt.
        ai_lev = self._frame_leverage(dec, is_swing)
        if ai_lev > 0:
            signal["ai_leverage"] = ai_lev
        # Max. Kapital pro Trade (nur KI-Trader): Die KI hat pro Trade selbst
        # entschieden, wie viel Kapital (capital_pct vom Max) sie einsetzt.
        max_cap = float(self.config.get("max_capital_per_trade") or 0)
        if max_cap > 0:
            signal["ai_max_capital"] = max_cap
            signal["ai_capital_pct"] = max(10, min(100, int(dec.get("capital_pct", 100) or 100)))
        # ML-Risiko-Skalierung: Fällt die Vorhersagegüte des ML-Gates (OOS-AUC)
        # unter die Schwelle (Default 0.55), wird die Positionsgröße automatisch
        # reduziert (Default 50%) – direkte Koppelung ML-Labor -> Positionsgröße.
        try:
            from services.ml_gate import ml_gate
            ml_scale, ml_why = ml_gate.size_factor()
            if ml_scale < 1.0:
                signal["ml_risk_scale"] = ml_scale
                signal["ml_risk_reason"] = ml_why
                dec["ml_risk_scale"] = ml_scale
                logger.info(f"AI-Signal {sym}: ML-Risiko-Skalierung ×{ml_scale:g} ({ml_why})")
        except Exception as e:
            logger.debug(f"ML-Risiko-Skalierung nicht verfügbar: {e}")
        # Kapital-Zuweisung je Setup × Asset innerhalb der Anlageklasse
        # (services/setup_capital.py): Historie des Setups in der Klasse, auf
        # diesem Asset und Einstiegsqualität -> Faktor 0.25..1 unter dem Max-Kapital.
        # Ausgesetzte Kombinationen kamen nie hier an (Live-Gate), Sammel-Trades
        # bleiben unskaliert (Lernen braucht volle Größe).
        if not collection and dec.get("setup"):
            alloc = dec.get("setup_alloc")
            if not isinstance(alloc, dict):
                cls = setup_asset_class.asset_class_of(sym)
                alloc = setup_capital.allocation(
                    ai_playbook.class_stats(cls, dec["setup"]),
                    ai_playbook.asset_stats(cls, dec["setup"], sym),
                    dec.get("confidence"), self.config.get("min_confidence", 65),
                    setup_capital.gate_p_win(dec))
                dec["setup_alloc"] = alloc
            scale = float(alloc.get("scale") or 1.0)
            if 0 < scale < 1.0:
                signal["setup_asset_scale"] = scale
                signal["setup_asset_reason"] = alloc.get("note")
                logger.info(f"AI-Signal {sym}: Kapital-Zuweisung ×{scale:g} ({alloc.get('note')})")
        # Risiko-basierte Positionsgröße (services/position_sizing.py): Marge
        # und Hebel werden in bitunix_trade aus Equity × Risiko % / SL-Abstand
        # berechnet – ersetzt im Modus 'risk' die Kette max_capital × capital_pct.
        if str(self.config.get("sizing_mode", "legacy")) == "risk":
            signal["ai_sizing"] = position_sizing.build_params(
                self.config, dec, is_swing, float(signal.get("ml_risk_scale") or 1.0),
                collection=bool(collection),
                setup_scale=float(signal.get("setup_asset_scale") or 1.0))
        # Key-Level-Limit-Order: Signal NICHT sofort ausführen, sondern als
        # wartende Order am Level speichern. Fill/Ablauf prüft der Scheduler
        # (core/scheduler.py), Neu-Bewertung übernimmt jeder Analyse-Zyklus.
        if limit_req:
            signal["use_ai_levels"] = True
            signal["ai_limit_price"] = entry
            try:
                placed = await key_level_limits.place(self.db, signal, dec)
            except Exception as e:
                logger.error(f"Limit-Order-Platzierung {sym} fehlgeschlagen: {e}")
                return False
            if placed:
                self._last_signal_ts[sym] = time.time()
                dec["limit_order_id"] = placed
            return bool(placed)
        try:
            ok = await self.signal_cb(signal)
            if ok:
                if collection:
                    self._last_collection_ts[sym] = time.time()
                else:
                    self._last_signal_ts[sym] = time.time()
                dec["signal_id"] = signal.get("id")
            return bool(ok)
        except Exception as e:
            logger.error(f"AI signal emit failed for {sym}: {e}")
            return False


    def _maker_suspended(self) -> bool:
        """Maker-Order-Modus aktuell ausgesetzt? (abgelaufene Aussetzung
        wird automatisch aufgehoben)"""
        until = self.config.get("maker_suspended_until")
        if not until:
            return False
        try:
            if datetime.now(timezone.utc) >= datetime.fromisoformat(str(until)):
                self.config["maker_suspended_until"] = None
                return False
        except (TypeError, ValueError):
            self.config["maker_suspended_until"] = None
            return False
        return True

    async def _maker_check_performance(self):
        """Auto-Aussetzung durch den KI-Trader: Fill-Quote der letzten
        Maker-Versuche zu schlecht (<30% bei >=8 Versuchen) -> Modus 24h
        aussetzen und den Trader informieren (Glocke + Telegram).
        Der Trader kann jederzeit über das Setup wieder aktivieren."""
        if self._maker_suspended():
            return
        try:
            doc = await self.db.settings.find_one({"_id": "maker_mode_stats"}) or {}
        except Exception:
            return
        attempts = (doc.get("attempts") or [])[-10:]
        if len(attempts) < 8:
            return
        fills = sum(1 for a in attempts if a.get("filled"))
        rate = fills / len(attempts)
        if rate >= 0.3:
            return
        await self.update_config({"maker_suspend_hours": 24})
        try:
            await self.db.settings.update_one(
                {"_id": "maker_mode_stats"}, {"$set": {"attempts": []}})
        except Exception:
            pass
        msg = (f"Maker-Order-Modus für 24h ausgesetzt: nur {fills}/{len(attempts)} "
               "der letzten Limit-Entries wurden gefüllt – Entries laufen solange "
               "wieder als Market-Order. Wieder aktivieren: KI-Setup -> Maker-Order-Modus.")
        try:
            from services import notifications
            from core import state
            await notifications.website_notify(
                self.db, "maker_mode", "Maker-Modus ausgesetzt", msg,
                cooldown_min=60, source="KI-Trader")
            await notifications.telegram_notify(
                self.db, state.telegram, "maker_mode", f"⚠️ {msg}",
                cooldown_min=60)
        except Exception as e:
            logger.warning(f"Maker-Aussetzungs-Meldung fehlgeschlagen: {e}")
        logger.info(f"Maker-Modus auto-ausgesetzt (Fill-Quote {rate:.0%})")



    # ---------------- deep analysis (Tiefen-Analyst) ----------------
    async def run_deep_analysis(self, manual: bool = False, trigger: str = "schedule",
                                news_event: Optional[Dict] = None,
                                depth: str = "full") -> Dict:
        """Sehr tiefe Analyse durch die 'deep_analyst'-Rolle. Erzeugt einen
        Report (kein direkter Trade), der die regulären Analysen speist.

        trigger: 'schedule' (feste Uhrzeit), 'manual' oder 'news' (Event-getriggert –
        dann analysiert der Prompt explizit Ergebnis vs. Erwartung + Prognose).
        depth:   'full' = komplette Tiefenanalyse (Session-Openings, HIGH-Impact-News).
                 'update' = kompaktes, aufbauendes Delta-Update auf Basis der letzten
                 Tiefenanalyse (MEDIUM-News) – spart massiv Tokens beim teuren Modell."""
        from core.config import local_engine_disabled
        if not manual and local_engine_disabled():
            return {"status": "skipped",
                    "detail": "AI_TRADER_LOCAL_DISABLE aktiv (Preview-Instanz)"}
        try:
            symbols = [s for s in self.symbols
                       if len(self.scanner.candle_buffer.get(s, [])) >= 60]
            # Gleiche Asset-Auswahl wie die normale Analyse: nur Coins, die für
            # den KI Trader freigeschaltet sind (Modus paper/live, nicht 'off').
            try:
                docs = await self.db.strategy_coin_configs.find(
                    {"_id": {"$regex": "^ai_trader_"}}).to_list(200)
                modes = {d["_id"].replace("ai_trader_", "", 1):
                         (d.get("config") or {}).get("mode") for d in docs}
                from core.defaults import DEFAULT_STRATEGY_COIN_CFG
                default_mode = DEFAULT_STRATEGY_COIN_CFG.get("mode", "off")
                active = [s for s in symbols if (modes.get(s) or default_mode) != "off"]
                if active:
                    symbols = active
            except Exception as fe:
                logger.warning(f"Deep-Analyse Coin-Filter fehlgeschlagen: {fe}")
            # Token-Sparmodus für Event-getriggerte Läufe: nur die vom Ereignis
            # betroffenen Assets (+ Majors) analysieren, statischer Kontext
            # (Research/ML/Performance) bleibt draußen – analysiert wird die
            # ÄNDERUNG durch das Ereignis, nicht der komplette Markt erneut.
            lean_news = trigger == "news" and bool(news_event)
            # 'update': kompaktes Delta-Update (MEDIUM-News) – noch schlanker
            is_update = depth == "update" and lean_news
            if lean_news:
                # Betroffene Assets ALLER Klassen über die Alias-/Präfix-Auflösung
                # des News-Wächters (news_symbols) ermitteln – ein Gold-/Öl-/
                # Forex-Event trifft so auch Nicht-Krypto-Assets, nicht nur
                # exakte Symbol-Namen.
                from services.ai_news_watcher import news_symbols
                hit = news_symbols(news_event.get("events") or [], symbols)
                if hit:
                    majors = [s for s in ("BTCUSDT", "ETHUSDT")
                              if s in symbols and s not in hit]
                    symbols = hit + majors
                elif is_update:
                    # Update ohne klar betroffene Assets: nur die Majors updaten
                    symbols = [s for s in ("BTCUSDT", "ETHUSDT") if s in symbols] or symbols[:2]
            snap_map = {s: self._snapshot(s) for s in symbols}
            snaps = [v for v in snap_map.values() if v]
            covered = [s for s, v in snap_map.items() if v]
            # Adaptiver Nicht-Krypto-Vorfilter (services/deep_scope.py): relative
            # Volatilität lokal messen (0 LLM-Kosten) – erhöhte Gold/Öl/Indizes/
            # Forex-Assets werden für die Tiefenanalyse explizit markiert.
            vol_radar = ""
            if not lean_news:
                try:
                    from services import deep_scope
                    vol_radar = deep_scope.block_text(deep_scope.radar(
                        self.scanner.candle_buffer, covered))
                except Exception as ve:
                    logger.debug(f"Volatilitäts-Radar übersprungen: {ve}")
            news_block = "(News deaktiviert)"
            if self.config.get("news_enabled"):
                news = await news_feed.get_headlines(8 if is_update else (10 if lean_news else 25))
                news_block = "\n".join(f"- {n['title']} ({n['source']})" for n in news) or "(keine News)"
            macro = await self._macro_block()
            liq = "" if is_update else await self._liquidity_block()
            perf = "" if lean_news else await self._strategy_performance_text()
            directives = await self._user_directives()
            open_trades = await self._open_trades_text()
            lessons = "(keine)"
            if not is_update:
                lessons = await self.learning.lessons_text() if self.learning else "(keine)"
            research_block = ""
            ml_block = ""
            if not lean_news:
                try:
                    from services.ai_research import research_analyst
                    research_block = await research_analyst.context_text()
                except Exception:
                    pass
                try:
                    from services.ai_ml_lab import ml_lab
                    ml_block = await ml_lab.context_text()
                except Exception:
                    pass
            from services.ai_news_watcher import news_watcher
            nw_block = await news_watcher.context_text(2 if is_update else 5) \
                or "(keine relevanten Ereignisse)"
            # Aufbauendes Update: letzte Tiefenanalyse als Basis mitgeben,
            # damit nur die DELTAS analysiert werden (statt Komplett-Neuanalyse).
            prev_block = ""
            if is_update:
                prev = await self.db.settings.find_one({"_id": "ai_deep_report_full"}) \
                    or await self.db.settings.find_one({"_id": "ai_deep_report"}) or {}
                if prev.get("report"):
                    prev_outlook = "; ".join(
                        f"{o.get('symbol')}: {o.get('bias')}"
                        for o in (prev.get("outlook") or [])[:12] if isinstance(o, dict))
                    prev_block = (
                        f"=== DEINE LETZTE TIEFENANALYSE ({str(prev.get('ts', ''))[:16]}) ===\n"
                        f"{str(prev['report'])[:1500]}\n"
                        f"Outlook damals: {prev_outlook or '-'}\n\n"
                        "Baue auf dieser Analyse AUF und beschreibe nur, was sich "
                        "durch das neue Ereignis ÄNDERT.\n\n")
            trigger_block = ""
            if trigger == "news" and news_event:
                ev_lines = "\n".join(
                    f"- {e.get('title')} [Impact {e.get('impact')}, betrifft "
                    f"{','.join(e.get('affects') or []) or '-'}]"
                    for e in (news_event.get("events") or [])[:5])
                trigger_block = (
                    f"=== AUSLÖSER DIESER TIEFENANALYSE: NEWS-EREIGNIS "
                    f"(Relevanz {str(news_event.get('severity', '?')).upper()}) ===\n"
                    f"{news_event.get('summary', '')}\n{ev_lines}\n\n"
                    "Diese Tiefenanalyse wurde automatisch durch das obige Ereignis ausgelöst. "
                    "Gehe im Report explizit darauf ein:\n"
                    "1) Tatsächliches Ergebnis/Ereignis vs. vorherige Erwartung/Prognose – "
                    "Überraschung oder erwartet?\n"
                    "2) Ist die Marktreaktion bereits eingepreist oder läuft sie noch?\n"
                    "3) Kurzfristige Prognose je Asset unter Einbezug dieses Ereignisses "
                    "(Szenarien + Invalidierungslevel).\n\n"
                    "TOKEN-SPARMODUS: Analysiere primär, was sich durch dieses Ereignis "
                    "GEÄNDERT hat – keine vollständige Neubewertung unveränderter "
                    "Marktstruktur, fasse Unverändertes in je 1 Satz zusammen.\n\n")
            berlin = self.scanner.berlin_now().strftime("%d.%m.%Y %H:%M")
            prompt = (
                f"{master_prompt.prompt_block()}\n\n"
                f"{self._role_context_block()}\n\n"
                f"Zeit (Berlin): {berlin}\n\n"
                f"=== MARKTDATEN (Multi-Timeframe) ===\n" +
                "\n".join(v["text"] for v in snaps) +
                (f"\n\n{macro}" if macro else "") +
                (f"\n\n{liq}" if liq else "") +
                (f"\n\n{vol_radar}" if vol_radar else "") +
                f"\n\n=== NEWS ===\n{news_block}\n\n"
                f"=== NEWS-WÄCHTER EREIGNISSE ===\n{nw_block}\n\n"
                f"{prev_block}"
                f"{trigger_block}"
                + ("" if lean_news else
                   f"=== PERFORMANCE ALLER STRATEGIEN DER PLATTFORM (lerne daraus) ===\n{perf}\n\n")
                + f"=== GELERNTE LEKTIONEN ===\n{lessons}\n\n"
                + (f"{research_block}\n\n" if research_block else "")
                + (f"{ml_block}\n\n" if ml_block else "")
                + f"=== ANWEISUNGEN DES TRADERS ===\n{directives}\n\n"
                f"=== OFFENE POSITIONEN ===\n{open_trades}\n\n"
                f"WICHTIG: Das 'outlook'-Array muss GENAU EINEN Eintrag für JEDES dieser "
                f"{len(covered)} Assets enthalten (keines auslassen): {', '.join(covered)}\n\n"
                + ("Erstelle jetzt das kompakte Delta-Update als JSON."
                   if is_update else "Erstelle jetzt die tiefe Marktanalyse als JSON.")
            )
            text, provider, model = await self.generate_for_role(
                "deep_analyst", prompt,
                DEEP_UPDATE_SYSTEM if is_update else DEEP_ANALYSIS_SYSTEM,
                temperature=0.4)
            data = self._parse_json(text)
            now = _now_iso()
            outlook_new = [o for o in (data.get("outlook") or []) if isinstance(o, dict)][:40]
            if is_update:
                # Delta-Update: Outlooks der nicht betroffenen Assets aus der
                # letzten Analyse übernehmen (Konsumenten sehen weiter ALLE Assets).
                try:
                    prev_doc = await self.db.settings.find_one({"_id": "ai_deep_report"}) or {}
                    seen = {str(o.get("symbol")) for o in outlook_new}
                    for o in (prev_doc.get("outlook") or []):
                        if isinstance(o, dict) and str(o.get("symbol")) not in seen:
                            outlook_new.append(o)
                except Exception:
                    pass
            doc = {
                "report": str(data.get("report", ""))[:4000],
                "outlook": outlook_new[:40],
                "risks": [str(r)[:200] for r in (data.get("risks") or [])][:8],
                "recommendations": [str(r)[:250] for r in (data.get("recommendations") or [])][:8],
                "model": f"{provider}/{model}",
                "weight": ai_providers.model_weight(model),
                "weight_label": ai_providers.weight_label(model),
                "ts": now,
                "manual": manual,
                "trigger": "manual" if manual else trigger,
                "depth": "update" if is_update else "full",
            }
            await self.db.settings.update_one(
                {"_id": "ai_deep_report"}, {"$set": dict(doc)}, upsert=True)
            if not is_update:
                # Letzte VOLLE Analyse separat merken – Updates bauen darauf auf
                await self.db.settings.update_one(
                    {"_id": "ai_deep_report_full"}, {"$set": dict(doc)}, upsert=True)
            await self.db.ai_chat.insert_one({
                "id": str(uuid.uuid4()), "role": "deep_analysis",
                "text": doc["report"], "outlook": doc["outlook"], "risks": doc["risks"],
                "recommendations": doc["recommendations"], "model": doc["model"],
                "weight_label": doc["weight_label"], "manual": manual, "ts": now,
                "trigger": doc["trigger"], "depth": doc["depth"],
            })
            self.deep_last = now
            self.deep_last_error = None
            logger.info(f"Deep analysis done ({doc['model']}): "
                        f"{len(doc['outlook'])} Outlooks, {len(doc['recommendations'])} Empfehlungen")
            return {"status": "ok", "report": doc["report"], "model": doc["model"],
                    "ts": doc["ts"], "outlooks": len(doc["outlook"])}
        except Exception as e:
            self.deep_last_error = str(e)[:300]
            logger.error(f"Deep analysis failed: {e}")
            return {"status": "error", "detail": self.deep_last_error}

    async def _check_deep_schedule(self):
        """Feuert die Tiefenanalyse zu den konfigurierten Berlin-Uhrzeiten.
        Bereits vergangene Slots des Tages werden beim Boot übersprungen."""
        cfg = role_manager.role_cfg("deep_analyst")
        if not cfg.get("enabled", True):
            return
        # Tiefenanalysen dauern Minuten und blockieren den schnellen Event-Takt:
        # im FOMC-Fenster aufschieben – der Slot feuert danach automatisch nach.
        try:
            from services import econ_event, fomc_event
            if fomc_event.window_active() or econ_event.any_window_active():
                return
        except Exception:
            pass
        times = cfg.get("schedule_times") or []
        if not times:
            return
        now_b = datetime.now(BERLIN_TZ)
        today = now_b.strftime("%Y-%m-%d")
        cur = now_b.strftime("%H:%M")
        for slot in times:
            if cur < slot:
                continue
            if slot not in self._deep_ran:
                self._deep_ran[slot] = today  # Boot: vergangenen Slot überspringen
                continue
            if self._deep_ran[slot] == today:
                continue
            self._deep_ran[slot] = today
            logger.info(f"Deep analysis Slot {slot} Berlin fällig – starte Tiefenanalyse")
            await self.run_deep_analysis(manual=False)

    # ---------------- background loop ----------------
    async def run_loop(self):
        self.running = True
        logger.info("AI Trader engine loop started (multi-provider: gemini/groq/openrouter/mistral)")
        while self.running:
            await asyncio.sleep(5)
            try:
                # Housekeeping läuft IMMER (auch wenn Engine aus ist / kein Key), damit
                # stündliches Analyse-Cleanup und der 00:00-Berlin-Reset zuverlässig feuern.
                try:
                    await self._run_housekeeping()
                except Exception as hk_err:
                    logger.error(f"AI housekeeping loop error: {hk_err}")

                # Preview-/Dev-Guard: zweite Instanz neben der Render-Prod darf
                # weder LLM-Analysen fahren noch Trades auslösen (geteilte DB).
                from core.config import local_engine_disabled
                if local_engine_disabled():
                    self.next_run = None
                    continue

                # Lern-Modul: Ergebnisse synchronisieren + ggf. Lernlauf nach Trade-Close
                try:
                    if self.learning:
                        await self.learning.tick()
                except Exception as le:
                    logger.error(f"AI learning tick error: {le}")

                # Tiefen-Analyst: geplante Deep-Analysen (Berlin-Uhrzeiten)
                try:
                    await self._check_deep_schedule()
                except Exception as de:
                    logger.error(f"AI deep schedule error: {de}")

                # 20-Trade-Review nach dem Heatmap-Fix (max. alle 10 min prüfen)
                try:
                    if time.time() - self._review_last_check > 600:
                        self._review_last_check = time.time()
                        await self._check_heatmap_review()
                except Exception as hre:
                    logger.error(f"AI heatmap review error: {hre}")

                # KI-Ökosystem: Markt-Beobachter (Datensammlung), Forschungs-Analyst
                # (Backtest-/Optimizer-Auswertung) und ML-Labor (Optuna/XGBoost).
                # Alle drei laufen unabhängig von der Analyse-Engine weiter.
                for name, mod_attr in (("market observer", "ai_market_observer.market_observer"),
                                       ("market radar", "ai_market_radar.market_radar"),
                                       ("research analyst", "ai_research.research_analyst"),
                                       ("ml lab", "ai_ml_lab.ml_lab"),
                                       ("ml gate", "ml_gate.ml_gate"),
                                       ("strategy lab", "ai_strategy_lab.strategy_lab"),
                                       ("policy lab", "policy_lab.policy_lab"),
                                       ("trade manager", "ai_trade_manager.trade_manager")):
                    try:
                        mod_name, obj_name = mod_attr.split(".")
                        mod = __import__(f"services.{mod_name}", fromlist=[obj_name])
                        await getattr(mod, obj_name).tick()
                    except Exception as ex:
                        logger.error(f"AI {name} tick error: {ex}")
                try:
                    from services.ai_memory import memory
                    await memory.housekeeping()
                except Exception as me:
                    logger.error(f"AI memory housekeeping error: {me}")

                if not self.config.get("enabled") or not self.key:
                    self.next_run = None
                    continue
                now = time.time()
                if now >= self._next_due:
                    interval_min, window = self.current_interval()
                    # FOMC-Fenster: dichter analysieren (Event-Trading braucht Tempo)
                    try:
                        from services import fomc_event
                        if fomc_event.window_active():
                            interval_min = min(interval_min, fomc_event.FAST_INTERVAL_MIN)
                            window = f"{window} +FOMC"
                    except Exception:
                        pass
                    # CPI-/NFP-Fenster: gleiches Tempo wie beim FOMC-Event
                    try:
                        from services import econ_event
                        _ev = econ_event.active_event()
                        if _ev:
                            interval_min = min(interval_min, _ev.fast_interval_min)
                            window = f"{window} +{_ev.key.upper()}"
                    except Exception:
                        pass
                    # Am Voll-Stunden-Raster ausrichten: Läufe fallen immer auf
                    # gerade Uhrzeiten (z.B. 15 min -> :00/:15/:30/:45 Berlin).
                    self._next_due = ai_schedule.next_aligned_ts(
                        now, max(1, interval_min), BERLIN_TZ)
                    self.next_run = datetime.fromtimestamp(
                        self._next_due, timezone.utc).isoformat()
                    self.active_window = window
                    logger.info(f"AI Analyse-Zyklus ({window}: alle {interval_min} min)")
                    # RAM-Bremse: Zyklus verschieben statt OOM zu riskieren
                    from services import ram_queue
                    await ram_queue.wait_for_ram(110, 600, "ki-analyse")
                    await self.run_analysis()
                else:
                    # Zwischen den Zyklen: Sweep-Trigger (lokal, ohne LLM) – feuert
                    # bei Wick-Sweep eine gezielte Einzel-Symbol-Analyse (Budget).
                    try:
                        await sweep_trigger.check(self)
                    except Exception as ste:
                        logger.error(f"Sweep-Trigger error: {ste}")
                    # TF2-Trigger (Trendfolge-2-Signal, lokal) – gleiche Mechanik
                    try:
                        await tf2_signal.check(self)
                    except Exception as tfe:
                        logger.error(f"TF2-Trigger error: {tfe}")
                    # Setup-Review (täglich): inaktive/chronisch negative Setups
                    # deaktivieren + überarbeiten (max. 3 LLM-Aufrufe/Tag)
                    try:
                        await setup_review.maybe_run(self.db)
                    except Exception as sre:
                        logger.error(f"Setup-Review error: {sre}")
            except Exception as e:
                logger.error(f"AI loop error: {e}")

    # ---------------- chat ----------------
    async def chat_history(self, limit: int = 80) -> List[Dict]:
        """Liefert den Chatverlauf für das Frontend.

        Garantiert, dass die aktuelle gepinnte Tages-Summary IMMER als erstes
        Element enthalten ist – unabhängig vom Limit. Ohne diese Absicherung
        würde die Summary (älteste Nachricht des Tages) nach ~limit
        Neu-Nachrichten aus dem `sort("ts", -1).limit(limit)`-Fenster fallen
        und im Frontend nicht mehr angezeigt werden."""
        pinned = await self.db.ai_chat.find_one(
            {"role": "summary", "pinned": True}, sort=[("ts", -1)],
            projection={"_id": 0}
        )
        # Projection + Index (core/indexes.py: ai_chat_ts) halten die Abfrage auch
        # bei vielen tausend Nachrichten schnell.
        rows = await self.db.ai_chat.find({}, projection={"_id": 0}) \
            .sort("ts", -1).limit(limit).to_list(limit)
        rows.reverse()
        if pinned:
            pinned_id = pinned.get("id")
            # Dedupe: falls die gepinnte Summary bereits im Fenster ist, entferne
            # sie dort – sie wird stattdessen garantiert an den Anfang gesetzt.
            if pinned_id:
                rows = [r for r in rows if r.get("id") != pinned_id]
            rows = [pinned] + rows
        return rows

    async def chat_stream(self, text: str, coins=None):
        """SSE-Streaming der KI-Antwort. Wechselt bei 429 automatisch das Modell
        innerhalb desselben Providers. Unterstützt Gemini + OpenAI-kompatible
        Provider (Groq, OpenRouter, Mistral).

        `coins`: optionale Liste der Symbole, auf die der Chat-Kontext
        eingegrenzt wird (leer / None / "ALL" => alle Coins)."""
        chain = role_manager.chain("chat", self.config)
        if not any(ai_providers.provider_keys(p) for p, _ in chain):
            yield "⚠️ Kein API-Key für die konfigurierten Provider gesetzt – bitte in Render EnvVars setzen."
            return

        hist_rows = await self.db.ai_chat.find({"role": {"$in": ["user", "assistant", "summary"]}}) \
            .sort("ts", -1).limit(14).to_list(14)
        hist_rows.reverse()
        def _role_label(r):
            role = r.get("role")
            if role == "user":
                return "Nutzer"
            if role == "summary":
                return f"KI-Tageszusammenfassung ({r.get('day', '')})"
            return "KI"
        history = "\n".join(
            f"{_role_label(r)}: {r.get('text', '')}" for r in hist_rows
        ) or "(noch keine Nachrichten)"
        await self.db.ai_chat.insert_one({
            "id": str(uuid.uuid4()), "role": "user", "text": text, "ts": _now_iso(),
        })

        # Trader-Anweisungen REAL ausführen (Positionen schließen, Lektionen,
        # Einstellungen ...), bevor die Antwort generiert wird. Die echten
        # Ergebnisse fließen in den Kontext ein – die KI berichtet nur Fakten.
        exec_block = ""
        try:
            from services.ai_chat_commands import chat_commands
            cmd_res = await chat_commands.run(self, text)
            if cmd_res and cmd_res.get("results_text"):
                exec_block = ("\n\n=== SOEBEN REAL AUSGEFÜHRTE AKTIONEN "
                              "(vom System verifiziert) ===\n"
                              + cmd_res["results_text"])
        except Exception as e:
            logger.error(f"Chat-Kommandos fehlgeschlagen: {e}")

        context = await self._context_brief(coins=coins)
        system = CHAT_SYSTEM_TEMPLATE.format(context=context, history=history) + exec_block

        acc = ""
        async for kind, payload in ai_providers.stream_chain(chain, text, system, temperature=0.6, role="chat"):
            if kind == "token":
                acc += payload
                yield payload
            elif kind == "meta":
                provider, model = payload
                self._effective_provider, self._effective_model = provider, model
                if model != self.config.get("model"):
                    logger.info(f"AI chat: genutzt {provider}/{model}")
            elif kind == "error":
                err = f"\n⚠️ {payload}"
                acc += err
                yield err

        if acc:
            await self.db.ai_chat.insert_one({
                "id": str(uuid.uuid4()), "role": "assistant", "text": acc, "ts": _now_iso(),
            })

    async def clear_chat(self):
        await self.db.ai_chat.delete_many({})

    def current_interval(self) -> tuple:
        """Aktuelles Analyse-Intervall gemäß Zeitplan (Berlin-Zeit)."""
        now = self.scanner.berlin_now()
        minutes = now.hour * 60 + now.minute
        return ai_schedule.effective_interval(
            self.config.get("schedule"), self.config.get("interval_min", 10), minutes)

    async def _check_heatmap_review(self):
        """20-Trade-Review: Nach 20 geschlossenen KI-Trades seit dem Heatmap-Fix
        einmalig eine statistische Auswertung in den Feed schreiben (ohne
        LLM-Kosten), damit der Trader sieht, ob es ohne Heatmap besser läuft."""
        if self.db is None:
            return
        doc = await self.db.settings.find_one({"_id": "ai_heatmap_review"}) or {}
        if doc.get("done"):
            return
        if not doc.get("start_ts"):
            await self.db.settings.update_one(
                {"_id": "ai_heatmap_review"},
                {"$set": {"start_ts": _now_iso(), "done": False, "target_trades": 20}},
                upsert=True)
            return
        target = int(doc.get("target_trades", 20) or 20)
        trades = await self.db.auto_trades.find(
            {"strategy_id": "ai_trader", "status": "closed",
             "closed_at": {"$gte": doc["start_ts"]}}).to_list(500)
        if len(trades) < target:
            return
        pnls = [float(t.get("realized_pnl") or 0) for t in trades]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p < 0]
        wr = round(len(wins) / len(pnls) * 100) if pnls else 0
        stats = {
            "trades": len(pnls), "winrate_pct": wr,
            "pnl_total": round(sum(pnls), 2),
            "avg_win": round(sum(wins) / len(wins), 2) if wins else 0,
            "avg_loss": round(sum(losses) / len(losses), 2) if losses else 0,
            "use_heatmap_data": bool(self.config.get("use_heatmap_data", False)),
            "use_liquidation_data": bool(self.config.get("use_liquidation_data", True)),
            "since": doc["start_ts"],
        }
        text = (f"📊 20-TRADE-REVIEW seit dem Heatmap-Fix "
                f"({timeutil.fmt_berlin(doc['start_ts'])}): {stats['trades']} Trades · "
                f"Winrate {wr}% · PnL {stats['pnl_total']:+.2f} USDT · "
                f"Ø Gewinn {stats['avg_win']:+.2f} / Ø Verlust {stats['avg_loss']:+.2f} USDT. "
                f"Einstellungen: Heatmap-Daten "
                f"{'AN' if stats['use_heatmap_data'] else 'AUS'}, echte Liquidations-Daten "
                f"{'AN' if stats['use_liquidation_data'] else 'AUS'}. "
                + ("Winrate im Ziel-Sweetspot (30-50%+) – Setup beibehalten."
                   if wr >= 30 else
                   "Winrate unter 30% – Setup prüfen (Lektionen/Filter nachschärfen)."))
        await self.db.ai_chat.insert_one({
            "id": str(uuid.uuid4()), "role": "learning",
            "trigger": "heatmap_review", "text": text,
            "stats": stats, "ts": _now_iso()})
        await self.db.settings.update_one(
            {"_id": "ai_heatmap_review"},
            {"$set": {"done": True, "finished_ts": _now_iso(), "stats": stats}})
        logger.info(f"AI 20-Trade-Review veröffentlicht: {stats}")

    def status(self) -> Dict:
        from services.ai_news_watcher import news_watcher
        from core.config import local_engine_disabled
        cfg_view = dict(self.config)
        if local_engine_disabled():
            # Preview-Instanz: Engine gilt lokal als AUS (Prod-Config unverändert)
            cfg_view["enabled"] = False
            cfg_view["local_disabled"] = True
        return {
            "config": cfg_view,
            "has_key": bool(self.key),
            "provider_keys": self._available_providers(),
            "backup_keys": ai_providers.backup_keys_info(),
            "analyzing": self._analyzing,
            "last_run": self.last_run,
            "next_run": self.next_run,
            "last_error": self.last_error,
            "decisions": self.decisions,
            "allowed_models": ALLOWED_MODELS,
            "model_weights": ai_providers.MODEL_WEIGHTS,
            "effective_model": self._effective_model,
            "effective_provider": self._effective_provider,
            "learning": self.learning.summary() if self.learning else None,
            "roles": role_manager.snapshot(),
            "deep_last": self.deep_last,
            "deep_last_error": self.deep_last_error,
            "news_watcher": news_watcher.status(),
            "schedule_active": {
                "interval_min": self.current_interval()[0],
                "window": self.current_interval()[1],
                "text": ai_schedule.schedule_text(self.config.get("schedule"),
                                                 self.config.get("interval_min", 10)),
            },
            "providers_health": ai_providers.health_status(),
            "day_risk": self._day_risk_cache,
            "master_prompt": master_prompt.snapshot(),
            "validation": validation_gate.status(),
            "strategy_lab": strategy_lab.status(),
            "fomc": self._fomc_status_safe(),
            "econ": self._econ_status_safe(),
            "key_credits": self._key_credits_safe(),
        }

    @staticmethod
    def _fomc_status_safe() -> Optional[Dict]:
        try:
            from services import fomc_event
            return fomc_event.status_snapshot()
        except Exception:
            return None

    @staticmethod
    def _econ_status_safe() -> Optional[Dict]:
        try:
            from services import econ_event
            return econ_event.status_snapshots()
        except Exception:
            return None

    @staticmethod
    def _key_credits_safe() -> Optional[Dict]:
        try:
            from services import key_credits
            return key_credits.snapshot()
        except Exception:
            return None


ai_engine = AIEngine()
