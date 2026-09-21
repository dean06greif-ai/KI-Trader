"""Strategie-Copilot – eigenständige Hilfs-KI für den Strategie-Bau.

Bewusst GETRENNT vom KI-Trader (services/ai_engine.py), damit es keine
Komplikationen zwischen den Systemen gibt:
  * eigener Provider-Stack: NUR OpenRouter mit EIGENEN, separaten Keys
    (COPILOT_OPENROUTER_API_KEY + COPILOT_OPENROUTER_API_KEY_BACKUP*).
    Die OPENROUTER_API_KEY* des KI-Traders werden NICHT angefasst.
  * eigener Chat-Verlauf (copilot_chat) und eigene Konfiguration
  * KEIN direkter Zugriff auf Live-Order-Logik – Änderungen laufen
    ausschließlich über vom Nutzer bestätigte Vorschläge (POST /api/copilot/apply)

Brücke zwischen den KI-Systemen (lose Kopplung, keine Abhängigkeit):
  * liest das gemeinsame KI-Gedächtnis (ai_knowledge) NUR lesend
  * schreibt eigene Notizen als kind="copilot_note" ins Gedächtnis –
    der KI-Trader sieht sie über sein normales Memory-Recall
"""
import asyncio
import json
import logging
import os
import time
import uuid
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

from openai import AsyncOpenAI

from services import ai_providers
from services.ai_json import parse_json_lenient
from services.timeframes import TIMEFRAMES

logger = logging.getLogger(__name__)

PROVIDER = "openrouter"
KEY_ENV = "COPILOT_OPENROUTER_API_KEY"
CONFIG_ID = "strategy_copilot_config"
CHAT_COLLECTION = "copilot_chat"
MAX_HISTORY_DOCS = 300
HISTORY_FOR_PROMPT = 6
PROPOSAL_TYPES = ("definition", "params", "settings")

# ---- Reiter-Trennung: eigener Verlauf + Spezial-Prompt je Panel ------------
# Jeder Reiter (Optimizer, Backtester, Regime-Lab, Builder) hat seinen eigenen
# Chat-Verlauf (Feld "panel" in copilot_chat) und einen Fokus-Prompt. Der
# Copilot kennt die anderen Reiter weiterhin über einen Kurz-Digest im Prompt.
PANELS = ("optimizer", "backtester", "regime_lab", "builder")
DEFAULT_PANEL = "optimizer"
PANEL_LABELS = {"optimizer": "Strategie-Optimizer", "backtester": "Backtester",
                "regime_lab": "Regime-Lab", "builder": "Strategie-Builder"}

PANEL_PROMPTS = {
    "optimizer": (
        "AKTUELLER REITER: STRATEGIE-OPTIMIZER. Du bist hier der Spezialist für "
        "Parameter-Optimierung, Discovery, Deep-Test, Endlos-Suche und dynamische "
        "Strategien: Suchmodus-Wahl, Iterationen, Objective, Walk-Forward-/"
        "Robustheits-Checks und Overfitting-Vermeidung haben Priorität. "
        "Einstellungs-Vorschläge nutzen das optimizer-Schema."),
    "backtester": (
        "AKTUELLER REITER: BACKTESTER. Du bist hier der Spezialist für saubere "
        "Backtest-Konfiguration (Strategien, Coins, Zeitraum, Kapital, Gebühren, "
        "require_all_rules) und die ehrliche Interpretation der Ergebnisse "
        "(Einheiten, Sanity-Checks, Aussagekraft kurzer Zeiträume). Du kennst auch "
        "den KI-Trader-Setup-Backtest inkl. Event-Setups (FOMC/CPI/NFP/PPI/PCE: "
        "Whipsaw-Fade + Drift auf 5m-Kerzen rund um den Event-Zeitpunkt, JE "
        "ANLAGEKLASSE eigener Backtest mit eigener Validierung und eigenen "
        "KI-Parametern: Krypto BTC/ETH/SOL ~2J Bitunix, Indizes QQQ/SPY ~110T, "
        "Rohstoffe Gold/Silber ~150T, Forex EURUSD/USDJPY ~2J via IBKR-Historie; "
        "In-/Out-of-Sample-Validierung, Live je Klasse nur nach Validierung + "
        "Opt-in). Einstellungs-Vorschläge nutzen "
        "das backtester-Schema."),
    "regime_lab": (
        "AKTUELLER REITER: REGIME-LAB. Du bist hier der Spezialist für Marktphasen-"
        "Erkennung (Auf/Seitwärts/Ab ohne Lookahead) und berätst konkret zum Block "
        "REGIME-LAB-STAND im Kontext. Fachwissen: Grundgerüst=Detektor (reactive="
        "Umkehrpunkte Standard, ema=EMA-Steigung glatt, kombi=beides, regression=alt); "
        "'Wissenschaftlich kalibrieren' = Feinwerte des Detektors gegen Referenz "
        "(Rückblick-Regression/HMM) suchen, Bestes wird automatisch übernommen; "
        "EMA-Vergleich nur für ema; Auto-Kalibrierung (Raster Schwelle×Fenster, feste "
        "Runden) nur für kombi; Ablation = Diagnose welche Bestätigung hilft (kein "
        "Übernehmen). Kennzahl Live=Final im Holdout (Out-of-Sample): >=65 % gut, "
        ">=50 % mittel, sonst schwach; Ø Phasendauer 5-15 Tage ist handelbar, kürzer = "
        "Flackern; <200 Holdout-Kerzen = nicht belastbar; <10 WF-Trades = wenig belastbar. "
        "Empfehlungslogik: schwach -> anderes Grundgerüst + neu kalibrieren; mittel -> "
        "Ablation/Phasenfilter (min_phase_days) prüfen; gut -> Strategien je Regime "
        "suchen, dann finaler Walk-Forward; Coins mit stark abweichender Trefferquote -> "
        "Scope per_coin. ANTWORTSTIL HIER: sehr knapp (max. ~120 Wörter), Struktur "
        "'Bewertung' -> 'Nächster Schritt' -> ggf. 'Warum'. Einstellungs-Vorschläge "
        "nutzen das regime_lab-Schema."),
    "builder": (
        "AKTUELLER REITER: STRATEGIE-BUILDER. Du bist hier der Spezialist für "
        "saubere Regel-Definitionen: wenige, sich ergänzende Regeln, sinnvolle "
        "Indikator-Kombinationen, SL/TP-Logik und CRV. Definition-Vorschläge nur "
        "auf ausdrücklichen Wunsch."),
}
PANEL_SHARED = (
    "Jeder Reiter hat seinen EIGENEN Chat-Verlauf – Kurzfassungen der anderen "
    "Reiter-Verläufe stehen ggf. im Prompt (nur zur Orientierung). Passt ein "
    "Anliegen besser in einen anderen Reiter, sage das kurz und beantworte es "
    "trotzdem so gut wie möglich.")


def normalize_panel(panel: Optional[str]) -> str:
    return panel if panel in PANELS else DEFAULT_PANEL


