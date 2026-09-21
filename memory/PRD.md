# KI-Trader – PRD / Arbeitsstand (Emergent-Session 09/2026)

## Original-Problemstellung (Kurzfassung)
Bestehende, produktiv laufende Daytrading-Website (Repo `dean06greif-ai/KI-Trader`, Branch
`conflict_200926_2001`, Deploy extern auf Render). Struktur 1:1 beibehalten. Verbesserungen sauber,
modular, rückwärtskompatibel, mit Regressionstests:
1. Freies Kapital vorhanden, aber trotz Signalen (z.B. TrendFolge2) keine Trades – obwohl „Rest-Kapital“-Logik existiert.
2. Telegram zeigt bei TrendFolge2 „3/4 Rules“, obwohl die Strategie je Richtung nur 3 Regeln hat.
3. Regime-Lab: Auswahl einer Analyse zeigt oben zeitweise eine andere; „Ausgewählt“-Label gewünscht.
4. Regime-Lab: Punkt 3 fehlt komplett bis die Daten geladen sind -> Platzhalter.
5. Regime-Lab: Asset-Auswahl der Analyse direkt unter „Freigabe an KI-Trader“.
6. „je Coin einzeln“ -> „je Asset einzeln“ + Erklärung.

## Architektur (relevant)
- Backend FastAPI (`backend/`): `core/pipeline.py` (Signal -> Telegram -> `AutoTradeManager.on_signal`),
  `services/bitunix_trade.py` (Entry-Pfad, Kapital/Hebel), `services/capital_fit.py` (Rest-Kapital),
  `services/risk_budget.py` (Gesamt-Risikobudget), `services/entry_guard.py` (zentrale Einstiegsprüfungen),
  `services/telegram_bot.py`, `routers/regime_lab.py`.
- Frontend React (`frontend/src/components/RegimeLab.js` + `.css`).
- Prod-DB: MongoDB Atlas `crypto_scanner` (geteilt). Lokale Preview läuft mit `AI_TRADER_LOCAL_DISABLE=1`.

## Befund (Ursache Punkt 1)
- TF2 (`custom_23a30b65`) hat je Coin Live-Configs mit 40–100 USDT Marge (ADA 50, XRP 40, POL 100).
- Live-Equity ~22.7 USDT -> Risikobudget 6 % (Portfolio) / 4 % (Cluster) = ~0.9–1.4 USDT erlaubtes Risiko.
- `capital_fit` griff nicht (frei 22 > gewünscht nicht immer), das **Risikobudget** blockierte danach STILL
  (kein Telegram, kein persistierter Grund). Trades mit 5–10 USDT Marge (HYPE/ETH/SOL) passten noch.

## Umgesetzt (20.09.2026)
- **Rest-Budget-Trading**: `risk_budget.headroom()/fit_scale()/scale_for_new_trade()`,
  `entry_guard.fit_risk_budget()`, `AutoTradeManager._fit_risk_budget()` – Marge wird auf das
  freie Rest-Risikobudget verkleinert statt abgelehnt (Untergrenze `capital_fit.MIN_MARGIN_USDT`,
  Börsen-Minimum). Config-Schalter `risk_budget_config.scale_to_fit` (Default an).
- Risikobudget-Ablehnungen melden jetzt per Telegram (`_notify_reject`, 30-min-Cooldown).
- Ablehnungsgrund wird am Signal persistiert (`signals.trade_opened`, `signals.trade_reject_reason`).
- Telegram: `Rules: met/total` aus dem Signal (`TelegramNotifier.rules_counts`) statt fest „/4“.
- Preview-Guard `AI_TRADER_LOCAL_DISABLE` deckt jetzt auch Scanner-Auto-Trades und Trade-Monitor ab
  (auf Render ohne Env unverändert).
- Regime-Lab UI: Auswahl-Wechsel verwirft alte Detaildaten sofort (kein „falsches“ Regime oben),
  „Ausgewählt“-Tag in Liste, Punkt 3 immer sichtbar (Platzhalter „Bitte wähle …“ / „Lade Regime …“),
  Asset-Tabs direkt unter „Freigabe an den KI-Trader“ (+ „· ausgewählt“), Wording „je Asset“.
- Performance: `GET /api/regime-lab/{id}` ohne `chart`/`chart_emas` (–70 % Größe, `?full=1` wie bisher),
  neu `GET /api/regime-lab/{id}/chart/{symbol}`; Frontend lädt Charts je Asset lazy.
  Label-Migration nutzt gezieltes `$set` statt `replace_one`.
- Tests: `backend/tests/test_risk_budget_scale_fit.py`, `backend/tests/test_regime_lab_detail_slim.py`,
  `test_risk_budget.py` (Fenster), `test_manual_web_trades.py` (Guard neutralisiert).

## Backlog / Nächste Schritte
- P1: Risikobudget-Prozente im UI prüfen/anpassen (6 %/4 % sind bei ~22 USDT Equity sehr eng).
- P1: Signal-Liste im Frontend könnte `trade_reject_reason` anzeigen (Daten liegen jetzt vor).
- P2: Regime-Lab Detail weiter verschlanken (live_segments ~40 KB je Asset).
- P2: TF2 5m-Signal wiederholt sich je 1m-Kerze bis zur nächsten 5m-Kerze (Signals-Spam in DB).
