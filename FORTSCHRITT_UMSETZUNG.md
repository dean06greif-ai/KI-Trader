# Fortschritt Umsetzung – PLAN_LEKTIONS_BILANZ & PLAN_REGIME_BRUECKE

Stand: 17.09.2026 · Branch-Basis `conflict_170926_1759` · Umgebung: Preview mit **lokaler Mongo**
(`DB_NAME=crypto_scanner_dev`, `AI_TRADER_LOCAL_DISABLE=1`), Produktiv-DB unangetastet.

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

| Baustein | Status | Dateien | Notizen |
|---|---|---|---|
| B1.1 Nachweis-Gate `validate_release` / `validate_activation` | ⬜ | | |
| B1.2 Endpunkte `/release`, `/releases`, `/release-check` | ⬜ | | |
| B1.3 UI RegimeLab Knöpfe/Badge | ⬜ | | |
| B1.4 Proposal-Scope `regime_release` (suggest/auto+Karenz) | ⬜ | | |
| B1.5 Tests | ⬜ | | |
| B2 `structural_regime.py` Resolver + Frische-Regel + Gate-Quelle | ⬜ | | |
| B3 Prompt-Block + Snapshot + Rewards `by_structural_regime` | ⬜ | | |
| B4 Fingerprint `regime_artifact` | ⬜ | | |
| B5 Strukturelle Lektionen | ⏸ | | laut Plan erst nach 4 Wochen Daten |

---

## Regressions-Lauf
- `cd backend && python -m pytest tests/test_lesson_*.py tests/test_trade_postmortem.py tests/test_ai_lessons_merge.py tests/test_ai_learning.py tests/test_policy_fingerprint.py -q -n 0` → 72 passed (17.09.)

## Hinweise für Render-Deploy
- Keine neuen Abhängigkeiten, keine Ordner-Änderungen. Neue Collection `ai_lesson_cf` entsteht automatisch.
- Rollback: Flags in `ai_trader_config` auf `false` bzw. Loop-Registrierung in `server.py` entfernen.