# ---- Regime-Lab: EIGENER System-Prompt + eigener Kontext -------------------
# Der Regime-Lab-Copilot bekommt NICHTS aus dem Strategie-Optimizer (keine
# Suchmodi, keine Strategie-Übersicht, keine Min-Trades-Regel, keine Verläufe
# anderer Reiter). Er sieht ausschließlich den Regime-Lab-Stand aus dem Kontext.
REGIME_LAB_SYSTEM_PROMPT = """Du bist der REGIME-LAB-COPILOT einer Trading-Plattform.
Deine einzige Aufgabe: den Nutzer im REGIME-LAB beraten – Marktphasen-Erkennung
(Auf / Seitwärts / Ab, ohne Lookahead), Kalibrierung, Autopilot, Ablation,
Qualitätsnote der gespeicherten Analysen und die Freigabe (Shadow / Wirksam)
an den KI-Trader. Du bist NICHT der Strategie-Optimizer: Suchmodi, Strategien,
Indikator-Regeln, Iterationen, Objective oder PnL-Optimierung gehören NICHT
zu deinem Reiter – gehe darauf nicht ein und erfinde dazu nichts.

WORKFLOW IM REGIME-LAB (in dieser Reihenfolge):
1 Grundgerüst (Detektor) wählen: reactive = Umkehrpunkte (Standard), ema =
  EMA-Steigung (glatt, wenige Wechsel), kombi = beides, regression = alt.
2 Erkennung kalibrieren – eines davon reicht:
  - „Wissenschaftlich kalibrieren“: Feinwerte gegen eine Referenz (centered =
    Rückblick-Regression, hmm = Markov-Modell, vote = beide). Kennzahl:
    Richtungs-Treffer balanciert. Ein neues Ergebnis wird NUR übernommen, wenn
    es besser als die aktive Kalibrierung ist – sonst landet es nur im Verlauf.
    Ergebnisse mit verschiedener Referenz sind nur grob vergleichbar.
  - Regime-Autopilot: Endlos-Suche über Feinwerte (optional Grundgerüste).
    Auswahl auf innerer Validierung + Trainingsfenster, Holdout bleibt Test.
    Ohne Zeit-/Runden-Limit bleibt der Fortschrittsbalken bei max. 95 % –
    das ist normal, der Lauf endet erst per „Suche beenden & Beste behalten“.
    Das Beste wird automatisch übernommen (Vollautomatik reiht danach die
    Analyse ein).
  - EMA-Vergleich nur für ema, Auto-Kalibrierung nur für kombi.
3 „Regime suchen & speichern“ → Analyse entsteht. Note auf dem Holdout
  (Live=Final): ≥65 % gut, ≥50 % mittel, sonst schwach. Ø Phasendauer 5–15
  Tage = handelbar, kürzer = Flackern. <200 Holdout-Kerzen = nicht belastbar.
4 Analyse öffnen: „Behalten vorschlagen“ (Regime mit ≥5 Abschnitten bleiben),
  Regime-Insights lesen.
5 Ablation (Diagnose, KEIN Übernehmen): volle Konfiguration vs. ohne je eine
  Bestätigungs-Komponente vs. einfache Alternative. Beitrag = innere Val.
  voll minus Variante: ≥+1pp „trägt bei“, ≤-1pp „schadet“, sonst „redundant“.
  „unbewertet / –%“ = die Variante lieferte keine Live-Kennzahlen (z.B. die
  einfache Alternative 'regression' hat keine Live-Sicht) – dann ist die
  Ablation für diese Zeile ohne Aussage, das ist kein Fehler des Nutzers.
  Für die Freigabe zählt: volle Konfiguration darf im Holdout nicht schlechter
  sein als die beste einfache Alternative.
6 Freigabe an den KI-Trader (zweistufig, nachweisgebunden):
  - Shadow („Beobachten“): braucht Kalibrierung + Ablation mit gleichen
    Coins/Timeframe, behaltene Regime mit ≥5 Abschnitten. Im Shadow schreibt
    der KI-Trader je Trade das Struktur-Regime mit – KEINE Wirkung auf Prompt
    oder Gate. Der Nutzer muss nichts weiter tun außer den KI-Trader (Paper/
    Live) laufen lassen; die Shadow-Trades sammeln sich von selbst.
  - Wirksam: zusätzlich ≥30 Shadow-Trades je Struktur-Regime und ein
    Ø-Reward-Unterschied ≥0.25 R zwischen bestem und schlechtestem Regime.
    Erst dann sieht der KI-Trader das Regime im Prompt.
7 Danach optional: Strategien je Regime suchen, finaler Walk-Forward auf dem
  Holdout, dynamische Strategie zusammenstellen.

ANTWORTSTIL: deutsch, sehr knapp (max. ~120 Wörter), Struktur
„Bewertung“ → „Nächster Schritt“ → ggf. „Warum“. Beziehe dich konkret auf
die Zahlen im Block REGIME-LAB-STAND. Erfinde keine Daten; fehlt etwas, sage
kurz, welcher Schritt es liefert.

ANTWORTFORMAT – antworte AUSSCHLIESSLICH mit einem JSON-Objekt:
{
  "reply": "deine Antwort (deutsch, kompakt)",
  "proposal": null ODER {"type": "settings", "summary": "1 Satz",
                         "settings": {"coins": ["BTCUSDT"], "timeframe": "15m",
                                      "days": 360, "scope": "both|combined|per_coin"}},
  "checks": ["optionale konkrete Prüf-/Warnhinweise"]
}
Ein proposal nur, wenn der Nutzer ausdrücklich eine Änderung der Regime-Lab-
Einstellungen wünscht; nur die Felder angeben, die sich ändern sollen."""

# Antwort-Budget: Render/Ingress kappt HTTP-Requests nach ~60s. Der Copilot
# probiert deshalb SCHNELLE Free-Modelle zuerst und bricht langsame Modelle
# hart ab, statt (wie der KI-Trader im Hintergrund) minutenlang zu warten.
FAST_MODEL_ORDER = [
    "nvidia/nemotron-3-super-120b-a12b:free",
    "google/gemma-4-31b-it:free",
    "nvidia/nemotron-3.5-lightning:free",
    "nvidia/nemotron-3-ultra-550b-a55b:free",
]
PER_CALL_TIMEOUT = 28.0
TOTAL_DEADLINE = 52.0


SHARED_KEY_ENV = "OPENROUTER_API_KEY"


def _env_keys(env_name: str) -> List[str]:
    keys: List[str] = []
    primary = (os.environ.get(env_name) or "").strip()
    if primary:
        keys.append(primary)
    for name in sorted(k for k in os.environ if k.startswith(env_name + "_BACKUP")):
        val = (os.environ.get(name) or "").strip()
        if val and val not in keys:
            keys.append(val)
    return keys


def copilot_keys() -> List[str]:
    """Copilot-Keys: eigene COPILOT_OPENROUTER_API_KEY* haben Vorrang.
    Sind keine gesetzt, nutzt der Copilot als Fallback die vorhandenen
    OPENROUTER_API_KEY* des KI-Traders – so funktioniert er ohne
    zusätzliche .env-Einträge (gleicher Anbieter, kein neuer Key nötig)."""
    own = _env_keys(KEY_ENV)
    return own if own else _env_keys(SHARED_KEY_ENV)


