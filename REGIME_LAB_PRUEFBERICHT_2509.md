# Prüfbericht 25.09.2026 – Regime-Lab/Autopilot, Event-Setups, Signal-Meldungen, lokaler Worker

Basis: Branch `conflict_240926_1947`. Produktiv-DB/Börse **nicht** berührt (Preview mit lokaler Test-DB,
`AI_TRADER_LOCAL_DISABLE=1`). Alte Regime-Analysen wurden wie gewünscht ignoriert.
Tests: `backend/tests/test_regime_jump.py`, `backend/tests/test_event_setups_and_signal_notify.py` (20 neue
Unit-Tests) – die komplette Unit-Suite hat vorher/nachher exakt dieselben (alten, fremden) Fehlschläge.

---

## 1. Regime-Lab & Autopilot

### 1a. Befunde (Prüfung)
| # | Befund | Wirkung | Status |
|---|---|---|---|
| 1 | **Übernahme-Schutz maß die falsche Größe:** `holdout_regressed` (Backend) und `autopilotDecision` (Frontend) verglichen *Live=Final* auf dem Holdout. Live=Final misst nur Selbst-Übereinstimmung – träge Detektoren haben hier 90–98 %. | Ein klar besserer, schnellerer Detektor wurde als „Holdout gefallen“ **blockiert** (genau der Fall Kombi → besseres Modell). | ✅ behoben: Vergleich über **Macro-F1 gegen Referenz v2 (Holdout)**, Alt-Läufe ohne Referenz v2 behalten Live=Final. |
| 2 | **Holdout-Leck in der Endlos-Suche:** bei Score-Gleichstand entschied `robustness_key` zuerst nach dem Holdout. Bei tausenden Varianten wird der Holdout so indirekt mitoptimiert. | Holdout kein ehrlicher Endtest mehr. | ✅ behoben: Gleichstand nur noch nach **Trainings**-Referenz-F1 → Nähe zum Phasen-Sweet-Spot → weniger Wechsel. |
| 3 | **Decke der Erkennung:** alle drei Detektoren (reactive/ema/kombi) sind Schwellen + Ad-hoc-Persistenzfilter. Beste bisherige Holdout-Werte: F1 ≈ 47, κ ≈ 17–20 („mäßig“). | Autopilot konnte nur im schwachen Raum suchen. | ✅ neuer Detektor **`jump`** (s. 1b). |
| 4 | Scoring (75 % Referenz v2 Macro-F1 + 25 % Live=Final, Phasen-Strafe, Nutzen nur Training), innere Validierung/Holdout-Trennung, Referenz v2 (7-d-Fenster) | fachlich korrekt | unverändert |

### 1b. Neuer Detektor „jump“ – Statistisches Jump-Modell (`services/regime_jump.py`)
Stand der Forschung für Regime-Erkennung (Nystrup et al. 2020/21, Aydınhan/Kolm/Mulvey/Shu 2024): je Kerze wird der
Zustand gewählt, der **Abstand zum Zustands-Zentrum + Sprungkosten je Wechsel** minimiert. Die Sprungkosten ersetzen
alle Ad-hoc-Persistenzregeln durch eine optimale Abwägung.
- Features: zwei vola-normierte EWMA-Renditen (schnell/langsam) – über Coins und Timeframes vergleichbar.
- Zentren semantisch fest (auf / seitwärts / ab) → **kein Fit, kein Holdout-Leck**.
- LIVE = Online-Filter (streng kausal, per Test abgesichert), FINAL = Viterbi-Rückverfolgung mit phasenfreien Features.
- Nur numpy/pandas → läuft unverändert auf dem lokalen Worker (Paket enthält `services/*.py` automatisch).

**Benchmark** (`backend/scripts/regime_jump_benchmark.py`, Autopilot-Bewerter, 1080 Tage, 75 % Training, Holdout-Werte):

| Detektor | Daten | Holdout Macro-F1 | Holdout κ | Ø Richtungs-Phase |
|---|---|---|---|---|
| kombi (Standard / Mini-Suche 24.09.) | BTC/ETH/SOL 1h | 33,7 / 34,0 | 2,6 / 4,7 | 11,9 / 8,5 d |
| ema (bisher bester, thr 0,34) | BTC/ETH/SOL 1h | 46,7 | 16,9 | 10,4 d |
| **jump (Standard)** | BTC/ETH/SOL 1h | **≈ 61** | **≈ 41** | ≈ 7 d |
| ema thr 0,34 | XRP/DOGE 1h (nie zum Einstellen benutzt) | 49,1 | 21,9 | 10,8 d |
| **jump (Standard)** | XRP/DOGE 1h (Out-of-Sample-Coins) | **63,1** | **43,0** | 7,2 d |
| ema / kombi / **jump** | 5 Coins 4h | 49,4 / 34,8 / **62,1** | 21,6 / 7,5 / **41,1** | 9,8 / 12,2 / 7,2 d |

