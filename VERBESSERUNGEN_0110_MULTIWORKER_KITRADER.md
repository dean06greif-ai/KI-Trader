# Prüf- & Umsetzungsbericht 01.10.2026 – Multi-Worker, Asset-Vorschlag, KI-Trader-Blocker

## 1. Ursachenanalyse „KI-Trader-Setups handeln kaum noch“ (Prod-DB, nur lesend)
Zeitraum 17.09.–01.10. (14 Tage): **512 KI-Trades eröffnet – aber ~650 Signale technisch verworfen**.

| Ursache | Wirkung (14 Tage) | Fix |
|---|---|---|
| **Funding 100× überschätzt**: Bitunix liefert `fundingRate` in **Prozent** (`"0.015"` = 0,015 %, Kappe `maxFundingRate "0.375"`), der Code las es als Bruch und rechnete nochmals ×100. Fee-Wächter verlangte dadurch Mindest-SL 1,3–1,7 % auf Krypto. | **207** Fee-Wächter-Blocks („erwartete Funding-Kosten 0.375%“) | `funding_fees._as_fraction` erkennt die Einheit über `maxFundingRate` |
| **Zwei widersprüchliche SL-Regeln**: Engine klemmte Scalp-SL auf 0,15 %, Mindest-SL-Regel verlangt 0,25 % (Forex) / 0,35 % (Indizes) / 0,5 % (Rohstoffe) / 0,4 % (Krypto) → Signal verworfen statt angepasst. Betraf v.a. Detektor-Trigger und Forex/Indizes/Rohstoffe. | **433** verworfene Entscheidungen | `min_sl_rule.widen`: SL auf Minimum +5 % erweitern, TPs skalieren (CRV bleibt), Notiz in `levels_clamp`; abschaltbar `min_sl_auto_widen` |
| **Drift-Block „0.25% < 0.25%“** bei der Ausführung (Kursdrift/Tick-Rundung zwischen Signal und Order) | **14** Signale | Ausführungs-Check mit 10 % Drift-Toleranz (`EXEC_DRIFT_TOLERANCE`) |
| **Order-Block (Krypto)**: Profil v2 lief **+110,69 USDT aus 10 Trades**; am 26.09. hat die KI die Regeln revidiert → „Validierung neu gestartet“ (nur Paper), danach **0 Paper-Trades** (Paper-Trades scheiterten an den Blockern oben) → Setup konnte nie wieder freigeschaltet werden. | Setup praktisch tot | Rücksprung per Chat + **automatische Selbstkorrektur** (`auto_revert_due`): tote/verlustreiche Revision nach Re-Test → zurück auf Original + bestes bewährtes Profil |

Prüfskripte: `scripts/analyze_low_trades.py`, `scripts/prod_setup_activity_probe.py` (read-only).

## 2. KI-Chat (Screenshot „fehlender Block SOEBEN REAL AUSGEFÜHRTE AKTIONEN“)
Ursache: Der Kommando-Parser kannte keine Setup-Befehle → nichts ausgeführt → die Antwort-KI zitierte interne Prompt-Begriffe.
- Neue Chat-Kommandos `setup_revert` (Original/vorherige Variante, je Klasse oder alle) und `setup_revise` (neue Regeln, Trader-Anweisung ohne Revisions-Sperre).
- `ai_playbook.revert_setup`: Revision raus (abgelöste Revision bleibt in `revision_history`), Profil auf beste bewährte Version (neue Version, nichts überschrieben), revisionsbedingte Paper-Sperre aufgehoben, Feed-Eintrag.
- Kein-Aktion-Fall: neutraler Hinweis an die KI, Prompt verbietet interne Block-/Systemnamen.

## 3. Lokale Worker: bis zu 2 gleichzeitig, Job-Zuweisung
- `local_exec`: `MAX_ACTIVE_WORKERS = 2`, dritter Worker wartet (`waiting_workers`), Job-Ziel (`worker_id` im Body → `target`), Claim nur durch Ziel-Worker, Ziel offline → klare Meldung/Timeout.
- Parallelität: ein weiterer Regime-Lab-/Werkbank-/Optimizer-Job darf starten, wenn alle laufenden Jobs **lokal auf einem anderen Worker** liegen (`parallel_allowed`). Cloud bleibt 1 Job (Render-RAM).
- UI: Auswahl „Worker“ neben „Ausführung → Lokal“ (Optimizer, Backtester, Regime-Lab, Dynamik-Werkbank) erscheint ab 2 verbundenen Workern; Verwalten-Panel listet alle Worker.
- Worker-Paket unverändert kompatibel (keine neue Pflichtversion); `worker.py` loggt nur den Warte-Hinweis.

## 4. Dynamik-Werkbank: Asset-Vorschlag je Regime
`services/asset_suggest.py` + `GET /api/dynamic-workbench/suggest-assets/{aid}?regimes=`:
Score je (Regime, Asset) aus Regime-Präsenz (Segmente der Analyse), Gleichlauf (coin_similarity) und bisherigen Trades dynamischer Strategien dieser Analyse. UI-Box „Vorschlag … Übernehmen / Je Regime ansehen“ unter der Asset-Auswahl.

## 5. Risiken & Rückwärtskompatibilität
- Ohne `worker_id` verhalten sich alle Jobs exakt wie vorher (beliebiger freier Worker).
- SL-Erweiterung vergrößert die Stop-Distanz (TPs mit) – bei Auto-Hebel/Risiko-Sizing bleibt das Risiko gedeckelt; abschaltbar per `min_sl_auto_widen: false`.
- Auto-Rücksprung greift nur bei KI/Review-Revisionen (nie bei Trader-Revisionen), erst nach Re-Test-Datum und nur wenn die Vorgänger-Variante nachweislich profitabel war.
- Prompt-Text des Chats/Mindest-SL geändert → neue Prompt-Version im Policy-Fingerprint (erwartet).

## 6. Tests
`backend/tests/test_multi_worker_and_trader_fixes.py` (14 Regressionstests) + bestehende Suiten (Chat-Kommandos, Funding, Local-Worker, Playbook, Worker-Paket) grün; Testing-Agent: Backend 7/7, Frontend-Flows verifiziert.