def copilot_key_source() -> str:
    """'copilot' (eigene Keys) | 'shared' (OpenRouter-Keys des KI-Traders)
    | 'none' (gar keine Keys gesetzt)."""
    if _env_keys(KEY_ENV):
        return "copilot"
    if _env_keys(SHARED_KEY_ENV):
        return "shared"
    return "none"

SYSTEM_PROMPT = """Du bist der STRATEGIE-COPILOT einer Krypto-Daytrading-Plattform.
Deine Rolle: BERATER und EINSTELLUNGS-ASSISTENT für das Strategie-Labor
(Parameter-Optimierung, Discovery, Deep-Test, Endlos-Suche, Dynamische
Strategien, Backtester, Regime-Lab, Strategie-Builder). Du bist NICHT der
KI-Trader – du handelst nie selbst und du entwickelst NIEMALS eigenmächtig
neue Strategien.

VERHALTENSREGELN (WICHTIG):
- Du berätst: erklärst die Einstellungen, bewertest Ergebnisse und gibst
  konkrete Tipps, welche vorhandene Strategie man optimieren sollte, warum,
  und welche sinnvollen Setups derzeit fehlen. Nutze dafür die
  STRATEGIE-ÜBERSICHT im Kontext (Indikatoren, Regeln, echte Ergebnisse).
- Änderungen machst du NUR als Vorschlag, den der Nutzer bestätigen muss –
  und NUR wenn der Nutzer eine Änderung ausdrücklich will oder um Hilfe beim
  Einstellen bittet. KEINE ungefragten Strategie-Entwürfe.
- Du siehst die aktuellen Einstellungen des Panels im Kontext. Beziehe dich
  konkret darauf ("Du hast X eingestellt, ich würde Y, weil …").

DEIN WISSEN ÜBER DIE SUCH-MODI (wann was empfehlen):
- Parameter-Optimierung (random/bayes): bestehende Strategie feinjustieren.
- Discovery (Greedy): schnell neue Regel-Kombis, findet aber keine Synergien.
- Discovery + Optimierung (combo): erst entdecken, dann Schwellen feinjustieren.
- Deep-Test (deep/extreme): erschöpfende Paar-/Beam-Suche – beste Qualität für
  neue Kombinationen, dauert deutlich länger.
- Endlos-Suche (Explore): läuft bis genug Champions Training UND Walk-Forward
  bestehen – am robustesten gegen Overfitting, ideal über Nacht.
- Dynamische Strategie: erkennt Marktregime (Bulle/Bär/Seitwärts, ohne
  Lookahead) und sucht pro Phase eigene Parameter/Regeln. Das ist die richtige
  Antwort, wenn eine Strategie im Bullen-/Bärenmarkt gewinnt, aber im
  Seitwärtsmarkt verliert. Leichtere Alternative: der Marktphasen-Filter im
  Auto-Trade-Setup der Strategie (regime_filter_enabled) blockiert neue Trades
  in gewählten Phasen (z.B. Seitwärts) komplett.
Empfiehl immer Walk-Forward/Robustheits-Checks, warne vor Overfitting
(zu viele Regeln, zu wenig Trades, zu kurzer Zeitraum).

OPTIMIEREN OHNE OVERFITTING (dein Leitfaden, kompakt):
- Trades-Fundament: Der Kontext liefert "MIN-TRADES-EMPFEHLUNG" – deterministisch
  aus Assets × Jahren × Timeframe berechnet. Nutze DIESEN Wert (nicht pauschal
  20/300). Mehr Assets oder mehr Tage = proportional mehr Trades nötig, sonst
  handelt die Strategie in Wahrheit fast nie (10 Assets × 4 Jahre mit 300 Trades
  = ~7 Trades pro Asset und Jahr = Zufall, nicht Edge).
- Regeln: max. 3–4 pro Seite; jede zusätzliche Regel muss die Walk-Forward-
  Konsistenz verbessern, sonst raus. Weniger Regeln + mehr Trades > umgekehrt.
- Zeitraum: min. 1 Jahr (alle Marktphasen), Intraday-TF eher 180–720 Tage,
  4h+ eher 1000+ Tage. Träge Indikator-Perioden (>200) meiden.
- Pflicht-Checks: Walk-Forward (train 70–75 %, rolling ≥ 3 Fenster bei viel
  Historie), Konsistenz-Test (Chunks 30–60 Tage, Abweichung ≤ 25 %), DD-Filter
  (max. 25–40 % vom Kapital), Fee-Stress ×1.5, Slippage-Stress bei 1m–5m.
  Champion nur, wenn Test-PnL > 0 UND Konsistenz ≥ 40 % – sonst Near-Miss.
- Objective: "combo" als Standard (PnL × Winrate × DD); "pnl" nur mit DD-Filter;
  "win_rate" nur mit min. CRV-Vorgabe – sonst optimiert es Mini-TPs.
- Iterationen: 40–60 random für Überblick, danach bayes 60–120 auf dem Sieger.
  Endlos-Suche für Kombinationen über Nacht (Zeitlimit setzen!).
- Trade-Einstellungen (SL/TP/BE/Trail): erst Regeln finden, DANN Trade-Gruppen
  mitoptimieren; Auto-Leverage aus lassen beim Vergleichen (verzerrt PnL).
- Ergebnis prüfen: Profit-Faktor 1.3–2.5 plausibel, > 4 = Verdacht auf Overfit/
  Lookahead; Winrate > 75 % bei CRV ≥ 2 unrealistisch; PnL-Verteilung: kein
  einzelner Trade > 20 % des Gesamt-PnL.

ANTWORTFORMAT – antworte AUSSCHLIESSLICH mit einem JSON-Objekt:
{
  "reply": "deine Antwort an den Nutzer (deutsch, kompakt, konkret)",
  "proposal": null ODER ein Vorschlag (siehe unten),
  "checks": ["optionale Liste konkreter Prüf-/Warnhinweise"]
}

VORSCHLAGS-TYPEN (nur auf ausdrücklichen Wunsch des Nutzers):
1) Such-/Labor-Einstellungen ändern (dein Hauptwerkzeug, wird direkt ins
   Formular übernommen – nur die Felder angeben, die sich ändern sollen):
   {"type": "settings", "summary": "1 Satz was sich ändert und warum",
    "settings": {"mode": "params|discovery|combo|dynamic|explore",
                 "strategy_id": "<id>", "coins": ["BTCUSDT"], "days": 90,
                 "timeframe": "5m", "objective": "combo|win_rate|pnl",
                 "iterations": 60, "min_trades": 20, "max_rules": 4,
                 "algorithm": "random|bayes", "deep_test": true,
                 "deep_depth": "deep|extreme", "indicators": ["rsi", "ema"],
                 "base_strategy_id": "<custom id|null>", "sessions": "09:00-17:00|''",
                 "execution": "cloud|local",
                 "opt_groups": {"tpsl": true, "sl": true, "be": true, "trail": true,
                                "profit_secure": false, "leverage": false, "sessions": false},
                 "rule_timeframes": {"enabled": true, "min": "1m", "max": "4h"},
                 "walk_forward": {"enabled": true, "mode": "single|rolling|anchored",
                                  "windows": 4, "train_pct": 75},
                 "robustness": {"dd_max_pct": 40|null, "constancy": {"chunk_days": 30, "max_dev_pct": 20}|null,
                                "stress_fee_mult": 1.5|null, "slippage_var_pct": 10|null,
                                "monte_carlo_runs": 200|null, "regime_check": true|false},
                 "explore": {"champions": 5, "max_minutes": 0},
                 "dynamic": {"max_regimes": 5, "conf_min": 70, "min_hold_days": 2,
                             "lookback_days": 3, "train_pct": 75, "max_rules": 4,
                             "rule_variants": false, "per_regime": false, "start_from_base": false}}}
   (null in robustness = Check aus; opt_groups-Schlüssel = Einstellungs-Gruppen,
   die mitoptimiert werden)
   Die settings-Schlüssel hängen vom AKTUELLEN PANEL ab:
   - panel=optimizer: Schema oben.
   - panel=backtester: {"strategies": ["<strategy_id>"], "coins": ["BTCUSDT"],
                        "days": 3, "capital": 100, "fee_percent": 0.06,
                        "require_all_rules": false}
   - panel=regime_lab: {"coins": ["BTCUSDT"], "timeframe": "15m", "days": 360,
                        "scope": "both|combined|per_coin"}
2) Parameter/Trade-Einstellungen einer BESTEHENDEN Strategie:
   {"type": "params", "strategy_id": "<id>", "summary": "1 Satz",
    "params": {"rsi_period": 12}, "trade_params": {"leverage": 5}, "timeframe": "5m"}
3) Strategie-Definition anlegen/ändern – NUR wenn der Nutzer das ausdrücklich
   verlangt ("erstelle/ändere die Strategie …"), sonst NIE:
   {"type": "definition", "strategy_id": "<id oder null für neu>", "summary": "1 Satz",
    "definition": {"name": "...", "timeframe": "5m", "indicators": {"rsi_period": 14},
                   "long_rules": [{"indicator": "rsi", "op": "<", "value": 30}],
                   "short_rules": [{"indicator": "rsi", "op": ">", "value": 70}],
                   "sl_mode": "structure", "crv_target": 2}}

REGELN:
- Nutze NUR die erlaubten Indikatoren, Operatoren und Timeframes aus dem Kontext.
- Erfinde keine Ergebnisse. Wenn dir Daten fehlen, sage was du brauchst.
- Bei Ergebnis-Bewertungen: nutze die mitgelieferten Sanity-Checks und Metriken,
  rechne nach (Winrate = wins/(wins+losses), PnL-Konsistenz) und sage klar,
  ob die Berechnung plausibel ist.
- EINHEITEN (kritisch): pnl, max_drawdown, fees, avg_pnl sind ABSOLUTE USDT-
  Beträge – NIEMALS Prozent. Prozentwerte enden auf _pct oder heißen win_rate
  (pnl_pct, max_drawdown_pct). Ein max_drawdown von 1600 bedeutet 1600 USDT
  Drawdown, NICHT 1600 %. Nutze den Block "METRIKEN (mit Einheiten)" als
  Referenz und bewerte Drawdown IMMER relativ zum Startkapital (max_drawdown_pct).
- proposal nur setzen, wenn du eine konkrete, vollständige Änderung vorschlägst.
- KÜRZE: reply max. ~120 Wörter, keine Wiederholung des Kontexts, keine Floskeln;
  Zahlen statt Prosa. Ausführlicher nur, wenn der Nutzer es verlangt."""


