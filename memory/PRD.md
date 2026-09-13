# PRD – KI-Trader (Crypto Scanner) · Plan-Fortsetzung

## Original-Problemstellung
Bestehende, produktiv auf Render laufende Daytrading-Website (GitHub: dean06greif-ai/KI-Trader,
Branch conflict_130926_2129) soll gemäß dem im Repo gepflegten Audit-Plan verbessert werden:
sauber, modular, rückwärtskompatibel, Original-Ordnerstruktur beibehalten (Render-Deploy).
Fortschritt wird laufend in `UMSETZUNG_FORTSCHRITT.md` protokolliert (lebendes Protokoll),
damit der Stand jederzeit nachvollziehbar ist. Nutzer pusht über "Save to GitHub".

## Architektur
- Backend: FastAPI (`backend/server.py`, `routers/`, `services/` – >100 Service-Module),
  MongoDB (Atlas in Prod, lokal für Tests), Bitunix (Krypto-Futures) + IBKR (Forex),
  Multi-LLM (OpenRouter/Groq/Mistral/Gemini mit Backup-Keys), Telegram-Notify.
- Frontend: React (CRA/Craco), Komponenten unter `frontend/src/components/`.
- Tests: `backend/tests` (pytest, -m unit, xdist -n 2), Stand 26.06.2026: **1347 passed**;
  einzige Fails `test_iter38_*` (braucht Dev-Server :8055 mit Live-Keys, umgebungsbedingt).
- Lokale Umgebung: lokale Mongo, keine Exchange-Keys, `AI_TRADER_LOCAL_DISABLE=1`;
  Admin-Login lokal: Admin / LocalTest06! (siehe memory/test_credentials.md).

## Nutzer-Personas
- Einzelner Trader (Betreiber): überwacht KI-Trader, Live-/Paper-Trading, Analytics.

## Kern-Anforderungen (statisch)
- Plan-Dokumente: `KI_TRADER_AUDIT.md` (verbindlicher Maßnahmenkatalog),
  `UMSETZUNG_FORTSCHRITT.md` (Status je Schritt + Protokoll – IMMER aktualisieren!).
- Kein Bruch bestehender API-Verträge/Workflows; neue Verhaltensänderungen per Schalter.

## Umgesetzt (Historie)
- Phase 1 (Geldschutz) 1.1–1.9: komplett (frühere Sessions, 09/2026–26.06.2026).
- 2.1 Entry-Guard, 2.2 ML-Leckage, 2.3 R in Geld (26.06.2026, frühere Session).
- **Diese Session (26.06.2026): Phase 2 KOMPLETT**
  - 2.4 Sicherheitsstatus + Ampel (safety_status.py, /api/safety/status, SafetyLight im Header,
    critical blockt via entry_guard) – war fast fertig, Protokoll + Login-Refresh-Fix ergänzt.
  - 2.5 Wilson-Konfidenzintervall (`wr_ci`) + „unsicher“-Kennzeichnung (n<15) in Stats/UI;
    Setup-Gewichtung mit Live-Vorrang (Paper nur Prior mit Abzug; Schalter `setup_weight_live_pref`).
  - 2.6 Gewichtung nach geschrumpftem Erwartungswert je riskiertem USDT (Schalter `setup_weight_ev`).
  - 2.7 Reward ohne Konfidenz-Anreiz (Schalter `confidence_reward` Default aus), `violations[]`,
    neue Kennzahl Kalibrierungsfehler (API `calibration` + Tabelle im AIRewardPanel).
  - 2.8 Echte Bestätigungen (`ai_validation.real_confirmations`, `macro_evidence_min_trades=3`).
  - 2.9 Mitternachts-Wrap im Stunden-Filter der Backtest-Detektoren.
  - 2.10 `auc_risk_scaling` Default AUS, `risk_scaling_active` im Status, `ml_risk_scale` am Trade.
  - Testing-Agent iteration_60: Backend 10/10, Frontend-Smoke grün.

## Backlog (priorisiert)
- P0: Phase 3 – 3.1 Policy-Fingerprint an Entscheidung/Trade (nächster Plan-Schritt).
- P0: 3.2 Kandidaten-Modus `policy_lab.py`, 3.3 Promotion-Regel, 3.4 Policy-Report.
- P1: Phase T – T1 `/app/tests` Skript-Asserts kapseln, T4 Paritätstest check_signal vs. fast_sim.
- P1: Messphase-Auswertung Slippage/Low-Vol (GET /api/autotrade/slippage-stats) nach 2–4 Wochen.
- P2: 3.5 Portfolio-Backtest.
- Nutzer (Phase 0, offen): JWT_SECRET auf Render setzen (Pflicht vor Deploy!), Keys rotieren,
  Bitunix-Key-Whitelist.

## Nächste Aufgaben
1. Phase 3.1 Policy-Fingerprint (prompt_hash, lessons_hash, playbook_version, model, …).
2. Danach 3.2–3.4 (Champion vs. Kandidat).
