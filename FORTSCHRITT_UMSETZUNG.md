# Fortschritt Umsetzung – PLAN_LEKTIONS_BILANZ & PLAN_REGIME_BRUECKE

Stand: 17.09.2026 · Branch-Basis `dein-branch-name3` · Session 1: Preview mit lokaler Mongo
(`DB_NAME=crypto_scanner_dev`, `AI_TRADER_LOCAL_DISABLE=1`) · Session 2: Preview gegen Produktiv-Atlas
(`crypto_scanner`, `AI_TRADER_LOCAL_DISABLE=1` → keine Engine-Loops/Trades aus der Preview).

Diese Datei ist das Logbuch: Wer hier weiterarbeitet, sieht, welcher Schritt zuletzt abgeschlossen war.
Reihenfolge laut Absprache: **erst PLAN_LEKTIONS_BILANZ (A→B→C), dann PLAN_REGIME_BRUECKE (B1→B4)**.

Legende: ✅ umgesetzt + Tests grün · 🔶 teilweise · ⬜ offen · ⏸ bewusst zurückgestellt (Rollout-Phase)

---

## Teil 1 – PLAN_LEKTIONS_BILANZ

| Baustein | Status | Dateien | Notizen |
|---|---|---|---|
| A1 Prompt-Schema `applied_lessons` / `would_be`, Kurz-IDs im Lektionsblock | ✅ | `services/lesson_attribution.py` (`PROMPT_ADDENDUM`, `short_id`), `services/ai_lessons.py` (`lessons_text(with_ids=)`, `_lesson_line`), `services/ai_engine.py` (Addendum an `sys_prompt` nur mit Flag + aktivem Bestand) | Systemprompt-Konstanten unverändert → `ANALYSIS_PROMPT_HASHES`/Fingerprint stabil; Addendum wird zur Laufzeit angehängt |
| A2 Parsing/Validierung | ✅ | `lesson_attribution.parse_applied/parse_would_be/attribution_fields`; Aufruf in `ai_engine.py` beim Bau von `dec` | Nur IDs des aktiven Bestands, max. 6; `would_be` nur bei HOLD mit `blocked_by ⊆ applied`; Level durch `clamp_levels`. Ohne KI-Felder: `dec` exakt wie bisher |
| A3 Vererbung an Trades + Backfill | ✅ | `ai_engine._emit_signal` Signal-Dict `applied_lessons`; `bitunix_trade.py` (Market + Limit-Fill); `boot_migrations.migrate_lesson_attribution_backfill` (Marker `lesson_attribution_backfill_v1`) | |
| A4 Tests | ✅ | `backend/tests/test_lesson_attribution.py` (7 Tests) | |
| B1 Modul Gegenprobe | ✅ | `services/lesson_counterfactual.py` (`frame_from_decision`, `review_hold`, `aggregate`, `CounterfactualService`), gemeinsame Kerzen-Hilfe `services/candles.recent_1m` (Postmortem delegiert dorthin – kein Duplikat) | Loop alle 15 min, respektiert `AI_TRADER_LOCAL_DISABLE`; Registrierung `server.py` neben Postmortem, Flag `lesson_counterfactual_enabled` |
| B2 Retention | ✅ | `services/retention.py` (`ai_lesson_cf`, 60 Tage) | |
| B3 Tests | ✅ | `backend/tests/test_lesson_counterfactual.py` (5 Tests: SL/TP/beide/kein Treffer, Kosten, Short, Aggregation, `_pending`-FakeDB) | |
| C1 Bilanz-Modul | ✅ | `services/lesson_impact.py` (`impact_rows`, `verdict`, `prompt_block`, `evidence_for`, `LessonImpactService` mit 10-min-Cache) | Urteilsregeln wie Plan; `hinderlich` ab `net_r <= -1.0` (Grenzwert-Test −0,99 vs. −1,0) |
| C2 Endpunkt | ✅ | `routers/ai_governance.py`: `GET /api/ai/lessons/impact?days=`, `POST /api/ai/lessons/impact/run` (Admin) | Antwort: `rows`, `totals` (Attribution-Quote, cf_done/pending), `recent_prevented`, `flags` |
| C3 Prompt-Block (Flag `lesson_impact_in_prompt`, Default aus) | ✅ | `ai_learning._impact_block` (nach BISHERIGE LEKTIONEN), `reevaluate_lessons` (Evidenz je id) | Ohne Flag Prompt byte-identisch zu vorher |
| C4 UI | ✅ | `frontend/src/components/LessonImpact.js` (Badge, Ausklapper, Übergabe-Knopf), Einbau in `AITradingPanel.js` Lektionen-Liste | `data-testid`: `lesson-impact-summary`, `lesson-impact-badge-<id>`, `lesson-impact-toggle-<id>`, `lesson-impact-table-<id>`, `lesson-impact-handover-<id>` |
| C5 Tests | ✅ | `backend/tests/test_lesson_impact.py` (7 Tests: Grenzwerte 11/12, −0,99/−1,0, Kontrollgruppe je Klasse, Sammel-Trades getrennt, Prompt-Block max. 8 Zeilen + LOCKED, Service-Cache) | |
| Config-Flags | ✅ | `ai_engine.DEFAULT_CONFIG`: `lesson_attribution_enabled=True`, `lesson_counterfactual_enabled=True`, `lesson_impact_in_prompt=False`; über `update_config` schaltbar | |
| Rollout-Phasen 1–5 (Laufzeit-Beobachtung, Kalibrierung) | ⏸ | – | Erst nach Deploy: Attribution-Quote in `GET /api/ai/lessons/impact` → `totals.attribution_pct` beobachten |