# Metrik-Schlüssel -> (Label, Einheit). Grundlage der lesbaren Zusammenfassung
# für den Copiloten (Bug-Report: 1600 USDT Drawdown wurde als 1600 % gelesen).
METRIC_UNITS = (
    ("trades", "Trades", "Anzahl"), ("wins", "Wins", "Anzahl"),
    ("losses", "Losses", "Anzahl"), ("breakevens", "Break-Even", "Anzahl"),
    ("win_rate", "Winrate", "%"),
    ("pnl", "PnL", "USDT"), ("pnl_pct", "PnL", "% des Startkapitals"),
    ("max_drawdown", "Max. Drawdown", "USDT"),
    ("max_drawdown_pct", "Max. Drawdown", "% des Startkapitals"),
    ("profit_factor", "Profit-Faktor", "Verhältnis"),
    ("avg_pnl", "Ø PnL/Trade", "USDT"), ("fees", "Gebühren", "USDT"),
    ("avg_leverage", "Ø Hebel", "x"), ("avg_duration_min", "Ø Dauer", "min"),
    ("liquidations", "Liquidationen", "Anzahl"),
    ("long_trades", "Long-Trades", "Anzahl"), ("short_trades", "Short-Trades", "Anzahl"),
)
_PCT_KEYS = {"win_rate", "pnl_pct", "max_drawdown_pct"}
_USDT_KEYS = {"pnl", "max_drawdown", "avg_pnl", "fees"}


def _num(v) -> Optional[float]:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def describe_metrics(metrics: Dict, capital: Optional[float] = None) -> List[str]:
    """Metriken als eindeutig beschriftete Zeilen (rein & testbar), z.B.
    'Max. Drawdown: 1600.00 USDT (= 16.0 % des Startkapitals 10000 USDT)'."""
    if not isinstance(metrics, dict):
        return []
    cap = _num(capital)
    out: List[str] = []
    for key, label, unit in METRIC_UNITS:
        if key not in metrics:
            continue
        v = _num(metrics.get(key))
        if v is None:
            continue
        if key in _PCT_KEYS:
            out.append(f"{label}: {v:.1f} %")
        elif key in _USDT_KEYS:
            extra = ""
            if cap and cap > 0 and key in ("pnl", "max_drawdown"):
                extra = f" (= {v / cap * 100:.1f} % des Startkapitals {cap:g} USDT)"
            out.append(f"{label}: {v:.2f} USDT{extra}")
        elif unit == "Anzahl":
            out.append(f"{label}: {int(v)}")
        else:
            out.append(f"{label}: {v:g} {unit}".rstrip())
    return out


def _find_capital(result: Dict, settings: Optional[Dict]) -> Optional[float]:
    for src in (result, (result or {}).get("config"), (result or {}).get("trade_params"),
                settings):
        if isinstance(src, dict):
            for k in ("capital", "max_capital", "start_capital"):
                v = _num(src.get(k))
                if v and v > 0:
                    return v
    return None


