# PRD – KI-Trader (extern deploybare Daytrading-Website)

## Original-Problemstellung
Bestehende, produktiv auf Render laufende Daytrading-Website (Repo `dean06greif-ai/KI-Trader`, Branch `conflict_210826_1112`) soll verbessert werden – sauber, modular, rückwärtskompatibel, Originalstruktur beibehalten (Render-Deploy). FastAPI-Backend (`backend/`), React-Frontend (`frontend/`), MongoDB, Bitunix-Anbindung, Multi-LLM-KI-Trader.

## User Personas
- Trader/Admin (Dean): betreibt die Website, handelt live auf Bitunix, steuert den KI-Trader über MasterPrompt/Lektionen/Direktiven.

## Kern-Anforderungen (statisch)
- Stabilität & Rückwärtskompatibilität vor aggressiven Änderungen; Regressionstests für größere Änderungen.
- Original-Ordnerstruktur beibehalten (Render-Deploy durch User selbst via "Save to GitHub").
- Auto-Leverage-Philosophie: max. möglicher Hebel (Liquidation hinter SL), Risiko über Marge/SL steuern – NICHT über Hebel-Deckel.

## Umgesetzt (22.08.)
1. **Multi-Positions-Fix (Bitunix)**: Watchdog bindet jede Börsen-Position exakt per Position-ID (`_find_local` mit claimed-Set + persistierter Bindung, `position_watchdog.py`); mehrere Positionen auf demselben Symbol/Seite und Hedge Long+Short erscheinen als getrennte Trades. `sync_live_positions` (`bitunix_trade.py`) verbucht externe Closes per Position-ID (vorher: Symbol+Seite → geschlossene Position blieb offen, solange eine andere gleiche offen war). Real verifiziert (2x BTC SHORT + 1x BTC LONG getrennt übernommen).
2. **„Harte Regel“ entfernt**: Code-Default `max_leverage: 25` in `ai_master_prompt.DEFAULT_RULES` → 0 (kein Limit). Wenn der Trader die Regeln nie selbst gespeichert hat (kein `editor`-Feld), werden Defaults beim Start automatisch aktualisiert. `lev_auto_max`-Clamp 100→200 (Engine + KI-Whitelist `auto_lev_max` max 200).
3. **Lektions-Qualität**: Neue MasterPrompt-Flags (maschinell erzwungen): `block_leverage_lessons` (keine Hebel-Deckel-Lektionen; Verweis auf Marge/SL) und `require_direction_context` (keine pauschalen Richtungs-Lektionen ohne Marktkontext). Lektionen tragen jetzt optional `context` (Gültigkeitsbedingung) + `valid_until` (Verfall, via `expires_days` vom Lernlauf); abgelaufene, nicht gesperrte Lektionen fallen aus dem Prompt (`ai_lessons.is_expired`/`active_lessons`). Lernlauf-Prompt + Lesson-Policy entsprechend erweitert.
4. **Kapital-Fallback**: Trade wird mit verfügbarem Rest-Kapital eröffnet, wenn gewünschte Marge nicht frei ist (bestand bereits in `bitunix_trade.py` ~Z.1437, min. 5 USDT); KI-Prompt-Hinweis ergänzt, damit die KI Setups nicht wegen knappen Kapitals ablehnt.
5. **Strategie-Vergleich ohne manuelle Trades**: `/api/analytics/strategy-comparison` Default `include_manual=false`; per-Coin "Performance je Strategie" (Frontend `PerformanceAnalytics.js`) filtert manuelle Trades ebenfalls.
6. **Regressionstests**: `backend/tests/test_multi_position_and_lesson_quality.py` (10 Tests) + Fake-Erweiterung in `test_watchdog_sync_only.py`. Testing-Agent: 10/10 pass (Backend + Frontend).

## Bekannte Hinweise
- 2 vorbestehende Test-Fails in `tests/test_position_watchdog.py` (fehlendes `telegram`-Modul lokal) – kein Regressionsbezug.
- Hat der Trader die MasterPrompt-Regeln früher einmal selbst gespeichert, bleibt sein gespeicherter `max_leverage`-Wert bestehen → im KI-Governance-Panel auf 0 stellen.
- Lokale Umgebung nutzt lokale MongoDB (Prod: Atlas via Render-Env) – Code unverändert env-basiert.

## Backlog / Nächste Aufgaben
- P1: Offene manuelle Positionen in `strategy-comparison` bei `include_manual=true` auch ohne geschlossene Trades mit open_trades-Zählung anzeigen (Kosmetik).
- P1: UI-Toggles für neue Regel-Flags (`block_leverage_lessons`, `require_direction_context`) im KI-Governance-Panel.
- P2: Lektionen-UI: `context`/`valid_until` anzeigen und editierbar machen.
- P2: Watchdog: gemergte Misch-Positionen (manuell aufgestockt) weiterhin nur beobachtet – optional anteilige Verrechnung.