## Teil 2 – PLAN_REGIME_BRUECKE_LAB_KI_TRADER

Stand 17.09.2026 (Session 2, Preview gegen **Produktiv-Atlas** `crypto_scanner`, `AI_TRADER_LOCAL_DISABLE=1`):
Backend B1–B4 war bereits im Branch vorhanden, aber hier nicht protokolliert und **ohne Frontend**.
Diese Session: Backend gegen echte Daten geprüft, kleine Lücken geschlossen, komplettes Frontend nachgezogen.

| Baustein | Status | Dateien | Notizen |
|---|---|---|---|
| B1.1 Nachweis-Gate `validate_release` / `validate_activation` | ✅ | `services/regime_release.py` (rein oben, DB-Helfer unten); Re-Export in `regime_lab.py` | Session 2: Analyse-Scope `both` wird für die Freigabe als `combined` normalisiert (kein `both` im release-Dokument) |
| B1.2 Endpunkte `/release`, `/releases`, `/release-check`, `/release-proposals/{pid}/stop` | ✅ | `routers/regime_lab.py` | `require_admin` an Schreib-Endpunkten; 400 mit ALLEN Gründen; Audit `regime_release`. Live geprüft (17.09.): 401 ohne Token, 400 mit Gründen bei fehlenden Nachweisen, `reason` Pflicht |
| B1.3 UI RegimeLab Knöpfe/Badge/History | ✅ | **neu** `frontend/src/components/RegimeRelease.js` + `.css`; Einbau `RegimeLab.js` (Badge in Liste, `RegimeReleaseControls` über Analyse-Detail) | Knöpfe grau bis `release-check` grün, Tooltip = fehlende Nachweise; `reason` per Prompt (Pflicht); History-Ausklapper; Widerruf. `data-testid`: `regime-release-shadow-<aid>`, `regime-release-active-<aid>`, `regime-release-revoke-<aid>`, `regime-release-badge-<aid>`, `regime-release-history-<aid>` |
| B1.3 KI-Trader-Panel: Stufe je Klasse + KI-Empfehlung | ✅ | `RegimeRelease.StructuralStagePanel`, Select `structural_regime_autonomy` in `AITradingPanel.js` (neben Regime-Sperre); Vorschlagskarte zeigt Scope `regime_release` lesbar | `data-testid`: `ai-structural-stage-<cls>`, `ai-structural-autonomy-select`, `ai-structural-activation`, `ai-structural-proposal-stop-<pid>` |
| B1.4 Proposal-Scope `regime_release` (suggest/auto+Karenz) | ✅ | `regime_release.proposal_for/handle_recommendation/apply_due_auto_proposals`, `ai_research` (Prompt-Feld `regime_release_recommendation` + STRUKTUR-REGIME-Block), `ai_engine_governance._apply_changes/decide_proposal`, `ai_engine.DEFAULT_CONFIG.structural_regime_autonomy="suggest"` | Session 2: Trader-Klick „Übernehmen“ prüft das Stichproben-Gate **erneut** (`needs_data` bleibt nicht anklickbar → 400 mit Grund; `routers/ai.py`) |
| B1.5 Tests | ✅ | `tests/test_regime_release.py` (9 Tests, +2 in Session 2: Scope-Normalisierung, Gate beim Übernehmen) | |
| B2 `structural_regime.py` Resolver + Frische-Regel + Gate-Quelle | ✅ | `services/structural_regime.py`, `regime_gate.gate_source/check_signal_allowed`, `server.py` (Loop-Registrierung), `bitunix_trade.DEFAULT` `regime_gate_source="own"` (Session 2), `routers/autotrade.regime_phase` liefert `lab{stage,phase,state}` (Session 2) | Live-Check `scripts/live_check_regime_bridge.py` (nur lesend, 17.09.): echte 1h-Analyse `ra_8fe52dd6` + 720 echte 1h-Kerzen → `state ok`, Richtung `down`, Prompt-Zeile ohne Kurzfrist-Begriffe, Artefakt `lab:<aid>:<fp8>` |
| B2.2 UI Auto-Trade-Modal „Regime-Quelle“ | ✅ | `StrategyAutoTradeModal.js` Select `regime_gate_source` (own/lab) + Hinweis, ob Lab wirksam ist | `data-testid`: `sat-regime-gate-source`, `sat-regime-lab-hint` |
| B2.3 Tests | ✅ | `tests/test_structural_regime.py` (8), `tests/test_regime_gate.py` | |
| B3 Prompt-Block + Snapshot + Rewards `by_structural_regime` | ✅ | `ai_engine.py` (~Z. 1655 Struktur-Block nur Stufe `active`; Snapshot `structural` ab `shadow`; Smart-Skip bei `direction_changed`), `ai_learning.LEARNING_SYSTEM` (Ebene im `context` benennen), `ai_rewards.by_structural_regime`, `GET /api/ai/rewards` | UI Session 2: zweite Tabelle „Reward nach Struktur-Regime“ in `AIRewardPanel.js` (`reward-structural-regimes`), erscheint erst mit Shadow-Daten |
| B4 Fingerprint `regime_artifact` | ✅ | `ai_engine.py` ~Z. 1749 `policy_fingerprint.build(..., regime_artifact=…)`, `setup_variant` Label „Regime-Modell“, `PolicyReportCard.policySubtitle` zeigt „Lab-Modell a1b2c3“ (Session 2) | Artefakt = Freigabe-ID + Modell-FP (stabil je Freigabe) |
| B5 Strukturelle Lektionen | ⏸ | | laut Plan erst nach 4 Wochen Daten |
| Rollout-Phasen 1–5 | ⏸ | | **Nächster Schritt für den Trader:** im Lab eine 1h-Krypto-Analyse (BTC/ETH/SOL/BNB, combined, 360–720 d) anlegen, Regime „behalten“ markieren, **Kalibrierung + Ablation** laufen lassen → Knopf „Beobachten (Shadow)“ wird grün. Aktuell (17.09.) erfüllt keine gespeicherte Analyse das Nachweis-Gate (kein `kept`, keine Kalibrierung/Ablation) |