def metrics_summary(result: Dict, settings: Optional[Dict] = None,
                    max_rows: int = 8) -> str:
    """Lesbare, einheiten-sichere Zusammenfassung eines Optimizer-/Backtest-
    Ergebnisses (rein & testbar). Leer, wenn keine Metriken gefunden werden."""
    if not isinstance(result, dict):
        return ""
    cap = _find_capital(result, settings)
    blocks: List[str] = []
    m = result.get("metrics")
    if isinstance(m, dict):
        rows = describe_metrics(m, cap)
        if rows:
            blocks.append("Gesamt: " + " · ".join(rows))
    for key, title in (("per_strategy", "Pro Strategie"), ("per_pair", "Pro Strategie×Coin")):
        rows = result.get(key)
        if isinstance(rows, list) and rows:
            lines = []
            for r in rows[:max_rows]:
                if not isinstance(r, dict):
                    continue
                name = r.get("strategy_name") or r.get("strategy_id") or "?"
                if r.get("symbol"):
                    name = f"{name} @ {r['symbol']}"
                desc = describe_metrics(r, cap)
                if desc:
                    lines.append(f"  - {name}: " + " · ".join(desc))
            if lines:
                more = f"\n  … {len(rows) - max_rows} weitere" if len(rows) > max_rows else ""
                blocks.append(f"{title}:\n" + "\n".join(lines) + more)
    if not blocks:
        return ""
    head = ("METRIKEN (mit Einheiten – USDT-Beträge sind KEINE Prozentwerte; "
            "Prozent nur bei _pct/Winrate"
            + (f"; Startkapital {cap:g} USDT" if cap else "") + "):")
    return head + "\n" + "\n".join(blocks)


def sanity_check(metrics: Dict, capital: Optional[float] = None) -> List[str]:
    """Deterministische Plausibilitäts-Prüfung von Backtest-/Optimizer-Metriken.
    `capital` (Startkapital in USDT) erlaubt den Abgleich USDT- vs. %-Drawdown."""
    notes: List[str] = []
    if not isinstance(metrics, dict) or not metrics:
        return notes
    try:
        t = metrics.get("trades")
        w = metrics.get("wins")
        l = metrics.get("losses")
        be = metrics.get("breakevens") or 0
        if None not in (t, w, l) and int(w) + int(l) + int(be) != int(t):
            notes.append(f"Trade-Summe inkonsistent: wins({w}) + losses({l}) + "
                         f"breakeven({be}) != trades({t})")
        wr = metrics.get("win_rate")
        if wr is not None and w is not None and l is not None and (int(w) + int(l)) > 0:
            expected = round(int(w) / (int(w) + int(l)) * 100, 1)
            if abs(expected - float(wr)) > 0.15:
                notes.append(f"Win-Rate weicht ab: berechnet {expected}%, gemeldet {wr}%")
        pnl, avg = metrics.get("pnl"), metrics.get("avg_pnl")
        if t and pnl is not None and avg is not None:
            expected = float(pnl) / int(t)
            if abs(expected - float(avg)) > max(0.02, abs(expected) * 0.05):
                notes.append(f"Ø-PnL weicht ab: berechnet {round(expected, 3)}, gemeldet {avg}")
        dd, ddp = metrics.get("max_drawdown"), metrics.get("max_drawdown_pct")
        if dd is not None and float(dd) < 0:
            notes.append(f"max_drawdown negativ ({dd}) – erwartet wird ein Betrag >= 0")
        if ddp is not None and float(ddp) > 100:
            notes.append(f"max_drawdown_pct > 100% ({ddp}) – prüfen")
        cap = _num(capital)
        if dd is not None and cap and cap > 0:
            dd_pct_calc = round(float(dd) / cap * 100, 1)
            if ddp is not None and abs(dd_pct_calc - float(ddp)) > 0.6:
                notes.append(f"Drawdown-Einheiten prüfen: {dd} USDT sind {dd_pct_calc}% des "
                             f"Startkapitals {cap:g} USDT, gemeldet max_drawdown_pct={ddp}%")
            else:
                notes.append(f"Drawdown-Einordnung: max_drawdown {float(dd):.2f} USDT = "
                             f"{dd_pct_calc}% des Startkapitals {cap:g} USDT (USDT-Betrag, kein Prozent)")
        elif dd is not None and ddp is not None:
            notes.append(f"Drawdown-Einordnung: max_drawdown {float(dd):.2f} USDT (absolut), "
                         f"max_drawdown_pct {float(ddp):.1f}% (relativ zum Startkapital)")
    except (TypeError, ValueError, ZeroDivisionError):
        notes.append("Metriken unvollständig/nicht numerisch – Teil der Prüfung übersprungen")
    return notes