→ κ verdoppelt, Richtungs-Phase liegt im Ziel 4–14 d. Die Standardwerte (1 d / 14 d / Zentrum 0,6 / Sprungkosten 1,5 d)
wurden auf BTC/ETH/SOL gewählt und auf XRP/DOGE sowie 4h bestätigt (kein Overfitting auf die Such-Coins).

**Integration:** Detektor-Auswahl „Jump-Modell (Sprungkosten, empfohlen)“, eigene Parametergruppe, Autopilot-Suchraum,
Kalibrier-Raster, Ablation (`ohne Sprungkosten`, `Alternative ema`), Zusammenfassung. Der Engine-Standard bleibt
`reactive` → bestehende Analysen/Freigaben und der KI-Trader verhalten sich unverändert, bis du bewusst umstellst.

**E2E-Nachweis (Preview):** Autopilot 4h BTC/ETH, Start `kombi`, 40 Runden → Wechsel auf `jump`,
Holdout-F1 21 → 62, Übernahme empfohlen.

### 1c. Empfehlung
1. Deploy. Worker-Paket muss **nicht** neu geladen werden (Code kommt automatisch mit, keine neue Abhängigkeit).
2. Autopilot neu laufen lassen (1h, Krypto-Kern, Ziel 4–14 d, „Grundgerüst mitsuchen“ an) – er findet `jump` von selbst;
   oder direkt Detektor „Jump-Modell“ wählen. Danach Analyse → Ablation → erst dann Shadow-Freigabe.
3. Bewertung auf der Qualitätskarte: Holdout-F1 (Referenz v2) ≥ 55 = gut, κ, Ø Richtungs-Phase 4–14 d.

## 2. Event-Setups ✅ behoben
Befund: `fomc_event`, `cpi_event`, `nfp_event`, `ppi_event`, `pce_event` liefen durch den normalen Aktivitäts-Wächter
(Ziel 5 Trades/Woche) → sie wurden zwischen den Events als INAKTIV geflaggt und von `setup_review` per LLM überarbeitet.
Neu (`setup_lifecycle.event_inactivity_reason`):
- Kein Event seit Anlage/Revision → **nie** inaktiv.
- Letztes abgeschlossenes Event-Fenster (FOMC: T−90…T+150 min, Daten-Events: T−60…T+120 min) **mit** Trade → aktiv.
- Event fand statt, aber **kein Trade** im Fenster → inaktiv → Überarbeitung erlaubt.
- `revision_allowed`: Event-Setups dürfen auch von der KI nur nach verpasstem Event oder echter Ergebnis-Rückstufung
  überarbeitet werden; alte Wochen-Regel-Flags werden von `setup_review` ignoriert.
Das Handeln im Event-Fenster selbst (Gate, Schnell-Takt) ist unverändert.

## 3. Signal-Meldungen ✅ umgesetzt
- Telegram-Signal wird erst **nach** dem Trade-Versuch gesendet – standardmäßig nur, wenn wirklich ein Trade eröffnet wurde.
- Meldung enthält **Trade: 🔴 LIVE / 📄 PAPER / 🧪 DATENSAMMLUNG** und beim KI-Trader **Setup** (`fomc_event` …);
  auch die „TRADE ERÖFFNET“-Meldung zeigt das Setup.
- Neue Schalter (Einstellungen → Telegram → Signale): „nur melden, wenn Trade eröffnet“ und „auch Datensammel-Trades melden“
  (beide Standard an; aus = altes Verhalten).
- Website: Signal-Popup nach derselben Regel, Toast mit Modus + Setup; KI-Trader-Analyse zeigt je Entscheidung
  LIVE/PAPER/DATENSAMMLUNG + Setup.
- Signal-Dokument speichert zusätzlich `trade_id`, `trade_mode`, `trade_setup`.

## 4. Lokaler Worker – RAM ❎ keine Änderung (bewusst)
Der Worker ist bereits geräte-adaptiv: Kerzen-Cache-Budget = **gesamter RAM minus 1 GB** (automatisch je Gerät),
Rechenprozesse = **alle CPU-Kerne**. 60–70 % Auslastung heißt: die Jobs brauchen schlicht nicht mehr. RAM ist hier nur
Cache – mehr RAM macht Analysen nicht schneller (Engpass = CPU). Ein künstlich höheres Ziel brächte nur Swap-/Freeze-Risiko
auf kleineren Geräten. Empfehlung: **so lassen.**
