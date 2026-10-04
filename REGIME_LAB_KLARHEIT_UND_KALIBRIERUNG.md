# Regime-Lab: Klarheit, Kalibrierungs-Fix & Erkennungs-Qualität (Stand 06/2026)

Antworten auf die Fragen „Ich verstehe das Regime-Lab null“, „Kalibrierung wird nicht
angezeigt“, „Wie sehe ich, wie gut die Regime-Erkennung für Krypto ist?“ und „Funktioniert
das Backup langfristig?“ – inkl. der umgesetzten Änderungen (Branch `regime-lab-clarity`).

---

## 1. Was macht das Regime-Lab eigentlich? (2 Welten)

| | **A · Regime erkennen & speichern** (Schritt 1) | **B · Erkennung verbessern** (Schritt 1b + „Wissenschaftlich kalibrieren“) |
|---|---|---|
| Was passiert | Regelbasierte Erkennung auf den Kerzen (Umkehrpunkte / EMA / Kombi / Regression). Kein Training → in Sekunden fertig. | Mehrere Einstellungen werden gegeneinander getestet (gegen eine Referenz oder gegen die Live=Final-Kennzahl). |
| Ergebnis | **Gespeicherte Analyse** (Chart, Regime-Karten, Erkennungs-Qualität, Strategie-Suche, Walk-Forward). | **Empfohlene Einstellungen** – keine Analyse! Erst „Übernehmen“ + Schritt A neu starten macht sie wirksam. |
| Wo sichtbar | „2 · Gespeicherte Analysen“ → „3 · Analyse“. | Direkt unter dem Werkzeug + neu: **Kalibrierungs-Verlauf** (gespeichert, jederzeit erneut übernehmbar). |

**Das war die Verwirrung:** „Regime suchen“ war schnell, weil es keine Optimierung ist. Die
Kalibrierung lief separat, ihr Ergebnis war nur eine kleine Textzeile – und ging nach einem
Neustart / Schließen des Panels verloren. Und (siehe 2.) sie hat für die Standard-Detektoren
faktisch nichts verändert.

## 2. Bug: „Wissenschaftlich kalibrieren“ war für reactive / ema / kombi wirkungslos

`services/regime_truth.calibrate` durchlief IMMER den Suchraum des alten Regressions-Detektors
(`trend_t`, `adx_min`, `hysteresis`, `confirm_days`, `confidence_min`, `smooth_days`). Diese
Parameter werden von den Detektoren **reactive (Standard), ema und kombi gar nicht benutzt**
(`eng.CONFIG_GROUPS`). Folge: alle Kandidaten lieferten identische Labels → „vorher = nachher“.
Zusätzlich fehlte `detector` in `best_config` und die UI ersetzte die komplette Konfiguration →
der gewählte Detektor (z.B. „kombi“) wurde beim Übernehmen still auf „reactive“ zurückgesetzt.

**Fix (rückwärtskompatibel):**
- `regime_truth.calibration_grid(detector, base, days)`: Suchraum je Detektor
  - reactive: `rev_atr_mult`, `persist_candles`, `side_leg_atr_mult`, `min_phase_days`, `min_hold_days`
  - ema: `ema_regime_days`, `ema_regime_thr`, `ema_regime_smooth_days`, `ema_regime_persist_days`, `min_phase_days`, `min_hold_days`
  - kombi: `kombi_ema_days`, `kombi_thr`, `kombi_slope_days`, `kombi_persist_days`, `kombi_dominance_days`, `min_phase_days`, `min_hold_days`
  - regression: unverändert (alter Suchraum, identische Ergebnisse)
- Bericht enthält jetzt `detector`, `tuned_keys`, `changes` (nur geänderte Parameter), `improved`.
- `best_config` enthält `detector`; die UI **ergänzt** die Konfiguration statt sie zu ersetzen.
- Synthetischer Test (klare Phasen, 1h): balancierte Richtungs-Treffer reactive 12 → 53, ema 40 → 75,
  kombi 23 → 52; regression unverändert 25 → 33.

**Wichtig für „Lokal“:** Der lokale Worker enthält eine Kopie von `services/` → nach dem Deploy
das Worker-Paket neu herunterladen (Ausführung → Lokal → ⚙ Verwalten → Download) und den Worker
neu starten, sonst rechnet er weiterhin mit dem alten Kalibrierungs-Code.

## 3. „Ergebnis wird nicht angezeigt“ – Ursachen & Fixes