---

## Prüfung PLAN_LEKTIONS_BILANZ (Session 2)
- Tests `test_lesson_attribution.py`, `test_lesson_counterfactual.py`, `test_lesson_impact.py` → grün (17.09.).
- Endpunkt `GET /api/ai/lessons/impact` und UI `LessonImpact.js` vorhanden; Boot-Migration `lesson_attribution_backfill_v1` lief in der Preview durch (0 Trades nachgezogen – Attribution beginnt erst mit neuen Entscheidungen). Teil 1 bleibt **fertig**; offen nur Rollout-Beobachtung.

## Regressions-Lauf
- `cd backend && python -m pytest tests/test_lesson_*.py tests/test_trade_postmortem.py tests/test_ai_lessons_merge.py tests/test_ai_learning.py tests/test_policy_fingerprint.py -q -n 0` → 72 passed (17.09.)
- `python -m pytest tests/test_regime_release.py tests/test_structural_regime.py tests/test_policy_fingerprint.py tests/test_regime_gate.py tests/test_lesson_*.py -q -n 0` → 52 passed (17.09., Session 2)

## Hinweise für Render-Deploy
- Keine neuen Abhängigkeiten, keine Ordner-Änderungen. Neue Collection `ai_lesson_cf` entsteht automatisch; `regime_analyses[aid].release` und `settings/structural_regime_cache` sind additiv.
- Rollback: Flags in `ai_trader_config` auf `false` bzw. Loop-Registrierung in `server.py` entfernen; Regime-Freigabe per Widerruf (Stufe `none`) → Prompt/Gate exakt wie zuvor.

