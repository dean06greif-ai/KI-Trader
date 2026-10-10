# PRD – MarketMaker (KI-Trader) Verbesserungen

## Original Problem Statement (gekürzt)
Externe, produktive Daytrading-Website (GitHub dean06greif-ai/KI-Trader, Branch conflict_071026_2044, Render-Deploy) modular verbessern, Originalstruktur beibehalten:
Asset-Korrelation (±1 markieren, alle gewählten Assets anzeigen, Ergebnisse speichern/löschen, Bug „nur 12 Coins“/„ohne Daten“, „Coins“→„Assets“), Pausieren + Zwischen-Suche in anderen Bereichen inkl. globaler Anzeige, Varianten auf Mobil + Score mit 1 Nachkommastelle, Trail-SL/BE-Analyse und Optimierung inkl. Live-Test, Broker-Frage, Autopilot-Verlauf „Regime suchen“-Bug + Löschen (kein Warmstart), Bewertung Note-Schutz.
User choices: direkt im Repo (Save to GitHub), Live-Test mit Minimal-Orders erlaubt, Speicherung in MongoDB, Energiesparplan entfällt.

## Architektur
FastAPI (backend/routers + services), React/CRACO (frontend/src/components), lokaler Worker (local_worker/worker.py, nutzt dieselben services).

## Umgesetzt (08.10.2026)
- services/asset_correlation.py: Stillstands-Watchdog statt 300 s-Limit, Verlauf (insert + prune 40), perfect_pairs, list/get/delete; history_sources: kombinierter Fortschritt der Teil-Downloads, Yahoo-1m-Fenster, Dukascopy je Tag; candle_cache: Forex-Fallback auf Dukascopy
- services/job_overview.py + routers/jobs_overview.py (/api/jobs/paused), PausedJobsBadge im Header
- job_control: Pool-Pause je Job, parked_locally; Worker 1.23.0: pausierte Jobs geben den Slot frei; Sperren Backtester↔KI-Trader-Lab und Werkbank↔Regime-Lab angepasst
- services/trail_policy.py; Backtester + Live (_manage_trade) nutzen dieselben Regeln; BE-Ratsche live; Trail-Werte im Trade gespeichert; Optimizer-Raum erweitert; Backtester-UI Trail-Start
- Autopilot: Regime-suchen-Fix, Löschen
- Mobil-CSS für Fortschrittsbalken, Score .1f
- Tests: test_improvements_0810_trail_corr_pause.py (18), iteration_62 (Testing-Agent 100 %)

## Backlog
- P1: Korrelation optional auf dem lokalen Worker rechnen (lange Erst-Downloads nicht auf Render)
- P1: Trail-Abstand in % als Alternative zu ATR
- P2: Pausierte Cloud-Jobs (Render-RAM) – bewusst nicht freigegeben
- P2: Note-Schutz „weich“ (Ausnahme bei großem Score-Plus + besserem Holdout)