1. **Server-Neustart während/nach dem Job** (Render-Deploy, Idle): die In-Memory-Jobliste ist leer,
   `GET /status/{job}` lieferte 404 – die UI wertete das als „fertig ohne Ergebnis“ und blendete
   still alles aus. → Status-Fallback liest jetzt auch `regime_calibrations` und `regime_lab_runs`;
   die UI meldet unbekannte Jobs als Fehler statt zu schweigen (Kalibrierung, EMA-Vergleich,
   Kombi-Auto-Kalibrierung, Ablation).
2. **Ergebnis nur flüchtig:** neu `GET /api/regime-lab/calibrations` + UI „Kalibrierungs-Verlauf“
   (Cloud + lokaler Worker), mit „Übernehmen“.
3. **Ergebnis unlesbar:** neue Vorher/Nachher-Tabelle (`RegimeCalibrationResult.js`) mit den
   geänderten Parametern und dem verwendeten Detektor.

## 4. „Wie gut ist die Regime-Erkennung für Krypto?“ → Erkennungs-Qualität

Neu in jeder geöffneten Analyse (oben, je Bereich „kombiniert“ / je Coin): **Erkennungs-Qualität**
mit Note **gut / mittel / schwach** je Anlageklasse (Krypto, Forex, …), berechnet aus bereits
gespeicherten Kennzahlen (`services/regime_quality.py`, funktioniert auch für alte Analysen):

- **Holdout Live=Final** (Hauptkennzahl): wie oft die Live-Erkennung (ohne Zukunftswissen) im
  unangetasteten Testzeitraum dieselbe Richtung sieht wie die finale Rückschau. ≥65 % gut, ≥50 % mittel.
- Gesamt Live=Final, Trend-Treffer, Ø Phasendauer, Ø Erkennungs-Verzögerung, Verstöße, Holdout-Kerzen.
- Ohne belastbaren Holdout (<200 Kerzen) wird der Gesamtwert mit Warnhinweis verwendet.

**Vorgehen „ultimative Krypto-Erkennung“:** Krypto-Coins wählen → Training 75 % → Detektor wählen →
„Wissenschaftlich kalibrieren“ (Referenz: Rückblick) → „Regime suchen & speichern“ → Qualität lesen →
mit anderem Detektor / EMA-Vergleich / Kombi-Auto-Kalibrierung wiederholen → Analyse mit der besten
Holdout-Qualität behalten und für Strategie-Suche / Walk-Forward nutzen.

## 5. Backup – läuft das langfristig ohne weiteres Zutun?

**Ja, sofern das Backend läuft.** `services/backup.py` startet 15 min nach Boot und prüft alle
30 min, ob das letzte Backup ≥ 24 h alt ist → Dump der kritischen Collections (Settings, Trades,
Lektionen, Regime-Analysen, Kalibrierungen, Strategien …) als `backup_YYYYMMDD_HHMMSS.json.gz`
in den privaten Supabase-Bucket `mongo-backups`, Aufbewahrung 30 Tage. Standard `enabled=true`,
nichts weiter zu aktivieren. Voraussetzungen/Checks:

- `SUPABASE_URL` + `SUPABASE_SERVICE_ROLE_KEY` auf Render gesetzt (sind es).
- Kontrolle: `GET /api/maintenance/backup` → `last_run.at` sollte < 24 h alt sein, `last_error` leer;
  `GET /api/maintenance/backup/list` zeigt die Dateien im Bucket. In der UI: Wartung → Backup-Karte.
- Manuell: `POST /api/maintenance/backup/run` (Admin).
- Wiederherstellung: `POST /api/maintenance/backup/restore` erst als Trockenlauf (`apply=false`),
  dann `apply=true` – ersetzt nur per `_id`, löscht nie.
- Einzige echte Lücke: schläft der Render-Dienst regelmäßig < 15 min nach dem Start wieder ein
  (Free-Tier-Idle), kommt der erste Lauf nie zustande. Da der KI-Trader dauerhaft läuft, ist das im
  Normalbetrieb kein Problem – bei Zweifel `last_run` prüfen.

## 6. Geänderte / neue Dateien

- `backend/services/regime_truth.py` – detektor-spezifischer Suchraum, `config_changes`, erweiterter Bericht
- `backend/services/regime_quality.py` – NEU: Erkennungs-Qualität je Anlageklasse
- `backend/routers/regime_lab.py` – Status-Fallback, `GET /calibrations`, `quality` in `GET /{aid}`
- `backend/tests/test_regime_lab_clarity.py` – NEU: 25 Regressionstests (unit, ohne Netzwerk)
- `frontend/src/components/RegimeCalibrationResult.js`, `RegimeCalibrationHistory.js`, `RegimeQualityCard.js` – NEU
- `frontend/src/components/RegimeEngineSettings.js`, `RegimeLab.js`, `RegimeLab.css` – Einbindung + Texte