def _dumps(obj, limit: int = 3500) -> str:
    try:
        s = json.dumps(obj, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        s = str(obj)
    return s[:limit]


# Erwartete Mindest-Trades pro Asset und Jahr, damit eine Strategie überhaupt
# "aktiv" ist (statistisch belastbar). Intraday-Timeframes müssen deutlich
# häufiger handeln als Swing-Timeframes.
_TF_MINUTES = {"1m": 1, "2m": 2, "3m": 3, "5m": 5, "10m": 10, "15m": 15, "30m": 30,
               "1h": 60, "2h": 120, "4h": 240, "6h": 360, "8h": 480, "12h": 720,
               "24h": 1440, "1d": 1440, "3d": 4320, "1w": 10080, "1M": 43200}


def trades_per_asset_year(timeframe: Optional[str]) -> int:
    m = _TF_MINUTES.get(str(timeframe or "1m"), 1)
    if m <= 5:
        return 150
    if m <= 60:
        return 80
    if m <= 240:
        return 40
    if m <= 1440:
        return 20
    return 8


def min_trades_hint(settings: Optional[Dict]) -> Optional[Dict]:
    """Deterministische Min-Trades-Empfehlung aus Assets × Jahre × Timeframe.
    Grundlage für den Copiloten, statt pauschal 20/300 vorzuschlagen."""
    if not isinstance(settings, dict):
        return None
    coins = settings.get("coins") or settings.get("symbols") or settings.get("strategies_coins")
    n_assets = len(coins) if isinstance(coins, list) and coins else 1
    try:
        days = float(settings.get("days") or 0)
    except (TypeError, ValueError):
        days = 0.0
    if days <= 0:
        return None
    tf = settings.get("timeframe") or "1m"
    years = max(days / 365.0, 0.05)
    per_year = trades_per_asset_year(tf)
    recommended = int(round(n_assets * years * per_year))
    # Walk-Forward: Testfenster muss ebenfalls belastbar sein (>= 30 Trades)
    floor = 100 if years >= 1 else 40
    recommended = max(recommended, floor)
    current = settings.get("min_trades")
    try:
        current = int(current) if current is not None else None
    except (TypeError, ValueError):
        current = None
    return {"assets": n_assets, "days": int(days), "years": round(years, 2), "timeframe": tf,
            "per_asset_year": per_year, "recommended_min_trades": recommended,
            "lower_bound": max(int(recommended * 0.5), floor),
            "current_min_trades": current,
            "too_low": current is not None and current < int(recommended * 0.5)}


def min_trades_hint_text(settings: Optional[Dict]) -> str:
    h = min_trades_hint(settings)
    if not h:
        return ""
    cur = (f"aktuell eingestellt: {h['current_min_trades']}"
           + (" – DEUTLICH ZU NIEDRIG" if h["too_low"] else "")) \
        if h["current_min_trades"] is not None else "aktuell nicht gesetzt"
    return (f"MIN-TRADES-EMPFEHLUNG (deterministisch): {h['assets']} Assets × {h['years']} Jahre × "
            f"~{h['per_asset_year']} Trades/Asset/Jahr auf {h['timeframe']} "
            f"=> min_trades ≈ {h['recommended_min_trades']} (Untergrenze {h['lower_bound']}); {cur}.")


_RESULT_DROP_KEYS = {"steps", "refine_log", "last_trades", "all_trades", "export_trades",
                     "per_pair", "equity", "benchmark", "search_stats", "explore_report",
                     "deep_report", "ranges", "windows"}


def _slim_result(result: Dict) -> Dict:
    """Ergebnis für den Prompt verschlanken: Roh-Listen (Trades, Suchschritte,
    Logs) raus, Kennzahlen/Definition/Top-5-Metriken rein – spart ~60 % Tokens."""
    if not isinstance(result, dict):
        return result
    out = {k: v for k, v in result.items() if k not in _RESULT_DROP_KEYS}
    if isinstance(result.get("top5"), list):
        out["top5"] = [{k: v for k, v in (t or {}).items()
                        if k in ("rank", "score", "metrics", "test_metrics", "trade_params",
                                 "params", "wf", "consistency_pct", "passed", "rules")}
                       for t in result["top5"][:5]]
    if isinstance(result.get("per_strategy"), list):
        out["per_strategy"] = [{k: v for k, v in (p or {}).items()
                                if k not in ("last_trades", "all_trades")}
                               for p in result["per_strategy"][:6]]
    if isinstance(result.get("explore_report"), dict):
        er = result["explore_report"]
        out["explore_report"] = {k: er.get(k) for k in
                                 ("tested", "refined", "champions_found", "stop_reason",
                                  "near_misses", "combos_per_min")}
    return out


class StrategyCopilot:
    """Chat-Logik des Copiloten. DB kommt zur Laufzeit aus core.state."""

    def __init__(self):
        self._cfg_cache: Optional[Dict] = None

    def _db(self):
        from core import state
        return state.db

    # ---------------- Konfiguration ----------------
    async def config(self) -> Dict:
        if self._cfg_cache is not None:
            return self._cfg_cache
        db = self._db()
        doc = {}
        if db is not None:
            doc = await db.settings.find_one({"_id": CONFIG_ID}) or {}
        self._cfg_cache = {"model": doc.get("model")}
        return self._cfg_cache

    async def set_model(self, model: Optional[str]) -> Dict:
        allowed = ai_providers.allowed_models(PROVIDER)
        if model and model not in allowed:
            raise ValueError(f"Modell '{model}' ist für {PROVIDER} nicht erlaubt")
        db = self._db()
        if db is not None:
            await db.settings.update_one({"_id": CONFIG_ID},
                                         {"$set": {"model": model}}, upsert=True)
        self._cfg_cache = {"model": model}
        return self._cfg_cache

    # ---------------- Verlauf ----------------
    async def history(self, limit: int = 60, panel: Optional[str] = None) -> List[Dict]:
        """Verlauf – optional je Reiter gefiltert (panel=None: alle, abwärtskompatibel)."""
        db = self._db()
        if db is None:
            return []
        query = {"panel": normalize_panel(panel)} if panel else {}
        rows = await db[CHAT_COLLECTION].find(query, {"_id": 0}) \
            .sort("ts", -1).limit(max(1, min(200, limit))).to_list(200)
        return list(reversed(rows))

    async def clear_history(self, panel: Optional[str] = None) -> int:
        db = self._db()
        if db is None:
            return 0
        query = {"panel": normalize_panel(panel)} if panel else {}
        res = await db[CHAT_COLLECTION].delete_many(query)
        return res.deleted_count

    async def _other_panels_digest(self, panel: str, per_panel: int = 4,
                                   max_chars: int = 160) -> str:
        """Kurz-Digest der anderen Reiter-Verläufe – der Copilot bleibt so über
        alle Reiter informiert, ohne dass sich die Verläufe vermischen."""
        db = self._db()
        if db is None:
            return ""
        lines: List[str] = []
        for p in PANELS:
            if p == panel:
                continue
            rows = await db[CHAT_COLLECTION] \
                .find({"panel": p}, {"_id": 0, "role": 1, "content": 1}) \
                .sort("ts", -1).limit(per_panel).to_list(per_panel)
            if not rows:
                continue
            snip = " | ".join(
                f"{'NUTZER' if r.get('role') == 'user' else 'COPILOT'}: "
                f"{str(r.get('content') or '')[:max_chars]}"
                for r in reversed(rows))
            lines.append(f"- {PANEL_LABELS.get(p, p)}: {snip}")
        if not lines:
            return ""
        return ("VERLÄUFE DER ANDEREN REITER (Kurzfassung, nur zur Orientierung):\n"
                + "\n".join(lines))

    async def _store(self, role: str, content: str, extra: Optional[Dict] = None,
                     panel: Optional[str] = None) -> Dict:
        doc = {"id": str(uuid.uuid4()), "role": role, "content": content,
               "panel": normalize_panel(panel),
               "ts": datetime.now(timezone.utc).isoformat(), **(extra or {})}
        db = self._db()
        if db is not None:
            await db[CHAT_COLLECTION].insert_one({**doc})
            # Verlauf begrenzen (Render 512 MB / kleine DB)
            n = await db[CHAT_COLLECTION].count_documents({})
            if n > MAX_HISTORY_DOCS:
                old = await db[CHAT_COLLECTION].find({}, {"_id": 1}) \
                    .sort("ts", 1).limit(n - MAX_HISTORY_DOCS).to_list(None)
                await db[CHAT_COLLECTION].delete_many(
                    {"_id": {"$in": [o["_id"] for o in old]}})
        return doc

    # ---------------- Kontext ----------------
    async def _strategies_overview(self, max_lines: int = 40) -> str:
        """Kompakte Zeile pro Strategie: Indikatoren/Regeln + echte Ergebnisse
        (Paper/Live) + letztes Optimizer-Best – Grundlage für Beratungs-Tipps."""
        from strategies.registry import registry
        from services import strategy_insights

        db = self._db()
        stats = await strategy_insights.strategy_trade_stats(db)
        opt = await strategy_insights.optimizer_best(db)
        lines: List[str] = []
        for strat in registry._strategies.values():
            sid = strat.STRATEGY_ID
            if sid == "ai_trader":
                continue
            perf_parts = []
            for mode in ("live", "paper"):
                m = (stats.get(sid) or {}).get(mode)
                if m:
                    perf_parts.append(f"{mode}: {m['trades']}T · WR {m['win_rate']}% · "
                                      f"PnL {m['pnl']:+.2f}$")
            perf = " | ".join(perf_parts) or "noch keine geschlossenen Trades"
            o = opt.get(sid)
            opt_txt = (f" | Optimizer-Best ({o.get('symbol') or '?'}): "
                       f"{o.get('trades', '?')}T · WR {o.get('win_rate', '?')}% · "
                       f"PnL {o.get('pnl_pct', '?')}%") if o else ""
            rules_txt = ""
            if getattr(strat, "IS_CUSTOM", False):
                d = getattr(strat, "definition", {}) or {}
                longs = d.get("long_rules") or []
                shorts = d.get("short_rules") or []
                inds = sorted({str(r.get("indicator")) for r in list(longs) + list(shorts)
                               if r.get("indicator")})
                rules_txt = (f" | Custom · Indikatoren: {', '.join(inds) or '–'} "
                             f"({len(longs)} Long-/{len(shorts)} Short-Regeln)")
            tf = getattr(strat, "STRATEGY_TIMEFRAME", "?")
            lines.append(f"- {strat.STRATEGY_NAME} [{sid}] · TF {tf}{rules_txt} | {perf}{opt_txt}")
            if len(lines) >= max_lines:
                break
        return "\n".join(lines)

    async def _regime_lab_context_block(self, ctx: Dict) -> str:
        """Kontext NUR für den Regime-Lab-Reiter: Einstellungen + Regime-Stand
        (Kalibrierung, Autopilot, Ablation, Analyse, Freigabe). Keine
        Strategie-Übersicht, keine Min-Trades-Regel, kein Optimizer-Wissen."""
        parts: List[str] = ["AKTUELLES PANEL: regime_lab (Regime-Lab-Copilot)",
                            "ERLAUBTE TIMEFRAMES: " + ", ".join(TIMEFRAMES)]
        if (ctx or {}).get("settings"):
            parts.append("REGIME-LAB-EINSTELLUNGEN (Coins, Timeframe, Zeitraum, Training %): "
                         + _dumps(ctx["settings"], 900))
        if (ctx or {}).get("regime"):
            parts.append("REGIME-LAB-STAND (darauf beziehen): "
                         + _dumps(ctx["regime"], 3200))
        return "\n\n".join(parts)

    async def _context_block(self, ctx: Dict) -> str:  # noqa: C901
        if normalize_panel((ctx or {}).get("panel")) == "regime_lab":
            return await self._regime_lab_context_block(ctx or {})
        from core.state import scanner
        from strategies.registry import registry
        from strategies.custom_strategy import INDICATORS, OPERATORS

        parts: List[str] = []
        parts.append("ERLAUBTE INDIKATOREN: " + ", ".join(INDICATORS))
        parts.append("ERLAUBTE OPERATOREN: " + ", ".join(OPERATORS))
        parts.append("ERLAUBTE TIMEFRAMES: " + ", ".join(TIMEFRAMES))
        panel = (ctx or {}).get("panel")
        if panel:
            parts.append(f"AKTUELLES PANEL: {panel}")

        # Vollständige Übersicht aller Strategien (Indikatoren + echte Ergebnisse),
        # damit der Copilot Optimierungs-Tipps und Lücken-Analysen geben kann.
        # Regime-Lab: gekürzt (Token-Budget) – dort zählt der Regime-Stand.
        try:
            overview = await self._strategies_overview(
                max_lines=12 if panel == "regime_lab" else 40)
            if overview:
                parts.append("STRATEGIE-ÜBERSICHT (alle Strategien, Indikatoren, "
                             "echte Paper-/Live-Ergebnisse und Optimizer-Bestwerte):\n"
                             + overview)
        except Exception as e:
            logger.debug(f"Copilot: Strategie-Übersicht nicht verfügbar: {e}")

        sid = (ctx or {}).get("editing_strategy_id") or (ctx or {}).get("strategy_id")
        if sid:
            strat = registry.get(sid)
            if strat:
                parts.append(f"AKTUELLE STRATEGIE ({sid}):")
                if getattr(strat, "IS_CUSTOM", False):
                    parts.append("Definition: " + _dumps(strat.definition))
                gp = scanner.settings.get("strategy_params", {}).get(sid)
                if gp:
                    parts.append("Aktive globale Parameter: " + _dumps(gp, 1200))
                tf = scanner.settings.get("strategy_timeframes", {}).get(sid)
                if tf:
                    parts.append(f"Aktiver Kerzen-Timeframe: {tf}")

        if (ctx or {}).get("draft"):
            parts.append("AKTUELLER ENTWURF IM BUILDER (noch nicht gespeichert): "
                         + _dumps(ctx["draft"]))
        if (ctx or {}).get("preview"):
            parts.append("REGEL-VORSCHAU (7-Tage-Mini-Backtest): " + _dumps(ctx["preview"], 1800))
        if (ctx or {}).get("regime"):
            # Kompakter Regime-Lab-Stand (Detektor, Kalibrierung, Note der offenen
            # Analyse, Regime-Anteile, Walk-Forward) – bewusst klein gehalten.
            parts.append("REGIME-LAB-STAND (kompakt, darauf beziehen): "
                         + _dumps(ctx["regime"], 1400))
        if (ctx or {}).get("settings"):
            parts.append("AKTUELLE EINSTELLUNGEN DES PANELS: " + _dumps(ctx["settings"], 1800))
            hint = min_trades_hint_text(ctx["settings"])
            if hint:
                parts.append(hint)
        result = (ctx or {}).get("result")
        if result:
            # Erst die einheiten-sichere Zusammenfassung (Bug-Report: 1600 USDT
            # Drawdown wurde als 1600 % gelesen), dann ein GEKÜRZTES Roh-JSON
            # (Token-Budget: Trade-Listen/Steps/Logs raus, Kennzahlen bleiben).
            summary = metrics_summary(result, (ctx or {}).get("settings"))
            if summary:
                parts.append(summary)
            slim = _slim_result(result)
            raw = _dumps(slim, 1500)
            truncated = len(json.dumps(slim, ensure_ascii=False, default=str)) > 1500
            parts.append("LETZTES ERGEBNIS (Kern-JSON"
                         + (", gekürzt – Zusammenfassung oben ist vollständig" if truncated else "")
                         + "): " + raw)
            metrics = result.get("metrics") or {}
            if not metrics and isinstance(result.get("per_strategy"), list) \
                    and result["per_strategy"]:
                metrics = result["per_strategy"][0] if isinstance(result["per_strategy"][0], dict) else {}
            checks = sanity_check(metrics, _find_capital(result, (ctx or {}).get("settings")))
            parts.append("SANITY-CHECKS (deterministisch nachgerechnet): "
                         + ("; ".join(checks) if checks else "keine Auffälligkeiten"))

        # Brücke zum KI-Trader-Gedächtnis (nur lesend)
        try:
            from services.ai_memory import memory
            know = await memory.context_text(
                kinds=["copilot_note", "research_insight", "idea"],
                per_kind=2, max_chars=1500)
            if know:
                parts.append("GETEILTES KI-GEDÄCHTNIS (KI-Trader & Copilot):\n" + know)
        except Exception as e:
            logger.debug(f"Copilot: Gedächtnis nicht lesbar: {e}")
        return "\n\n".join(parts)

    # ---------------- Chat ----------------
    def _chain(self, preferred: Optional[str]) -> List[str]:
        allowed = ai_providers.allowed_models(PROVIDER)
        models: List[str] = []
        if preferred and preferred in allowed:
            models.append(preferred)
        for m in FAST_MODEL_ORDER:
            if m in allowed and m not in models:
                models.append(m)
        for m in allowed:
            if m not in models and m not in ai_providers.PAID_MODELS_NO_FALLBACK:
                models.append(m)
        return models

    async def _openrouter_call(self, key: str, model: str, prompt: str,
                               timeout: float, system: Optional[str] = None) -> str:
        client = AsyncOpenAI(
            base_url="https://openrouter.ai/api/v1", api_key=key, timeout=timeout,
            max_retries=0,
            default_headers={
                "HTTP-Referer": os.environ.get("OPENROUTER_REFERER", "https://localhost"),
                "X-Title": (os.environ.get("OPENROUTER_TITLE") or "KI Trader").strip('"') + " Copilot",
            })
        resp = await client.chat.completions.create(
            model=model, temperature=0.3,
            messages=[{"role": "system", "content": system or SYSTEM_PROMPT},
                      {"role": "user", "content": prompt}])
        text = (resp.choices[0].message.content or "").strip() if resp.choices else ""
        if not text:
            raise RuntimeError("leere Antwort")
        return text

    async def freeform(self, prompt: str, system: Optional[str] = None,
                       deadline: float = 110.0, per_call: float = 45.0) -> str:
        """Einmalige Analyse OHNE Chat-Verlauf (Hintergrund-Reports, z.B. der
        wöchentliche Telegram-Report) – gleiche Modell-/Key-Kette wie chat(),
        aber mit großzügigerem Zeitbudget (kein HTTP-Request wartet darauf)."""
        keys = copilot_keys()
        if not keys:
            raise RuntimeError(f"Kein OpenRouter-Key ({KEY_ENV} oder {SHARED_KEY_ENV}) gesetzt")
        models = self._chain((await self.config()).get("model"))
        start = time.monotonic()
        last_err: Optional[Exception] = None
        for m in models:
            for ki, key in enumerate(keys):
                remaining = deadline - (time.monotonic() - start)
                if remaining < 6:
                    break
                try:
                    return await asyncio.wait_for(
                        self._openrouter_call(key, m, prompt, min(per_call, remaining),
                                              system=system),
                        timeout=min(per_call, remaining))
                except asyncio.TimeoutError:
                    last_err = RuntimeError(f"{m}: Timeout")
                    break
                except Exception as e:
                    last_err = e
                    if "429" not in str(e).lower() and "rate" not in str(e).lower():
                        break
            if (deadline - (time.monotonic() - start)) < 6:
                break
        raise RuntimeError(f"Copilot-Report: kein Modell erreichbar "
                           f"({str(last_err)[:120] if last_err else '?'})")

    async def chat(self, message: str, ctx: Optional[Dict] = None,
                   deadline: float = TOTAL_DEADLINE,
                   per_call: float = PER_CALL_TIMEOUT) -> Dict:
        """deadline/per_call: der Hintergrund-Job-Endpoint (/api/copilot/chat/start)
        ruft mit großzügigerem Budget auf – kein HTTP-Request wartet darauf,
        Render/Ingress-Timeouts (~60s) können die Antwort nicht mehr abbrechen."""
        message = (message or "").strip()
        if not message:
            raise ValueError("Leere Nachricht")

        keys = copilot_keys()
        if not keys:
            raise RuntimeError(
                f"Kein OpenRouter-Key gefunden: bitte {KEY_ENV} (eigene Copilot-Keys) "
                f"oder {SHARED_KEY_ENV} in der .env setzen")

        panel = normalize_panel((ctx or {}).get("panel"))
        if panel == "regime_lab":
            # Eigener Copilot: nur Regime-Lab-Wissen, kein Optimizer-Prompt,
            # keine Verläufe anderer Reiter.
            system = REGIME_LAB_SYSTEM_PROMPT
        else:
            system = "\n\n".join([SYSTEM_PROMPT, PANEL_PROMPTS.get(panel, ""), PANEL_SHARED])
        history = await self.history(HISTORY_FOR_PROMPT, panel=panel)
        hist_txt = "\n".join(
            f"{'NUTZER' if m['role'] == 'user' else 'COPILOT'}: {m['content'][:400]}"
            for m in history) or "(kein Verlauf)"
        context = await self._context_block(ctx or {})
        digest = "" if panel == "regime_lab" else await self._other_panels_digest(panel)
        prompt = (f"KONTEXT:\n{context}\n\n"
                  + (f"{digest}\n\n" if digest else "")
                  + f"BISHERIGER CHAT ({PANEL_LABELS.get(panel, panel)}):\n{hist_txt}\n\n"
                  f"NUTZER: {message}\n\nAntworte als JSON gemäß Formatvorgabe.")

        models = self._chain((await self.config()).get("model"))

        # Hartes Zeitbudget: langsames Modell/Key abbrechen -> nächste Kombination
        start = time.monotonic()
        text = model = None
        last_err: Optional[Exception] = None
        for m in models:
            for ki, key in enumerate(keys):
                remaining = deadline - (time.monotonic() - start)
                if remaining < 6:
                    break
                try:
                    text = await asyncio.wait_for(
                        self._openrouter_call(key, m, prompt,
                                              min(per_call, remaining),
                                              system=system),
                        timeout=min(per_call, remaining))
                    model = m
                    break
                except asyncio.TimeoutError:
                    logger.info(f"Copilot: {m} zu langsam (> {per_call}s)")
                    last_err = RuntimeError(f"{m}: Timeout")
                    break  # langsames Modell nicht mit weiteren Keys probieren
                except Exception as e:
                    logger.info(f"Copilot: {m} (Key {ki + 1}/{len(keys)}) fehlgeschlagen: {str(e)[:150]}")
                    last_err = e
                    msg_l = str(e).lower()
                    if "429" not in msg_l and "rate" not in msg_l:
                        break  # kein Rate-Limit -> Key-Wechsel bringt nichts, nächstes Modell
            if text or (deadline - (time.monotonic() - start)) < 6:
                break
        if not text:
            raise RuntimeError(
                f"Copilot-Modelle derzeit überlastet – bitte gleich erneut senden "
                f"({str(last_err)[:120] if last_err else 'kein Modell erreichbar'})")
        provider = PROVIDER
        data = parse_json_lenient(text) or {}
        reply = str(data.get("reply") or text or "").strip()
        proposal = data.get("proposal")
        if not (isinstance(proposal, dict) and proposal.get("type") in PROPOSAL_TYPES):
            proposal = None
        checks = [str(c) for c in (data.get("checks") or []) if c][:10]

        # Nutzer-Nachricht erst NACH erfolgreichem LLM-Call speichern
        # (keine verwaisten Halb-Turns im Verlauf bei Timeout/Fehler)
        await self._store("user", message, panel=panel)
        msg = await self._store("assistant", reply, {
            "proposal": proposal, "checks": checks,
            "provider": provider, "model": model}, panel=panel)
        return {"reply": reply, "proposal": proposal, "checks": checks,
                "provider": provider, "model": model, "id": msg["id"]}


copilot = StrategyCopilot()


# ---- Wöchentlicher Setup-Report (Telegram) – Endpoints in routers/copilot.py
